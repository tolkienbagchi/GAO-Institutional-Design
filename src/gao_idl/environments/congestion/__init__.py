"""Congestion pricing as an institution: ``Loop(Seq(toll_setting, travel, exposure))``.

A regulator (or a committed rule) sets a toll from ``[0, 10, 15]``; commuters pick a route
simultaneously; an automatic layer turns the flows into emissions, zone concentrations and two
equity metrics, and the *reporting regime* decides which of those metrics the regulator is told.
The model is a stylized, UTECH-shaped surrogate (see ``model``): the point is institutional
comparison, not prediction.

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
from gao_idl.environments.congestion.layers import (
    AGAIN,
    DONE,
    FINISHED,
    REGULATOR,
    Metric,
    Regime,
    committed_toll_layer,
    commuter_role,
    exposure_layer,
    regulator_toll_layer,
    travel_layer,
)
from gao_idl.environments.congestion.model import (
    EV_SHARE_E0_E4,
    City,
    Commuter,
    default_city,
    draw_population,
    tutorial_commuters,
)
from gao_idl.environments.congestion.schemas import (
    TOLL_MENU,
    ExposureExit,
    PeriodIn,
    TollExit,
    TravelExit,
    TravelState,
)

__all__ = [
    "EV_SHARE_E0_E4",
    "TOLL_MENU",
    "build_congestion",
    "default_city",
    "tutorial_commuters",
]

REGULATOR_AGENT = AgentId("regulator")


def _city(city: City | dict[str, Any] | None, k: int) -> City:
    if city is None:
        return default_city(k)
    try:
        return city if isinstance(city, City) else City.model_validate(city)
    except ValidationError as e:
        raise ContractError(f"invalid city: {e}") from e


def _commuters(
    city: City, commuters: list[Commuter] | list[dict[str, Any]] | None, n: int, seed: int
) -> list[Commuter]:
    if commuters is None:
        return draw_population(city, n, seed)
    try:
        out = [c if isinstance(c, Commuter) else Commuter.model_validate(c) for c in commuters]
    except ValidationError as e:
        raise ContractError(f"invalid commuter: {e}") from e
    if not out:
        raise ContractError("commuters must not be empty")
    if len({c.agent for c in out}) != len(out):
        raise ContractError("commuter agent names must be distinct")
    if REGULATOR_AGENT in {c.agent for c in out}:
        raise ContractError(f"{REGULATOR_AGENT!r} is reserved for the regulator")
    bad = sorted({c.home_zone for c in out} - set(city.periphery))
    if bad:
        raise ContractError(f"commuters live in unknown or central zones: {bad}")
    return out


def _policy(toll_policy: dict[str, Any]) -> tuple[Literal["committed", "regulator"], int | None]:
    kind = toll_policy.get("kind") if isinstance(toll_policy, dict) else None
    if kind == "committed" and set(toll_policy) == {"kind", "level"}:
        level = toll_policy["level"]
        if level not in TOLL_MENU or isinstance(level, bool):
            raise ContractError(f"committed level must be one of {list(TOLL_MENU)}, got {level!r}")
        return "committed", int(level)
    if kind == "regulator" and set(toll_policy) == {"kind"}:
        return "regulator", None
    raise ContractError(
        'toll_policy must be {"kind": "committed", "level": 0|10|15} or {"kind": "regulator"}, '
        f"got {toll_policy!r}"
    )


def build_congestion(
    n_commuters: int = 6,
    periods: int = 10,
    toll_policy: dict[str, Any] | None = None,
    report_regime: Regime = "hotspot",
    equity_metric: Metric | None = None,
    ev_share: float = 0.0,
    public_report: bool = False,
    k: int = 3,
    population_seed: int = 0,
    city: City | dict[str, Any] | None = None,
    commuters: list[Commuter] | list[dict[str, Any]] | None = None,
    timeout: TimeoutPolicy | dict[str, Any] = Fail(),  # noqa: B008 (frozen model)
    default_toll: int | None = None,
) -> tuple[Process, Config]:
    """Environment factory; the root process is a ``Loop`` named ``congestion``.

    Each period is ``Seq(toll_setting, travel, exposure)``; ``exposure`` exits ``again`` or, after
    the last period, ``finished`` (normal termination through a counter, as in the PD).

    ``toll_policy`` is ``{"kind": "committed", "level": 0|10|15}`` (automatic layer, no roles) or
    ``{"kind": "regulator"}`` (role ``regulator``, agent ``regulator``); default: regulator. Both
    forms announce the toll to the commuters identically, so everything downstream is the same
    for the same toll. Commuters are roles ``commuter:i`` held by agents ``c0, c1, ...`` unless
    ``commuters`` is given; otherwise their home zones and values of time are drawn from
    ``population_seed`` (a factory parameter: the session seed is not visible to factories).

    ``report_regime`` decides which equity metric(s) the regulator's report carries.
    ``equity_metric`` is the metric the regulator is paid on (utility ``-objective``); it defaults
    to the regime's metric and must be given with ``"both"``. ``public_report`` adds the
    citywide mean concentration to every report. ``ev_share`` in ``[0, 1]`` scales road emission
    factors by ``1 - ev_share``. ``timeout`` is the policy of the regulator and commuter layers
    (``Fail`` or ``DefaultAction``; commuters then take ``transit`` and the regulator
    ``default_toll``).
    """
    for name, value in (("periods", periods), ("n_commuters", n_commuters), ("k", k)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ContractError(f"{name} must be an int >= 1, got {value!r}")
    if report_regime not in ("hotspot", "pop_weighted", "both"):
        raise ContractError(
            f"report_regime must be hotspot, pop_weighted or both: {report_regime!r}"
        )
    if equity_metric is None:
        if report_regime == "both":
            raise ContractError('report_regime "both" needs an explicit equity_metric')
        equity_metric = report_regime
    elif equity_metric not in ("hotspot", "pop_weighted"):
        raise ContractError(f"equity_metric must be hotspot or pop_weighted: {equity_metric!r}")
    if not isinstance(public_report, bool):
        raise ContractError(f"public_report must be a bool, got {public_report!r}")
    if isinstance(ev_share, bool) or not isinstance(ev_share, int | float):
        raise ContractError(f"ev_share must be a number in [0, 1], got {ev_share!r}")
    if not 0.0 <= ev_share <= 1.0:
        raise ContractError(f"ev_share must be a number in [0, 1], got {ev_share!r}")
    try:
        policy: TimeoutPolicy = TypeAdapter(TimeoutPolicy).validate_python(timeout)
    except ValidationError as e:
        raise ContractError(f"invalid timeout policy {timeout!r}: {e}") from e
    kind, level = _policy(toll_policy if toll_policy is not None else {"kind": "regulator"})

    the_city = _city(city, k)
    people = _commuters(the_city, commuters, n_commuters, population_seed)
    agents = [AgentId(c.agent) for c in people]
    regulator = REGULATOR_AGENT if kind == "regulator" else None

    toll_layer = (
        committed_toll_layer(level, agents)
        if level is not None
        else regulator_toll_layer(agents, policy, default_toll)
    )
    travel = travel_layer(the_city, people, policy)
    exposure = exposure_layer(
        the_city,
        people,
        periods=periods,
        ev_share=float(ev_share),
        regime=report_regime,
        equity_metric=equity_metric,
        public_report=public_report,
        regulator=regulator,
    )
    roles_now: dict[RoleId, AgentId] = {REGULATOR: REGULATOR_AGENT} if regulator else {}

    def to_travel(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        toll = TollExit.model_validate(rec)
        state = TravelState(period=toll.period, toll=toll.toll, prev_times=toll.prev_times)
        roles = {commuter_role(i): agent for i, agent in enumerate(agents)}
        return Config(state=state.model_dump(mode="json"), roles=roles)

    def to_exposure(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        state = TravelExit.model_validate(rec)
        return Config(state=state.model_dump(mode="json"), roles={})

    def next_period(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        done = ExposureExit.model_validate(rec)
        state = PeriodIn(
            period=done.period + 1,
            prev_times=store.get("prev:times"),
            prev_report=store.get("prev:report"),
        )
        return Config(state=state.model_dump(mode="json"), roles=dict(roles_now))

    inner = Seq(
        Primitive(travel),
        {DONE: (Connector(to_exposure, TravelExit), Primitive(exposure))},
        name="travel_exposure",
    )
    body = Seq(
        Primitive(toll_layer),
        {DONE: (Connector(to_travel, TravelState), inner)},
        name="period",
    )
    feedback = Connector(next_period, PeriodIn, store_reads=frozenset({"prev:*"}))
    loop = Loop(
        body,
        {AGAIN: feedback},
        {FINISHED: DONE},
        max_iter=periods + 1,
        on_limit="fail",
        name="congestion",
    )
    first = PeriodIn(period=0, prev_times={r.rid: r.t0 for r in the_city.routes})
    return loop, Config(state=first.model_dump(mode="json"), roles=dict(roles_now))
