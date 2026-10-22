"""TableAgent driven by hand with synthetic requests and reports (numbers are checked exactly)."""

import ast
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import ValidationError

from gao_idl.agents.base import EpisodeContext, EpisodeEnd
from gao_idl.core.ids import AgentId, DecisionId, ReportId, RoleId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.environments.pd.schemas import PDAction
from gao_idl.learning import (
    FieldObsCodec,
    QConfig,
    RewardExtractor,
    SchemaActCodec,
    TableAgent,
)
from tests.aio import run_test

ME = AgentId("me")
C, D = (("move", "C"),), (("move", "D"),)


def request(x: int, n: int = 0, agent: str = "me") -> DecisionRequest:
    return DecisionRequest(
        decision_id=DecisionId(f"g/{n}#row"),
        path=["g"],
        layer="game",
        role=RoleId("row"),
        agent_id=AgentId(agent),
        observation={"x": x},
        action_schema=PDAction.model_json_schema(),
    )


def report(
    n: int, payoff: float | None = None, agent: str = "me", **extra: Any
) -> ParticipantReport:
    payload: dict[str, Any] = dict(extra)
    if payoff is not None:
        payload["own_payoff"] = payoff
    return ParticipantReport(
        report_id=ReportId(f"g@{agent}:{n}"),
        path=["g"],
        layer="game",
        agent_id=AgentId(agent),
        payload=payload,
    )


def agent(
    cfg: QConfig | None = None, *, learning: bool = True, record: bool = True, seed: int = 0
) -> TableAgent:
    return TableAgent(
        ME,
        FieldObsCodec(["x"]),
        SchemaActCodec(),
        RewardExtractor(fields=["own_payoff"]),
        cfg or QConfig(alpha=0.5, gamma=0.9, epsilon=0.0),
        seed,
        learning=learning,
        record_transitions=record,
    )


async def start(a: TableAgent, *, keep_parameters: bool = True) -> None:
    ctx = EpisodeContext(agent_id=a.agent_id, env_ref="t", seed=0, keep_parameters=keep_parameters)
    await a.episode_start(ctx)


async def end(a: TableAgent, terminal: Literal["normal", "truncated", "failed"] = "normal") -> None:
    await a.episode_end(EpisodeEnd(agent_id=a.agent_id, terminal=terminal))


def go(coro: Callable[[], Any]) -> Any:
    return run_test(coro())


# ------------------------------------------------------------------ transition boundary


def test_transitions_accumulate_reports_between_decisions() -> None:
    a = agent()

    async def main() -> None:
        await start(a)
        await a.observe(report(0, 7.0))  # before the first decision: nothing to attach it to
        assert await a.act(request(0, 0)) == {"move": "C"}
        await a.observe(report(1, 2.0))
        await a.observe(report(2, 3.0))
        await a.observe(report(3, None, note="no reward field"))
        await a.act(request(1, 1))
        await a.observe(report(4, 4.0))
        await end(a)

    go(main)
    assert a.dropped_reward == 7.0
    first, last = a.transitions
    assert (first.s, first.a, first.r, first.s_next, first.done) == ((0,), C, 5.0, (1,), False)
    assert first.elapsed == 3  # three reports between the two decisions, rewarded or not
    assert (last.s, last.a, last.r, last.s_next, last.done) == ((1,), C, 4.0, None, True)
    assert (last.elapsed, last.terminal) == (1, "normal")
    # Q((0,),C) = 0 + 0.5 * (5 + 0.9 * 0 - 0); Q((1,),C) = 0 + 0.5 * (4 - 0)
    assert a.q == {((0,), C): 2.5, ((1,), C): 2.0}
    assert a.episodes_seen == 1


def test_consecutive_decisions_without_reports_have_elapsed_zero() -> None:
    a = agent()

    async def main() -> None:
        await start(a)
        await a.act(request(0, 0))
        await a.act(request(1, 1))  # e.g. two roles of one agent in a Par
        await end(a)

    go(main)
    assert [(t.r, t.elapsed, t.done) for t in a.transitions] == [(0.0, 0, False), (0.0, 0, True)]


def test_next_state_bootstraps_from_the_best_action_of_the_next_request() -> None:
    a = agent()
    a.q[((1,), D)] = 10.0

    async def main() -> None:
        await start(a)
        await a.act(request(0, 0))
        await a.observe(report(0, 1.0))
        await a.act(request(1, 1))  # greedy here is D (value 10)
        await end(a)

    go(main)
    assert a.q[((0,), C)] == 0.5 * (1.0 + 0.9 * 10.0)


def _truncated_episode(bootstrap: bool) -> TableAgent:
    a = agent(QConfig(alpha=0.5, gamma=0.9, epsilon=0.0, bootstrap_on_truncation=bootstrap))
    a.q[((0,), C)], a.q[((0,), D)] = 2.0, 4.0

    async def main() -> None:
        await start(a)
        assert await a.act(request(0)) == {"move": "D"}  # greedy
        await a.observe(report(0, 1.0))
        await end(a, "truncated")

    go(main)
    return a


def test_truncation_bootstraps_vs_terminal() -> None:
    boot, terminal = _truncated_episode(True), _truncated_episode(False)
    # Q(s, D) = 4. The reward is 1, gamma 0.9, alpha 0.5, and max_a Q(s, a) = 4 before the update.
    assert boot.q[((0,), D)] == 4.0 + 0.5 * (1.0 + 0.9 * 4.0 - 4.0) == 4.3
    assert terminal.q[((0,), D)] == 4.0 + 0.5 * (1.0 - 4.0) == 2.5
    assert boot.q[((0,), C)] == terminal.q[((0,), C)] == 2.0  # only the taken action moves
    for a in (boot, terminal):
        (t,) = a.transitions
        assert (t.done, t.terminal, t.s_next, t.r) == (True, "truncated", None, 1.0)


def test_a_normal_end_never_bootstraps() -> None:
    a = agent()
    a.q[((0,), D)] = 4.0

    async def main() -> None:
        await start(a)
        await a.act(request(0))
        await a.observe(report(0, 1.0))
        await end(a, "normal")

    go(main)
    assert a.q[((0,), D)] == 2.5


def test_discount_by_elapsed() -> None:
    cfg = QConfig(alpha=1.0, gamma=0.5, epsilon=0.0, discount_by_elapsed=True)
    a = agent(cfg)
    a.q[((1,), C)] = 8.0

    async def main() -> None:
        await start(a)
        await a.act(request(0, 0))
        for n in range(3):
            await a.observe(report(n, 1.0 if n == 0 else None))
        await a.act(request(1, 1))
        await end(a)

    go(main)
    assert a.q[((0,), C)] == 1.0 + 0.5**3 * 8.0  # gamma ** elapsed, elapsed = 3 reports


def test_state_encoding_sees_exactly_the_reports_since_the_last_decision() -> None:
    seen: list[list[str]] = []

    class Spy(FieldObsCodec):
        def encode(self, request: DecisionRequest, since_last: list[ParticipantReport]) -> Any:
            seen.append([str(r.report_id) for r in since_last])
            return super().encode(request, since_last)

    a = TableAgent(
        ME, Spy(["x"]), SchemaActCodec(), RewardExtractor(fields=[]), QConfig(epsilon=0.0), 0
    )

    async def main() -> None:
        await start(a)
        await a.observe(report(0))
        await a.act(request(0, 0))
        await a.observe(report(1))
        await a.observe(report(2))
        await a.act(request(0, 1))
        await a.act(request(0, 2))
        await end(a)

    go(main)
    assert seen == [["g@me:0"], ["g@me:1", "g@me:2"], []]


# ------------------------------------------------------------------ rewards come from reports


def test_reward_extractor_sums_fields_and_ignores_absent_ones() -> None:
    ex = RewardExtractor(fields=["own_payoff", "bonus.value"])
    assert ex.extract({"own_payoff": 2, "bonus": {"value": 0.5}, "other": 9}) == 2.5
    assert ex.extract({"other": 9}) == 0.0


@pytest.mark.parametrize("bad", ["3", True, None, float("nan"), float("inf")])
def test_reward_extractor_rejects_non_numbers(bad: Any) -> None:
    with pytest.raises(ValueError):
        RewardExtractor(fields=["r"]).extract({"r": bad})


def test_reward_extractor_is_not_given_anything_but_the_payload() -> None:
    # Only report payloads are visible to the learner: a layer that discloses no reward field
    # gives zero reward, whatever the ledger says.
    a = agent()

    async def main() -> None:
        await start(a)
        await a.act(request(0))
        await a.observe(report(0, None, own_move="C", other_move="D"))
        await end(a)

    go(main)
    assert a.q == {((0,), C): 0.0}


# ------------------------------------------------------------------ failed, abandoned, frozen


def test_failed_episode_is_discarded_and_counted() -> None:
    a = agent(QConfig(alpha=0.5, gamma=0.9, epsilon=0.5, epsilon_decay=0.5))
    a.q[((0,), C)] = 1.0
    before = dict(a.q)

    async def main() -> None:
        await start(a)
        await a.act(request(0, 0))
        await a.observe(report(0, 9.0))
        await a.act(request(1, 1))  # closes transition 0: an update that must be undone
        await a.observe(report(1, 9.0))
        await a.act(request(0, 2))
        await end(a, "failed")

    go(main)
    assert a.q == before
    assert a.transitions == []
    assert (a.failed_episodes, a.episodes_seen, a.epsilon) == (1, 0, 0.5)  # no decay


def test_failed_episode_keeps_the_transitions_of_earlier_episodes() -> None:
    a = agent()

    async def main() -> None:
        await start(a)
        await a.act(request(0))
        await a.observe(report(0, 1.0))
        await end(a)
        await start(a)
        await a.act(request(5))
        await end(a, "failed")

    go(main)
    assert len(a.transitions) == 1 and a.transitions[0].s == (0,)
    assert a.episodes_seen == 1 and a.failed_episodes == 1


def test_episode_start_over_an_unfinished_episode_discards_it() -> None:
    a = agent()

    async def main() -> None:
        await start(a)
        await a.act(request(0, 0))
        await a.observe(report(0, 3.0))
        await a.act(request(1, 1))  # one update applied, episode never ended
        await start(a)  # the runner died without episode_end

    go(main)
    assert a.q == {} and a.failed_episodes == 1 and a.transitions == []


def test_learning_false_never_updates_and_never_explores() -> None:
    a = agent(QConfig(alpha=0.5, gamma=0.9, epsilon=1.0), learning=False)
    a.q[((0,), D)] = 1.0
    before = dict(a.q)

    async def main() -> None:
        for _ in range(30):
            await start(a)
            assert await a.act(request(0)) == {"move": "D"}  # greedy although cfg.epsilon == 1
            await a.observe(report(0, 5.0))
            await a.act(request(1, 1))
            await end(a, "truncated")

    go(main)
    assert a.q == before
    assert a.epsilon == 1.0 and a.episodes_seen == 0  # frozen: no decay, no count


def test_epsilon_decays_once_per_learning_episode_down_to_the_floor() -> None:
    a = agent(QConfig(epsilon=0.5, epsilon_decay=0.5, epsilon_min=0.1))
    seen: list[float] = []

    async def main() -> None:
        for _ in range(4):
            await start(a)
            await a.act(request(0))
            await a.act(request(1, 1))  # several decisions, still one decay
            await end(a)
            seen.append(a.epsilon)

    go(main)
    assert seen == [0.25, 0.125, 0.1, 0.1]
    assert a.episodes_seen == 4


def test_keep_parameters_controls_the_table() -> None:
    a = agent()

    async def main() -> None:
        await start(a)
        await a.act(request(0))
        await a.observe(report(0, 1.0))
        await end(a)
        assert a.q
        await start(a, keep_parameters=True)
        assert a.q
        await start(a, keep_parameters=False)
        assert a.q == {}

    go(main)


def test_exploration_is_uniform_over_the_actions_and_seeded() -> None:
    def run(seed: int) -> list[dict[str, Any]]:
        a = agent(QConfig(epsilon=1.0), seed=seed)

        async def main() -> list[dict[str, Any]]:
            await start(a)
            return [await a.act(request(0, n)) for n in range(200)]

        return go(main)

    first = run(7)
    assert first == run(7) and first != run(8)
    assert 60 < sum(m["move"] == "C" for m in first) < 140


def test_ties_go_to_the_first_action_of_the_codec_order() -> None:
    a = agent()

    async def main() -> None:
        await start(a)
        assert await a.act(request(0)) == {"move": "C"}
        assert a.greedy_action(request(0)) == {"move": "C"}

    go(main)


# ------------------------------------------------------------------ addressing


def test_wrong_agent_is_refused_everywhere() -> None:
    a = agent()

    async def main() -> None:
        await start(a)
        with pytest.raises(ValueError):
            await a.observe(report(0, 1.0, agent="you"))
        with pytest.raises(ValueError):
            await a.act(request(0, agent="you"))
        with pytest.raises(ValueError):
            await a.episode_end(EpisodeEnd(agent_id=AgentId("you"), terminal="normal"))
        with pytest.raises(ValueError):
            await a.episode_start(EpisodeContext(agent_id=AgentId("you"), env_ref="t", seed=0))

    go(main)


def test_reports_are_deduplicated_by_id() -> None:
    a = agent()

    async def main() -> None:
        await start(a)
        await a.act(request(0))
        await a.observe(report(0, 2.0))
        await a.observe(report(0, 2.0))  # a re-delivery
        await end(a)

    go(main)
    assert a.transitions[0].r == 2.0 and a.transitions[0].elapsed == 1


def test_act_returns_a_copy_and_an_empty_action_set_is_an_error() -> None:
    a = agent()

    async def main() -> None:
        await start(a)
        action = await a.act(request(0))
        action["move"] = "tampered"
        assert a.greedy_action(request(0)) == {"move": "C"}
        bad = request(0).model_copy(
            update={
                "action_schema": {
                    "type": "object",
                    "properties": {"m": {"enum": []}},
                    "required": ["m"],
                }
            }
        )
        with pytest.raises(ValueError):
            await a.act(bad)

    go(main)


# ------------------------------------------------------------------ config


def test_qconfig_validation() -> None:
    for bad in ({"alpha": 0.0}, {"gamma": 1.5}, {"epsilon": -0.1}, {"epsilon_decay": 0.0}):
        with pytest.raises(ValidationError):
            QConfig(**bad)
    with pytest.raises(ValidationError):
        QConfig(epsilon=0.1, epsilon_min=0.2)
    assert QConfig().model_dump() == {
        "alpha": 0.1,
        "gamma": 0.95,
        "epsilon": 0.1,
        "epsilon_decay": 1.0,
        "epsilon_min": 0.0,
        "bootstrap_on_truncation": True,
        "discount_by_elapsed": False,
    }


# ------------------------------------------------------------------ checkpoints


def _trained(seed: int = 3) -> TableAgent:
    a = agent(QConfig(alpha=0.3, gamma=0.9, epsilon=0.4, epsilon_decay=0.9), seed=seed)

    async def main() -> None:
        for e in range(20):
            await start(a)
            for k in range(3):
                await a.act(request(k % 2, k))
                await a.observe(report(100 * e + k, float((e + k) % 3)))
            await end(a)

    go(main)
    return a


def test_checkpoint_is_json_and_restores_everything() -> None:
    a = _trained()
    d = a.checkpoint()
    assert json.loads(json.dumps(d)) == d
    assert d["format"] == "gao_idl.table_agent/1" and d["created_by"].startswith("gao_idl ")
    assert d["obs_codec"]["type"] == "FieldObsCodec" and d["act_codec"]["type"] == "SchemaActCodec"
    b = TableAgent.from_checkpoint(
        json.loads(json.dumps(d)), FieldObsCodec(["x"]), SchemaActCodec()
    )
    assert b.q == a.q and b.agent_id == a.agent_id and b.seed == a.seed
    assert (b.cfg, b.reward, b.epsilon, b.episodes_seen) == (a.cfg, a.reward, a.epsilon, 20)
    assert b.checkpoint() == d


def test_restored_agent_continues_the_same_random_stream() -> None:
    a = _trained()
    b = TableAgent.from_checkpoint(a.checkpoint(), FieldObsCodec(["x"]), SchemaActCodec())

    async def main() -> tuple[list[Any], list[Any]]:
        out = []
        for agent_ in (a, b):
            await start(agent_)
            out.append([await agent_.act(request(n % 2, n)) for n in range(50)])
        return out[0], out[1]

    first, second = go(main)
    assert first == second


def test_checkpoint_does_not_alias_the_agent_or_the_dict() -> None:
    a = _trained()
    d = a.checkpoint()
    d["q"].clear()
    d["rng_state"]["state"]["state"] = 0
    assert a.checkpoint()["q"] and a.checkpoint()["rng_state"]["state"]["state"] != 0
    d2 = a.checkpoint()
    snapshot = json.dumps(d2, sort_keys=True)
    b = TableAgent.from_checkpoint(d2, FieldObsCodec(["x"]), SchemaActCodec())
    b.q[((9,), C)] = 1.0
    assert json.dumps(d2, sort_keys=True) == snapshot


def test_checkpoint_codec_and_format_mismatches() -> None:
    d = _trained().checkpoint()
    with pytest.raises(ValueError, match="obs_codec"):
        TableAgent.from_checkpoint(d, FieldObsCodec(["y"]), SchemaActCodec())
    with pytest.raises(ValueError, match="act_codec"):
        TableAgent.from_checkpoint(d, FieldObsCodec(["x"]), SchemaActCodec(grid={"a": [1]}))
    with pytest.raises(ValueError, match="format"):
        TableAgent.from_checkpoint({**d, "format": "other"}, FieldObsCodec(["x"]), SchemaActCodec())
    dup = {**d, "q": d["q"] + d["q"][:1]}
    with pytest.raises(ValueError, match="duplicate"):
        TableAgent.from_checkpoint(dup, FieldObsCodec(["x"]), SchemaActCodec())


def test_from_checkpoint_can_freeze_the_agent() -> None:
    d = _trained().checkpoint()
    frozen = TableAgent.from_checkpoint(d, FieldObsCodec(["x"]), SchemaActCodec(), learning=False)
    assert frozen.learning is False
    assert TableAgent.from_checkpoint(d, FieldObsCodec(["x"]), SchemaActCodec()).learning is True


def test_tuple_keys_survive_the_json_round_trip() -> None:
    a = TableAgent(
        ME,
        FieldObsCodec(["x"], history=1, report_fields=["own_payoff", "tag"]),
        SchemaActCodec(),
        RewardExtractor(fields=["own_payoff"]),
        QConfig(alpha=1.0, epsilon=0.0),
        0,
    )

    async def main() -> None:
        await start(a)
        await a.act(request(4, 0))
        await a.observe(report(0, 2.0, tag="t"))
        await a.act(request(5, 1))
        await end(a)

    go(main)
    first, second = ((4,), None), ((5,), (2.0, "t"))  # history=1: padded, then the last report
    assert set(a.q) == {(first, C), (second, C)}
    d = json.loads(json.dumps(a.checkpoint()))
    b = TableAgent.from_checkpoint(
        d,
        FieldObsCodec(["x"], history=1, report_fields=["own_payoff", "tag"]),
        SchemaActCodec(),
    )
    assert b.q == a.q


# ------------------------------------------------------------------ invariant 1


def test_learning_modules_import_nothing_that_could_leak_the_session() -> None:
    """Static guard: the learner never imports the session, ledger, store or runner."""
    forbidden = (
        "gao_idl.runtime",
        "gao_idl.core.ledger",
        "gao_idl.core.store",
        "gao_idl.composition",
    )
    root = Path(__file__).resolve().parents[2] / "src" / "gao_idl" / "learning"
    for name in ("codecs.py", "qlearning.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            for module in modules:
                assert not module.startswith(forbidden), f"{name} imports {module}"
