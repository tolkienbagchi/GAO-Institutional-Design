"""Congestion pricing as an institution: forms, displacement, reporting regimes, learners, LLM."""

import json
from typing import Any

import pytest

from gao_idl.agents import ScriptedAgent
from gao_idl.core import events as ev
from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.environments.congestion import (
    EV_SHARE_E0_E4,
    TOLL_MENU,
    build_congestion,
    tutorial_commuters,
)
from gao_idl.environments.congestion.llm import (
    CommuterCodec,
    CongestionReportRenderer,
    RegulatorCodec,
)
from gao_idl.environments.congestion.model import default_city, draw_population
from gao_idl.environments.congestion.scripted import greedy_regulator, myopic_commuter
from gao_idl.learning import (
    FieldObsCodec,
    QConfig,
    RewardExtractor,
    SchemaActCodec,
    TableAgent,
    TrainSpec,
    default_opponent_factory,
    evaluate,
    train,
)
from gao_idl.llm.agent import LLMAgent
from gao_idl.llm.mock import MockBackend
from gao_idl.runtime.runner import run
from gao_idl.runtime.session import Session
from gao_idl.testing import (
    assert_arrival_invariant,
    assert_noninterference,
    assert_replay_equivalent,
)
from tests.aio import run_test

ENV = "gao_idl.environments.congestion:build_congestion"
REG = AgentId("regulator")
FIXTURE = [c.model_dump() for c in tutorial_commuters()]
COMMUTERS = [AgentId(c["agent"]) for c in FIXTURE]


def params(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "periods": 6,
        "commuters": FIXTURE,
        "toll_policy": {"kind": "regulator"},
    }
    base.update(over)
    return base


def commuter_actions() -> dict[AgentId, dict[str, Any]]:
    """The route every fixture commuter takes under the tutorial fixture at every toll it meets
    is decided by ``myopic_commuter``; for the order-invariance helpers one fixed action each."""
    return {a: {"route": "transit"} for a in COMMUTERS}


def told(session: Session) -> list[ParticipantReport]:
    """The reports delivered to the regulator so far."""
    return [i for i in session.ledger.information(REG) if isinstance(i, ParticipantReport)]


def play(
    p: dict[str, Any],
    toll: int | None = None,
    regulator: Any = None,
    seed: int = 1,
) -> Session:
    """Run to the end: myopic commuters; the regulator plays a fixed toll or ``regulator``."""
    session = Session.start(ENV, p, seed)
    while True:
        session.advance()
        if session.finished:
            return session
        for req in session.pending():
            if req.agent_id == REG:
                action = {"toll": toll} if regulator is None else regulator(req, told(session))
            else:
                action = myopic_commuter(req, [])
            session.submit(req.agent_id, req.decision_id, action)


def periods_of(session: Session) -> list[dict[str, Any]]:
    """The exposure layer's outcome of every period, from the trace."""
    return [
        e["outcome"]
        for e in ev.dump(session.trace)
        if e["kind"] == "layer_completed" and e["path"][-1] == "exposure"
    ]


def reports_of(session: Session, agent: AgentId = REG) -> list[dict[str, Any]]:
    return [
        i.payload
        for i in session.ledger.information(agent)
        if isinstance(i, ParticipantReport) and i.layer == "exposure"
    ]


# ------------------------------------------------------------------ forms of toll setting


def test_committed_and_regulator_forms_agree_on_downstream_trace() -> None:
    committed = play(params(toll_policy={"kind": "committed", "level": 10}))
    regulated = play(params(), toll=10)

    def downstream(s: Session) -> list[dict[str, Any]]:
        # everything below toll_setting, modulo the global clock (the regulator's own events
        # shift it) and modulo what only the regulator form has: the regulator's report/utility
        return ev.strip_paths(
            [
                e
                for e in ev.dump(s.trace)
                if e["path"][-1] in ("travel", "exposure") and '"regulator"' not in json.dumps(e)
            ],
            drop_t=True,
        )

    assert downstream(committed) == downstream(regulated)
    for c in COMMUTERS:
        assert [i.model_dump(mode="json") for i in committed.ledger.information(c)] == [
            i.model_dump(mode="json") for i in regulated.ledger.information(c)
        ]
        assert committed.ledger.utility(c) == regulated.ledger.utility(c)
    assert REG not in {a for e in ev.dump(committed.trace) for a in [e.get("agent_id")]}
    assert committed.result == regulated.result


# ------------------------------------------------------------------ BPR and displacement


def test_bpr_congestion_and_displacement() -> None:
    # toll-sensitive scripted commuters (myopic_commuter): a higher toll moves them from the
    # tolled road to the bypass, which loads the DAC zone Z3 and relieves the CBD
    last = {t: periods_of(play(params(periods=3), toll=t))[-1] for t in TOLL_MENU}
    on_bypass = {
        t: sum(v for r, v in o["flows"].items() if r.endswith("bypass")) for t, o in last.items()
    }
    assert on_bypass[0] <= on_bypass[10] <= on_bypass[15] and on_bypass[0] < on_bypass[15]
    assert last[15]["concentration"]["Z3"] > last[0]["concentration"]["Z3"]
    assert last[15]["concentration"]["CBD"] < last[0]["concentration"]["CBD"]
    # BPR: times rise with the flow and equal the free-flow time when the road is empty
    city = default_city()
    for o in last.values():
        for route in city.routes:
            flow = o["flows"].get(route.rid, 0.0)
            want = (
                route.t0 * (1 + 0.15 * (flow / route.cap) ** 4)
                if route.kind != "transit"
                else route.t0
            )
            assert o["times"][route.rid] == pytest.approx(want)
    assert any(
        o["times"][r.rid] > r.t0 for o in last.values() for r in city.routes if r.kind != "transit"
    )


def test_infeasible_car_route_is_refused() -> None:
    p = params(
        commuters=[
            {"agent": "c0", "home_zone": "Z1", "value_of_time": 30.0, "car_available": False}
        ]
    )
    session = Session.start(ENV, p, 0)
    session.advance()
    session.submit(REG, session.pending()[0].decision_id, {"toll": 0})
    session.advance()
    (req,) = session.pending()
    assert req.observation["car_available"] is False
    with pytest.raises(Exception, match="(?i)feasible|not allowed|infeasible"):
        session.submit(req.agent_id, req.decision_id, {"route": "tolled"})
    session.submit(req.agent_id, req.decision_id, {"route": "transit"})


# ------------------------------------------------------------------ reporting regimes


@pytest.mark.parametrize(
    ("regime", "shown", "hidden"),
    [
        ("hotspot", "hotspot", "pop_weighted"),
        ("pop_weighted", "pop_weighted", "hotspot"),
    ],
)
def test_report_regime_controls_what_the_regulator_sees(
    regime: str, shown: str, hidden: str
) -> None:
    session = play(params(report_regime=regime, public_report=True), toll=10)
    stream = json.dumps([i.model_dump(mode="json") for i in session.ledger.information(REG)])
    assert hidden not in stream
    assert shown in stream
    for report in reports_of(session):
        assert shown in report and hidden not in report
        assert report["payoff"] == -report["objective"]
    # the observation the regulator acted on is the same filtered report
    req = Session.start(ENV, params(report_regime=regime), 0)
    req.advance()
    assert "prev_report" not in req.pending()[0].observation  # period 0: nothing to show yet


def test_regime_both_needs_an_explicit_metric_and_shows_both() -> None:
    with pytest.raises(ContractError, match="equity_metric"):
        build_congestion(report_regime="both")
    session = play(params(report_regime="both", equity_metric="pop_weighted"), toll=10)
    report = reports_of(session)[0]
    assert {"hotspot", "pop_weighted"} <= report.keys()
    assert report["objective"] == report["pop_weighted"]["objective"]


def test_equity_metric_hidden_by_the_regime_is_refused() -> None:
    with pytest.raises(ContractError, match="hidden"):
        build_congestion(report_regime="hotspot", equity_metric="pop_weighted")


def test_commuter_reports_and_public_report() -> None:
    session = play(params(public_report=True), toll=10)
    report = reports_of(session, COMMUTERS[0])[0]
    assert set(report) == {
        "period",
        "own_route",
        "own_cost",
        "own_route_time",
        "zone_concentration",
        "citywide_mean",
    }
    quiet = reports_of(play(params(), toll=10), COMMUTERS[0])[0]
    assert "citywide_mean" not in quiet


# ------------------------------------------------------------------ the headline


def test_equity_metrics_can_disagree() -> None:
    """The hotspot metric prefers toll 15, the population-weighted one toll 10.

    Fixture (``tutorial_commuters`` on ``default_city``): six car-owning commuters, two per
    peripheral zone, who best-respond to the toll at last period's times. Margins between their
    best and second-best route exceed 1 so the choices are stable under BPR. At toll 15 both
    non-DAC zones' commuters move to the bypass and transit, which empties the CBD and leaves no
    zone above the hotspot threshold (objective 0), but the bypass loads the DAC zone, so the
    population-weighted mean is highest there (1.50). At toll 10 a zone still exceeds the
    threshold (objective 1) but the DAC zone is much less loaded (population-weighted 1.24).
    Toll 0 is worse than toll 15 on hotspots (1) and than toll 10 on the weighted mean (1.41).
    """
    objective = {}
    for regime, metric in (("hotspot", "hotspot"), ("pop_weighted", "pop_weighted")):
        objective[regime] = {
            t: periods_of(play(params(periods=3, report_regime=regime), toll=t))[-1][metric][
                "objective"
            ]
            for t in TOLL_MENU
        }
    assert min(objective["hotspot"], key=lambda t: (objective["hotspot"][t], t)) == 15
    assert min(objective["pop_weighted"], key=lambda t: (objective["pop_weighted"][t], t)) == 10
    for regime in objective:  # toll 0 is never best
        assert objective[regime][0] > min(objective[regime].values())

    chosen = {}
    for regime in ("hotspot", "pop_weighted"):
        session = play(params(report_regime=regime), regulator=greedy_regulator)
        chosen[regime] = [r["toll"] for r in reports_of(session)]
    assert chosen["hotspot"][:3] == [0, 10, 15] == chosen["pop_weighted"][:3]  # both explore
    assert chosen["hotspot"][3:] == [15, 15, 15]
    assert chosen["pop_weighted"][3:] == [10, 10, 10]


# ------------------------------------------------------------------ fleet


def test_ev_share_scales_emissions_not_flows() -> None:
    assert EV_SHARE_E0_E4 == (0.0, 0.5, 0.5, 0.5, 0.8)
    base = periods_of(play(params(periods=2, ev_share=0.0), toll=10))[-1]
    half = periods_of(play(params(periods=2, ev_share=0.5), toll=10))[-1]
    most = periods_of(play(params(periods=2, ev_share=0.8), toll=10))[-1]
    assert base["flows"] == half["flows"] == most["flows"]
    for route, e in base["emissions"].items():
        assert half["emissions"][route] == pytest.approx(e * 0.5)
        assert most["emissions"][route] == pytest.approx(e * 0.2)
    assert half["concentration"]["Z3"] == pytest.approx(base["concentration"]["Z3"] * 0.5)
    with pytest.raises(ContractError, match="ev_share"):
        build_congestion(ev_share=1.5)


# ------------------------------------------------------------------ generic invariants


def test_replay_and_partial_restore() -> None:
    session = play(params(periods=3), toll=10)
    assert_replay_equivalent(session)
    want = ev.dump(session.trace)

    def resume(s: Session) -> Session:
        return Session.restore(json.loads(json.dumps(s.snapshot())))

    def finish(s: Session) -> Session:
        for _ in range(100):
            s.advance()
            if s.finished:
                return s
            for req in s.pending():
                action = {"toll": 10} if req.agent_id == REG else myopic_commuter(req, [])
                s.submit(req.agent_id, req.decision_id, action)
        raise AssertionError("did not finish")

    start = Session.start(ENV, params(periods=3), 1)
    assert ev.dump(finish(resume(start)).trace) == want
    mid = Session.start(ENV, params(periods=3), 1)
    mid.advance()
    first = mid.pending()[0]
    mid.submit(first.agent_id, first.decision_id, {"toll": 10})
    mid.advance()
    assert ev.dump(finish(resume(mid)).trace) == want  # commuters' requests pending
    one = mid.pending()[0]
    mid.submit(one.agent_id, one.decision_id, myopic_commuter(one, []))
    assert ev.dump(finish(resume(mid)).trace) == want  # one commuter already answered


def test_arrival_order_does_not_matter() -> None:
    assert_arrival_invariant(
        ENV,
        params(periods=2, commuters=FIXTURE[:4], toll_policy={"kind": "committed", "level": 10}),
        3,
        {a: {"route": "transit"} for a in COMMUTERS[:4]},
    )


def test_noninterference_of_private_value_of_time() -> None:
    """Changing c0's value of time changes nothing anyone else is told (c0's own cost differs)."""
    other = [dict(c) for c in FIXTURE]
    other[0] = {**other[0], "value_of_time": 5.0}
    committed = {"kind": "committed", "level": 10}
    assert_noninterference(
        ENV,
        params(periods=2, toll_policy=committed),
        params(periods=2, toll_policy=committed, commuters=other),
        4,
        {a: {"route": "transit"} for a in COMMUTERS},
        COMMUTERS[1:],
    )
    with pytest.raises(AssertionError):  # the check has teeth: c0 itself is told its own cost
        assert_noninterference(
            ENV,
            params(periods=2, toll_policy=committed),
            params(periods=2, toll_policy=committed, commuters=other),
            4,
            {a: {"route": "transit"} for a in COMMUTERS},
            COMMUTERS[:1],
        )


def test_population_is_a_function_of_the_seed() -> None:
    city = default_city()
    a, b = draw_population(city, 12, 5), draw_population(city, 12, 5)
    assert a == b != draw_population(city, 12, 6)
    p = {"n_commuters": 4, "periods": 2, "population_seed": 5}
    s1 = Session.start(ENV, p, 0)
    s1.advance()
    s2 = Session.start(ENV, p, 99)  # the session seed does not change who the commuters are
    s2.advance()
    assert [r.observation for r in s1.pending()] == [r.observation for r in s2.pending()]


def test_factory_validation() -> None:
    bad_params: list[dict[str, Any]] = [
        {"periods": 0},
        {"n_commuters": True},
        {"toll_policy": {"kind": "committed", "level": 7}},
        {"toll_policy": {"kind": "committed"}},
        {"toll_policy": {"kind": "auction"}},
        {"report_regime": "none"},
        {"public_report": "yes"},
        {"commuters": [{"agent": "regulator", "home_zone": "Z1", "value_of_time": 10}]},
        {"commuters": [{"agent": "a", "home_zone": "CBD", "value_of_time": 10}]},
        {"commuters": []},
    ]
    for bad in bad_params:
        with pytest.raises(ContractError):
            build_congestion(**bad)


# ------------------------------------------------------------------ learners


def learner(regime: str, seed: int = 0) -> tuple[TableAgent, TrainSpec]:
    agent = TableAgent(
        REG,
        FieldObsCodec([]),
        SchemaActCodec(),
        RewardExtractor(fields=["payoff"]),
        QConfig(alpha=0.5, gamma=0.0, epsilon=0.3),
        seed,
    )
    spec = TrainSpec(
        env_ref=ENV,
        params=params(periods=4, report_regime=regime),
        learner=REG,
        episodes=60,
        seed=1,
        opponents={a: "scripted:myopic" for a in COMMUTERS},
    )
    return agent, spec


@pytest.mark.slow
@pytest.mark.parametrize(
    ("regime", "metric", "best"), [("hotspot", "hotspot", 15), ("pop_weighted", "pop_weighted", 10)]
)
def test_tabular_regulator_converges_to_the_toll_minimizing_its_reported_objective(
    regime: str, metric: str, best: int
) -> None:
    agent, spec = learner(regime)
    factory = default_opponent_factory(scripted={"myopic": myopic_commuter})
    result = train(spec, agent, factory)
    played = evaluate(spec, result.checkpoint, factory, episodes=2, seed=9)
    per_period = {
        t: periods_of(play(params(periods=4, report_regime=regime), toll=t))[-1][metric][
            "objective"
        ]
        for t in TOLL_MENU
    }
    assert min(per_period, key=lambda t: (per_period[t], t)) == best
    # the frozen greedy policy earns exactly what the best constant toll earns
    assert played.mean == pytest.approx(-4 * per_period[best])


# ------------------------------------------------------------------ language-model regulator


def test_mocked_llm_regulator_completes_three_periods_and_sees_only_its_report() -> None:
    backend = MockBackend(lambda messages: {"toll": 10})
    llm = LLMAgent(REG, backend, RegulatorCodec(), report_renderer=CongestionReportRenderer())
    agents: dict[AgentId, Any] = {REG: llm}
    agents |= {a: ScriptedAgent(myopic_commuter) for a in COMMUTERS}
    session = Session.start(ENV, params(periods=3, report_regime="hotspot"), 2)
    run_test(run(session, agents))
    assert session.finished and not session.failed and len(backend.calls) == 3
    sent = json.dumps([c.model_dump(mode="json") for c in backend.calls])
    assert "pop_weighted" not in sent  # the regime hides it, and the prompt adds nothing
    assert "hotspot" in sent
    assert "value_of_time" not in sent and "c0" not in sent
    assert_replay_equivalent(session)


def test_commuter_codec_renders_the_observation() -> None:
    session = Session.start(ENV, params(), 0)
    session.advance()
    session.submit(REG, session.pending()[0].decision_id, {"toll": 10})
    session.advance()
    req: DecisionRequest = session.pending()[0]
    text = CommuterCodec().task_prompt(req)
    assert "zone Z1" in text and "toll is 10" in text
