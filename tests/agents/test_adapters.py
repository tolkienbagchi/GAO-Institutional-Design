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
from gao_idl.environments.pb.schemas import Nomination
from gao_idl.environments.pd.schemas import PDAction
from tests.aio import run_test, soon

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

    first = run_test(run(5))
    assert first == run_test(run(5)) != run_test(run(6))
    for action in first[:5]:
        Bid.model_validate(action)
    for action in first[5:]:
        PDAction.model_validate(action)


@pytest.mark.parametrize(
    "fragment, named",
    [
        ({"type": "null"}, {"type": "null"}),
        ({"anyOf": [{"type": "integer"}, {"type": "null"}]}, None),
        ({"type": "integer", "exclusiveMinimum": 0}, None),  # exclusive bounds: numbers only
        ({"type": "string", "pattern": "^a+$"}, None),
        ({"type": "string", "format": "date-time"}, None),
        ({"$ref": "#/$defs/X"}, None),
        ({"type": "array", "items": {"type": "integer"}}, None),
        (  # the innermost unsupported fragment is the one named
            {"type": "object", "required": ["a"], "properties": {"a": {"type": "null"}}},
            {"type": "null"},
        ),
    ],
)
def test_unsupported_schema_fragments_raise_naming_the_fragment(
    fragment: dict[str, Any], named: dict[str, Any] | None
) -> None:
    with pytest.raises(NotImplementedError) as info:
        sample_schema(fragment, np.random.default_rng(0))
    assert repr(named or fragment) in str(info.value)


def test_strings_have_lowercase_letters_and_bounded_lengths() -> None:
    rng = np.random.default_rng(0)
    default = [sample_schema({"type": "string"}, rng) for _ in range(600)]
    assert {len(x) for x in default} == set(range(1, 13))  # default range 1..12, all of it used
    assert all(x.isalpha() and x.islower() and x.isascii() for x in default)
    bounded = [
        sample_schema({"type": "string", "minLength": 3, "maxLength": 5}, rng) for _ in range(300)
    ]
    assert {len(x) for x in bounded} == {3, 4, 5}
    exact = {
        len(sample_schema({"type": "string", "minLength": 4, "maxLength": 4}, rng))
        for _ in range(30)
    }
    assert exact == {4}
    only_min = {len(sample_schema({"type": "string", "minLength": 20}, rng)) for _ in range(100)}
    assert min(only_min) == 20 and max(only_min) == 20  # max(minLength, 12)
    only_max = {len(sample_schema({"type": "string", "maxLength": 2}, rng)) for _ in range(100)}
    assert only_max == {1, 2}
    empty_ok = {len(sample_schema({"type": "string", "maxLength": 0}, rng)) for _ in range(10)}
    assert empty_ok == {0}
    for bad in (
        {"type": "string", "minLength": 5, "maxLength": 4},
        {"type": "string", "minLength": -1},
    ):
        with pytest.raises(ValueError):
            sample_schema(bad, rng)
    with pytest.raises(NotImplementedError):
        sample_schema({"type": "string", "minLength": 1.5}, rng)


def test_exclusive_number_bounds_are_strictly_respected() -> None:
    rng = np.random.default_rng(2)

    def draws(schema: dict[str, Any], n: int = 400) -> list[float]:
        return [sample_schema({"type": "number", **schema}, rng) for _ in range(n)]

    above = draws({"exclusiveMinimum": 0})  # open upper bound: exclusiveMinimum + 10
    assert all(0 < x <= 10 for x in above) and max(above) > 8
    below = draws({"exclusiveMaximum": 5})
    assert all(-5 <= x < 5 for x in below) and min(below) < -3
    both = draws({"exclusiveMinimum": 1, "exclusiveMaximum": 2})
    assert all(1 < x < 2 for x in both)
    mixed = draws({"minimum": 1, "exclusiveMinimum": 1, "maximum": 3})  # tie: the exclusive wins
    assert all(1 < x <= 3 for x in mixed)
    looser = draws({"minimum": 2, "exclusiveMinimum": 1, "maximum": 3})  # tighter bound wins
    assert all(2 <= x <= 3 for x in looser) and min(looser) < 2.1
    closed = draws({"minimum": 0, "maximum": 1})  # closed bounds are unchanged
    assert all(0 <= x <= 1 for x in closed)
    tiny = draws({"exclusiveMinimum": 0.0, "exclusiveMaximum": 1e-300})
    assert all(0.0 < x < 1e-300 for x in tiny)


class _EdgeRng:
    """A generator whose first uniform draw lands exactly on the lower end of the interval."""

    def __init__(self, *, edge: str) -> None:
        self.edge, self.calls = edge, 0

    def uniform(self, lo: float, hi: float) -> float:
        self.calls += 1
        if self.calls == 1:
            return lo if self.edge == "lo" else hi
        return (lo + hi) / 2


@pytest.mark.parametrize("edge", ["lo", "hi"])
def test_a_draw_on_an_open_end_is_redrawn(edge: str) -> None:
    rng = _EdgeRng(edge=edge)
    x = sample_schema({"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 4}, rng)  # type: ignore[arg-type]
    assert x == 2.0 and rng.calls == 2
    closed = _EdgeRng(edge=edge)  # a closed end may be returned as it is
    y = sample_schema({"type": "number", "minimum": 0, "maximum": 4}, closed)  # type: ignore[arg-type]
    assert y == (0.0 if edge == "lo" else 4.0) and closed.calls == 1


@pytest.mark.parametrize(
    "schema",
    [
        {"exclusiveMinimum": 1, "exclusiveMaximum": 1},
        {"exclusiveMinimum": 1, "maximum": 1},
        {"minimum": 2, "exclusiveMaximum": 2},
        {"exclusiveMinimum": 2, "exclusiveMaximum": 1},
        {"exclusiveMinimum": 1.0, "exclusiveMaximum": float(np.nextafter(1.0, 2.0))},
    ],
)
def test_empty_exclusive_ranges_raise(schema: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        sample_schema({"type": "number", **schema}, np.random.default_rng(0))


def test_non_numeric_bounds_are_not_guessed() -> None:
    for bad in ({"exclusiveMinimum": True}, {"exclusiveMinimum": "0"}):
        with pytest.raises(NotImplementedError):
            sample_schema({"type": "number", **bad}, np.random.default_rng(0))


def test_random_agent_samples_pb_nominations() -> None:
    """The 200 samples all validate (title length 1..60, strictly positive finite cost)."""
    rng = np.random.default_rng(7)
    schema = Nomination.model_json_schema()
    titles = set()
    for _ in range(200):
        raw = sample_schema(schema, rng)
        nomination = Nomination.model_validate(raw)
        assert set(raw) == {"title", "cost"} and nomination.cost > 0
        titles.add(nomination.title)
    assert len(titles) > 150  # not a constant
    action = run_test(RandomAgent(5).act(request(schema)))
    Nomination.model_validate(action)


def test_random_agent_needs_an_object_schema() -> None:
    with pytest.raises(NotImplementedError):
        run_test(RandomAgent(0).act(request({"type": "integer"})))


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

    run_test(run())


# ------------------------------------------------------------------ HumanTransportAgent


def test_human_transport_waits_for_provide() -> None:
    async def run() -> None:
        agent = HumanTransportAgent()
        await agent.episode_start(ctx())
        assert agent.outstanding() == []
        req1, req2 = request({}, 1), request({}, 2)
        t1 = asyncio.create_task(agent.act(req1))
        t2 = asyncio.create_task(agent.act(req2))
        assert (await soon(agent.wait_outstanding()))[0] == req1
        assert agent.outstanding() == [req1, req2]  # several requests may be outstanding
        assert not t1.done() and not t2.done()
        agent.provide(req2.decision_id, {"v": 2})
        assert await soon(t2) == {"v": 2}
        assert agent.outstanding() == [req1]
        agent.provide(req1.decision_id, {"v": 1})
        assert await soon(t1) == {"v": 1}
        assert agent.outstanding() == []
        with pytest.raises(UnknownDecision):
            agent.provide(req1.decision_id, {"v": 1})  # already answered

    run_test(run())


def test_human_transport_cancellation_withdraws_the_request() -> None:
    async def run() -> None:
        agent = HumanTransportAgent()
        task = asyncio.create_task(agent.act(request({}, 1)))
        await soon(agent.wait_outstanding())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await soon(task)
        assert agent.outstanding() == []
        with pytest.raises(UnknownDecision):
            agent.provide(DecisionId("root#r1"), {})

    run_test(run())


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

    run_test(run())


BALLOT: dict[str, Any] = {
    "type": "object",
    "required": ["approve"],
    "properties": {"approve": {"type": "array", "items": {"enum": ["p0", "p1", "p2"]}}},
}


def test_random_agent_samples_duplicate_free_ballots() -> None:
    rng = np.random.default_rng(0)
    sizes = set()
    seen_orders = set()
    for _ in range(200):
        ballot = sample_schema(BALLOT, rng)["approve"]
        assert len(ballot) == len(set(ballot)) and set(ballot) <= {"p0", "p1", "p2"}
        sizes.add(len(ballot))
        seen_orders.add(tuple(ballot))
    assert sizes == {0, 1, 2, 3}  # the whole range of sizes shows up
    assert len(seen_orders) > 8


def test_array_bounds_and_const_shaped_items() -> None:
    rng = np.random.default_rng(1)
    bounded = {"type": "array", "minItems": 2, "maxItems": 2, "items": {"enum": list("abcd")}}
    assert all(len(sample_schema(bounded, rng)) == 2 for _ in range(50))
    any_of = {"type": "array", "items": {"anyOf": [{"const": 1}, {"const": 2}, {"enum": [3]}]}}
    drawn = {tuple(sorted(sample_schema(any_of, rng))) for _ in range(200)}
    assert all(set(d) <= {1, 2, 3} for d in drawn) and () in drawn and (1, 2, 3) in drawn
    capped = {"type": "array", "maxItems": 9, "items": {"const": "x"}}
    assert {len(sample_schema(capped, rng)) for _ in range(50)} == {0, 1}  # clamped to len(values)
    with pytest.raises(ValueError):
        sample_schema({"type": "array", "minItems": 4, "items": {"enum": [1, 2]}}, rng)


@pytest.mark.parametrize(
    "items",
    [{"type": "integer"}, {"$ref": "#/$defs/P"}, {"anyOf": [{"type": "string"}, {"const": 1}]}, {}],
)
def test_arrays_of_non_enum_items_still_raise(items: dict[str, Any]) -> None:
    fragment = {"type": "array", "items": items}
    with pytest.raises(NotImplementedError) as info:
        sample_schema(fragment, np.random.default_rng(0))
    assert repr(fragment) in str(info.value)


def test_random_agent_acts_on_a_ballot_request() -> None:
    action = run_test(RandomAgent(3).act(request(BALLOT)))
    assert set(action) == {"approve"} and isinstance(action["approve"], list)
