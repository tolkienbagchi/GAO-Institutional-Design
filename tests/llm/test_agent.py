"""LLMAgent: attempts, repairs, failure, memory and the guards of the adapter protocol."""

import asyncio
import json

import pytest

from gao_idl.agents import EpisodeContext, EpisodeEnd
from gao_idl.core import events as ev
from gao_idl.core.errors import AgentFailed
from gao_idl.core.ids import AgentId, DecisionId, ReportId, RoleId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.llm.agent import INFEASIBLE_NOTE, LLMAgent, RepairPolicy
from gao_idl.llm.backend import BackendError
from gao_idl.llm.memory import FullTranscript, Window
from gao_idl.llm.mock import MockBackend
from tests.llm.helpers import (
    ALICE,
    BOB,
    CAPPED,
    PD,
    bidders,
    kinds,
    llm,
    play,
    submitted,
    truthful,
)

BID_SCHEMA = {
    "type": "object",
    "properties": {"amount": {"type": "number", "minimum": 0}},
    "required": ["amount"],
}


def request(
    agent: str = "alice", n: int = 0, schema: dict[str, object] | None = None
) -> DecisionRequest:
    return DecisionRequest(
        decision_id=DecisionId(f"auction/bidding#bidder:{n}"),
        path=["auction", "bidding"],
        layer="bidding",
        role=RoleId(f"bidder:{n}"),
        agent_id=AgentId(agent),
        observation={"value": 4.0},
        action_schema=schema or BID_SCHEMA,
    )


def report(agent: str = "alice", n: int = 0) -> ParticipantReport:
    return ParticipantReport(
        report_id=ReportId(f"auction/bidding@{agent}:{n}"),
        path=["auction", "bidding"],
        layer="bidding",
        agent_id=AgentId(agent),
        payload={"own_bid": 3.0},
    )


def start(agent: LLMAgent, keep_conversation: bool = False) -> None:
    asyncio.run(
        agent.episode_start(
            EpisodeContext(
                agent_id=agent.agent_id, env_ref="x", seed=0, keep_conversation=keep_conversation
            )
        )
    )


def act(agent: LLMAgent, req: DecisionRequest) -> dict[str, object]:
    return asyncio.run(asyncio.wait_for(agent.act(req), 5))


# ------------------------------------------------------------- the two reply paths


def test_structured_output_path_and_fallback_text_path() -> None:
    structured, sb = llm([{"amount": 5}])
    text, tb = llm(['Here is my bid: {"amount": 6}'])
    assert act(structured, request()) == {"amount": 5}
    assert act(text, request()) == {"amount": 6}
    # both asked the server for structured output; only the first got a parsed reply back
    assert sb.calls[0].response_schema == BID_SCHEMA == tb.calls[0].response_schema
    assert [m.attempt for m in structured.metadata()] == [1] == [m.attempt for m in text.metadata()]
    assert structured.metadata()[0].failure is None and text.metadata()[0].failure is None


def test_the_backend_is_called_with_the_agent_settings() -> None:
    a, b = llm([{"amount": 1}], temperature=0.7, max_tokens=33, timeout_s=9.0)
    act(a, request())
    (call,) = b.calls
    assert (call.temperature, call.max_tokens, call.timeout_s) == (0.7, 33, 9.0)
    assert call.decision_id == "auction/bidding#bidder:0"


# ------------------------------------------------------------------- repair loop


def test_invalid_json_then_repair_succeeds() -> None:
    a, b = llm(["I bid five", '{"amount": 5}'])
    assert act(a, request()) == {"amount": 5}
    first, second = a.metadata()
    assert (first.attempt, second.attempt) == (1, 2)
    assert first.repair_reason is None and first.failure == "no JSON object found in the reply"
    assert second.repair_reason == first.failure and second.failure is None
    assert "no JSON object found" not in b.calls[0].messages[-1].content
    assert "- no JSON object found in the reply" in b.calls[1].messages[-1].content


def test_schema_violation_repair_is_caught_locally_before_submit() -> None:
    a, b = llm(['{"amount": -1}', '{"amount": "5"}', '{"amount": 5}'])
    session = play(CAPPED, bidders(alice=4.0, bob=3.0), {ALICE: a, BOB: truthful()})
    assert [m.attempt for m in a.metadata()] == [1, 2, 3]
    assert "less than the minimum" in (a.metadata()[0].failure or "")
    assert "not of type number" in (a.metadata()[1].failure or "")  # "5" is a repair, not a cast
    assert submitted(session)[0].action == {"amount": 5} or submitted(session)[1].action == {
        "amount": 5
    }
    assert "action_rejected" not in kinds(session)  # the session never saw the bad ones
    assert [e.action for e in submitted(session) if e.agent_id == ALICE] == [{"amount": 5}]


def test_infeasible_from_session_triggers_runner_redispatch() -> None:
    a, b = llm(['{"amount": 50}', '{"amount": 5}'])
    session = play(CAPPED, bidders(alice=4.0, bob=3.0), {ALICE: a, BOB: truthful()})
    assert kinds(session).count("action_rejected") == 1  # amount > 10 is schema-valid, infeasible
    assert [e.action for e in submitted(session) if e.agent_id == ALICE] == [{"amount": 5}]
    assert len(b.calls) == 2
    assert INFEASIBLE_NOTE not in b.calls[0].messages[-1].content
    assert f"- {INFEASIBLE_NOTE}" in b.calls[1].messages[-1].content
    assert a.metadata()[1].repair_reason == INFEASIBLE_NOTE
    assert a.last_decision_id == "bidding#bidder:0"
    assert not session.failed


def test_a_second_infeasible_answer_times_out_into_the_default() -> None:
    a, _ = llm(['{"amount": 50}', '{"amount": 60}'])
    session = play(CAPPED, bidders(alice=4.0, bob=3.0), {ALICE: a, BOB: truthful()})
    assert "action_defaulted" in kinds(session)
    assert kinds(session).count("agent_failed") == 0  # the agent answered; the session refused


def test_repairs_exhausted_then_timeout_policy() -> None:
    a, b = llm(["no", "still no", "{broken"])
    session = play(CAPPED, bidders(alice=4.0, bob=3.0), {ALICE: a, BOB: truthful()})
    k = kinds(session)
    assert k.index("agent_failed") < k.index("action_defaulted")
    (failed,) = [e for e in session.trace if isinstance(e, ev.AgentFailedEvent)]
    assert failed.agent_id == ALICE and failed.decision_id == "bidding#bidder:0"
    assert len(failed.reasons) == 3 and failed.reasons[0].startswith("attempt 1:")
    assert [e.decision_id for e in session.input_log if isinstance(e, ev.TimedOut)] == [
        "bidding#bidder:0"
    ]
    (defaulted,) = [e for e in session.trace if isinstance(e, ev.ActionDefaulted)]
    assert defaulted.action == {"amount": 0}
    assert len(b.calls) == 3 and not session.failed


def test_transport_error_retries_then_fails() -> None:
    a, b = llm([BackendError("down"), BackendError("down"), BackendError("down")])
    session = play(CAPPED, bidders(alice=4.0, bob=3.0), {ALICE: a, BOB: truthful()})
    assert len(b.calls) == 3
    assert [m.attempt for m in a.metadata()] == [1, 2, 3]
    assert all("backend error: down" in (m.failure or "") for m in a.metadata())
    assert a.metadata()[0].model == "" and a.metadata()[0].usage == {}
    # the model never saw a transport problem as a rejection reason
    assert all("previous reply was rejected" not in c.messages[-1].content for c in b.calls)
    assert kinds(session).count("agent_failed") == 1 and "action_defaulted" in kinds(session)


def test_a_transport_error_then_a_valid_reply_succeeds() -> None:
    a, b = llm([BackendError("blip"), '{"amount": 2}'])
    assert act(a, request()) == {"amount": 2}
    assert len(b.calls) == 2


def test_repair_policy_limits_and_can_hide_the_reasons() -> None:
    a, b = llm(["x", "y"], repair=RepairPolicy(max_attempts=2, include_rejection_reason=False))
    with pytest.raises(AgentFailed) as info:
        act(a, request())
    assert len(info.value.reasons) == 2 and len(b.calls) == 2
    assert "previous reply was rejected" not in b.calls[1].messages[-1].content  # hidden
    assert a.metadata()[1].repair_reason is not None  # but still recorded
    with pytest.raises(ValueError):
        RepairPolicy(max_attempts=0)


def test_non_backend_exceptions_propagate() -> None:
    a, _ = llm([RuntimeError("bug")])
    with pytest.raises(RuntimeError, match="bug"):
        act(a, request())


def test_agent_failed_message_lists_every_attempt() -> None:
    a, _ = llm(["one", "two", "three"])
    with pytest.raises(AgentFailed, match="attempt 1: .*attempt 2: .*attempt 3: ") as info:
        act(a, request())
    assert info.value.reasons[2].startswith("attempt 3:")


# ------------------------------------------------------------------------ memory


def pd_params(rounds: int = 4) -> dict[str, object]:
    return {"agents": ["alice", "bob"], "rounds": rounds, "matching": "fixed"}


def test_window_memory_bounds_messages() -> None:
    a, b = llm([{"move": "C"}] * 6, memory=Window(4))
    session = play(PD, pd_params(6), {ALICE: a, BOB: truthful_pd()})
    assert not session.failed and len(b.calls) == 6
    lengths = [len(c.messages) for c in b.calls]
    assert max(lengths) == 1 + 4 + 1  # system + the last 4 remembered messages + this task
    # a round adds 3 messages (prompt, own action, report): 0, 3, then capped at 4
    assert lengths == [2, 5, 6, 6, 6, 6]


def test_full_transcript_grows_and_stateless_memory_does_not() -> None:
    full, fb = llm([{"move": "C"}] * 4, memory=FullTranscript())
    none, nb = llm([{"move": "C"}] * 4, memory=None)
    play(PD, pd_params(4), {ALICE: full, BOB: truthful_pd()})
    play(PD, pd_params(4), {ALICE: none, BOB: truthful_pd()})
    assert [len(c.messages) for c in fb.calls] == [2, 5, 8, 11]  # +prompt, action, report per round
    assert [len(c.messages) for c in nb.calls] == [2, 2, 2, 2]


def test_memory_holds_own_prompts_own_actions_and_own_reports() -> None:
    a, b = llm([{"move": "D"}, {"move": "C"}], memory=FullTranscript())
    play(PD, pd_params(2), {ALICE: a, BOB: truthful_pd()})
    second = [m.content for m in b.calls[1].messages]
    assert second[1].startswith("Observation:") and '"round_index": 0' in second[1]
    assert second[2] == json.dumps({"move": "D"})  # its own earlier action
    assert second[3].startswith("Report (pd_round):") and '"own_move": "D"' in second[3]
    assert second[4].startswith("Observation:") and '"round_index": 1' in second[4]
    assert "bob" not in " ".join(m.content for m in b.calls[1].messages[1:-1]).replace("other", "")


def test_a_redispatched_decision_is_not_remembered_twice() -> None:
    a, b = llm(['{"amount": 50}', '{"amount": 5}', '{"amount": 6}'], memory=FullTranscript())
    start(a)
    first = act(a, request())
    again = act(a, request())  # the session found it infeasible and asked again
    assert (first, again) == ({"amount": 50}, {"amount": 5})
    asyncio.run(a.episode_end(EpisodeEnd(agent_id=a.agent_id, terminal="normal")))
    assert [m.content for m in a._memory.messages()][1] == json.dumps({"amount": 5})
    assert len(a._memory.messages()) == 2  # one prompt, one (the accepted) action


def pd_truthful_policy(
    request: DecisionRequest, reports: list[ParticipantReport]
) -> dict[str, object]:
    return {"move": "C"}


def truthful_pd():  # type: ignore[no-untyped-def]
    from gao_idl.agents import ScriptedAgent

    return ScriptedAgent(pd_truthful_policy)


# ------------------------------------------------------------------------ guards


def test_reports_for_other_agents_are_refused_and_duplicates_ignored() -> None:
    a, _ = llm([])
    start(a, keep_conversation=True)
    with pytest.raises(ValueError, match="bob"):
        asyncio.run(a.observe(report("bob")))
    asyncio.run(a.observe(report("alice", 0)))
    asyncio.run(a.observe(report("alice", 0)))
    assert len(a._memory.messages()) == 1


def test_requests_and_episodes_for_other_agents_are_refused() -> None:
    a, b = llm([{"amount": 1}])
    with pytest.raises(ValueError):
        act(a, request("bob"))
    with pytest.raises(ValueError):
        asyncio.run(a.episode_start(EpisodeContext(agent_id=AgentId("bob"), env_ref="x", seed=0)))
    assert b.calls == []


def test_episode_start_forgets_the_conversation_unless_asked_not_to() -> None:
    a, _ = llm([{"amount": 1}] * 2, memory=FullTranscript())
    start(a)
    act(a, request())
    asyncio.run(a.episode_end(EpisodeEnd(agent_id=a.agent_id, terminal="normal")))
    assert len(a._memory.messages()) == 2
    start(a, keep_conversation=True)
    assert len(a._memory.messages()) == 2
    start(a)
    assert a._memory.messages() == [] and a.last_decision_id is None


def test_agents_never_share_the_default_memory() -> None:
    a1, _ = llm([{"amount": 1}], "alice")
    a2, _ = llm([{"amount": 1}], "bob")
    act(a1, request("alice"))
    asyncio.run(a1.episode_end(EpisodeEnd(agent_id=a1.agent_id, terminal="normal")))
    assert a2._memory.messages() == [] and a1._memory is not a2._memory


def test_record_text_keeps_prompt_and_reply_otherwise_not() -> None:
    quiet, _ = llm([{"amount": 1}])
    loud, _ = llm([{"amount": 1}], record_text=True)
    act(quiet, request())
    act(loud, request())
    q, l_ = quiet.metadata()[0], loud.metadata()[0]
    assert q.prompt is None and q.response is None
    assert l_.prompt is not None and l_.prompt[-1].content.startswith("Observation:")
    assert l_.response == '{"amount": 1}'
    assert l_.usage["completion_tokens"] > 0 and l_.model == "mock"


def test_metadata_is_a_copy() -> None:
    a, _ = llm([{"amount": 1}])
    act(a, request())
    a.metadata().clear()
    assert len(a.metadata()) == 1


def test_an_empty_script_surfaces_as_a_loud_error_not_a_retry() -> None:
    a = LLMAgent(ALICE, MockBackend([]))
    with pytest.raises(Exception, match="exhausted"):
        act(a, request())


# ------------------------------------------------------- the agent_failed trace event


def test_agent_failed_is_a_trace_event_that_replay_does_not_reproduce() -> None:
    a, _ = llm(["no", "no", "no"])
    session = play(CAPPED, bidders(alice=4.0, bob=3.0), {ALICE: a, BOB: truthful()})
    session.check_determinism()  # agent_failed is excluded, like action_rejected
    restored = type(session).restore(session.snapshot())
    assert "agent_failed" in kinds(session) and "agent_failed" not in kinds(restored)
    assert [e for e in restored.trace if e.kind != "agent_failed"] == [
        e for e in session.trace if e.kind != "agent_failed"
    ]
    (event,) = [e for e in session.trace if isinstance(e, ev.AgentFailedEvent)]
    assert ev.load(ev.dump([event])) == [event]  # JSON round trip


def test_record_agent_failure_needs_a_pending_decision() -> None:
    from gao_idl.core.errors import UnknownDecision
    from gao_idl.runtime.session import Session

    s = Session.start(CAPPED, bidders(alice=1.0, bob=2.0), 0)
    s.advance()
    with pytest.raises(UnknownDecision):
        s.record_agent_failure(DecisionId("nope"), ALICE, ["x"])
    before = s.input_log
    s.record_agent_failure(s.pending()[0].decision_id, ALICE, ["x"])
    assert s.input_log == before  # a trace-only event: nothing enters the input log
