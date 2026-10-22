import json
from typing import Any

import numpy as np
import pytest

from gao_idl.composition.contracts import describe
from gao_idl.composition.par import DictJoiner, DisjointSplitter, OpenState, Par
from gao_idl.composition.process import Process
from gao_idl.core import events as ev
from gao_idl.core.errors import (
    ContractError,
    DeterminismError,
    SchemaError,
    SessionFinished,
    UnknownDecision,
    WrongAgent,
)
from gao_idl.core.ids import AgentId, DecisionId, Port
from gao_idl.core.schemas import Config, DecisionRequest, Exit, ParticipantReport
from gao_idl.environments.auction import build_auction
from gao_idl.environments.auction.bidding import BidReceipt
from gao_idl.environments.auction.settlement import SettlementReport
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


def test_check_determinism_requires_advance_first() -> None:
    s = fresh()
    s.submit(ALICE, DA, {"amount": 5})
    s.submit(BOB, DB, {"amount": 6})  # queued but not advanced
    with pytest.raises(ValueError, match=r"call advance\(\) before check_determinism\(\)"):
        s.check_determinism()
    s.advance()
    s.check_determinism()  # fine once the inbox has been consumed
    assert s.finished


def test_advance_loops_until_not_continue() -> None:
    s = Session.start(envs.CONTINUE_TWICE, {}, 0)
    new = s.advance()  # one call drives three steps: Continue, Continue, Exited
    assert s.finished and not s.failed
    assert s.result is not None and s.result.record == {"steps": 3}
    assert len(new) == 3 and kinds(s) == ["layer_entered"] * 3
    s.check_determinism()


def test_continue_steps_receive_no_inputs() -> None:
    s = Session.start(envs.CONTINUE_TWICE, {}, 0)
    s.advance()
    frame = s._frame
    assert isinstance(frame, envs.StubFrame)
    assert frame.inputs_seen == [0, 0, 0]


def test_twelve_bidders_are_ordered_naturally() -> None:
    names = [f"p{i:02d}" for i in range(12)]  # agent order == role index order
    p = envs.params(**{n: float(i + 1) for i, n in enumerate(names)})
    s = fresh(params=p)
    want = [f"bidding#bidder:{i}" for i in range(12)]
    assert [r.decision_id for r in s.pending()] == want  # not bidder:0, bidder:1, bidder:10, ...
    for n in reversed(names):
        (req,) = s.pending_for(AgentId(n))
        s.submit(AgentId(n), req.decision_id, {"amount": 1})
    s.advance()
    accepted = [e.decision_id for e in s.trace if isinstance(e, ev.ActionAccepted)]
    assert accepted == want


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


# ------------------------------------------------ full auction (bidding + settlement)

AUCTION = "gao_idl.environments.auction:build_auction"


def auction_params(values: dict[str, float] | None = None, **kw: Any) -> dict[str, Any]:
    return {"values": {"alice": 9.0, "bob": 7.0} if values is None else values, **kw}


def play(params: dict[str, Any], bids: dict[str, float], seed: int = 1) -> Session:
    """Run the full auction to completion with the given bids."""
    s = Session.start(AUCTION, params, seed)
    s.advance()
    for agent, amount in bids.items():
        (req,) = s.pending_for(AgentId(agent))
        s.submit(AgentId(agent), req.decision_id, {"amount": amount})
    s.advance()
    return s


def record(s: Session) -> dict[str, Any]:
    assert s.result is not None and s.result.port == "done"
    return s.result.record


def report(s: Session, agent: str) -> dict[str, Any]:
    (payload,) = [r.payload for r in s.reports_for(AgentId(agent)) if "winner" in r.payload]
    return payload


def test_prices_and_utilities() -> None:
    """The v1 section 14 numbers: values 9/7, bids 8/6."""
    bids = {"alice": 8.0, "bob": 6.0}
    first = play(auction_params(rule="first_price"), bids)
    assert record(first)["winner"] == "alice" and record(first)["price"] == 8.0
    assert record(first)["utilities"] == {"alice": 1.0, "bob": 0.0}
    assert record(first)["payments"] == {"alice": 8.0, "bob": 0.0}
    second = play(auction_params(rule="second_price"), bids)
    assert record(second)["winner"] == "alice" and record(second)["price"] == 6.0
    assert record(second)["utilities"] == {"alice": 3.0, "bob": 0.0}
    assert first.result is not None and first.result.port == "done"


def test_settlement_ties_and_single_bidder() -> None:
    tie = play(auction_params(rule="second_price"), {"alice": 5.0, "bob": 5.0})
    assert record(tie)["winner"] == "alice" and record(tie)["price"] == 5.0  # smallest id wins
    tie_first = play(auction_params({"zed": 9, "amy": 9}, rule="first_price"), {"zed": 4, "amy": 4})
    assert record(tie_first)["winner"] == "amy"
    single = play(auction_params({"solo": 9.0}), {"solo": 4.0})
    assert record(single)["winner"] == "solo" and record(single)["price"] == 0.0
    assert record(single)["utilities"] == {"solo": 9.0}
    low = play(auction_params(rule="second_price"), {"alice": 1.0, "bob": 2.0})
    assert (record(low)["winner"], record(low)["price"]) == ("bob", 1.0)


def test_ledger_matches_outcome_utilities() -> None:
    for rule in ("first_price", "second_price"):
        s = play(auction_params(rule=rule), {"alice": 8.0, "bob": 6.0})
        for agent, u in record(s)["utilities"].items():
            assert s.ledger.utility(AgentId(agent)) == u
        incs = s.ledger.increments(ALICE)
        assert len(incs) == 1 and incs[0].path == ["auction", "settlement"]  # one, from settlement
    s.check_determinism()


def test_settlement_reports_private() -> None:
    s = play(auction_params({"alice": 9.37, "bob": 7.11}), {"alice": 8.0, "bob": 6.0})
    for agent, other_value in (("alice", 7.11), ("bob", 9.37)):
        payload = report(s, agent)
        assert "all_bids" not in payload  # omitted entirely unless bids are revealed
        assert set(payload) == {
            "winner",
            "winning_payment",
            "own_bid",
            "own_payment",
            "own_utility",
        }
        assert other_value not in payload.values()
        info = json.dumps([x.model_dump(mode="json") for x in s.ledger.information(AgentId(agent))])
        assert str(other_value) not in info  # nothing delivered to this agent mentions it
    assert report(s, "bob") == {
        "winner": "alice",
        "winning_payment": 6.0,
        "own_bid": 6.0,
        "own_payment": 0.0,
        "own_utility": 0.0,
    }
    assert report(s, "alice")["own_utility"] == pytest.approx(9.37 - 6.0)
    SettlementReport.model_validate(report(s, "bob"))


def test_reveal_bids_flag() -> None:
    bids = {"alice": 8.0, "bob": 6.0}
    shown = play(auction_params(reveal_bids=True), bids)
    for agent in ("alice", "bob"):
        assert report(shown, agent)["all_bids"] == bids
    hidden = play(auction_params(reveal_bids=False), bids)
    assert all("all_bids" not in report(hidden, a) for a in ("alice", "bob"))
    # revealing bids changes only that field
    assert {k: v for k, v in report(shown, "bob").items() if k != "all_bids"} == report(
        hidden, "bob"
    )


def test_reserve_unsold_port() -> None:
    s = play(auction_params(reserve=10.0), {"alice": 8.0, "bob": 6.0})
    assert s.finished and not s.failed
    assert s.result == Exit(
        port="unsold",
        record={
            "reserve": 10.0,
            "payments": {"alice": 0.0, "bob": 0.0},
            "utilities": {"alice": 0.0, "bob": 0.0},
        },
    )
    assert all(s.ledger.utility(AgentId(a)) == 0.0 for a in ("alice", "bob"))
    assert [r.payload for r in s.reports_for(ALICE)] == [{"status": "bid accepted", "own_bid": 8.0}]


def test_reserve_ignores_low_bids_and_clears_at_equality() -> None:
    first = play(auction_params(rule="first_price", reserve=7.0), {"alice": 8.0, "bob": 6.0})
    assert (record(first)["winner"], record(first)["price"]) == ("alice", 8.0)
    # bob's 6 is below the reserve and ignored; the reserve is the seller's bid, so alice pays it
    second = play(auction_params(rule="second_price", reserve=7.0), {"alice": 8.0, "bob": 6.0})
    assert (record(second)["winner"], record(second)["price"]) == ("alice", 7.0)
    both = play(auction_params(rule="second_price", reserve=6.0), {"alice": 8.0, "bob": 6.0})
    assert record(both)["price"] == 6.0  # a bid equal to the reserve is eligible
    only_bob = play(auction_params(rule="first_price", reserve=6.5), {"alice": 6.0, "bob": 7.0})
    assert record(only_bob)["winner"] == "bob"


def test_reserve_binds_second_price() -> None:
    """One bidder above the reserve pays the reserve, not 0; a higher rival still sets the price."""
    lone = play(auction_params(rule="second_price", reserve=4.0), {"alice": 8.0, "bob": 3.0})
    assert (record(lone)["winner"], record(lone)["price"]) == ("alice", 4.0)
    assert record(lone)["payments"] == {"alice": 4.0, "bob": 0.0}
    assert record(lone)["utilities"] == {"alice": 9.0 - 4.0, "bob": 0.0}
    assert lone.ledger.utility(ALICE) == 5.0
    rival = play(auction_params(rule="second_price", reserve=4.0), {"alice": 8.0, "bob": 6.0})
    assert record(rival)["price"] == 6.0  # an eligible rival above the reserve sets the price
    no_reserve = play(auction_params(rule="second_price"), {"alice": 8.0, "bob": 3.0})
    assert record(no_reserve)["price"] == 3.0
    solo = play(auction_params({"solo": 9.0}, rule="second_price", reserve=2.0), {"solo": 5.0})
    assert record(solo)["price"] == 2.0


def test_reserve_is_the_same_structure_with_a_different_committed_rule() -> None:
    plain = describe(build_auction({"a": 1, "b": 2})[0])
    reserved = describe(build_auction({"a": 1, "b": 2}, reserve=1.0)[0])
    assert sorted(plain["exits"]) == ["done"] and sorted(reserved["exits"]) == ["done", "unsold"]
    assert plain["first"] == reserved["first"]
    assert plain["routes"]["settle"]["connector"] == reserved["routes"]["settle"]["connector"]
    then_plain, then_reserved = (t["routes"]["settle"]["then"] for t in (plain, reserved))
    assert then_plain["entry"] == then_reserved["entry"] and then_plain["name"] == "settlement"
    with pytest.raises(ContractError):
        build_auction({"a": 1}, reserve=-1.0)


def test_settlement_misconfiguration_is_a_contract_error() -> None:
    with pytest.raises(ContractError, match="no bids"):
        play(auction_params({}), {})
    s = Session.start(AUCTION, auction_params(rule="third_price"), 1)
    # the bidding layer's own schema rejects an unknown rule first
    with pytest.raises(ContractError):
        s.advance()


def test_full_auction_replay_and_partial_restore() -> None:
    bids = {"alice": 8.0, "bob": 6.0}
    whole = play(auction_params(), bids, seed=4)
    assert_replay_equivalent(whole)
    assert_incremental_equals_fold(whole)
    full = Session.restore(whole.snapshot())
    assert ev.dump(full.trace) == ev.dump(whole.trace) and full.result == whole.result
    # partial: only alice has bid when the snapshot is taken (bob's decision is pending)
    part = Session.start(AUCTION, auction_params(), 4)
    part.advance()
    (req,) = part.pending_for(ALICE)
    part.submit(ALICE, req.decision_id, {"amount": 8.0})
    part.advance()
    assert not part.finished and [r.agent_id for r in part.pending()] == [BOB]
    resumed = Session.restore(json.loads(json.dumps(part.snapshot())))  # through real JSON
    assert [r.decision_id for r in resumed.pending()] == ["auction/bidding#bidder:1"]
    (rb,) = resumed.pending_for(BOB)
    resumed.submit(BOB, rb.decision_id, {"amount": 6.0})
    resumed.advance()
    assert resumed.finished
    assert ev.dump(resumed.trace) == ev.dump(whole.trace)  # same trace as the uninterrupted run
    assert [r.payload for r in resumed.reports_for(BOB)] == [
        r.payload for r in whole.reports_for(BOB)
    ]


def test_full_auction_noninterference() -> None:
    actions = {ALICE: {"amount": 8.0}, BOB: {"amount": 6.0}}
    base, perturbed = auction_params(), auction_params({"alice": 9.0, "bob": 2.5})
    assert_noninterference(AUCTION, base, perturbed, 7, actions, agents=[ALICE])
    # control: bob's own stream does depend on bob's own value (his utility is value-dependent only
    # when he wins, so make him win)
    bob_wins = {ALICE: {"amount": 3.0}, BOB: {"amount": 6.0}}
    with pytest.raises(AssertionError):
        assert_noninterference(AUCTION, base, perturbed, 7, bob_wins, agents=[BOB])
    # and a value change on a revealed-bids auction still leaks no values
    assert_noninterference(
        AUCTION,
        auction_params(reveal_bids=True),
        auction_params({"alice": 9.0, "bob": 2.5}, reveal_bids=True),
        7,
        actions,
        agents=[ALICE],
    )


def test_arrival_invariance_full() -> None:
    actions = {ALICE: {"amount": 8.0}, BOB: {"amount": 6.0}}
    for rule in ("first_price", "second_price"):
        assert_arrival_invariant(AUCTION, auction_params(rule=rule), 3, actions)
    assert_arrival_invariant(
        AUCTION,
        auction_params({"a": 3, "b": 4, "c": 5}, reveal_bids=True),
        3,
        {
            AgentId("a"): {"amount": 1.0},
            AgentId("b"): {"amount": 2.0},
            AgentId("c"): {"amount": 2.0},
        },
    )


PAR_AUCTIONS = "tests.environments.test_auction:par_auctions"


def par_auctions(auctions: dict[str, dict[str, float]], **kw: Any) -> tuple[Process, Config]:
    """One full auction per branch (disjoint bidders), joined when all have settled."""

    def split(cfg: Config) -> dict[str, Config]:
        return {b: build_auction(vals, **kw)[1] for b, vals in cfg.state["auctions"].items()}

    branches = {b: build_auction(vals, **kw)[0] for b, vals in auctions.items()}
    par = Par(branches, DisjointSplitter(split, OpenState), DictJoiner(Port("done"), OpenState))
    return par, Config(state={"auctions": auctions}, roles={})


def test_parallel_auctions_arrival_projection_invariant() -> None:
    params = {"auctions": {"A": {"alice": 9.0, "bob": 7.0}, "B": {"carol": 5.0, "dave": 8.0}}}
    actions = {
        ALICE: {"amount": 8.0},
        BOB: {"amount": 6.0},
        AgentId("carol"): {"amount": 4.0},
        AgentId("dave"): {"amount": 7.0},
    }
    for rule in ("first_price", "second_price"):
        assert_arrival_invariant(PAR_AUCTIONS, {**params, "rule": rule}, 5, actions)
    s = Session.start(PAR_AUCTIONS, params, 5)
    s.advance()
    assert {r.decision_id for r in s.pending()} == {
        f"par/branch[{b}]/bidding#bidder:{i}" for b in "AB" for i in (0, 1)
    }


def test_truthful_second_price_on_grid() -> None:
    """Bidding one's value is weakly dominant under second price (all values, rivals, bids)."""
    grid = [0.0, 1.0, 2.0, 3.0, 4.0]

    def alice_utility(value: float, rival: float, bid: float, rule: str = "second_price") -> float:
        s = play(
            auction_params({"alice": value, "bob": 0.0}, rule=rule), {"alice": bid, "bob": rival}
        )
        return s.ledger.utility(ALICE)

    for value in grid:
        for rival in grid:
            truthful = alice_utility(value, rival, value)
            for bid in grid:
                assert truthful >= alice_utility(value, rival, bid) - 1e-12, (value, rival, bid)


def test_first_price_is_not_truthful() -> None:
    """Negative control for the grid test: shading below one's value beats a truthful bid."""

    def utility(bid: float) -> float:
        s = play(
            auction_params({"alice": 4.0, "bob": 0.0}, rule="first_price"),
            {"alice": bid, "bob": 2.0},
        )
        return s.ledger.utility(ALICE)

    assert utility(3.0) > utility(4.0) == 0.0
