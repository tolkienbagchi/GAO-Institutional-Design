import json
from typing import Any

import numpy as np
import pytest

from gao_idl.core import events as ev
from gao_idl.core.errors import (
    DeterminismError,
    SchemaError,
    SessionFinished,
    UnknownDecision,
    WrongAgent,
)
from gao_idl.core.ids import AgentId, DecisionId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.environments.auction.bidding import BidReceipt
from gao_idl.runtime.session import Session
from gao_idl.testing import (
    assert_arrival_invariant,
    assert_incremental_equals_fold,
    assert_noninterference,
    assert_replay_equivalent,
)
from tests.runtime import envs

ENV = envs.BIDDING
ALICE, BOB = AgentId("alice"), AgentId("bob")
DA, DB = DecisionId("bidding#bidder:0"), DecisionId("bidding#bidder:1")
PARAMS = envs.params(alice=10, bob=7)


def fresh(env: str = ENV, params: dict[str, Any] | None = None, seed: int = 1) -> Session:
    s = Session.start(env, params or PARAMS, seed)
    s.advance()
    return s


def kinds(s: Session) -> list[str]:
    return [e.kind for e in s.trace]


def dumped(s: Session, *, drop_rejections: bool = False) -> list[dict[str, Any]]:
    out = ev.dump(s.trace)
    return [d for d in out if not (drop_rejections and d["kind"] == "action_rejected")]


def test_requests_precede_any_submission() -> None:
    s = fresh()
    assert len(s.pending()) == 2
    for agent, value, other in ((ALICE, 10.0, 7.0), (BOB, 7.0, 10.0)):
        (req,) = s.pending_for(agent)
        assert req.observation["value"] == value
        assert "values" not in req.observation
        assert other not in req.observation.values()  # the other bidder's value never appears
    assert kinds(s) == ["layer_entered", "decision_requested", "decision_requested"]
    assert [e.kind for e in s.input_log] == ["started"]


def test_pending_before_first_advance_is_empty() -> None:
    s = Session.start(ENV, PARAMS, 1)
    assert s.pending() == [] and s.trace == []
    with pytest.raises(UnknownDecision):
        s.submit(ALICE, DA, {"amount": 1})


def test_barrier_and_no_disclosure() -> None:
    s = fresh()
    (before,) = s.pending_for(BOB)
    s.submit(ALICE, DA, {"amount": 5})
    new = s.advance()
    assert not s.finished
    assert new == []  # nothing about alice's action has been disclosed
    assert "action_accepted" not in kinds(s)
    (after,) = s.pending_for(BOB)
    assert after.observation == before.observation
    assert s.pending_for(ALICE) == []
    assert s.reports_for(BOB) == []
    assert s.reports_for(ALICE) == []


def test_completion_reports_and_exit() -> None:
    s = fresh()
    s.submit(ALICE, DA, {"amount": 5})
    s.submit(BOB, DB, {"amount": 6.5})
    s.advance()
    assert s.finished and not s.failed
    assert s.result is not None and s.result.port == "settle"
    assert s.result.record == {
        "bids": {"alice": 5.0, "bob": 6.5},
        "values": {"alice": 10.0, "bob": 7.0},
        "rule": "second_price",
        "reveal_bids": False,
    }
    for agent, own in ((ALICE, 5.0), (BOB, 6.5)):
        (report,) = s.reports_for(agent)
        assert report.report_id == f"bidding@{agent}:0"
        assert BidReceipt.model_validate(report.payload) == BidReceipt(
            status="bid accepted", own_bid=own
        )
    assert kinds(s) == [
        "layer_entered",
        "decision_requested",
        "decision_requested",
        "action_accepted",
        "action_accepted",
        "layer_completed",
        "report_delivered",
        "report_delivered",
        "exited",
    ]
    assert s.pending() == []
    # each agent's information stream is: its request, then its report
    types = [type(x) for x in s.ledger.information(ALICE)]
    assert types == [DecisionRequest, ParticipantReport]


def test_rejections() -> None:
    s = fresh()
    invalid = [
        {"amount": -1},
        {"amount": float("nan")},
        {"amount": float("inf")},
        {"amount": "abc"},
        {"amount": "5"},
        {"amount": True},
        {},
    ]
    for action in invalid:
        with pytest.raises(SchemaError):
            s.submit(ALICE, DA, action)
    with pytest.raises(WrongAgent):
        s.submit(BOB, DA, {"amount": 1})
    s.submit(ALICE, DA, {"amount": 1})
    with pytest.raises(UnknownDecision):  # stale: already accepted
        s.submit(ALICE, DA, {"amount": 2})
    with pytest.raises(UnknownDecision):
        s.submit(ALICE, DecisionId("nonexistent#role"), {"amount": 1})
    s.submit(BOB, DB, {"amount": 2})
    s.advance()
    assert s.finished
    with pytest.raises(SessionFinished):
        s.submit(BOB, DB, {"amount": 3})

    rejected = [e for e in s.trace if isinstance(e, ev.ActionRejected)]
    assert [e.reason for e in rejected] == (
        ["SchemaError"] * len(invalid)
        + ["WrongAgent", "UnknownDecision", "UnknownDecision", "SessionFinished"]
    )
    assert [e.kind for e in s.input_log] == ["started", "submitted", "submitted"]


def test_rejections_do_not_perturb_the_rest_of_the_trace() -> None:
    clean = fresh()
    clean.submit(ALICE, DA, {"amount": 1})
    clean.submit(BOB, DB, {"amount": 2})
    clean.advance()

    noisy = fresh()
    with pytest.raises(SchemaError):
        noisy.submit(ALICE, DA, {"amount": -1})
    noisy.submit(ALICE, DA, {"amount": 1})
    with pytest.raises(UnknownDecision):
        noisy.submit(ALICE, DA, {"amount": 1})
    noisy.submit(BOB, DB, {"amount": 2})
    noisy.advance()
    assert dumped(noisy, drop_rejections=True) == dumped(clean)
    assert noisy.input_log == clean.input_log


def test_timeout_fail_policy() -> None:
    s = fresh()
    s.timeout(DA)
    s.advance()
    assert s.finished and s.failed and s.result is None
    assert kinds(s)[-1] == "failed"
    n = len(s.trace)
    assert s.advance() == [] and len(s.trace) == n
    with pytest.raises(SessionFinished):
        s.submit(BOB, DB, {"amount": 1})
    assert [e.kind for e in s.input_log] == ["started", "timed_out"]


def test_timeout_of_unknown_decision() -> None:
    s = fresh()
    with pytest.raises(UnknownDecision):
        s.timeout(DecisionId("nope#role"))
    s.timeout(DA)
    with pytest.raises(UnknownDecision):
        s.timeout(DA)  # already queued


def test_timeout_default_policy() -> None:
    s = fresh(envs.DEFAULT_TIMEOUT)
    s.timeout(DA)
    s.advance()
    assert not s.finished
    assert [r.decision_id for r in s.pending()] == [DB]
    s.submit(BOB, DB, {"amount": 5})
    s.advance()
    assert s.finished and s.result is not None
    assert s.result.record["bids"] == {"alice": 0.0, "bob": 5.0}
    assert "action_defaulted" in kinds(s)
    assert kinds(s).count("action_accepted") == 1  # defaulted is distinguishable from submitted
    log_kinds = [e.kind for e in s.input_log]
    assert log_kinds == ["started", "timed_out", "submitted"]
    submitted = [e for e in s.input_log if isinstance(e, ev.Submitted)]
    assert all(e.decision_id != DA for e in submitted)
    assert_replay_equivalent(s)


def test_timeout_drop_policy() -> None:
    s = fresh(envs.DROP_TIMEOUT)
    s.timeout(DB)
    s.submit(ALICE, DA, {"amount": 3})
    s.advance()
    assert s.finished and s.result is not None
    assert s.result.record["bids"] == {"alice": 3.0}
    assert "role_dropped" in kinds(s)
    assert s.reports_for(BOB) == []  # no bid, so no receipt
    assert_replay_equivalent(s)


def test_arrival_invariance() -> None:
    assert_arrival_invariant(ENV, PARAMS, 3, {ALICE: {"amount": 5}, BOB: {"amount": 6}})


def test_arrival_invariance_three_bidders() -> None:
    p = envs.params(alice=10, bob=7, carol=3)
    acts = {ALICE: {"amount": 5}, BOB: {"amount": 6}, AgentId("carol"): {"amount": 2.5}}
    assert_arrival_invariant(ENV, p, 11, acts)


def test_arrival_invariance_many_bidders_uses_rotations() -> None:
    names = [f"b{i}" for i in range(7)]
    p = envs.params(**{n: float(i + 1) for i, n in enumerate(names)})
    acts = {AgentId(n): {"amount": float(i)} for i, n in enumerate(names)}
    assert_arrival_invariant(ENV, p, 2, acts)


def test_noninterference_of_other_value() -> None:
    pa = {"values": {"alice": 9, "bob": 7}}
    pb = {"values": {"alice": 9, "bob": 100}}
    acts = {ALICE: {"amount": 4}, BOB: {"amount": 5}}
    assert_noninterference(ENV, pa, pb, 1, acts, [ALICE])


def test_noninterference_helper_detects_a_leak() -> None:
    pa = {"values": {"alice": 9, "bob": 7}}
    pb = {"values": {"alice": 9, "bob": 100}}
    acts = {ALICE: {"amount": 4}, BOB: {"amount": 5}}
    with pytest.raises(AssertionError):
        assert_noninterference(envs.LEAKY, pa, pb, 1, acts, [ALICE])


def test_noninterference_own_value_does_matter() -> None:
    pa = {"values": {"alice": 9, "bob": 7}}
    pb = {"values": {"alice": 8, "bob": 7}}
    acts = {ALICE: {"amount": 4}, BOB: {"amount": 5}}
    with pytest.raises(AssertionError):
        assert_noninterference(ENV, pa, pb, 1, acts, [ALICE])  # alice sees her own value


def test_replay_and_snapshot() -> None:
    s = fresh()
    s.submit(ALICE, DA, {"amount": 5})
    s.submit(BOB, DB, {"amount": 6})
    s.advance()
    assert s.finished
    s.check_determinism()
    assert_replay_equivalent(s)
    assert_incremental_equals_fold(s)
    snap = json.loads(json.dumps(s.snapshot()))
    assert Session.restore(snap).trace == s.trace
    assert Session.restore(snap).input_log == s.input_log

    # partially complete: one bid in
    p = fresh()
    p.submit(ALICE, DA, {"amount": 5})
    p.advance()
    partial = json.loads(json.dumps(p.snapshot()))
    r = Session.restore(partial)
    assert r.pending() == p.pending() and [x.decision_id for x in r.pending()] == [DB]
    assert r.trace == p.trace
    r.submit(BOB, DB, {"amount": 6})
    r.advance()
    p.submit(BOB, DB, {"amount": 6})
    p.advance()
    assert r.finished and r.trace == p.trace == s.trace


def test_snapshot_shape() -> None:
    s = fresh()
    snap = s.snapshot()
    assert set(snap) == {"env_ref", "params", "seed", "log"}
    assert snap["env_ref"] == ENV and snap["seed"] == 1
    assert snap["log"][0]["kind"] == "started"
    json.dumps(snap)  # JSON-able


def test_advance_after_finish_is_noop() -> None:
    s = fresh()
    s.submit(ALICE, DA, {"amount": 5})
    s.submit(BOB, DB, {"amount": 6})
    s.advance()
    n = len(s.trace)
    assert s.advance() == []
    assert len(s.trace) == n and s.finished


def test_automatic_layer_runs_without_decisions() -> None:
    s = Session.start(envs.AUTOMATIC, {}, 0)
    new = s.advance()
    assert s.finished and s.pending() == []
    assert kinds(s) == ["layer_entered", "layer_completed", "report_delivered", "exited"]
    assert [e.kind for e in new] == kinds(s)
    (report,) = s.reports_for(AgentId("observer"))
    assert report.payload == {"msg": "hello"} and report.report_id == "auto@observer:0"
    assert s.result is not None and s.result.port == "done"
    assert_replay_equivalent(s)


def test_one_agent_with_two_pending_decisions() -> None:
    s = Session.start(envs.DOUBLE_ROLE, {"agent": "alice"}, 0)
    s.advance()
    reqs = s.pending_for(ALICE)
    assert len(reqs) == 2
    assert len({r.decision_id for r in reqs}) == 2  # keyed by (path, role), never by agent
    s.submit(ALICE, reqs[0].decision_id, {"amount": 1})
    assert [r.decision_id for r in s.pending_for(ALICE)] == [reqs[1].decision_id]


def test_check_determinism_flags_nondeterministic_environment() -> None:
    s = fresh(envs.NONDETERMINISTIC)
    with pytest.raises(DeterminismError, match="index"):
        s.check_determinism()


def test_replay_rejects_inconsistent_logs() -> None:
    started = ev.Started(env_ref=ENV, params=PARAMS, seed=1)
    bogus = ev.Submitted(decision_id=DA, agent_id=ALICE, action={"amount": 1})
    wrong_agent = ev.Submitted(decision_id=DA, agent_id=BOB, action={"amount": 1})
    with pytest.raises(DeterminismError):
        Session.replay(ENV, PARAMS, 1, [started, bogus, bogus])  # second one is stale
    with pytest.raises(DeterminismError):
        Session.replay(ENV, PARAMS, 1, [started, wrong_agent])
    with pytest.raises(DeterminismError):
        Session.replay(ENV, PARAMS, 2, [started])  # seed mismatch with Started
    with pytest.raises(DeterminismError):
        Session.replay(ENV, PARAMS, 1, [started, ev.Aborted(reason="x")])


def test_restore_rejects_trace_events_in_log() -> None:
    snap = fresh().snapshot()
    snap["log"].append(ev.dump([ev.Failed(t=1, path=["x"], reason="r")])[0])
    with pytest.raises(ValueError, match="non-input"):
        Session.restore(snap)


def test_record_external_is_logged_replayed_and_inert() -> None:
    s = fresh()
    before = dumped(s)
    s.record_external(DA, {"tool": "result"})
    s.advance()
    assert dumped(s) == before
    assert [e.kind for e in s.input_log] == ["started", "external"]
    assert Session.restore(s.snapshot()).input_log == s.input_log
    s.check_determinism()


def test_interleaved_advances_give_same_trace_as_batched() -> None:
    batched = fresh()
    batched.submit(BOB, DB, {"amount": 6})
    batched.submit(ALICE, DA, {"amount": 5})
    batched.advance()

    stepped = fresh()
    stepped.submit(BOB, DB, {"amount": 6})
    stepped.advance()
    stepped.submit(ALICE, DA, {"amount": 5})
    stepped.advance()
    assert dumped(stepped) == dumped(batched)  # trace independent of step boundaries and order
    stepped.check_determinism()  # replay groups steps differently, still identical


def test_check_determinism_with_unadvanced_inbox() -> None:
    s = fresh()
    s.submit(ALICE, DA, {"amount": 5})
    s.submit(BOB, DB, {"amount": 6})  # not advanced: the finish is not in s.trace yet
    s.check_determinism()


def test_random_interleavings_match_canonical_run() -> None:
    """Randomized stand-in for a property test: any order, any advance/snapshot pattern."""
    rng = np.random.default_rng(1234)
    for trial in range(40):
        n = int(rng.integers(2, 5))
        names = [f"p{i}" for i in range(n)]
        p = envs.params(**{nm: float(rng.integers(1, 20)) for nm in names})
        amounts = {AgentId(nm): {"amount": float(rng.integers(0, 30))} for nm in names}

        canon = Session.start(ENV, p, trial)
        canon.advance()
        for nm in sorted(names):
            (req,) = canon.pending_for(AgentId(nm))
            canon.submit(AgentId(nm), req.decision_id, amounts[AgentId(nm)])
        canon.advance()

        s = Session.start(ENV, p, trial)
        s.advance()
        for idx in rng.permutation(n):
            agent = AgentId(names[int(idx)])
            (req,) = s.pending_for(agent)
            s.submit(agent, req.decision_id, amounts[agent])
            if rng.random() < 0.5:
                s.advance()
            if rng.random() < 0.3:
                s = Session.restore(json.loads(json.dumps(s.snapshot())))
        s.advance()
        assert s.finished
        assert dumped(s) == dumped(canon)
        for nm in names:
            a, b = s.ledger.information(AgentId(nm)), canon.ledger.information(AgentId(nm))
            assert a == b
        s.check_determinism()
