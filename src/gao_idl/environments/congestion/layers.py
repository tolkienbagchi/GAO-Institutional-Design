"""The three layers of a congestion period: toll setting (two forms), travel, exposure.

Everything a layer needs besides its entry state (the city, the commuter table, ``ev_share``, the
reporting regime) is closed over by the factory in ``__init__``; it is fixed for the whole
session, so the layers stay pure functions of ``(cfg, actions, rng, store)``.

Stability: internal; it may change without notice. Use the names exported by ``gao_idl`` where
there is one.
"""

from collections.abc import Callable, Sequence
from typing import Literal

from numpy.random import Generator
from pydantic import BaseModel

from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config, Exit
from gao_idl.core.store import StoreView
from gao_idl.core.timeout import DefaultAction, DropRole, Fail, TimeoutPolicy
from gao_idl.environments.congestion.model import (
    City,
    Commuter,
    citywide_mean,
    exposure_kernel,
    route_times,
    trip_cost,
)
from gao_idl.environments.congestion.schemas import (
    TOLL_MENU,
    CommuterObs,
    CommuterReport,
    ExposureExit,
    ExposureOutcome,
    PeriodIn,
    RegulatorReport,
    RouteName,
    TollAction,
    TollAnnouncement,
    TollExit,
    TollObs,
    TollOutcome,
    TravelExit,
    TravelOutcome,
    TravelState,
    Trip,
)

DONE = Port("done")
AGAIN, FINISHED = Port("again"), Port("finished")
REGULATOR = RoleId("regulator")
STORE_SCOPE = frozenset({"prev:*"})
Regime = Literal["hotspot", "pop_weighted", "both"]
Metric = Literal["hotspot", "pop_weighted"]


def commuter_role(i: int) -> RoleId:
    """The role of the ``i``-th commuter, ``commuter:i``."""
    return RoleId(f"commuter:{i}")


# ------------------------------------------------------------------ toll setting


def _toll_outcome(cfg: Config, level: int) -> TollOutcome:
    state = PeriodIn.model_validate(cfg.state)
    return TollOutcome(period=state.period, toll=level, prev_times=state.prev_times)


def _toll_export(cfg: Config, outcome: BaseModel) -> Exit:
    if not isinstance(outcome, TollOutcome):
        raise TypeError(f"expected TollOutcome, got {type(outcome).__name__}")
    record = TollExit(period=outcome.period, toll=outcome.toll, prev_times=outcome.prev_times)
    return Exit(port=DONE, record=record.model_dump(mode="json"))


def _announce(
    commuters: Sequence[AgentId],
) -> tuple[
    Callable[[Config, BaseModel], set[AgentId]],
    Callable[[Config, BaseModel, AgentId, Generator], BaseModel | None],
]:
    def audience(cfg: Config, outcome: BaseModel) -> set[AgentId]:
        return set(commuters)

    def report(
        cfg: Config, outcome: BaseModel, recipient: AgentId, rng: Generator
    ) -> BaseModel | None:
        if not isinstance(outcome, TollOutcome):
            raise TypeError(f"expected TollOutcome, got {type(outcome).__name__}")
        return TollAnnouncement(period=outcome.period, toll=outcome.toll)

    return audience, report


def committed_toll_layer(level: int, commuters: Sequence[AgentId]) -> SimpleLayer:
    """Automatic ``toll_setting``: the toll is ``level`` every period and is announced publicly."""
    if level not in TOLL_MENU:
        raise ContractError(f"committed toll level must be one of {list(TOLL_MENU)}, got {level!r}")
    audience, report = _announce(commuters)

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        return _toll_outcome(cfg, level)

    return SimpleLayer(
        name="toll_setting",
        entry=Contract(state_schema=PeriodIn),
        exits={DONE: TollExit},
        outcome=outcome,
        export=_toll_export,
        audience=audience,
        report=report,
    )


def regulator_toll_layer(
    commuters: Sequence[AgentId],
    timeout: TimeoutPolicy = Fail(),  # noqa: B008 (frozen model)
    default_toll: int | None = None,
) -> SimpleLayer:
    """``toll_setting`` with a ``regulator`` role choosing from ``TOLL_MENU``.

    The observation is the previous period's report as the regime filtered it. The announcement
    to the commuters is the same as under the committed form. ``DropRole`` is refused: without a
    toll there is no period.
    """
    if isinstance(timeout, DropRole):
        raise ContractError("toll_setting cannot use DropRole: the period needs a toll")
    if isinstance(timeout, DefaultAction) and default_toll not in TOLL_MENU:
        raise ContractError(f"a DefaultAction timeout needs default_toll from {list(TOLL_MENU)}")
    audience, report = _announce(commuters)

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        state = PeriodIn.model_validate(cfg.state)
        return TollObs(period=state.period, menu=list(TOLL_MENU), prev_report=state.prev_report)

    def action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
        return TollAction

    def default_action(cfg: Config, role: RoleId) -> BaseModel:
        assert default_toll in (0, 10, 15)  # only called under DefaultAction, which requires it
        return TollAction(toll=default_toll)

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        action = actions[REGULATOR]
        if not isinstance(action, TollAction):
            raise TypeError(f"expected TollAction, got {type(action).__name__}")
        return _toll_outcome(cfg, action.toll)

    return SimpleLayer(
        name="toll_setting",
        entry=Contract(state_schema=PeriodIn, roles={REGULATOR: {}}),
        exits={DONE: TollExit},
        timeout=timeout,
        observe=observe,
        action_schema=action_schema,
        default_action=default_action,
        outcome=outcome,
        export=_toll_export,
        audience=audience,
        report=report,
    )


# ------------------------------------------------------------------ travel


def travel_layer(
    city: City,
    commuters: Sequence[Commuter],
    timeout: TimeoutPolicy = Fail(),  # noqa: B008 (frozen model)
) -> SimpleLayer:
    """Simultaneous route choice by ``commuter:i``; BPR times and costs from the joint choice.

    ``feasible`` forbids ``tolled`` and ``bypass`` for a commuter without a car. Under
    ``DefaultAction`` an unanswered commuter takes ``transit``, which is open to everyone.
    ``DropRole`` is refused: the flows need every commuter.
    """
    if isinstance(timeout, DropRole):
        raise ContractError("travel cannot use DropRole: the flows need every commuter's route")
    by_agent = {c.agent: c for c in commuters}
    roles: dict[RoleId, dict[str, object]] = {commuter_role(i): {} for i in range(len(commuters))}

    def commuter_of(cfg: Config, role: RoleId) -> Commuter:
        return by_agent[cfg.roles[role]]

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        state = TravelState.model_validate(cfg.state)
        c = commuter_of(cfg, role)
        return CommuterObs(
            period=state.period,
            zone=c.home_zone,
            value_of_time=c.value_of_time,
            car_available=c.car_available,
            toll=state.toll,
            fare=city.fare,
            route_times={
                r.kind: state.prev_times[r.rid] for r in city.routes if r.origin == c.home_zone
            },
        )

    def action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
        return Trip

    def feasible(cfg: Config, role: RoleId, action: BaseModel) -> bool:
        return isinstance(action, Trip) and (
            action.route == "transit" or commuter_of(cfg, role).car_available
        )

    def default_action(cfg: Config, role: RoleId) -> BaseModel:
        return Trip(route="transit")

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = TravelState.model_validate(cfg.state)
        chosen: dict[str, RouteName] = {}
        flows: dict[str, float] = {}
        for role in sorted(cfg.roles):
            action = actions[role]
            if not isinstance(action, Trip):
                raise TypeError(f"expected Trip for role {role!r}, got {type(action).__name__}")
            agent = cfg.roles[role]
            chosen[str(agent)] = action.route
            rid = f"{by_agent[agent].home_zone}/{action.route}"
            flows[rid] = flows.get(rid, 0.0) + city.agent_weight
        times = route_times(city, flows)
        costs: dict[str, float] = {}
        for name, kind in chosen.items():
            c = by_agent[name]
            costs[name] = trip_cost(
                c.value_of_time,
                times[f"{c.home_zone}/{kind}"],
                float(state.toll) if kind == "tolled" else 0.0,
                city.fare if kind == "transit" else 0.0,
            )
        return TravelOutcome(
            period=state.period,
            toll=state.toll,
            routes=chosen,
            flows=flows,
            times=times,
            costs=costs,
        )

    def utilities(cfg: Config, outcome: BaseModel) -> dict[AgentId, float]:
        if not isinstance(outcome, TravelOutcome):
            raise TypeError(f"expected TravelOutcome, got {type(outcome).__name__}")
        return {AgentId(agent): -cost for agent, cost in outcome.costs.items()}

    def export(cfg: Config, outcome: BaseModel) -> Exit:
        if not isinstance(outcome, TravelOutcome):
            raise TypeError(f"expected TravelOutcome, got {type(outcome).__name__}")
        record = TravelExit.model_validate(outcome.model_dump())
        return Exit(port=DONE, record=record.model_dump(mode="json"))

    return SimpleLayer(
        name="travel",
        entry=Contract(state_schema=TravelState, roles=roles),
        exits={DONE: TravelExit},
        timeout=timeout,
        observe=observe,
        action_schema=action_schema,
        feasible=feasible,
        default_action=default_action,
        outcome=outcome,
        utilities=utilities,
        export=export,
    )


# ------------------------------------------------------------------ exposure


def exposure_layer(
    city: City,
    commuters: Sequence[Commuter],
    *,
    periods: int,
    ev_share: float,
    regime: Regime,
    equity_metric: Metric,
    public_report: bool,
    regulator: AgentId | None,
) -> SimpleLayer:
    """Automatic ``exposure``: the UTECH surrogate, both equity metrics, the regime's report.

    Writes ``prev:times`` and ``prev:report`` to the store for the next period's observations.
    The regulator (if the toll form has one) is paid ``-objective`` of ``equity_metric`` and gets
    only the metrics ``regime`` allows. Exits ``again`` after every period but the last,
    ``finished`` after it.
    """
    by_agent = {c.agent: c for c in commuters}
    shown = {
        "hotspot": regime in ("hotspot", "both"),
        "pop_weighted": regime in ("pop_weighted", "both"),
    }
    if not shown[equity_metric]:
        raise ContractError(
            f"equity_metric {equity_metric!r} is hidden by report_regime {regime!r}"
        )

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = TravelExit.model_validate(cfg.state)
        kernel = exposure_kernel(city, state.flows, ev_share)
        objective = getattr(kernel, equity_metric).objective
        result = ExposureOutcome(
            period=state.period,
            periods=periods,
            toll=state.toll,
            routes=state.routes,
            flows=state.flows,
            times=state.times,
            costs=state.costs,
            concentration=kernel.concentration,
            exposure=kernel.exposure,
            emissions=kernel.emissions,
            hotspot=kernel.hotspot,
            pop_weighted=kernel.pop_weighted,
            citywide_mean=citywide_mean(city, kernel.concentration),
            objective=objective,
        )
        store.set("prev:times", state.times)
        store.set("prev:report", _regulator_report(result).model_dump(mode="json"))
        return result

    def _regulator_report(out: ExposureOutcome) -> RegulatorReport:
        return RegulatorReport(
            period=out.period,
            toll=out.toll,
            objective=out.objective,
            payoff=-out.objective,
            hotspot=out.hotspot if shown["hotspot"] else None,
            pop_weighted=out.pop_weighted if shown["pop_weighted"] else None,
            citywide_mean=out.citywide_mean if public_report else None,
        )

    def audience(cfg: Config, outcome: BaseModel) -> set[AgentId]:
        agents = {AgentId(c.agent) for c in commuters}
        return agents | ({regulator} if regulator is not None else set())

    def report(
        cfg: Config, outcome: BaseModel, recipient: AgentId, rng: Generator
    ) -> BaseModel | None:
        if not isinstance(outcome, ExposureOutcome):
            raise TypeError(f"expected ExposureOutcome, got {type(outcome).__name__}")
        if recipient == regulator:
            return _regulator_report(outcome)
        c = by_agent[recipient]
        kind = outcome.routes[recipient]
        return CommuterReport(
            period=outcome.period,
            own_route=kind,
            own_cost=outcome.costs[recipient],
            own_route_time=outcome.times[f"{c.home_zone}/{kind}"],
            zone_concentration=outcome.concentration[c.home_zone],
            citywide_mean=outcome.citywide_mean if public_report else None,
        )

    def utilities(cfg: Config, outcome: BaseModel) -> dict[AgentId, float]:
        if not isinstance(outcome, ExposureOutcome):
            raise TypeError(f"expected ExposureOutcome, got {type(outcome).__name__}")
        return {regulator: -outcome.objective} if regulator is not None else {}

    def export(cfg: Config, outcome: BaseModel) -> Exit:
        if not isinstance(outcome, ExposureOutcome):
            raise TypeError(f"expected ExposureOutcome, got {type(outcome).__name__}")
        record = ExposureExit.model_validate(outcome.model_dump())
        last = outcome.period + 1 >= outcome.periods
        return Exit(port=FINISHED if last else AGAIN, record=record.model_dump(mode="json"))

    exits: dict[Port, type[BaseModel]] = {AGAIN: ExposureExit, FINISHED: ExposureExit}
    return SimpleLayer(
        name="exposure",
        entry=Contract(state_schema=TravelExit, store_writes=STORE_SCOPE),
        exits=exits,
        outcome=outcome,
        utilities=utilities,
        export=export,
        audience=audience,
        report=report,
    )
