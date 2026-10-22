"""Trainer and evaluator plumbing, on the repeated PD against scripted opponents."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from gao_idl.agents import RandomAgent, ScriptedAgent
from gao_idl.core.ids import AgentId
from gao_idl.learning import (
    FieldObsCodec,
    QConfig,
    SchemaActCodec,
    TrainResult,
    TrainSpec,
    default_opponent_factory,
    evaluate,
    train,
)
from gao_idl.learning.opponents import derive_seed
from gao_idl.learning.trainer import LearnerError, episode_seed
from tests.learning.helpers import ALICE, BOB, PD, pd_learner, pd_spec


def test_episode_seed_formula() -> None:
    assert episode_seed(3, 0) == 3_000_009
    assert episode_seed(3, 7) == 3 * 1_000_003 + 7
    assert episode_seed(0, 5) == 5


def test_train_uses_the_documented_session_seeds() -> None:
    seen: list[int] = []
    inner = default_opponent_factory()

    def spy(agent: AgentId, description: str, seed: int) -> Any:
        seen.append(seed)
        return inner(agent, description, seed)

    train(pd_spec(episodes=4, seed=5), pd_learner(), spy)
    assert seen == [5 * 1_000_003 + e for e in range(4)]


def test_train_returns_true_utility_and_is_json() -> None:
    spec = pd_spec(rounds=3, episodes=12)
    learner = pd_learner(cfg=QConfig(epsilon=0.0))
    result = train(spec, learner, None)
    assert len(result.returns) == 12 and result.terminals == ["normal"] * 12
    # epsilon 0, ties go to "C": cooperating against always-cooperate pays 3 per round at first
    assert result.returns[0] == 9.0
    assert result.failed_episodes == 0
    dumped = result.model_dump(mode="json")
    assert json.loads(json.dumps(dumped)) == dumped
    assert TrainResult.model_validate(dumped) == result
    assert result.spec == spec and result.checkpoint == learner.checkpoint()


def test_training_is_reproducible() -> None:
    spec = pd_spec(episodes=30, opponent="random")
    results = [train(spec, pd_learner(seed=2), None) for _ in range(2)]
    assert results[0].returns == results[1].returns
    assert results[0].checkpoint == results[1].checkpoint
    # The session seed drives the (random) opponents; the learner has a seed of its own.
    other_spec = train(pd_spec(episodes=30, seed=2, opponent="random"), pd_learner(seed=2), None)
    other_learner = train(spec, pd_learner(seed=3), None)
    assert other_spec.returns != results[0].returns
    assert other_learner.checkpoint != results[0].checkpoint


def test_spec_validation() -> None:
    with pytest.raises(ValidationError):
        TrainSpec(
            env_ref=PD, params={}, learner=ALICE, episodes=1, seed=0, opponents={ALICE: "random"}
        )
    with pytest.raises(ValidationError):
        pd_spec(episodes=0)


def test_train_refuses_the_wrong_learner_and_frozen_agents() -> None:
    other = pd_learner()
    other.agent_id = BOB
    with pytest.raises(ValueError, match="spec.learner"):
        train(pd_spec(), other, None)
    with pytest.raises(ValueError, match="learning"):
        train(pd_spec(), pd_learner(learning=False), None)


def test_a_learner_that_raises_in_act_is_not_swallowed() -> None:
    bad = pd_learner()
    bad.obs_codec = FieldObsCodec(["no_such_field"])
    with pytest.raises(LearnerError) as info:
        train(pd_spec(episodes=2), bad, None)
    assert isinstance(info.value.__cause__, ValueError)
    assert "no_such_field" in str(info.value.__cause__)


# ------------------------------------------------------------------ evaluate


def test_evaluate_is_frozen_greedy_and_leaves_the_checkpoint_alone() -> None:
    trained = train(pd_spec(episodes=40), pd_learner(seed=1), None)
    ckpt = trained.checkpoint
    before = copy.deepcopy(ckpt)
    result = evaluate(pd_spec(), ckpt, None, 10, 99)
    assert ckpt == before
    assert result.episodes == 10 and result.seed == 99 and len(result.returns) == 10
    assert result.mean is not None and result.std is not None
    assert result.mean == sum(result.returns) / 10
    assert result.failed_episodes == 0
    again = evaluate(pd_spec(), ckpt, None, 10, 99)
    assert again.returns == result.returns


def test_evaluate_uses_the_documented_session_seeds_and_builds_a_new_frozen_agent() -> None:
    ckpt = train(pd_spec(episodes=3), pd_learner(), None).checkpoint
    seen: list[int] = []
    inner = default_opponent_factory()

    def spy(agent: AgentId, description: str, seed: int) -> Any:
        seen.append(seed)
        return inner(agent, description, seed)

    evaluate(pd_spec(), ckpt, spy, 3, 4)
    assert seen == [4 * 1_000_003 + e for e in range(3)]


def test_evaluate_needs_episodes() -> None:
    ckpt = pd_learner().checkpoint()
    with pytest.raises(ValueError):
        evaluate(pd_spec(), ckpt, None, 0, 1)


def test_evaluate_can_seat_the_table_at_another_agent_and_takes_explicit_codecs() -> None:
    ckpt = train(pd_spec(episodes=10), pd_learner(), None).checkpoint
    spec = pd_spec().model_copy(update={"learner": BOB, "opponents": {ALICE: "random"}})
    res = evaluate(
        spec, ckpt, None, 3, 1, obs_codec=FieldObsCodec(["round_index"]), act_codec=SchemaActCodec()
    )
    assert len(res.returns) == 3
    with pytest.raises(ValueError, match="obs_codec"):
        evaluate(spec, ckpt, None, 1, 1, obs_codec=FieldObsCodec(["other"]))


def test_a_failing_session_is_counted_not_averaged() -> None:
    # A DefaultAction-less PD with a learner whose answers are always infeasible cannot be built
    # cheaply, so use the Fail-on-timeout path: an opponent that raises times the decision out.
    class Boom:
        async def episode_start(self, ctx: Any) -> None: ...
        async def act(self, request: Any) -> dict[str, Any]:
            raise RuntimeError("boom")

        async def observe(self, report: Any) -> None: ...
        async def episode_end(self, end: Any) -> None: ...

    ckpt = pd_learner().checkpoint()
    res = evaluate(pd_spec(), ckpt, lambda a, d, s: Boom(), 3, 1)  # type: ignore[arg-type,return-value]
    assert res.terminals == ["failed"] * 3 and res.failed_episodes == 3
    assert res.mean is None and res.std is None


# ------------------------------------------------------------------ opponents


def test_opponent_forms(tmp_path: Path) -> None:
    factory = default_opponent_factory()
    assert isinstance(factory(BOB, "random", 1), RandomAgent)
    assert isinstance(factory(BOB, "scripted:tit_for_tat", 1), ScriptedAgent)
    for bad in ("nonsense", "scripted:nope", "table:", "random:1"):
        with pytest.raises(ValueError, match="unknown opponent"):
            factory(BOB, bad, 1)
    path = tmp_path / "opp.json"
    path.write_text(json.dumps(train(pd_spec(episodes=5), pd_learner(), None).checkpoint))
    table = factory(BOB, f"table:{path}", 1)
    assert table.agent_id == BOB and table.learning is False  # type: ignore[attr-defined]
    assert table is not factory(BOB, f"table:{path}", 1)  # a new frozen agent every time


def test_custom_scripted_policies() -> None:
    factory = default_opponent_factory({"mine": lambda request, reports: {"move": "D"}})
    assert isinstance(factory(BOB, "scripted:mine", 1), ScriptedAgent)
    assert isinstance(factory(BOB, "scripted:always_defect", 1), ScriptedAgent)


def test_derived_seeds_are_stable_and_distinct() -> None:
    assert derive_seed(1, BOB, "random") == derive_seed(1, BOB, "random")
    assert len({derive_seed(s, a, "random") for s in range(5) for a in (ALICE, BOB)}) == 10


def test_the_evaluation_agent_is_new_frozen_and_never_updated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gao_idl.learning import TableAgent

    learner = pd_learner(seed=1, cfg=QConfig(alpha=0.2, gamma=0.9, epsilon=0.9))
    ckpt = train(pd_spec(episodes=30), learner, None).checkpoint
    q_before = dict(learner.q)
    created: list[TableAgent] = []
    original = TableAgent.from_checkpoint.__func__  # type: ignore[attr-defined]

    def spy(cls: type[TableAgent], *args: Any, **kwargs: Any) -> TableAgent:
        created.append(original(cls, *args, **kwargs))
        return created[-1]

    monkeypatch.setattr(TableAgent, "from_checkpoint", classmethod(spy))
    evaluate(pd_spec(), ckpt, None, 5, 3)
    (agent,) = created
    assert agent is not learner and agent.learning is False
    assert agent.q == q_before == learner.q  # nothing learned during evaluation, learner untouched
    assert agent.episodes_seen == learner.episodes_seen  # no episode counted, no ε decay
    assert agent.epsilon == learner.epsilon
