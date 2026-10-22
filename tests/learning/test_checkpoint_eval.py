"""Checkpoints, train/eval isolation, and baselines through the same ``evaluate``."""

import copy
import json
import math

import numpy as np

from gao_idl.core.ids import DecisionId, RoleId
from gao_idl.core.schemas import DecisionRequest
from gao_idl.environments.pd.schemas import PDAction
from gao_idl.learning import (
    FieldObsCodec,
    QConfig,
    RewardExtractor,
    SchemaActCodec,
    TableAgent,
    evaluate,
    train,
)
from tests.learning.helpers import ALICE, pd_learner, pd_spec


def rich_learner(seed: int = 0) -> TableAgent:
    """A learner whose state is (round, whole own history): many distinct states get visited."""
    return TableAgent(
        ALICE,
        FieldObsCodec(["round_index", "own_history"]),
        SchemaActCodec(),
        RewardExtractor(fields=["own_payoff"]),
        QConfig(alpha=0.2, gamma=0.9, epsilon=0.4, epsilon_decay=0.99, epsilon_min=0.05),
        seed,
    )


def pd_request(round_index: int, history: list[tuple[str, str]]) -> DecisionRequest:
    return DecisionRequest(
        decision_id=DecisionId("pd/loop[0]/round/branch[pair_0]#row"),
        path=["pd"],
        layer="pd_round",
        role=RoleId("row"),
        agent_id=ALICE,
        observation={"round_index": round_index, "own_history": [list(h) for h in history]},
        action_schema=PDAction.model_json_schema(),
    )


def test_checkpoint_round_trip() -> None:
    spec = pd_spec(rounds=4, episodes=150, seed=3, opponent="random")
    learner = rich_learner(seed=3)
    ckpt = train(spec, learner, None).checkpoint
    restored_dict = json.loads(json.dumps(ckpt))  # through real JSON text
    assert restored_dict == ckpt

    first = evaluate(spec, ckpt, None, 20, 11)
    again = evaluate(spec, ckpt, None, 20, 11)
    from_json = evaluate(spec, restored_dict, None, 20, 11)
    assert first.returns == again.returns == from_json.returns  # identical, bit for bit

    restored = TableAgent.from_checkpoint(
        restored_dict,
        FieldObsCodec(["round_index", "own_history"]),
        SchemaActCodec(),
        learning=False,
    )
    assert restored.q == learner.q and len(learner.q) > 20  # non-trivial table
    rng = np.random.default_rng(0)
    greedy: list[str] = []
    for _ in range(50):
        n = int(rng.integers(0, 4))
        history = [("CD"[int(rng.integers(2))], "CD"[int(rng.integers(2))]) for _ in range(n)]
        request = pd_request(n, history)
        want = learner.greedy_action(request)
        assert restored.greedy_action(request) == want
        greedy.append(want["move"])
    assert {"C", "D"} <= set(greedy)  # the comparison is not between two constant policies


def test_train_eval_isolation() -> None:
    spec = pd_spec(rounds=3, episodes=60, seed=2, opponent="random")
    learner = pd_learner(seed=2, cfg=QConfig(alpha=0.2, epsilon=0.5, epsilon_decay=0.95))
    trained = train(spec, learner, None)
    ckpt = trained.checkpoint
    ckpt_before = copy.deepcopy(ckpt)
    learner_before = copy.deepcopy(learner.checkpoint())
    q_before = dict(learner.q)

    evaluate(spec, ckpt, None, 25, 5)
    evaluate(spec, learner.checkpoint(), None, 25, 6)

    assert ckpt == ckpt_before  # the checkpoint dict was not touched
    assert learner.q == q_before  # the trainer's agent was not updated by evaluation ...
    assert learner.checkpoint() == learner_before  # ... nor its RNG, ε or counters
    # And the learner can go on training afterwards, from where it was.
    more = train(spec.model_copy(update={"seed": 3, "episodes": 5}), learner, None)
    assert more.checkpoint["episodes_seen"] == trained.checkpoint["episodes_seen"] + 5


def test_random_and_scripted_baselines_run_through_same_evaluate() -> None:
    rounds = 4
    spec = pd_spec(rounds=rounds)
    untrained = pd_learner().checkpoint()  # an empty table: greedy = the first action, "C"
    results = {
        name: evaluate(spec.model_copy(update={"opponents": {"bob": name}}), untrained, None, 40, 8)
        for name in (
            "scripted:always_cooperate",
            "scripted:always_defect",
            "scripted:tit_for_tat",
            "random",
        )
    }
    for res in results.values():
        assert res.failed_episodes == 0 and res.terminals == ["normal"] * 40
        assert res.mean is not None and math.isfinite(res.mean)
    # An always-"C" learner is paid 3 per round by a cooperator or tit-for-tat, 0 by a defector.
    assert results["scripted:always_cooperate"].returns == [3.0 * rounds] * 40
    assert results["scripted:tit_for_tat"].returns == [3.0 * rounds] * 40
    assert results["scripted:always_defect"].returns == [0.0] * 40
    # Against a uniformly random opponent it earns 1.5 per round on average (3 w.p. 1/2).
    rnd = results["random"]
    assert rnd.mean is not None and abs(rnd.mean - 1.5 * rounds) < 1.5
    assert len(set(rnd.returns)) > 1 and rnd.std is not None and rnd.std > 0
    # Same seed, same numbers; a different seed changes the random baseline only.
    again = evaluate(
        spec.model_copy(update={"opponents": {"bob": "random"}}), untrained, None, 40, 8
    )
    assert again.returns == rnd.returns
