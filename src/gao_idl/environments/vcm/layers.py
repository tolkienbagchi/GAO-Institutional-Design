"""The four layers of a market round: verification, market, clearing, audit.

The population (qualities, costs, values, the verifiers' assignment) and the rules (fee, penalty,
tolerance, audit probability, settlement basis) are fixed for the session and closed over by the
factory; each layer is a pure function of ``(cfg, actions, rng, store)`` given them.

Stability: internal; it may change without notice. Use the names exported by ``gao_idl`` where
there is one.
"""

from collections.abc import Sequence
from typing import Literal

import numpy as np
from numpy.random import Generator
from pydantic import BaseModel, ConfigDict, Field, create_model

from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config, Exit
from gao_idl.core.store import StoreView
from gao_idl.core.timeout import Fail, TimeoutPolicy
from gao_idl.environments.vcm.clearing import clear_call_auction
from gao_idl.environments.vcm.model import Population
from gao_idl.environments.vcm.schemas import (
    AuditReport,
    BuyerObs,
    ClearingRecord,
    ClearingReport,
    Order,
    ProjectClearing,
    ProjectResult,
    ProjectView,
    RepStat,
    Revelation,
    RoundState,
    SellerObs,
    VerifiedProject,
    VerifierObs,
)

DONE = Port("done")
AGAIN, FINISHED = Port("again"), Port("finished")
STORE_WRITES = frozenset({"rep:*", "last:*"})
SettleOn = Literal["reported", "true"]
MarketForm = Literal["per_project", "pooled"]


def verifier_role(k: int) -> RoleId:
    """Role of the ``k``-th verifier."""
    return RoleId(f"verifier:{k}")


def seller_role(j: int) -> RoleId:
    """Role of the ``j``-th seller."""
    return RoleId(f"seller:{j}")


def buyer_role(i: int) -> RoleId:
    """Role of the ``i``-th buyer."""
    return RoleId(f"buyer:{i}")


def mean_error(stat: RepStat) -> float | None:
    """Mean signed error (report minus revealed quality) over audited reports, or ``None``."""
    return stat.sum_error / stat.audited if stat.audited else None


def _state(cfg: Config) -> RoundState:
    return RoundState.model_validate(cfg.state)


def _carry(cfg: Config, outcome: BaseModel) -> Exit:
    return Exit(port=DONE, record=outcome.model_dump(mode="json"))


def _outcome_state(outcome: BaseModel) -> RoundState:
    if not isinstance(outcome, RoundState):
        raise TypeError(f"expected RoundState, got {type(outcome).__name__}")
    return outcome


# ------------------------------------------------------------------ verification


def verification_layer(
    pop: Population, *, fee: float, penalty: float, tol: float, p_audit: float
) -> SimpleLayer:
    """Simultaneous reports by ``verifier:k``: one bounded float per assigned project.

    Each verifier first sees its own noisy measurement of every assigned project
    (``clip(theta + bias + N(0, sigma))``, drawn from the layer's path-keyed stream) and then
    reports any number in ``[0, 1]``; reporting the measurement is honest. The fee is credited
    for every report. Only ``Fail`` is offered: there is no neutral report to default to.
    """
    verifiers = {verifier_role(k): v for k, v in enumerate(pop.verifiers)}
    roles: dict[RoleId, dict[str, object]] = {r: {} for r in verifiers}

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        v = verifiers[role]
        seen: dict[str, float] = {}
        for project in v.projects:
            noise = float(rng.normal(0.0, v.sigma)) if v.sigma > 0 else 0.0
            seen[project] = float(np.clip(pop.seller(project).theta + v.bias + noise, 0.0, 1.0))
        return VerifierObs(
            round=_state(cfg).round,
            measurements=seen,
            fee=fee,
            penalty=penalty,
            tolerance=tol,
            audit_probability=p_audit,
        )

    def action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
        fields = {p: (float, Field(ge=0.0, le=1.0)) for p in verifiers[role].projects}
        return create_model("Reports", **fields)  # type: ignore[call-overload,no-any-return]

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = _state(cfg)
        reports: dict[str, VerifiedProject] = {}
        for role, action in actions.items():
            v = verifiers[role]
            values = action.model_dump()
            for project in v.projects:
                reports[project] = VerifiedProject(verifier=v.agent, report=float(values[project]))
        return state.model_copy(update={"reports": reports})

    def utilities(cfg: Config, outcome: BaseModel) -> dict[AgentId, float]:
        done = _outcome_state(outcome)
        paid: dict[AgentId, float] = {}
        for project in done.reports.values():
            agent = AgentId(project.verifier)
            paid[agent] = paid.get(agent, 0.0) + fee
        return paid

    return SimpleLayer(
        name="verification",
        entry=Contract(state_schema=RoundState, roles=roles),
        exits={DONE: RoundState},
        timeout=Fail(),
        observe=observe,
        action_schema=action_schema,
        outcome=outcome,
        utilities=utilities,
        export=_carry,
    )


# ------------------------------------------------------------------ market


def market_layer(
    pop: Population,
    *,
    price_cap: float,
    timeout: TimeoutPolicy = Fail(),  # noqa: B008 (frozen model)
    form: MarketForm = "per_project",
) -> SimpleLayer:
    """Simultaneous sealed orders by ``seller:j`` and ``buyer:i``, prices per credit.

    A seller sees only its own project, its verified quality and the last public price; a buyer
    sees the verified projects (quantity, verifier, reported quality) and, if the state carries
    it, the verifiers' track records. Nobody sees another side's or another seller's order.
    ``DefaultAction`` and ``DropRole`` both mean the neutral thing, an order for zero credits.

    ``per_project``: a buyer's action is a generated schema with one optional ``(price, qty)``
    per project id (omitted or ``qty = 0`` means no order); the total over projects must not
    exceed its demand (``feasible``). ``pooled``: one ``(price, qty)`` for the whole pool.
    """
    sellers = {seller_role(j): s for j, s in enumerate(pop.sellers)}
    buyers = {buyer_role(i): b for i, b in enumerate(pop.buyers)}
    roles: dict[RoleId, dict[str, object]] = {r: {} for r in (*sellers, *buyers)}

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        state = _state(cfg)
        if role in sellers:
            s = sellers[role]
            last = state.last_projects.get(s.agent) if state.last_projects else None
            return SellerObs(
                round=state.round,
                project=s.agent,
                quantity=s.quantity,
                cost=s.cost,
                verified_quality=state.reports[s.agent].report,
                last_price=last.price if last else state.last_price,
                last_volume=last.volume if last else state.last_volume,
            )
        b = buyers[role]
        track = None
        if state.reputation is not None:
            track = {}
            for verifier, stat in sorted(state.reputation.items()):
                m = mean_error(stat)
                track[verifier] = {
                    "audited": float(stat.audited),
                    "wrong": float(stat.wrong),
                    "mean_error": 0.0 if m is None else round(m, 2),  # 0.0 with audited == 0
                }
        return BuyerObs(
            round=state.round,
            demand=b.demand,
            value=b.value,
            projects=[
                ProjectView(
                    project=s.agent,
                    verifier=state.reports[s.agent].verifier,
                    quantity=s.quantity,
                    verified_quality=state.reports[s.agent].report,
                    last_price=(
                        state.last_projects[s.agent].price if state.last_projects else None
                    ),
                    last_volume=(
                        state.last_projects[s.agent].volume if state.last_projects else None
                    ),
                )
                for s in pop.sellers
            ],
            reputation=track,
            last_price=state.last_price,
            last_volume=state.last_volume,
        )

    def limit(role: RoleId) -> int:
        return sellers[role].quantity if role in sellers else buyers[role].demand

    def order_model(name: str, qty_limit: int) -> type[BaseModel]:
        return create_model(
            name,
            price=(float, Field(ge=0.0, le=price_cap)),
            qty=(int, Field(ge=0, le=qty_limit)),
        )

    def action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
        if role in sellers:
            return order_model("SellerOrder", limit(role))
        if form == "pooled":
            return order_model("BuyerOrder", limit(role))
        # one optional order per project, bounded by that project's quantity; `extra=forbid`
        # makes a name that is not a listed project an error
        fields = {
            s.agent: (order_model(f"Order_{s.agent}", s.quantity) | None, Field(default=None))
            for s in pop.sellers
        }
        return create_model(  # type: ignore[call-overload,no-any-return]
            "BuyerOrders", __config__=ConfigDict(extra="forbid"), **fields
        )

    def feasible(cfg: Config, role: RoleId, action: BaseModel) -> bool:
        if role in sellers or form == "pooled":
            return True
        total = sum(o["qty"] for o in action.model_dump().values() if o is not None)
        return bool(total <= buyers[role].demand)

    def default_action(cfg: Config, role: RoleId) -> BaseModel:
        if role not in sellers and form == "per_project":
            return action_schema(cfg, role)()
        return action_schema(cfg, role)(price=0.0, qty=0)

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        orders: dict[str, Order] = {}
        bids: dict[str, dict[str, Order]] = {}
        for role, action in actions.items():
            d = action.model_dump()
            if role not in sellers and form == "per_project":
                bids[cfg.roles[role]] = {
                    p: Order(price=float(o["price"]), qty=int(o["qty"]))
                    for p, o in sorted(d.items())
                    if o is not None and o["qty"] > 0
                }
            else:
                orders[cfg.roles[role]] = Order(price=float(d["price"]), qty=int(d["qty"]))
        return _state(cfg).model_copy(update={"orders": orders, "bids": bids})

    return SimpleLayer(
        name="market",
        entry=Contract(state_schema=RoundState, roles=roles),
        exits={DONE: RoundState},
        timeout=timeout,
        observe=observe,
        action_schema=action_schema,
        feasible=feasible,
        default_action=default_action,
        outcome=outcome,
        export=_carry,
    )


# ------------------------------------------------------------------ clearing


def clearing_layer(pop: Population, form: MarketForm = "per_project") -> SimpleLayer:
    """Automatic: the uniform-price call auction(s) over the orders, then money for the trades.

    ``pooled``: one auction for all credits. ``per_project``: one independent auction per
    project, each with the same rule (``clear_call_auction``) over the bids on that project and
    its seller's ask; a buyer's basket is exactly what it bid for. Sellers are credited
    ``fill * (price - cost)`` at their project's price, buyers ``-fill * price`` summed over
    projects. Everyone is told the price and volume (per project in the second form); buyers and
    sellers also their own fills and money.
    """
    sellers = {s.agent: s for s in pop.sellers}
    buyers = {b.agent for b in pop.buyers}
    everyone = [AgentId(a) for a in (*sellers, *buyers, *(v.agent for v in pop.verifiers))]

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = _state(cfg)
        asks = [(a, o.price, o.qty) for a, o in sorted(state.orders.items()) if a in sellers]
        if form == "pooled":
            bids = [(a, o.price, o.qty) for a, o in sorted(state.orders.items()) if a in buyers]
            result = clear_call_auction(bids, asks)
            fills = {**result.bid_fills, **result.ask_fills}
            record = ClearingRecord(price=result.price, volume=result.volume, fills=fills)
            return state.model_copy(update={"clearing": record})
        per: dict[str, ProjectClearing] = {}
        totals: dict[str, float] = {b: 0.0 for b in sorted(state.bids)}
        for project in sorted(sellers):
            bids = [
                (b, o.price, o.qty)
                for b, orders in sorted(state.bids.items())
                if (o := orders.get(project)) is not None
            ]
            result = clear_call_auction(bids, [a for a in asks if a[0] == project])
            fills = {**result.bid_fills, **result.ask_fills}
            per[project] = ProjectClearing(price=result.price, volume=result.volume, fills=fills)
            for agent, fill in fills.items():
                totals[agent] = totals.get(agent, 0.0) + fill
        record = ClearingRecord(
            price=None,
            volume=sum(c.volume for c in per.values()),
            fills=totals,
            projects=per,
        )
        return state.model_copy(update={"clearing": record})

    def utilities(cfg: Config, outcome: BaseModel) -> dict[AgentId, float]:
        record = _clearing(outcome)
        pay: dict[AgentId, float] = {}
        if record.projects is not None:
            for project, c in record.projects.items():
                if c.price is not None:
                    fill = c.fills.get(project, 0.0)
                    pay[AgentId(project)] = fill * (c.price - sellers[project].cost)
            for agent in record.fills:
                if agent in buyers and (paid := record.paid(agent)):
                    pay[AgentId(agent)] = -paid
            return pay
        if record.price is None:
            return {}
        for agent, fill in record.fills.items():
            if agent in sellers:
                pay[AgentId(agent)] = fill * (record.price - sellers[agent].cost)
            else:
                pay[AgentId(agent)] = -fill * record.price
        return pay

    def report(
        cfg: Config, outcome: BaseModel, recipient: AgentId, rng: Generator
    ) -> BaseModel | None:
        record = _clearing(outcome)
        state = _outcome_state(outcome)
        if record.projects is not None:
            shown = {
                p: ProjectResult(price=c.price, volume=c.volume) for p, c in record.projects.items()
            }
            own_fills = own_payment = own_revenue = own_fill = None
            if recipient in buyers and recipient in record.fills:
                own_fills = {
                    p: c.fills[recipient]
                    for p, c in record.projects.items()
                    if recipient in c.fills
                }
                own_payment = record.paid(recipient)
            elif recipient in sellers:
                mine = record.projects[recipient]
                own_fill = mine.fills.get(recipient, 0.0)
                own_revenue = own_fill * mine.price if mine.price is not None else None
            return ClearingReport(
                round=state.round,
                volume=record.volume,
                projects=shown,
                own_fill=own_fill,
                own_fills=own_fills,
                own_payment=own_payment,
                own_revenue=own_revenue,
            )
        own = record.fills.get(recipient)
        price = record.price
        return ClearingReport(
            round=state.round,
            price=price,
            volume=record.volume,
            own_fill=own if own is not None else None,
            own_payment=(own * price)
            if own is not None and price is not None and recipient in buyers
            else None,
            own_revenue=(own * price)
            if own is not None and price is not None and recipient in sellers
            else None,
        )

    def audience(cfg: Config, outcome: BaseModel) -> set[AgentId]:
        return set(everyone)

    return SimpleLayer(
        name="clearing",
        entry=Contract(state_schema=RoundState),
        exits={DONE: RoundState},
        outcome=outcome,
        utilities=utilities,
        export=_carry,
        audience=audience,
        report=report,
    )


def _clearing(outcome: BaseModel) -> ClearingRecord:
    record = _outcome_state(outcome).clearing
    if record is None:
        raise ContractError("the audit and clearing layers need a cleared market")
    return record


# ------------------------------------------------------------------ audit


class AuditResult(BaseModel):
    """What the audit computed: the round's state plus revelations, penalties and buyer values."""

    state: RoundState
    audited: dict[str, Revelation]
    penalties: dict[str, float]
    known_value: dict[str, float]
    settle_value: dict[str, float]


def audit_layer(
    pop: Population,
    *,
    fee: float,
    penalty: float,
    tol: float,
    p_audit: float,
    settle_on: SettleOn,
    reputation_visible: bool,
    form: MarketForm = "per_project",
) -> SimpleLayer:
    """Automatic: audit traded projects at random, penalize, settle buyers, update reputation.

    One uniform draw per project per round, in project order, whether or not it traded, so the
    stream does not depend on what happened in the market. A traded project is audited when its
    draw is below ``p_audit``; its true quality is then public. A verifier whose audited report is
    off by more than ``tol`` pays ``penalty``. A buyer's credits come from the sold projects in
    proportion to their fills (``pooled``; in ``per_project`` from exactly the projects it
    bid on); it is credited ``value`` times the quality it holds: the revealed
    one if audited (or always, if ``settle_on == "true"``), else the reported one. The buyer's
    *report* states the value at the quality it knows, never more. Audit results go into the
    verifiers' stored history (``rep:<verifier>``), the public price into ``last:clearing``.
    Exits ``again`` after every round but the last, ``finished`` after it.
    """
    projects = sorted(s.agent for s in pop.sellers)
    buyers = {b.agent: b for b in pop.buyers}
    verifiers = [v.agent for v in pop.verifiers]
    everyone = [AgentId(a) for a in (*(s.agent for s in pop.sellers), *buyers, *verifiers)]

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = _state(cfg)
        record = _clearing(state)
        draws = rng.random(len(projects))
        traded = {a: f for a, f in record.fills.items() if a in {s.agent for s in pop.sellers}}
        audited: dict[str, Revelation] = {}
        for project, u in zip(projects, draws, strict=True):
            if traded.get(project, 0.0) > 0 and u < p_audit:
                rep = state.reports[project]
                audited[project] = Revelation(
                    verifier=rep.verifier,
                    report=rep.report,
                    true_quality=pop.seller(project).theta,
                )
        penalties = {v: 0.0 for v in verifiers}
        for rev in audited.values():
            if abs(rev.report - rev.true_quality) > tol:
                penalties[rev.verifier] += penalty
        known: dict[str, float] = {}
        settle: dict[str, float] = {}
        volume = float(sum(traded.values()))
        for agent, b in buyers.items():
            fill = record.fills.get(agent, 0.0)
            k = s_ = 0.0
            if fill > 0 and volume > 0:
                if record.projects is not None:
                    held = {p: c.fills.get(agent, 0.0) for p, c in record.projects.items()}
                else:
                    held = {p: fill * sold / volume for p, sold in traded.items()}
                for project, share in held.items():
                    if share <= 0:
                        continue
                    reported = state.reports[project].report
                    revealed = audited.get(project)
                    known_q = revealed.true_quality if revealed else reported
                    holds_true = revealed is not None or settle_on == "true"
                    settle_q = pop.seller(project).theta if holds_true else reported
                    k += share * b.value * known_q
                    s_ += share * b.value * settle_q
            known[agent], settle[agent] = k, s_
        for v in verifiers:
            stat = _read_rep(store, v)
            mine = [r for r in audited.values() if r.verifier == v]
            stat = RepStat(
                audited=stat.audited + len(mine),
                wrong=stat.wrong + sum(1 for r in mine if abs(r.report - r.true_quality) > tol),
                sum_error=stat.sum_error + sum(r.report - r.true_quality for r in mine),
            )
            store.set(f"rep:{v}", stat.model_dump(mode="json"))
        store.set(
            "last:clearing",
            {
                "price": record.price,
                "volume": record.volume,
                "projects": None
                if record.projects is None
                else {
                    p: {"price": c.price, "volume": c.volume} for p, c in record.projects.items()
                },
            },
        )
        return AuditResult(
            state=state,
            audited=audited,
            penalties=penalties,
            known_value=known,
            settle_value=settle,
        )

    def result(outcome: BaseModel) -> AuditResult:
        if not isinstance(outcome, AuditResult):
            raise TypeError(f"expected the audit result, got {type(outcome).__name__}")
        return outcome

    def utilities(cfg: Config, outcome: BaseModel) -> dict[AgentId, float]:
        r = result(outcome)
        pay = {AgentId(v): -p for v, p in r.penalties.items() if p}
        pay.update({AgentId(b): x for b, x in r.settle_value.items() if x})
        return pay

    def audience(cfg: Config, outcome: BaseModel) -> set[AgentId]:
        return set(everyone)

    def report(
        cfg: Config, outcome: BaseModel, recipient: AgentId, rng: Generator
    ) -> BaseModel | None:
        r = result(outcome)
        own_value = own_net = fees = penalties = None
        if recipient in buyers:
            own_value = r.known_value[recipient]
            own_net = own_value - _clearing(r.state).paid(recipient)
        elif recipient in verifiers:
            fees = fee * len(next(v for v in pop.verifiers if v.agent == recipient).projects)
            penalties = r.penalties[recipient]
        return AuditReport(
            round=r.state.round,
            revealed=r.audited,
            own_value=own_value,
            own_net=own_net,
            fees=fees,
            penalties=penalties,
        )

    def export(cfg: Config, outcome: BaseModel) -> Exit:
        state = result(outcome).state
        last = state.round + 1 >= state.rounds
        record = state.model_dump(mode="json")
        return Exit(port=FINISHED if last else AGAIN, record=record)

    exits: dict[Port, type[BaseModel]] = {AGAIN: RoundState, FINISHED: RoundState}
    return SimpleLayer(
        name="audit",
        entry=Contract(
            state_schema=RoundState, store_reads=frozenset({"rep:*"}), store_writes=STORE_WRITES
        ),
        exits=exits,
        outcome=outcome,
        utilities=utilities,
        export=export,
        audience=audience,
        report=report,
    )


def _read_rep(store: StoreView, verifier: str) -> RepStat:
    try:
        return RepStat.model_validate(store.get(f"rep:{verifier}"))
    except KeyError:
        return RepStat()


def read_reputation(store: StoreView, verifiers: Sequence[str]) -> dict[str, RepStat]:
    """Every verifier's stored audit history (a fresh one if never audited)."""
    return {v: _read_rep(store, v) for v in verifiers}


def read_last_clearing(
    store: StoreView,
) -> tuple[float | None, int, dict[str, ProjectResult] | None]:
    """The previous round's public ``(price, volume, per-project results)``.

    ``(None, 0, None)`` before the first round; the third entry is ``None`` in the pooled form.
    """
    try:
        d = store.get("last:clearing")
    except KeyError:
        return None, 0, None
    projects = d.get("projects")
    return (
        d["price"],
        int(d["volume"]),
        None if projects is None else {p: ProjectResult(**r) for p, r in projects.items()},
    )
