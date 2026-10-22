"""Tutorial 10: congestion pricing as an institution, built layer by layer, then one experiment."""

from typing import Any, Literal

from numpy.random import Generator
from pydantic import BaseModel

from gao_idl import (
    AgentAdapter,
    Config,
    Connector,
    Contract,
    Loop,
    Primitive,
    ScriptedAgent,
    Seq,
    Session,
    SimpleLayer,
    run_sync,
)
from gao_idl.core.events import dump
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Exit
from gao_idl.environments.congestion import tutorial_commuters
from gao_idl.environments.congestion.llm import CongestionReportRenderer, RegulatorCodec
from gao_idl.environments.congestion.model import (
    default_city,
    exposure_kernel,
    route_times,
    trip_cost,
)
from gao_idl.environments.congestion.scripted import greedy_regulator, myopic_commuter
from gao_idl.llm import LLMAgent, MockBackend

DONE, AGAIN, FINISHED = Port("done"), Port("again"), Port("finished")
CITY = default_city()
# c2 lives in Z3 (the disadvantaged zone, value of time 45), c0 in Z1 (65): the two whose route
# changes first as the toll rises
TWO = [tutorial_commuters()[2], tutorial_commuters()[0]]
Metric = Literal["hotspot", "pop_weighted"]
MENU = (0, 10, 15)


# --8<-- [start:stage]
class Stage(BaseModel):
    """The one record every stage of a period hands to the next (the real environment uses one
    model per stage; a single permissive one keeps this walk-through short)."""

    period: int = 0
    periods: int = 1
    toll: int = 0
    last_objective: float | None = None  # what the regulator saw after the previous period
    flows: dict[str, float] = {}
    costs: dict[str, float] = {}
    concentration: dict[str, float] = {}
    objective: float = 0.0


class Trip(BaseModel):
    route: Literal["tolled", "bypass", "transit"]


class TollChoice(BaseModel):
    toll: Literal[0, 10, 15]


def commuter_roles(people: list[Any]) -> dict[RoleId, AgentId]:
    return {RoleId(f"commuter:{i}"): AgentId(c.agent) for i, c in enumerate(people)}


# --8<-- [end:stage]


# --8<-- [start:travel]
def travel_layer(people: list[Any]) -> SimpleLayer:
    """Simultaneous route choice. Nobody sees anybody else's route before the round settles."""
    by_agent = {c.agent: c for c in people}

    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        c = by_agent[cfg.roles[role]]
        free_flow = {r.kind: r.t0 for r in CITY.routes if r.origin == c.home_zone}
        return _Obs(
            period=Stage.model_validate(cfg.state).period,
            zone=c.home_zone,
            value_of_time=c.value_of_time,
            car_available=c.car_available,
            toll=Stage.model_validate(cfg.state).toll,
            fare=CITY.fare,
            route_times=free_flow,
        )

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: Any
    ) -> BaseModel:
        s = Stage.model_validate(cfg.state)
        flows: dict[str, float] = {}
        chosen: dict[str, str] = {}
        for role, action in actions.items():
            agent = cfg.roles[role]
            chosen[agent] = Trip.model_validate(action.model_dump()).route
            rid = f"{by_agent[agent].home_zone}/{chosen[agent]}"
            flows[rid] = flows.get(rid, 0.0) + CITY.agent_weight
        times = route_times(CITY, flows)  # BPR: the more vehicles, the slower the road
        costs = {}
        for agent, kind in chosen.items():
            c = by_agent[agent]
            toll = s.toll if kind == "tolled" else 0.0
            fare = CITY.fare if kind == "transit" else 0.0
            costs[agent] = trip_cost(c.value_of_time, times[f"{c.home_zone}/{kind}"], toll, fare)
        return s.model_copy(update={"flows": flows, "costs": costs})

    return SimpleLayer(
        name="travel",
        entry=Contract(state_schema=Stage, roles={r: {} for r in commuter_roles(people)}),
        exits={DONE: Stage},
        observe=observe,
        action_schema=lambda cfg, role: Trip,
        feasible=lambda cfg, role, a: bool(
            Trip.model_validate(a.model_dump()).route == "transit"
            or by_agent[cfg.roles[role]].car_available
        ),
        outcome=outcome,
        utilities=lambda cfg, out: {
            AgentId(a): -c for a, c in Stage.model_validate(out.model_dump()).costs.items()
        },
        export=lambda cfg, out: Exit(port=DONE, record=out.model_dump(mode="json")),
    )


class _Obs(BaseModel):
    period: int
    zone: str
    value_of_time: float
    car_available: bool
    toll: int
    fare: float
    route_times: dict[str, float]


def build_travel(toll: int = 10) -> tuple[Primitive, Config]:
    """Environment factory for step 1: the travel layer alone with two commuters."""
    state = Stage(toll=toll)
    return Primitive(travel_layer(TWO)), Config(state=state.model_dump(), roles=commuter_roles(TWO))


# --8<-- [end:travel]


# --8<-- [start:exposure]
def exposure_layer(metric: Metric = "hotspot") -> SimpleLayer:
    """Automatic layer (no roles): flows -> emissions -> concentrations -> both equity metrics.

    Swapping ``exposure_kernel`` for a call to a real air-quality pipeline is a change to this
    one function. The exit is ``again`` until the last period, then ``finished``.
    """

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: Any
    ) -> BaseModel:
        s = Stage.model_validate(cfg.state)
        kernel = exposure_kernel(CITY, s.flows, ev_share=0.0)
        objective = getattr(kernel, metric).objective
        return s.model_copy(update={"concentration": kernel.concentration, "objective": objective})

    def export(cfg: Config, out: BaseModel) -> Exit:
        s = Stage.model_validate(out.model_dump())
        last = s.period + 1 >= s.periods
        return Exit(port=FINISHED if last else AGAIN, record=s.model_dump(mode="json"))

    return SimpleLayer(
        name="exposure",
        entry=Contract(state_schema=Stage),
        exits={AGAIN: Stage, FINISHED: Stage},
        outcome=outcome,
        export=export,
    )


def carry(roles: dict[RoleId, AgentId] | None = None) -> Connector:
    """A connector that passes the record on as the next stage's state."""
    return Connector(lambda rec, ledger, store: Config(state=rec, roles=roles or {}), Stage)


def travel_then_exposure(people: list[Any], metric: Metric = "hotspot") -> Seq:
    return Seq(
        Primitive(travel_layer(people)),
        {DONE: (carry(), Primitive(exposure_layer(metric)))},
        name="travel_exposure",
    )


def build_travel_exposure(toll: int = 10) -> tuple[Seq, Config]:
    """Environment factory for step 2: travel, then the exposure kernel."""
    state = Stage(toll=toll)
    return travel_then_exposure(TWO), Config(state=state.model_dump(), roles=commuter_roles(TWO))


# --8<-- [end:exposure]


# --8<-- [start:toll]
def committed_toll(level: int) -> SimpleLayer:
    """The toll is a rule: an automatic layer with no roles."""
    return SimpleLayer(
        name="toll_setting",
        entry=Contract(state_schema=Stage),
        exits={DONE: Stage},
        outcome=lambda cfg, actions, rng, store: Stage.model_validate(cfg.state).model_copy(
            update={"toll": level}
        ),
        export=lambda cfg, out: Exit(port=DONE, record=out.model_dump(mode="json")),
    )


def regulator_toll() -> SimpleLayer:
    """The same stage with a ``regulator`` role choosing from the menu."""
    return SimpleLayer(
        name="toll_setting",
        entry=Contract(state_schema=Stage, roles={RoleId("regulator"): {}}),
        exits={DONE: Stage},
        observe=lambda cfg, role, rng: _RegObs(
            menu=list(MENU), last_objective=Stage.model_validate(cfg.state).last_objective
        ),
        action_schema=lambda cfg, role: TollChoice,
        outcome=lambda cfg, actions, rng, store: Stage.model_validate(cfg.state).model_copy(
            update={
                "toll": TollChoice.model_validate(actions[RoleId("regulator")].model_dump()).toll
            }
        ),
        export=lambda cfg, out: Exit(port=DONE, record=out.model_dump(mode="json")),
    )


class _RegObs(BaseModel):
    menu: list[int]
    last_objective: float | None


def period(toll_stage: SimpleLayer, people: list[Any], metric: Metric = "hotspot") -> Seq:
    """One period: toll setting, then travel, then exposure. The toll stage is interchangeable."""
    roles = commuter_roles(people)
    return Seq(
        Primitive(toll_stage),
        {DONE: (carry(roles), travel_then_exposure(people, metric))},
        name="period",
    )


def build_period(form: str, periods: int = 1, level: int = 10) -> tuple[Seq, Config]:
    """Environment factory for steps 3 and 4: one period under either toll form."""
    stage = committed_toll(level) if form == "committed" else regulator_toll()
    roles: dict[RoleId, AgentId] = (
        {RoleId("regulator"): AgentId("regulator")} if form == "regulator" else {}
    )
    return period(stage, TWO), Config(state=Stage(periods=periods).model_dump(), roles=roles)


# --8<-- [end:toll]


# --8<-- [start:loop]
def build_loop(form: str, periods: int = 3, level: int = 10) -> tuple[Loop, Config]:
    """Step 5: loop the period. The feedback connector carries what the regulator will see."""
    stage = committed_toll(level) if form == "committed" else regulator_toll()
    regulator: dict[RoleId, AgentId] = (
        {RoleId("regulator"): AgentId("regulator")} if form == "regulator" else {}
    )

    def feedback(rec: dict[str, Any], ledger: Any, store: Any) -> Config:
        last = Stage.model_validate(rec)
        state = Stage(period=last.period + 1, periods=last.periods, last_objective=last.objective)
        return Config(state=state.model_dump(), roles=dict(regulator))

    loop = Loop(
        period(stage, TWO),
        {AGAIN: Connector(feedback, Stage)},
        {FINISHED: DONE},
        max_iter=periods + 1,
        on_limit="fail",
        name="city",
    )
    return loop, Config(state=Stage(periods=periods).model_dump(), roles=regulator)


# --8<-- [end:loop]


def two_commuters() -> dict[AgentId, AgentAdapter]:
    return {AgentId(c.agent): ScriptedAgent(myopic_commuter) for c in TWO}


def fixed_toll(toll: int) -> AgentAdapter:
    return ScriptedAgent(lambda request, reports: {"toll": toll})


MODULE = "10_congestion"


def steps_one_to_five() -> None:
    # step 1: the travel layer alone; a myopic commuter takes the cheapest route at free-flow times
    session = Session.start(f"{MODULE}:build_travel", {"toll": 10}, seed=0)
    run_sync(session, two_commuters())
    for c in TWO:
        print(
            f"step 1 {c.agent} (zone {c.home_zone}) utility at toll 10: "
            f"{session.ledger.utility(AgentId(c.agent)):.2f}"
        )

    # step 2: add the exposure kernel; at toll 15 c2 takes the bypass, which loads its own zone Z3
    # while c0 takes transit, which empties the CBD
    for toll in (0, 15):
        session = Session.start(f"{MODULE}:build_travel_exposure", {"toll": toll}, seed=0)
        run_sync(session, two_commuters())
        assert session.result is not None
        conc = Stage.model_validate(session.result.record).concentration
        print(f"step 2 toll {toll:2d}: concentration CBD {conc['CBD']:.2f}, Z3 {conc['Z3']:.2f}")

    # steps 3 and 4: a committed rule and a regulator choosing the same toll look alike downstream
    committed = Session.start(f"{MODULE}:build_period", {"form": "committed"}, seed=0)
    run_sync(committed, two_commuters())
    regulated = Session.start(f"{MODULE}:build_period", {"form": "regulator"}, seed=0)
    agents = {**two_commuters(), AgentId("regulator"): fixed_toll(10)}
    run_sync(regulated, agents)
    same = all(
        committed.ledger.utility(AgentId(c.agent)) == regulated.ledger.utility(AgentId(c.agent))
        for c in TWO
    )
    print("steps 3-4: committed and regulator forms agree for commuters:", same)

    # step 5: loop over three periods
    looped = Session.start(f"{MODULE}:build_loop", {"form": "regulator", "periods": 3}, seed=0)
    agents = {**two_commuters(), AgentId("regulator"): fixed_toll(15)}
    run_sync(looped, agents)
    played = sum(1 for e in looped.trace if e.kind == "layer_completed" and e.layer == "travel")
    print("step 5 periods played:", played)


# --8<-- [start:experiment]
def run_regime(regime: str, periods: int = 6) -> tuple[list[int], dict[str, float]]:
    """Step 6: the real environment, a greedy regulator, one reporting regime."""
    params = {
        "periods": periods,
        "commuters": [c.model_dump() for c in tutorial_commuters()],
        "report_regime": regime,
    }
    session = Session.start("gao_idl.environments.congestion:build_congestion", params, seed=1)
    agents: dict[AgentId, AgentAdapter] = {
        AgentId(c.agent): ScriptedAgent(myopic_commuter) for c in tutorial_commuters()
    }
    agents[AgentId("regulator")] = ScriptedAgent(greedy_regulator)
    run_sync(session, agents)
    tolls = [
        int(i.payload["toll"])
        for i in session.ledger.information(AgentId("regulator"))
        if hasattr(i, "payload") and "toll" in i.payload
    ]
    last = [
        e["outcome"]
        for e in dump(session.trace)
        if e["kind"] == "layer_completed" and e["path"][-1] == "exposure"
    ][-1]
    exposure = {"DAC": 0.0, "non-DAC": 0.0}
    for zone in CITY.zones[1:]:
        exposure["DAC" if zone.is_dac else "non-DAC"] += last["exposure"][zone.name]
    return tolls, exposure


# --8<-- [end:experiment]


# --8<-- [start:llm]
def llm_regulator(periods: int = 3) -> tuple[Session, MockBackend]:
    """Step 7: the regulator is a language model (here a mock that always answers toll 10)."""
    backend = MockBackend(lambda messages: {"toll": 10})
    regulator = LLMAgent(
        AgentId("regulator"), backend, RegulatorCodec(), report_renderer=CongestionReportRenderer()
    )
    params = {"periods": periods, "commuters": [c.model_dump() for c in tutorial_commuters()]}
    session = Session.start("gao_idl.environments.congestion:build_congestion", params, seed=2)
    agents: dict[AgentId, AgentAdapter] = {
        AgentId(c.agent): ScriptedAgent(myopic_commuter) for c in tutorial_commuters()
    }
    agents[AgentId("regulator")] = regulator
    run_sync(session, agents)
    return session, backend


# --8<-- [end:llm]


def main() -> None:
    steps_one_to_five()
    for regime in ("hotspot", "pop_weighted"):
        tolls, exposure = run_regime(regime)
        shown = ", ".join(f"{k} {v:,.0f}" for k, v in exposure.items())
        print(f"step 6 {regime:12s}: tolls {tolls}; final-period exposure {shown}")
    session, backend = llm_regulator()
    session.check_determinism()
    print("step 7 model calls:", len(backend.calls))


if __name__ == "__main__":
    main()
