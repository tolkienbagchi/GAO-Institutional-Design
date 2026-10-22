"""Transition boundaries and reward disclosure on real environments (PD, auction)."""

from typing import Any

from gao_idl.agents import ScriptedAgent
from gao_idl.environments.pd import STANDARD_PAYOFFS
from gao_idl.learning import (
    FieldObsCodec,
    QConfig,
    RewardExtractor,
    SchemaActCodec,
    TableAgent,
    default_opponent_factory,
    train,
)
from gao_idl.runtime.runner import run_sync
from gao_idl.runtime.session import Session
from tests.learning.helpers import ALICE, AUCTION, BOB, PD, pd_learner, pd_spec

SILENT = "tests.learning.envs:pd_without_payoff_report"


def play_pd(
    learner: TableAgent, opponent: str, rounds: int, seed: int = 1, env: str = PD
) -> Session:
    session = Session.start(
        env, {"agents": ["alice", "bob"], "rounds": rounds, "matching": "fixed"}, seed
    )
    other = default_opponent_factory()(BOB, opponent, seed)
    return run_sync(session, {ALICE: learner, BOB: other})


def test_transition_boundary_accumulates_rewards_between_decisions() -> None:
    learner = pd_learner(seed=4, cfg=QConfig(epsilon=0.5), record=True)
    session = play_pd(learner, "scripted:always_defect", rounds=3)
    reported = [r.payload["own_payoff"] for r in session.reports_for(ALICE)]
    # Against a defector: our C pays 0, our D pays 1 (the standard payoffs).
    moves = [t.a[0][1] for t in learner.transitions]
    assert reported == [STANDARD_PAYOFFS[m + "D"][0] for m in moves]
    assert len(learner.transitions) == 3
    assert [t.r for t in learner.transitions] == reported  # exactly the disclosed payoffs
    assert [t.done for t in learner.transitions] == [False, False, True]
    assert [t.terminal for t in learner.transitions] == [None, None, "normal"]
    assert [t.elapsed for t in learner.transitions] == [1, 1, 1]  # one report per round
    assert [t.s for t in learner.transitions] == [(0,), (1,), (2,)]
    assert [t.s_next for t in learner.transitions] == [(1,), (2,), None]
    assert session.ledger.utility(ALICE) == sum(t.r for t in learner.transitions)


def test_the_last_round_reward_is_in_the_terminal_transition_not_lost() -> None:
    learner = pd_learner(seed=4, cfg=QConfig(epsilon=0.0), record=True)  # always "C" (first action)
    session = play_pd(learner, "scripted:always_cooperate", rounds=2)
    assert [t.r for t in learner.transitions] == [3.0, 3.0]
    assert learner.dropped_reward == 0.0
    assert session.ledger.utility(ALICE) == 6.0


def test_settlement_reward_lands_in_final_transition() -> None:
    values = {"alice": 8.0, "bob": 6.0}
    learner = TableAgent(
        ALICE,
        FieldObsCodec(["value"]),
        SchemaActCodec(grid={"amount": [float(x) for x in range(11)]}),
        RewardExtractor(fields=["own_utility"]),
        QConfig(epsilon=0.0),
        0,
        record_transitions=True,
    )
    learner.q[((8.0,), (("amount", 10.0),))] = 1.0  # greedy: bid 10 and win
    session = Session.start(AUCTION, {"values": values, "rule": "second_price"}, 1)
    bob = ScriptedAgent(lambda request, reports: {"amount": 5.0})
    run_sync(session, {ALICE: learner, BOB: bob})
    # Alice wins and pays Bob's 5, so her utility is 8 - 5. It is reported after her only decision:
    # first a bid receipt (no reward field), then the settlement report (own_utility).
    reports = session.reports_for(ALICE)
    assert [r.layer for r in reports] == ["bidding", "settlement"]
    assert "own_utility" not in reports[0].payload and reports[1].payload["own_utility"] == 3.0
    (only,) = learner.transitions  # a single decision: the terminal transition is the only one
    assert (only.done, only.terminal, only.s_next, only.elapsed) == (True, "normal", None, 2)
    assert only.r == 3.0 == session.ledger.utility(ALICE)
    # 1.0 + 0.1 * (3 - 1.0): the terminal target is the reward alone
    assert abs(learner.q[((8.0,), (("amount", 10.0),))] - 1.2) < 1e-12


def test_reward_only_from_disclosed_fields() -> None:
    spec = pd_spec(rounds=3, episodes=25, opponent="scripted:always_cooperate")
    spec = spec.model_copy(update={"env_ref": SILENT})
    learner = pd_learner(seed=3, cfg=QConfig(alpha=0.3, epsilon=0.3), record=True)
    result = train(spec, learner, None)
    # The ledger is not zero (the trainer reads it): the learner is paid, it just is not told.
    assert result.failed_episodes == 0 and all(r > 0 for r in result.returns)
    assert len(learner.transitions) == 25 * 3
    assert all(t.r == 0.0 for t in learner.transitions)
    assert learner.dropped_reward == 0.0
    assert learner.q and all(v == 0.0 for v in learner.q.values())
    # Telling the learner the same thing through a disclosed field changes everything.
    paid = pd_learner(seed=3, cfg=QConfig(alpha=0.3, epsilon=0.3), record=True)
    train(spec.model_copy(update={"env_ref": PD}), paid, None)
    assert any(v > 0 for v in paid.q.values())


def test_reports_without_the_reward_field_still_enter_the_state_history() -> None:
    # The silent PD still tells the moves: a codec with history sees them although they pay nothing.
    learner = TableAgent(
        ALICE,
        FieldObsCodec(["round_index"], history=1, report_fields=["other_move"]),
        SchemaActCodec(),
        RewardExtractor(fields=["own_payoff"]),
        QConfig(epsilon=0.0),
        0,
        record_transitions=True,
    )
    play_pd(learner, "scripted:always_defect", rounds=3, env=SILENT)
    states: list[Any] = [t.s for t in learner.transitions]
    assert states == [((0,), None), ((1,), ("D",)), ((2,), ("D",))]
