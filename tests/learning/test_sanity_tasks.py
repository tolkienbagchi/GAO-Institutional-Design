"""Two sanity tasks learn to optimality in CI, and the PD learner finds the dominant move."""

import pytest

from gao_idl.core.ids import AgentId
from gao_idl.learning import (
    FieldObsCodec,
    QConfig,
    RewardExtractor,
    SchemaActCodec,
    TableAgent,
    TrainSpec,
    evaluate,
    train,
)
from tests.learning.helpers import ALICE, pd_learner, pd_spec

ME = AgentId("agent")
BANDIT = "gao_idl.environments.toy:build_bandit"
CHAIN = "gao_idl.environments.toy:build_chain"


def test_bandit_learns_best_arm() -> None:
    spec = TrainSpec(
        env_ref=BANDIT,
        params={"means": [0.1, 0.5, 0.9]},
        learner=ME,
        episodes=2000,
        seed=1,
        opponents={},
    )
    learner = TableAgent(
        ME,
        FieldObsCodec(["n_arms"]),
        SchemaActCodec(),
        RewardExtractor(fields=["own_reward"]),
        QConfig(alpha=0.05, gamma=0.0, epsilon=0.2, epsilon_decay=0.998, epsilon_min=0.02),
        seed=1,
    )
    trained = train(spec, learner, None)
    assert trained.failed_episodes == 0 and set(trained.returns) == {0.0, 1.0}
    arms = {k: learner.q.get(((3,), (("arm", k),)), 0.0) for k in range(3)}
    assert max(arms, key=lambda k: arms[k]) == 2, arms  # the greedy arm is the best one
    assert arms[0] < arms[1] < arms[2]
    result = evaluate(spec, trained.checkpoint, None, 500, 77)
    assert result.mean is not None and result.mean > 0.8
    assert set(result.returns) <= {0.0, 1.0}


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_chain_learns_optimal_policy(seed: int) -> None:
    spec = TrainSpec(env_ref=CHAIN, params={}, learner=ME, episodes=500, seed=seed, opponents={})
    learner = TableAgent(
        ME,
        FieldObsCodec(["state"]),
        SchemaActCodec(),
        RewardExtractor(fields=["own_reward"]),
        QConfig(alpha=0.5, gamma=0.9, epsilon=0.3, epsilon_decay=0.99, epsilon_min=0.05),
        seed=seed,
    )
    trained = train(spec, learner, None)
    assert max(trained.returns) == 1.0  # exploration did find the reward
    result = evaluate(spec, trained.checkpoint, None, 20, 5)
    assert result.returns == [1.0] * 20 and result.mean == 1.0 and result.std == 0.0


@pytest.mark.slow
def test_pd_single_learner_vs_always_cooperate() -> None:
    rounds = 5
    spec = pd_spec(rounds=rounds, episodes=1000, seed=1, opponent="scripted:always_cooperate")
    learner = pd_learner(
        seed=1,
        cfg=QConfig(alpha=0.1, gamma=0.9, epsilon=0.3, epsilon_decay=0.995, epsilon_min=0.1),
    )
    trained = train(spec, learner, None)
    result = evaluate(spec, trained.checkpoint, None, 50, 9)
    assert result.mean is not None and result.mean / rounds >= 4.5
    assert result.returns == [5.0 * rounds] * 50  # always defects: 5 per round
    assert all(
        learner.q[((r,), (("move", "D"),))] > learner.q[((r,), (("move", "C"),))]
        for r in range(rounds)
    )
    assert spec.learner == ALICE
