"""RandomAgent sampling, ScriptedAgent history, HumanTransportAgent transport and dedup."""

import asyncio
from typing import Any

import numpy as np
import pytest

from gao_idl.agents import (
    EpisodeContext,
    HumanTransportAgent,
    RandomAgent,
    ScriptedAgent,
    sample_schema,
)
from gao_idl.core.errors import UnknownDecision
from gao_idl.core.ids import AgentId, DecisionId, ReportId, RoleId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.environments.auction.bidding import Bid
from gao_idl.environments.pd.schemas import PDAction

ALICE = AgentId("alice")


def request(schema: dict[str, Any], n: int = 0, agent: str = "alice") -> DecisionRequest:
    return DecisionRequest(
        decision_id=DecisionId(f"root#r{n}"),
        path=["root"],
        layer="root",
        role=RoleId("r"),
        agent_id=AgentId(agent),
        observation={},
        action_schema=schema,
    )


def report(n: int, agent: str = "alice") -> ParticipantReport:
    return ParticipantReport(
        report_id=ReportId(f"root@{agent}:{n}"),
        path=["root"],
        layer="root",
        agent_id=AgentId(agent),
        payload={"n": n},
    )


def ctx(agent: str = "alice", **kw: Any) -> EpisodeContext:
    return EpisodeContext(agent_id=AgentId(agent), env_ref="m:f", seed=0, **kw)


NESTED: dict[str, Any] = {
    "type": "object",
    "required": ["flag", "count", "inner"],
    "properties": {
        "flag": {"type": "boolean"},
        "count": {"type": "integer", "minimum": -3, "maximum": 3},
        "inner": PDAction.model_json_schema(),
        "note": {"type": "string"},  # optional, so never sampled
    },
}


# ------------------------------------------------------------------ RandomAgent


def test_random_agent_samples_from_schema() -> None:
    rng = np.random.default_rng(0)
    enum_seen = {sample_schema(PDAction.model_json_schema(), rng)["move"] for _ in range(60)}
    assert enum_seen == {"C", "D"}  # enum / Literal: every value shows up, nothing else does

    bounded = {"type": "number", "minimum": 2.0, "maximum": 3.0}
    draws = [sample_schema(bounded, rng) for _ in range(200)]
    assert all(2.0 <= d <= 3.0 for d in draws) and len(set(draws)) > 100

    integers = [
        sample_schema({"type": "integer", "minimum": -2, "maximum": 2}, rng) for _ in range(200)
    ]
    assert set(integers) == {-2, -1, 0, 1, 2} and all(type(i) is int for i in integers)

    assert {sample_schema({"type": "boolean"}, rng) for _ in range(60)} == {True, False}
    assert sample_schema({"const": "x"}, rng) == "x"

    for _ in range(100):  # objects, recursively; validated by the model the schema came from
        raw = sample_schema(NESTED, rng)
        assert set(raw) == {"flag", "count", "inner"}  # only required properties
        assert isinstance(raw["flag"], bool) and -3 <= raw["count"] <= 3
        PDAction.model_validate(raw["inner"])


def test_random_agent_open_bounds_use_a_ten_wide_interval() -> None:
    rng = np.random.default_rng(1)
    up = [sample_schema({"type": "number", "minimum": 5}, rng) for _ in range(200)]
    down = [sample_schema({"type": "number", "maximum": 5}, rng) for _ in range(200)]
    both = [sample_schema({"type": "number"}, rng) for _ in range(200)]
    assert all(5 <= x <= 15 for x in up) and max(up) > 12
    assert all(-5 <= x <= 5 for x in down) and min(down) < -2
    assert all(0 <= x <= 10 for x in both)


def test_random_agent_acts_on_real_schemas_and_is_deterministic() -> None:
    async def run(seed: int) -> list[dict[str, Any]]:
        agent = RandomAgent(seed)
        await agent.episode_start(ctx())
        out = [await agent.act(request(Bid.model_json_schema())) for _ in range(5)]
        out += [await agent.act(request(PDAction.model_json_schema())) for _ in range(5)]
        return out

    first = asyncio.run(run(5))
    assert first == asyncio.run(run(5)) != asyncio.run(run(6))
    for action in first[:5]:
        Bid.model_validate(action)
    for action in first[5:]:
        PDAction.model_validate(action)


@pytest.mark.parametrize(
    "fragment, named",
    [
        ({"type": "string"}, {"type": "string"}),
        ({"anyOf": [{"type": "integer"}, {"type": "null"}]}, None),
        ({"type": "number", "exclusiveMinimum": 0}, None),
        ({"$ref": "#/$defs/X"}, None),
        ({"type": "array", "items": {"type": "integer"}}, None),
        (  # the innermost unsupported fragment is the one named
            {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}},
            {"type": "string"},
        ),
    ],
)
def test_unsupported_schema_fragments_raise_naming_the_fragment(
    fragment: dict[str, Any], named: dict[str, Any] | None
) -> None:
    with pytest.raises(NotImplementedError) as info:
        sample_schema(fragment, np.random.default_rng(0))
    assert repr(named or fragment) in str(info.value)


def test_random_agent_needs_an_object_schema() -> None:
    with pytest.raises(NotImplementedError):
        asyncio.run(RandomAgent(0).act(request({"type": "integer"})))


# ------------------------------------------------------------------ ScriptedAgent


def test_scripted_agent_keeps_only_its_report_history() -> None:
    def policy(req: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
        reports.append(report(99))  # the policy gets a copy: this must not leak into the history
        return {"n": len(reports)}

    async def run() -> None:
        agent = ScriptedAgent(policy)
        await agent.episode_start(ctx())
        assert await agent.act(request({})) == {"n": 1}
        await agent.observe(report(0))
        await agent.observe(report(0))  # duplicate delivery is ignored
        await agent.observe(report(1))
        assert await agent.act(request({}, 1)) == {"n": 3}
        with pytest.raises(ValueError, match="addressed to"):
            await agent.observe(report(2, agent="bob"))
        await agent.episode_start(ctx())  # a new episode forgets...
        assert await agent.act(request({}, 2)) == {"n": 1}
        await agent.observe(report(5))
        await agent.episode_start(ctx(keep_episode_memory=True))  # ...unless asked not to
        assert await agent.act(request({}, 3)) == {"n": 2}

    asyncio.run(run())


# ------------------------------------------------------------------ HumanTransportAgent


def test_human_transport_waits_for_provide() -> None:
    async def run() -> None:
        agent = HumanTransportAgent()
        await agent.episode_start(ctx())
        assert agent.outstanding() == []
        req1, req2 = request({}, 1), request({}, 2)
        t1 = asyncio.create_task(agent.act(req1))
        t2 = asyncio.create_task(agent.act(req2))
        assert (await agent.wait_outstanding())[0] == req1
        assert agent.outstanding() == [req1, req2]  # several requests may be outstanding
        assert not t1.done() and not t2.done()
        agent.provide(req2.decision_id, {"v": 2})
        assert await t2 == {"v": 2}
        assert agent.outstanding() == [req1]
        agent.provide(req1.decision_id, {"v": 1})
        assert await t1 == {"v": 1}
        assert agent.outstanding() == []
        with pytest.raises(UnknownDecision):
            agent.provide(req1.decision_id, {"v": 1})  # already answered

    asyncio.run(run())


def test_human_transport_cancellation_withdraws_the_request() -> None:
    async def run() -> None:
        agent = HumanTransportAgent()
        task = asyncio.create_task(agent.act(request({}, 1)))
        await agent.wait_outstanding()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert agent.outstanding() == []
        with pytest.raises(UnknownDecision):
            agent.provide(DecisionId("root#r1"), {})

    asyncio.run(run())


def test_report_dedup_in_human_transport() -> None:
    async def run() -> None:
        agent = HumanTransportAgent()
        await agent.episode_start(ctx())
        await agent.observe(report(0))
        await agent.observe(report(0))
        assert [r.report_id for r in agent.inbox] == [ReportId("root@alice:0")]
        await agent.observe(report(1))
        assert len(agent.inbox) == 2
        with pytest.raises(ValueError, match="addressed to"):
            await agent.observe(report(0, agent="bob"))

    asyncio.run(run())
