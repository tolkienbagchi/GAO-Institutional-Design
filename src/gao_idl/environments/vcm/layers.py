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
from pydantic import BaseModel, Field, create_model

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
) -> SimpleLayer:
    """Simultaneous sealed orders by ``seller:j`` and ``buyer:i``, prices per credit.

    A seller sees only its own project, its verified quality and the last public price; a buyer
    sees the verified projects (quantity, verifier, reported quality) and, if the state carries
    it, the verifiers' track records. Nobody sees another side's or another seller's order.
    ``DefaultAction`` and ``DropRole`` both mean the neutral thing, an order for zero credits.
    """
    sellers = {seller_role(j): s for j, s in enumerate(pop.sellers)}
    buyers = {buyer_role(i): b for i, b in enumerate(pop.buyers)}
    roles: dict[RoleId, dict[str, object]] = {r: {} for r in (*sellers, *buyers)}

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        state = _state(cfg)
        if role in sellers:
            s = sellers[role]
            return SellerObs(
                round=state.round,
                project=s.agent,
                quantity=s.quantity,
                cost=s.cost,
                verified_quality=state.reports[s.agent].report,
                last_price=state.last_price,
                last_volume=state.last_volume,
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
                )
                for s in pop.sellers
            ],
            reputation=track,
            last_price=state.last_price,
            last_volume=state.last_volume,
        )

    def limit(role: RoleId) -> int:
        return sellers[role].quantity if role in sellers else buyers[role].demand

    def action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
        name = "SellerOrder" if role in sellers else "BuyerOrder"
        return create_model(
            name,
            price=(float, Field(ge=0.0, le=price_cap)),
            qty=(int, Field(ge=0, le=limit(role))),
        )

    def default_action(cfg: Config, role: RoleId) -> BaseModel:
        return action_schema(cfg, role)(price=0.0, qty=0)

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        orders: dict[str, Order] = {}
        for role, action in actions.items():
            d = action.model_dump()
            orders[cfg.roles[role]] = Order(price=float(d["price"]), qty=int(d["qty"]))
        return _state(cfg).model_copy(update={"orders": orders})

    return SimpleLayer(
        name="market",
        entry=Contract(state_schema=RoundState, roles=roles),
        exits={DONE: RoundState},
        timeout=timeout,
        observe=observe,
        action_schema=action_schema,
        default_action=default_action,
        outcome=outcome,
        export=_carry,
    )


# ------------------------------------------------------------------ clearing


def clearing_layer(pop: Population) -> SimpleLayer:
    """Automatic: the uniform-price call auction over the orders, then money for the trades.

    Sellers are credited ``fill * (price - cost)``, buyers ``-fill * price``. Everyone is told
    the price and volume; buyers and sellers also their own fill and money.
    """
    sellers = {s.agent: s for s in pop.sellers}
    buyers = {b.agent for b in pop.buyers}
    everyone = [AgentId(a) for a in (*sellers, *buyers, *(v.agent for v in pop.verifiers))]

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = _state(cfg)
        bids = [(a, o.price, o.qty) for a, o in sorted(state.orders.items()) if a in buyers]
        asks = [(a, o.price, o.qty) for a, o in sorted(state.orders.items()) if a in sellers]
        result = clear_call_auction(bids, asks)
        fills = {**result.bid_fills, **result.ask_fills}
        record = ClearingRecord(price=result.price, volume=result.volume, fills=fills)
        return state.model_copy(update={"clearing": record})

    def utilities(cfg: Config, outcome: BaseModel) -> dict[AgentId, float]:
        record = _clearing(outcome)
        if record.price is None:
            return {}
        pay: dict[AgentId, float] = {}
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
) -> SimpleLayer:
    """Automatic: audit traded projects at random, penalize, settle buyers, update reputation.

    One uniform draw per project per round, in project order, whether or not it traded, so the
    stream does not depend on what happened in the market. A traded project is audited when its
    draw is below ``p_audit``; its true quality is then public. A verifier whose audited report is
    off by more than ``tol`` pays ``penalty``. A buyer's credits come from the sold projects in
    proportion to their fills; it is credited ``value`` times the quality it holds: the revealed
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
                for project, sold in traded.items():
                    share = fill * sold / volume
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
        store.set("last:clearing", {"price": record.price, "volume": record.volume})
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
        fills = _clearing(r.state).fills
        price = _clearing(r.state).price
        own_value = own_net = fees = penalties = None
        if recipient in buyers:
            own_value = r.known_value[recipient]
            own_net = own_value - fills.get(recipient, 0.0) * (price or 0.0)
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


def read_last_clearing(store: StoreView) -> tuple[float | None, int]:
    """The previous round's public ``(price, volume)``; ``(None, 0)`` before the first."""
    try:
        d = store.get("last:clearing")
    except KeyError:
        return None, 0
    return d["price"], int(d["volume"])
