"""A voluntary-carbon-market call auction with verifiers:
``Loop(Seq(verification, market, clearing, audit))``.

Verifiers, paid a fee per report, tell the market how good each project is; sellers and buyers
send sealed orders; a uniform-price call auction clears them; a random audit sometimes reveals
a traded project's true quality, penalizing verifiers who were far off and (if
``reputation_visible``) feeding a track record to the buyers of the next round. The numbers are
stylized: this is an institution to study verifier incentives on, not a model of any real market.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from gao_idl.composition.connector import Connector, LedgerReadOnly
from gao_idl.composition.loop import Loop
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.composition.seq import Seq
from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, RoleId
from gao_idl.core.schemas import Config
from gao_idl.core.store import StoreView
from gao_idl.core.timeout import Fail, TimeoutPolicy
from gao_idl.environments.vcm.clearing import Clearing, clear_call_auction
from gao_idl.environments.vcm.layers import (
    AGAIN,
    DONE,
    FINISHED,
    audit_layer,
    buyer_role,
    clearing_layer,
    market_layer,
    read_last_clearing,
    read_reputation,
    seller_role,
    verification_layer,
    verifier_role,
)
from gao_idl.environments.vcm.model import (
    Buyer,
    Population,
    Seller,
    Verifier,
    draw_population,
    lemons_population,
)
from gao_idl.environments.vcm.schemas import RepStat, RoundState

__all__ = [
    "Buyer",
    "Clearing",
    "Population",
    "Seller",
    "Verifier",
    "build_vcm",
    "clear_call_auction",
    "draw_population",
    "lemons_population",
]


def _population(
    sellers: list[dict[str, Any]] | None,
    buyers: list[dict[str, Any]] | None,
    verifiers: list[dict[str, Any]] | None,
    n: tuple[int | None, int | None, int | None],
    seed: int | None,
    bias: list[float] | None,
    sigma: float,
) -> Population:
    if sellers is None and buyers is None and verifiers is None:
        if all(x is None for x in (*n, seed)):
            base = lemons_population()
            if bias is not None and len(bias) != len(base.verifiers):
                raise ContractError(f"bias needs one entry per verifier ({len(base.verifiers)})")
            return Population(
                sellers=base.sellers,
                buyers=base.buyers,
                verifiers=[
                    v.model_copy(
                        update={"sigma": sigma, "bias": v.bias if bias is None else bias[k]}
                    )
                    for k, v in enumerate(base.verifiers)
                ],
            ).check()
        return draw_population(
            4 if n[0] is None else n[0],
            3 if n[1] is None else n[1],
            2 if n[2] is None else n[2],
            0 if seed is None else seed,
            bias,
            sigma,
        )
    if sellers is None or buyers is None or verifiers is None:
        raise ContractError("give sellers, buyers and verifiers together, or none of them")
    try:
        return Population(
            sellers=[Seller.model_validate(s) for s in sellers],
            buyers=[Buyer.model_validate(b) for b in buyers],
            verifiers=[Verifier.model_validate(v) for v in verifiers],
        ).check()
    except ValidationError as e:
        raise ContractError(f"invalid population: {e}") from e


def build_vcm(
    n_sellers: int | None = None,
    n_buyers: int | None = None,
    n_verifiers: int | None = None,
    rounds: int = 5,
    fee: float = 1.0,
    penalty: float = 5.0,
    tol: float = 0.1,
    p_audit: float = 0.5,
    settle_on: Literal["reported", "true"] = "reported",
    reputation_visible: bool = False,
    market_form: Literal["per_project", "pooled"] = "per_project",
    price_cap: float = 50.0,
    population_seed: int | None = None,
    bias: list[float] | None = None,
    sigma: float = 0.03,
    sellers: list[dict[str, Any]] | None = None,
    buyers: list[dict[str, Any]] | None = None,
    verifiers: list[dict[str, Any]] | None = None,
    timeout: TimeoutPolicy | dict[str, Any] = Fail(),  # noqa: B008 (frozen model)
) -> tuple[Process, Config]:
    """Environment factory; the root process is a ``Loop`` named ``vcm``.

    Each round is ``Seq(verification, market, clearing, audit)``; ``audit`` exits ``again`` or,
    after the last round, ``finished`` (normal termination through the round count, as in the
    PD). The population is, in order of precedence: the lists ``sellers``, ``buyers`` and
    ``verifiers`` if all are given (dicts, see ``model``); a population drawn from
    ``population_seed`` (default 0) with ``n_sellers`` / ``n_buyers`` / ``n_verifiers`` (default
    4 / 3 / 2) if any of those is given; otherwise the default fixture ``lemons_population()``:
    two high-quality, expensive projects verified by an honest verifier and two low-quality, cheap
    ones verified by a verifier that measures 0.3 too high, three buyers (see the tutorial).
    ``bias`` (one per verifier) and ``sigma`` apply to the drawn and the default populations.
    Every project has exactly one verifier.

    ``fee`` is paid per report; ``penalty`` is charged when a traded project is audited and the
    report is off by more than ``tol``; ``p_audit`` is the per-project, per-round audit
    probability. ``settle_on`` is the quality buyers are credited for when a project is not
    audited: ``"reported"`` (what they believed) or ``"true"`` (ground truth, for accounting
    only: buyers are never told). ``reputation_visible`` puts each verifier's audit history in
    the buyers' observations. ``market_form`` is ``"per_project"`` (default: buyers send one
    optional order per project and each project clears in its own uniform-price auction, so a
    basket is what was bid for) or ``"pooled"`` (one order per buyer, one auction, credits
    pooled across sellers pro rata). ``timeout`` is the market layer's policy (``Fail``,
    ``DefaultAction`` or ``DropRole``; both of the latter mean an order for zero credits);
    verification always fails on a missing report, since no report is neutral.
    """
    for name, value in (("rounds", rounds),):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ContractError(f"{name} must be an int >= 1, got {value!r}")
    for name, number in (
        ("fee", fee),
        ("penalty", penalty),
        ("tol", tol),
        ("price_cap", price_cap),
    ):
        if isinstance(number, bool) or not isinstance(number, int | float) or not number >= 0:
            raise ContractError(f"{name} must be a number >= 0, got {number!r}")
    if isinstance(p_audit, bool) or not isinstance(p_audit, int | float) or not 0 <= p_audit <= 1:
        raise ContractError(f"p_audit must be in [0, 1], got {p_audit!r}")
    if settle_on not in ("reported", "true"):
        raise ContractError(f'settle_on must be "reported" or "true", got {settle_on!r}')
    if market_form not in ("per_project", "pooled"):
        raise ContractError(f'market_form must be "per_project" or "pooled", got {market_form!r}')
    if not isinstance(reputation_visible, bool):
        raise ContractError(f"reputation_visible must be a bool, got {reputation_visible!r}")
    try:
        policy: TimeoutPolicy = TypeAdapter(TimeoutPolicy).validate_python(timeout)
    except ValidationError as e:
        raise ContractError(f"invalid timeout policy {timeout!r}: {e}") from e
    pop = _population(
        sellers, buyers, verifiers, (n_sellers, n_buyers, n_verifiers), population_seed, bias, sigma
    )

    verification = verification_layer(pop, fee=fee, penalty=penalty, tol=tol, p_audit=p_audit)
    market = market_layer(pop, price_cap=price_cap, timeout=policy, form=market_form)
    clearing = clearing_layer(pop, market_form)
    audit = audit_layer(
        pop,
        fee=fee,
        penalty=penalty,
        tol=tol,
        p_audit=p_audit,
        settle_on=settle_on,
        reputation_visible=reputation_visible,
        form=market_form,
    )
    verifier_roles: dict[RoleId, AgentId] = {
        verifier_role(k): AgentId(v.agent) for k, v in enumerate(pop.verifiers)
    }
    market_roles: dict[RoleId, AgentId] = {
        **{seller_role(j): AgentId(s.agent) for j, s in enumerate(pop.sellers)},
        **{buyer_role(i): AgentId(b.agent) for i, b in enumerate(pop.buyers)},
    }

    def to_market(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        return Config(
            state=RoundState.model_validate(rec).model_dump(mode="json"), roles=dict(market_roles)
        )

    def carry(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        return Config(state=RoundState.model_validate(rec).model_dump(mode="json"), roles={})

    def next_round(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        done = RoundState.model_validate(rec)
        price, volume, per_project = read_last_clearing(store)
        state = RoundState(
            round=done.round + 1,
            rounds=done.rounds,
            reputation=(
                read_reputation(store, [v.agent for v in pop.verifiers])
                if reputation_visible
                else None
            ),
            last_price=price,
            last_volume=volume,
            last_projects=per_project,
        )
        return Config(state=state.model_dump(mode="json"), roles=dict(verifier_roles))

    tail = Seq(
        Primitive(clearing),
        {DONE: (Connector(carry, RoundState), Primitive(audit))},
        name="clear_audit",
    )
    middle = Seq(
        Primitive(market),
        {DONE: (Connector(carry, RoundState), tail)},
        name="market_clear_audit",
    )
    body = Seq(
        Primitive(verification),
        {DONE: (Connector(to_market, RoundState), middle)},
        name="round",
    )
    feedback = Connector(next_round, RoundState, store_reads=frozenset({"rep:*", "last:*"}))
    loop = Loop(
        body,
        {AGAIN: feedback},
        {FINISHED: DONE},
        max_iter=rounds + 1,
        on_limit="fail",
        name="vcm",
    )
    first = RoundState(
        round=0,
        rounds=rounds,
        reputation={v.agent: RepStat() for v in pop.verifiers} if reputation_visible else None,
    )
    return loop, Config(state=first.model_dump(mode="json"), roles=dict(verifier_roles))
