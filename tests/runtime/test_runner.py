"""The runner: mixed populations, deadlines, retries, human transport, and what adapters see."""

import asyncio
import json
import logging
import threading
import time
from typing import Any

import pytest

from gao_idl.agents import (
    AgentAdapter,
    EpisodeContext,
    EpisodeEnd,
    HumanTransportAgent,
    RandomAgent,
    ScriptedAgent,
)
from gao_idl.core import events as ev
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.runtime.runner import RunnerPolicy, run, run_sync
from gao_idl.runtime.session import Session
from tests.runtime.envs import DEFAULT_TIMEOUT, params

PD = "gao_idl.environments.pd:build_repeated_pd"
AUCTION = "gao_idl.environments.auction:build_auction"
CAPPED = "tests.runtime.envs:bidding_capped"
STALLED = "tests.runtime.envs:stalled"
COUNTER = "tests.composition.envs:loop_counter"
ALICE, BOB = AgentId("alice"), AgentId("bob")
FOUR = [AgentId(a) for a in ("alice", "bob", "carol", "dave")]


def pd_params(agents: list[str], rounds: int, matching: str = "fixed") -> dict[str, Any]:
    return {"agents": agents, "rounds": rounds, "matching": matching}


def tit_for_tat(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    return {"move": reports[-1].payload["other_move"] if reports else "C"}


def always(move: str) -> Any:
    return lambda request, reports: {"move": move}


def kinds(session: Session) -> list[str]:
    return [e.kind for e in session.trace]


class Spy:
    """Wraps an adapter; records every object it is handed. ``tamper`` mutates requests."""

    def __init__(self, inner: AgentAdapter, tamper: bool = False) -> None:
        self.inner, self.tamper = inner, tamper
        self.received: list[Any] = []

    async def episode_start(self, ctx: EpisodeContext) -> None:
        self.received.append(ctx)
        await self.inner.episode_start(ctx)

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        self.received.append(request)
        if self.tamper:
            request.observation["own_history"] = "tampered"
            request.action_schema["tampered"] = True
        return await self.inner.act(request)

    async def observe(self, report: ParticipantReport) -> None:
        self.received.append(report)
        if self.tamper:
            report.payload["own_payoff"] = 1e9
        await self.inner.observe(report)

    async def episode_end(self, end: EpisodeEnd) -> None:
        self.received.append(end)
        await self.inner.episode_end(end)


class NeverAgent:
    """An adapter whose ``act`` never returns."""

    async def episode_start(self, ctx: EpisodeContext) -> None:
        pass

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def observe(self, report: ParticipantReport) -> None:
        pass

    async def episode_end(self, end: EpisodeEnd) -> None:
        pass


class ScriptedAnswers:
    """An adapter that answers from a fixed list (and counts the calls)."""

    def __init__(self, answers: list[dict[str, Any] | Exception]) -> None:
        self.answers, self.calls = list(answers), 0

    async def episode_start(self, ctx: EpisodeContext) -> None:
        pass

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def observe(self, report: ParticipantReport) -> None:
        pass

    async def episode_end(self, end: EpisodeEnd) -> None:
        pass


def bid(amount: float) -> Any:
    return lambda request, reports: {"amount": amount}


# ------------------------------------------------------------------ complete runs


def test_scripted_pd_run_sync() -> None:
    session = Session.start(PD, pd_params(["alice", "bob"], 5), 1)
    adapters: dict[AgentId, AgentAdapter] = {
        ALICE: ScriptedAgent(tit_for_tat),
        BOB: ScriptedAgent(always("D")),
    }
    assert run_sync(session, adapters) is session
    assert session.finished and not session.failed
    assert session.result is not None and session.result.port == "done"
    # round 0: C vs D -> (0, 5); rounds 1-4: D vs D -> (1, 1) each
    assert session.ledger.utility(ALICE) == 0 + 4 * 1
    assert session.ledger.utility(BOB) == 5 + 4 * 1
    assert len(session.reports_for(ALICE)) == len(session.reports_for(BOB)) == 5
    session.check_determinism()


def test_runs_are_reproducible() -> None:
    logs = []
    for _ in range(2):
        session = Session.start(PD, pd_params([str(a) for a in FOUR], 4, "round_robin"), 3)
        run_sync(session, {a: ScriptedAgent(tit_for_tat) for a in FOUR})
        logs.append(ev.dump(session.input_log))
    assert logs[0] == logs[1]


def test_mixed_population_auction() -> None:
    session = Session.start(AUCTION, params(alice=10, bob=8), 4)
    adapters: dict[AgentId, AgentAdapter] = {ALICE: RandomAgent(11), BOB: ScriptedAgent(bid(5))}
    run_sync(session, adapters)
    assert session.finished and not session.failed
    assert len(session.reports_for(ALICE)) == len(session.reports_for(BOB)) == 2
    session.check_determinism()


# ------------------------------------------------------------------ human transport


def _human_pd() -> dict[str, Any]:
    return pd_params(["alice", "bob"], 2)


def test_human_transport_pauses_and_resumes() -> None:
    async def main() -> Session:
        session = Session.start(PD, _human_pd(), 5)
        human = HumanTransportAgent()
        task = asyncio.ensure_future(run(session, {ALICE: human, BOB: ScriptedAgent(always("D"))}))
        (first,) = await human.wait_outstanding()
        assert first.decision_id == "pd/loop[0]/round/branch[pair_0]#row"
        assert first.agent_id == ALICE and not task.done()  # the runner is blocked on the human
        human.provide(first.decision_id, {"move": "C"})
        (second,) = await human.wait_outstanding()
        assert second.decision_id == "pd/loop[1]/round/branch[pair_0]#row"
        assert len(human.inbox) == 1  # last round's report arrived while waiting
        human.provide(second.decision_id, {"move": "C"})
        return await task

    session = asyncio.run(main())
    reference = run_sync(
        Session.start(PD, _human_pd(), 5),
        {ALICE: ScriptedAgent(always("C")), BOB: ScriptedAgent(always("D"))},
    )
    assert session.finished and not session.failed
    assert ev.dump(session.trace) == ev.dump(reference.trace)
    assert session.ledger.snapshot() == reference.ledger.snapshot()


def test_human_transport_snapshot_restore_resumes_in_a_new_runner() -> None:
    async def main() -> tuple[Session, Session]:
        session = Session.start(PD, _human_pd(), 5)
        human = HumanTransportAgent()
        task = asyncio.ensure_future(run(session, {ALICE: human, BOB: ScriptedAgent(always("D"))}))
        (first,) = await human.wait_outstanding()
        human.provide(first.decision_id, {"move": "C"})
        (second,) = await human.wait_outstanding()
        snapshot = json.loads(json.dumps(session.snapshot()))  # mid-wait, through real JSON
        task.cancel()  # the first runner dies with its process
        with pytest.raises(asyncio.CancelledError):
            await task
        assert human.outstanding() == []

        restored = Session.restore(snapshot)
        fresh = HumanTransportAgent()
        task2 = asyncio.ensure_future(
            run(restored, {ALICE: fresh, BOB: ScriptedAgent(always("D"))})
        )
        (again,) = await fresh.wait_outstanding()
        assert again.decision_id == second.decision_id and not task2.done()
        # the fresh agent has caught up on what alice had been told before the restart
        assert [r.report_id for r in fresh.inbox] == [r.report_id for r in human.inbox]
        fresh.provide(again.decision_id, {"move": "C"})
        return restored, await task2

    restored, _ = asyncio.run(main())
    reference = run_sync(
        Session.start(PD, _human_pd(), 5),
        {ALICE: ScriptedAgent(always("C")), BOB: ScriptedAgent(always("D"))},
    )
    assert restored.finished and not restored.failed
    assert ev.dump(restored.trace) == ev.dump(reference.trace)
    assert ev.dump(restored.input_log) == ev.dump(reference.input_log)
    restored.check_determinism()


# ------------------------------------------------------------------ deadlines and retries


def test_deadline_triggers_timeout_policy() -> None:
    session = Session.start(DEFAULT_TIMEOUT, params(alice=10, bob=8), 1)
    adapters: dict[AgentId, AgentAdapter] = {ALICE: NeverAgent(), BOB: ScriptedAgent(bid(3))}
    began = time.monotonic()
    asyncio.run(run(session, adapters, RunnerPolicy(deadline_seconds=0.01)))
    assert time.monotonic() - began < 1.0
    assert session.finished and not session.failed
    defaulted = [e for e in session.trace if isinstance(e, ev.ActionDefaulted)]
    assert [e.decision_id for e in defaulted] == ["bidding#bidder:0"]  # alice, not bob
    assert defaulted[0].action == {"amount": 0}
    assert any(isinstance(e, ev.TimedOut) for e in session.input_log)
    assert "action_accepted" in kinds(session)  # bob's bid went through normally
    session.check_determinism()


def test_deadline_withdraws_the_humans_request() -> None:
    async def main() -> HumanTransportAgent:
        session = Session.start(DEFAULT_TIMEOUT, params(alice=10, bob=8), 1)
        human = HumanTransportAgent()
        await run(
            session, {ALICE: human, BOB: ScriptedAgent(bid(3))}, RunnerPolicy(deadline_seconds=0.01)
        )
        assert "action_defaulted" in kinds(session)
        return human

    assert asyncio.run(main()).outstanding() == []


def test_deadline_with_a_fail_policy_fails_the_session() -> None:
    session = Session.start(PD, pd_params(["alice", "bob"], 2), 1)
    spy = Spy(ScriptedAgent(always("D")))
    asyncio.run(run(session, {ALICE: NeverAgent(), BOB: spy}, RunnerPolicy(deadline_seconds=0.01)))
    assert session.finished and session.failed and session.result is None
    assert "failed" in kinds(session)
    assert [e.terminal for e in spy.received if isinstance(e, EpisodeEnd)] == ["failed"]


def test_an_invalid_answer_is_asked_for_once_more() -> None:
    session = Session.start(DEFAULT_TIMEOUT, params(alice=10, bob=8), 1)
    flaky = ScriptedAnswers([{"amount": "lots"}, {"amount": 4}])
    run_sync(session, {ALICE: flaky, BOB: ScriptedAgent(bid(3))})
    assert flaky.calls == 2
    assert kinds(session).count("action_rejected") == 1
    accepted = {e.decision_id: e.action for e in session.trace if isinstance(e, ev.ActionAccepted)}
    assert accepted["bidding#bidder:0"] == {"amount": 4}
    assert "action_defaulted" not in kinds(session)


@pytest.mark.parametrize(
    "answers, env",
    [
        ([{"amount": "lots"}, {"amount": "many"}], DEFAULT_TIMEOUT),  # SchemaError twice
        ([{"amount": 11}, {"amount": 12}], CAPPED),  # Infeasible twice
    ],
)
def test_two_bad_answers_time_the_decision_out(answers: list[dict[str, Any]], env: str) -> None:
    session = Session.start(env, params(alice=10, bob=8), 1)
    flaky = ScriptedAnswers(answers)
    run_sync(session, {ALICE: flaky, BOB: ScriptedAgent(bid(3))})
    assert flaky.calls == 2  # exactly one retry
    assert kinds(session).count("action_rejected") == 2
    assert [e.decision_id for e in session.trace if isinstance(e, ev.ActionDefaulted)] == [
        "bidding#bidder:0"
    ]


def test_an_infeasible_answer_can_be_fixed_on_the_retry() -> None:
    session = Session.start(CAPPED, params(alice=10, bob=8), 1)
    flaky = ScriptedAnswers([{"amount": 11}, {"amount": 9}])
    run_sync(session, {ALICE: flaky, BOB: ScriptedAgent(bid(3))})
    assert flaky.calls == 2 and "action_defaulted" not in kinds(session)


def test_an_adapter_exception_times_the_decision_out(caplog: pytest.LogCaptureFixture) -> None:
    session = Session.start(DEFAULT_TIMEOUT, params(alice=10, bob=8), 1)
    broken = ScriptedAnswers([RuntimeError("boom")])
    with caplog.at_level(logging.WARNING, logger="gao_idl.runtime.runner"):
        run_sync(session, {ALICE: broken, BOB: ScriptedAgent(bid(3))})
    assert broken.calls == 1  # no retry for an exception
    assert "action_defaulted" in kinds(session)
    assert any("boom" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------ what adapters see


def test_adapter_receives_only_own_payloads() -> None:
    session = Session.start(PD, pd_params([str(a) for a in FOUR], 3, "round_robin"), 2)
    spies = {a: Spy(ScriptedAgent(tit_for_tat)) for a in FOUR}
    run_sync(session, {a: s for a, s in spies.items()})
    allowed = (EpisodeContext, DecisionRequest, ParticipantReport, EpisodeEnd)
    for agent, spy in spies.items():
        assert spy.received, agent
        for obj in spy.received:
            assert type(obj) in allowed  # never a Session, Ledger, Store, Config or outcome
            assert obj.agent_id == agent
        others = [a for a in FOUR if a != agent]
        text = json.dumps([o.model_dump(mode="json") for o in spy.received])
        assert not any(other in text for other in others)  # nothing names another agent
        counts = {t: sum(type(o) is t for o in spy.received) for t in allowed}
        assert counts[EpisodeContext] == counts[EpisodeEnd] == 1
        assert counts[DecisionRequest] == counts[ParticipantReport] == 3


def test_adapters_get_copies_they_cannot_use_to_corrupt_the_session() -> None:
    session = Session.start(PD, pd_params(["alice", "bob"], 3), 2)
    run_sync(
        session,
        {ALICE: Spy(ScriptedAgent(always("C")), tamper=True), BOB: ScriptedAgent(always("D"))},
    )
    text = json.dumps([x.model_dump(mode="json") for x in session.ledger.information(ALICE)])
    assert "tampered" not in text and "1000000000" not in text
    assert session.ledger.utility(ALICE) == 0.0  # C vs D every round
    session.check_determinism()


def test_all_session_calls_happen_in_the_runners_coroutine() -> None:
    calls: list[tuple[str, asyncio.Task[Any] | None, int]] = []

    class Recording(Session):
        def advance(self) -> list[Any]:
            calls.append(("advance", asyncio.current_task(), threading.get_ident()))
            return super().advance()

        def submit(self, agent_id: AgentId, decision_id: Any, action: dict[str, Any]) -> None:
            calls.append(("submit", asyncio.current_task(), threading.get_ident()))
            super().submit(agent_id, decision_id, action)

        def timeout(self, decision_id: Any) -> None:
            calls.append(("timeout", asyncio.current_task(), threading.get_ident()))
            super().timeout(decision_id)

    async def main() -> asyncio.Task[Session]:
        session = Recording.start(DEFAULT_TIMEOUT, params(alice=10, bob=8), 1)
        task = asyncio.ensure_future(
            run(
                session,
                {ALICE: NeverAgent(), BOB: ScriptedAgent(bid(3))},
                RunnerPolicy(deadline_seconds=0.01),
            )
        )
        await task
        return task

    task = asyncio.run(main())
    assert {name for name, _, _ in calls} == {"advance", "submit", "timeout"}
    assert {t for _, t, _ in calls} == {task}
    assert len({thread for _, _, thread in calls}) == 1


def test_the_runner_reads_the_ledger_only_to_disclose_final_utility() -> None:
    class NoLedger(Session):
        @property
        def ledger(self) -> Any:
            raise AssertionError("the runner read the ledger")

    def adapters() -> dict[AgentId, AgentAdapter]:
        return {ALICE: Spy(ScriptedAgent(tit_for_tat)), BOB: Spy(ScriptedAgent(always("D")))}  # type: ignore[dict-item]

    run_sync(NoLedger.start(PD, pd_params(["alice", "bob"], 3), 1), adapters())  # default policy
    with pytest.raises(AssertionError, match="read the ledger"):
        run_sync(
            NoLedger.start(PD, pd_params(["alice", "bob"], 3), 1),
            adapters(),
            RunnerPolicy(disclose_final_utility=True),
        )


def test_final_utility_is_disclosed_only_when_configured() -> None:
    def ends(policy: RunnerPolicy) -> dict[str, float | None]:
        session = Session.start(PD, pd_params(["alice", "bob"], 3), 1)
        spies = {ALICE: Spy(ScriptedAgent(tit_for_tat)), BOB: Spy(ScriptedAgent(always("D")))}
        run_sync(session, dict(spies.items()), policy)  # type: ignore[arg-type]
        return {
            str(a): [o for o in s.received if isinstance(o, EpisodeEnd)][0].final_utility
            for a, s in spies.items()
        }

    assert ends(RunnerPolicy()) == {"alice": None, "bob": None}
    assert ends(RunnerPolicy(disclose_final_utility=True)) == {"alice": 2.0, "bob": 7.0}


@pytest.mark.parametrize(
    "env, env_params, expected",
    [
        (PD, pd_params(["alice", "bob"], 2), "normal"),
        (COUNTER, {"max_iter": 2, "limit": 10}, "truncated"),
        (COUNTER, {"max_iter": 2, "limit": 10, "on_limit": "fail"}, "failed"),
    ],
)
def test_terminal_state_reaches_the_adapters(
    env: str, env_params: dict[str, Any], expected: str
) -> None:
    session = Session.start(env, env_params, 1)
    spy = Spy(ScriptedAgent(always("C")))
    adapters: dict[AgentId, AgentAdapter] = {ALICE: spy, BOB: ScriptedAgent(always("D"))}
    run_sync(session, adapters if env == PD else {ALICE: spy})
    assert [e.terminal for e in spy.received if isinstance(e, EpisodeEnd)] == [expected]


# ------------------------------------------------------------------ misuse and bugs


def test_runner_stalled_detection() -> None:
    session = Session.start(STALLED, {}, 0)
    with pytest.raises(RuntimeError, match="stalled"):
        run_sync(session, {ALICE: ScriptedAgent(always("C"))})


def test_an_agent_without_an_adapter_is_an_error() -> None:
    session = Session.start(PD, pd_params(["alice", "bob"], 1), 1)
    with pytest.raises(RuntimeError, match="no adapter for agent 'bob'"):
        run_sync(session, {ALICE: ScriptedAgent(always("C"))})


def test_a_cancelled_run_leaves_no_tasks_behind() -> None:
    async def main() -> int:
        session = Session.start(PD, pd_params(["alice", "bob"], 1), 1)
        humans = {ALICE: HumanTransportAgent(), BOB: HumanTransportAgent()}
        task = asyncio.ensure_future(run(session, dict(humans.items())))  # type: ignore[arg-type]
        for human in humans.values():
            await human.wait_outstanding()  # both adapter tasks are in flight
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(h.outstanding() == [] for h in humans.values())
        return len(asyncio.all_tasks())

    assert asyncio.run(main()) == 1  # only the test's own task is left


# ------------------------------------------------------------------ the runner dies cleanly


class RecordingHuman(HumanTransportAgent):
    """A human transport that remembers how its episode ended."""

    def __init__(self) -> None:
        super().__init__()
        self.ends: list[EpisodeEnd] = []

    async def episode_end(self, end: EpisodeEnd) -> None:
        self.ends.append(end)
        await super().episode_end(end)


class BadObserver(ScriptedAgent):
    """Raises from ``observe``; ``fail_end`` makes ``episode_end`` raise too."""

    def __init__(self, fail_end: bool = False) -> None:
        super().__init__(always("D"))
        self.fail_end, self.ends = fail_end, []

    async def observe(self, report: ParticipantReport) -> None:
        raise ValueError("observe blew up")

    async def episode_end(self, end: EpisodeEnd) -> None:
        self.ends.append(end)
        if self.fail_end:
            raise KeyError("episode_end blew up too")


async def _die_with_a_human_waiting(
    bob: AgentAdapter,
) -> tuple[RecordingHuman, DecisionRequest, "asyncio.Task[Session]"]:
    """Two pairs: pair_0 finishes (and bob's ``observe`` raises) while carol (human) still waits."""
    session = Session.start(PD, pd_params([str(a) for a in FOUR], 1), 1)
    human = RecordingHuman()
    adapters: dict[AgentId, AgentAdapter] = {
        ALICE: ScriptedAgent(always("C")),
        BOB: bob,
        AgentId("carol"): human,
        AgentId("dave"): ScriptedAgent(always("D")),
    }
    task = asyncio.ensure_future(run(session, adapters))
    (waiting,) = await asyncio.wait_for(human.wait_outstanding(), 5)
    return human, waiting, task


def test_runner_exception_in_observe_cancels_inflight_and_reraises() -> None:
    async def main() -> None:
        bad = BadObserver()
        human, _, task = await _die_with_a_human_waiting(bad)
        with pytest.raises(ValueError, match="observe blew up"):
            await asyncio.wait_for(task, 5)
        assert human.outstanding() == []  # the in-flight act was cancelled and awaited
        assert [e.terminal for e in human.ends] == ["failed"]
        assert [e.terminal for e in bad.ends] == ["failed"]
        assert all(e.final_utility is None for e in human.ends)
        assert len(asyncio.all_tasks()) == 1  # nothing left running

    asyncio.run(main())


def test_a_failing_episode_end_does_not_hide_the_original_exception() -> None:
    async def main() -> None:
        bad = BadObserver(fail_end=True)
        human, _, task = await _die_with_a_human_waiting(bad)
        with (
            pytest.warns(UserWarning, match="episode_end failed for 'bob'"),
            pytest.raises(ValueError, match="observe blew up"),  # not the KeyError
        ):
            await asyncio.wait_for(task, 5)
        assert [e.terminal for e in human.ends] == ["failed"]  # the others still got theirs

    asyncio.run(main())


def test_provide_after_runner_death_raises() -> None:
    async def main() -> None:
        human, waiting, task = await _die_with_a_human_waiting(BadObserver())
        with pytest.raises(ValueError):
            await asyncio.wait_for(task, 5)
        assert human.closed
        with pytest.raises(RuntimeError, match="runner is gone"):
            human.provide(waiting.decision_id, {"move": "C"})

    asyncio.run(main())


def test_provide_after_a_normal_finish_also_raises() -> None:
    async def main() -> None:
        session = Session.start(PD, pd_params(["alice", "bob"], 1), 1)
        human = HumanTransportAgent()
        task = asyncio.ensure_future(run(session, {ALICE: human, BOB: ScriptedAgent(always("D"))}))
        (req,) = await asyncio.wait_for(human.wait_outstanding(), 5)
        human.provide(req.decision_id, {"move": "C"})
        await asyncio.wait_for(task, 5)
        assert human.closed
        with pytest.raises(RuntimeError, match="runner is gone"):
            human.provide(req.decision_id, {"move": "C"})

    asyncio.run(main())
