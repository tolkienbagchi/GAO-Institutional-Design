"""Raw model replies enter the input log; a replay needs no backend calls at all."""

import asyncio
import json
from typing import Any

import pytest

from gao_idl.agents import ScriptedAgent
from gao_idl.core import events as ev
from gao_idl.core.errors import DeterminismError
from gao_idl.core.ids import AgentId
from gao_idl.llm.agent import LLMAgent
from gao_idl.llm.backend import BackendError, current_decision
from gao_idl.llm.mock import MockBackend
from gao_idl.llm.replay import ReplayBackend, ReplayExhausted
from gao_idl.runtime.session import Session
from tests.aio import run_test
from tests.llm.helpers import ALICE, AUCTION, BOB, CAPPED, PD, bidders, kinds, llm, play, truthful


def externals(session: Session) -> list[ev.ExternalResponse]:
    return [e for e in session.input_log if isinstance(e, ev.ExternalResponse)]


def log_kinds(session: Session) -> list[str]:
    return [e.kind for e in session.input_log]


def test_every_attempt_is_logged_before_the_submission_it_led_to() -> None:
    a, _ = llm(["I bid five", '{"amount": 5}'])
    session = play(AUCTION, bidders(alice=4.0, bob=3.0), {ALICE: a, BOB: truthful()})
    assert log_kinds(session) == ["started", "external", "external", "submitted", "submitted"]
    first, second = externals(session)
    assert first.decision_id == second.decision_id == "auction/bidding#bidder:0"
    assert first.payload["attempt"] == 1 and first.payload["text"] == "I bid five"
    assert first.payload["parsed"] is None
    assert second.payload == {
        "attempt": 2,
        "text": '{"amount": 5}',
        "parsed": None,
        "usage": second.payload["usage"],
        "model": "mock",
        "latency_s": 0.0,
    }
    assert "decision_id" not in second.payload  # the envelope key is not part of the payload
    assert "raw" not in second.payload


def test_structured_replies_keep_parsed_and_raw_is_opt_in() -> None:
    plain, _ = llm([{"amount": 1}])
    raw, _ = llm([{"amount": 1}], record_raw=True)
    s1 = play(AUCTION, bidders(alice=4.0, bob=3.0), {ALICE: plain, BOB: truthful()})
    s2 = play(AUCTION, bidders(alice=4.0, bob=3.0), {ALICE: raw, BOB: truthful()})
    assert (
        externals(s1)[0].payload["parsed"] == {"amount": 1}
        and "raw" not in externals(s1)[0].payload
    )
    assert externals(s2)[0].payload["raw"] == {}  # the mock has no raw body; the key is present


def test_failed_attempts_and_transport_errors_are_logged_too() -> None:
    a, _ = llm([BackendError("down"), "x", "y"])
    session = play(CAPPED, bidders(alice=4.0, bob=3.0), {ALICE: a, BOB: truthful()})
    kinds_ = log_kinds(session)
    assert kinds_.count("external") == 3 and kinds_.count("timed_out") == 1
    payloads = [e.payload for e in externals(session)]
    assert payloads[0] == {"attempt": 1, "error": "down"}
    assert [p["attempt"] for p in payloads] == [1, 2, 3]
    assert max(i for i, k in enumerate(kinds_) if k == "external") < kinds_.index("timed_out")


def test_infeasible_redispatch_logs_both_dispatches_in_order() -> None:
    a, _ = llm(['{"amount": 50}', '{"amount": 5}'])
    session = play(CAPPED, bidders(alice=4.0, bob=3.0), {ALICE: a, BOB: truthful()})
    mine = [
        e
        for e in session.input_log
        if isinstance(e, ev.ExternalResponse)
        or (isinstance(e, ev.Submitted) and e.agent_id == ALICE)
    ]
    # both answers are logged; the refused submission never enters the log, only the accepted one
    assert [e.kind for e in mine] == ["external", "external", "submitted"]
    assert [
        json.loads(e.payload["text"]) for e in mine[:2] if isinstance(e, ev.ExternalResponse)
    ] == [
        {"amount": 50},
        {"amount": 5},
    ]


def test_external_responses_in_log_and_replay_without_calls() -> None:
    values = bidders(alice=4.0, bob=3.0)
    ma = MockBackend(["not json", {"amount": 3.5}])
    mb = MockBackend([{"amount": 2.0}])
    live = play(
        AUCTION,
        values,
        {ALICE: LLMAgent(ALICE, ma), BOB: LLMAgent(BOB, mb)},
        seed=11,
    )
    assert len(ma.calls) == 2 and len(mb.calls) == 1
    # through JSON, as a stored snapshot would be
    snapshot = json.loads(json.dumps(live.snapshot()))
    log = [e for e in ev.load(snapshot["log"]) if isinstance(e, ev.INPUT_EVENT_TYPES)]
    replay_a, replay_b = ReplayBackend(log), ReplayBackend(log)
    fresh = MockBackend([])  # a mock that must never be consulted
    again = play(
        AUCTION,
        values,
        {ALICE: LLMAgent(ALICE, replay_a), BOB: LLMAgent(BOB, replay_b)},
        seed=11,
    )
    assert fresh.calls == [] and replay_a.served == 2 and replay_b.served == 1
    assert ev.dump(again.trace) == ev.dump(live.trace)
    assert ev.dump(again.input_log) == ev.dump(live.input_log)  # even the recorded payloads
    assert again.ledger.snapshot() == live.ledger.snapshot()
    # and restoring the snapshot needs no agent at all
    restored = Session.restore(snapshot)
    assert [d for d in ev.dump(restored.trace)] == ev.dump(live.trace)


def test_replaying_a_failed_decision_reproduces_the_failure_and_the_default() -> None:
    values = bidders(alice=4.0, bob=3.0)
    live = play(
        CAPPED,
        values,
        {
            ALICE: LLMAgent(ALICE, MockBackend([BackendError("down"), "nope", '{"bid": 1}'])),
            BOB: truthful(),
        },
    )
    assert kinds(live).count("agent_failed") == 1
    backend = ReplayBackend(live.input_log)
    again = play(CAPPED, values, {ALICE: LLMAgent(ALICE, backend), BOB: truthful()})
    assert ev.dump(again.trace) == ev.dump(live.trace)  # including agent_failed and its reasons
    assert ev.dump(again.input_log) == ev.dump(live.input_log)
    assert backend.remaining() == 0


def test_replay_backend_raises_when_asked_for_more_than_was_recorded() -> None:
    session = Session.start(AUCTION, bidders(alice=4.0, bob=3.0), 0)
    session.advance()
    request = session.pending_for(ALICE)[0]
    agent = LLMAgent(ALICE, ReplayBackend([]))
    # not retried as a transport error: a replay mismatch must be loud
    with pytest.raises(ReplayExhausted):
        run_test(agent.act(request))
    assert issubclass(ReplayExhausted, DeterminismError)
    assert agent.metadata() == []


def test_replay_backend_is_per_decision_and_ignores_other_events() -> None:
    log: list[ev.InputEvent] = [
        ev.Started(env_ref="x", params={}, seed=0),
        ev.ExternalResponse(decision_id="d1", payload={"text": "a1"}),
        ev.ExternalResponse(decision_id="d2", payload={"text": "b1"}),
        ev.ExternalResponse(decision_id="d1", payload={"text": "a2", "parsed": {"k": 1}}),
        ev.ExternalResponse(decision_id="d2", payload={"error": "Authorization: Bearer abc"}),
    ]
    backend = ReplayBackend(log)

    async def ask(did: str | None) -> Any:
        token = current_decision.set(did)
        try:
            return await backend.complete([], None, temperature=0, max_tokens=1, timeout_s=1)
        finally:
            current_decision.reset(token)

    async def main() -> list[Any]:
        out = [await ask("d2"), await ask("d1"), await ask("d1")]
        with pytest.raises(BackendError) as info:
            await ask("d2")
        assert "abc" not in str(info.value)  # recorded errors are scrubbed again on the way out
        with pytest.raises(ReplayExhausted):
            await ask("d1")
        with pytest.raises(ReplayExhausted):
            await ask(None)
        return out

    out = asyncio.run(main())
    assert [o.text for o in out] == ["b1", "a1", "a2"] and out[2].parsed == {"k": 1}
    assert backend.remaining() == 0


def test_mixed_agents_with_a_par_replay_exactly() -> None:
    """Two pairs play in parallel branches; the log order of the externals must not matter."""
    params: dict[str, Any] = {"agents": ["alice", "bob", "carol", "dave"], "rounds": 3}
    mock_a = MockBackend([{"move": "C"}, {"move": "D"}, {"move": "C"}])
    mock_c = MockBackend(['{"move": "D"}', "oops", {"move": "D"}, {"move": "C"}])
    scripted = ScriptedAgent(lambda request, reports: {"move": "C"})
    live = play(
        PD,
        params,
        {
            AgentId("alice"): LLMAgent(AgentId("alice"), mock_a),
            AgentId("bob"): scripted,
            AgentId("carol"): LLMAgent(AgentId("carol"), mock_c),
            AgentId("dave"): ScriptedAgent(lambda request, reports: {"move": "D"}),
        },
        seed=5,
    )
    live.check_determinism()
    again = play(
        PD,
        params,
        {
            AgentId("alice"): LLMAgent(AgentId("alice"), ReplayBackend(live.input_log)),
            AgentId("bob"): ScriptedAgent(lambda request, reports: {"move": "C"}),
            AgentId("carol"): LLMAgent(AgentId("carol"), ReplayBackend(live.input_log)),
            AgentId("dave"): ScriptedAgent(lambda request, reports: {"move": "D"}),
        },
        seed=5,
    )
    assert ev.dump(again.trace) == ev.dump(live.trace)
    assert ev.dump(again.input_log) == ev.dump(live.input_log)
