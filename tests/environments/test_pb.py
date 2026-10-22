"""Participatory budgeting institution: nomination -> eligibility -> approval voting -> funding."""

import json
from typing import Any

import pytest

from gao_idl.agents import RandomAgent, ScriptedAgent
from gao_idl.agents.base import AgentAdapter, EpisodeContext, EpisodeEnd
from gao_idl.composition.contracts import check, describe
from gao_idl.core import events as ev
from gao_idl.core.errors import ContractError, SchemaError
from gao_idl.core.ids import AgentId
from gao_idl.core.layer import validate_entry
from gao_idl.core.schemas import Config, DecisionRequest, ParticipantReport
from gao_idl.environments.pb import FUNDING, RULES, build_pb
from gao_idl.environments.pb.layers import funding_layer
from gao_idl.environments.pb.schemas import BallotsExit, Project, ballot_schema
from gao_idl.llm.agent import LLMAgent
from gao_idl.llm.mock import MockBackend
from gao_idl.runtime.runner import run
from gao_idl.runtime.session import Session
from gao_idl.testing import assert_replay_equivalent
from tests.aio import run_test

ENV = "gao_idl.environments.pb:build_pb"
Noms = dict[str, dict[str, Any]]
Ballots = dict[str, list[str]]


def params(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "proposers": ["alice", "bob"],
        "voters": ["alice", "bob", "carol"],
        "budget": 10,
        "max_cost": 8,
        "rule": "greedy",
    }
    base.update(over)
    return base


def nom(title: str, cost: Any) -> dict[str, Any]:
    return {"title": title, "cost": cost}


NOMS: Noms = {"alice": nom("Park", 4), "bob": nom("Library", 5)}
VOTES: Ballots = {"alice": ["p0"], "bob": ["p0", "p1"], "carol": ["p1"]}


def submit_stage(session: Session, noms: Noms, ballots: Ballots, *, reverse: bool = False) -> None:
    """Answer every pending request: nominations from ``noms``, ballots from ``ballots``."""
    pending = session.pending()
    for req in reversed(pending) if reverse else pending:
        action = (
            noms[req.agent_id] if req.layer == "nomination" else {"approve": ballots[req.agent_id]}
        )
        session.submit(req.agent_id, req.decision_id, action)


def run_pb(
    p: dict[str, Any], noms: Noms, ballots: Ballots, seed: int = 3, *, reverse: bool = False
) -> Session:
    """Start a session and answer stage by stage until it finishes."""
    session = Session.start(ENV, p, seed)
    for _ in range(10):
        session.advance()
        if session.finished:
            return session
        submit_stage(session, noms, ballots, reverse=reverse)
    raise AssertionError("session did not finish")


def at_voting(p: dict[str, Any], noms: Noms, seed: int = 3) -> Session:
    """A session whose nominations are in and whose voting requests are pending."""
    session = Session.start(ENV, p, seed)
    session.advance()
    submit_stage(session, noms, {})
    session.advance()
    assert {r.layer for r in session.pending()} == {"voting"}
    return session


def reports(session: Session, agent: str) -> list[ParticipantReport]:
    info = session.ledger.information(AgentId(agent))
    return [i for i in info if isinstance(i, ParticipantReport)]


def requests(session: Session, agent: str) -> list[DecisionRequest]:
    info = session.ledger.information(AgentId(agent))
    return [i for i in info if isinstance(i, DecisionRequest)]


def report_from(session: Session, agent: str, layer: str) -> ParticipantReport:
    (found,) = [r for r in reports(session, agent) if r.layer == layer]
    return found


def stream(session: Session, agent: str) -> list[dict[str, Any]]:
    """The agent's whole information stream as JSON, in delivery order."""
    return [i.model_dump(mode="json") for i in session.ledger.information(AgentId(agent))]


def differing(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> list[int]:
    assert len(a) == len(b), (len(a), len(b))
    return [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True)


def item_ids(schema: dict[str, Any]) -> list[str]:
    """The ids a ballot schema allows (``enum``; ``const`` when there is a single project)."""
    items = schema["properties"]["approve"]["items"]
    return list(items["enum"]) if "enum" in items else [items["const"]]


def trace_kinds(session: Session) -> list[str]:
    return [e.kind for e in session.trace]


# ------------------------------------------------------------------ construction and smoke


@pytest.mark.parametrize("rule", RULES)
def test_every_rule_completes_the_pipeline(rule: str) -> None:
    session = run_pb(params(rule=rule), NOMS, VOTES)
    assert session.finished and not session.failed
    assert session.result is not None and session.result.port == "done"
    record = session.result.record
    assert record["rule"] == rule
    assert record["funded"] == ["p0", "p1"]  # both projects (4 + 5) fit the budget of 10
    assert record["spent"] == 9.0
    assert_replay_equivalent(session)


def test_the_process_is_well_wired() -> None:
    process, cfg = build_pb(**params())
    check(process)
    assert sorted(process.exits) == ["done", "none"]
    assert sorted(cfg.roles) == ["proposer:0", "proposer:1"]
    tree = describe(process)
    assert tree["kind"] == "seq" and tree["name"] == "pb"
    assert set(FUNDING) == set(RULES)


@pytest.mark.parametrize(
    "bad",
    [
        {"rule": "borda"},
        {"voters": []},
        {"voters": ["a", "a"]},
        {"proposers": ["a", ""]},
        {"proposers": "alice"},
        {"budget": 0},
        {"budget": True},
        {"max_cost": float("inf")},
        {"max_cost": -1},
        {"reveal_ballots": "yes"},
        {"private_values": {"zed": {"p0": 1}}},
        {"private_values": {"alice": {"p0": float("nan")}}},
        {"private_values": {"alice": {"p0": True}}},
        {"private_values": ["alice"]},
        {"timeout_nomination": {"kind": "default"}},
        {"timeout_nomination": {"kind": "nope"}},
    ],
)
def test_build_pb_refuses_bad_arguments(bad: dict[str, Any]) -> None:
    with pytest.raises(ContractError):
        build_pb(**params(**bad))


# ------------------------------------------------------------------ the generated ballot schema


def test_generated_ballot_schema_has_project_enum() -> None:
    # carol's 9 exceeds max_cost 8, so p2 is nominated but not eligible.
    p = params(proposers=["alice", "bob", "carol"])
    session = at_voting(p, {**NOMS, "carol": nom("Mega", 9)})
    pending = session.pending()
    assert [r.role for r in pending] == ["voter:0", "voter:1", "voter:2"]
    for req in pending:
        assert item_ids(req.action_schema) == ["p0", "p1"]  # exactly the eligible ids
        assert [x["id"] for x in req.observation["projects"]] == ["p0", "p1"]
        assert req.action_schema["properties"]["approve"]["type"] == "array"
        assert req.action_schema["additionalProperties"] is False
        # the schema is the generated model's, not a hand-written one
        assert req.action_schema == ballot_schema(req.observation["projects"]).model_json_schema()
    items = pending[0].action_schema["properties"]["approve"]["items"]
    assert items["enum"] == ["p0", "p1"]


def test_ballot_schema_follows_the_nominations() -> None:
    wide = at_voting(
        params(proposers=["a", "b", "c", "d"]),
        {"a": nom("A", 1), "b": nom("B", 2), "c": nom("C", 3), "d": nom("D", 4)},
    )
    assert item_ids(wide.pending()[0].action_schema) == ["p0", "p1", "p2", "p3"]
    one = at_voting(params(proposers=["alice"]), {"alice": nom("Only", 3)})
    assert item_ids(one.pending()[0].action_schema) == ["p0"]  # a lone project renders as const


def test_duplicate_or_unknown_project_rejected() -> None:
    session = at_voting(params(), NOMS)
    carol = next(r for r in session.pending() if r.agent_id == "carol")
    for bad in (
        {"approve": ["p0", "p0"]},
        {"approve": ["p7"]},
        {"approve": ["p0", "p7"]},
        {"approve": "p0"},
        {"approve": [0]},
        {"approve": ["p0"], "extra": 1},
        {},
    ):
        with pytest.raises(SchemaError):
            session.submit(AgentId("carol"), carol.decision_id, bad)
        assert carol.decision_id in [r.decision_id for r in session.pending()]  # still pending
    votes_logged = [
        e for e in session.input_log if isinstance(e, ev.Submitted) and "voting" in e.decision_id
    ]
    assert not votes_logged  # none of the refused ballots reached the input log
    assert trace_kinds(session).count("action_rejected") == 7
    session.submit(AgentId("carol"), carol.decision_id, {"approve": []})  # abstaining is allowed
    assert carol.decision_id not in [r.decision_id for r in session.pending()]


@pytest.mark.parametrize(
    "bad",
    [
        nom("Park", 0),
        nom("Park", -2),
        nom("Park", "5"),
        nom("Park", True),
        nom("Park", float("nan")),
        nom("", 3),
        nom("x" * 61, 3),
        {"title": "Park", "cost": 3, "extra": 1},
        {"title": "Park"},
    ],
)
def test_bad_nominations_are_rejected_and_stay_pending(bad: dict[str, Any]) -> None:
    session = Session.start(ENV, params(), 1)
    session.advance()
    req = session.pending()[0]
    with pytest.raises(SchemaError):
        session.submit(req.agent_id, req.decision_id, bad)
    assert req.decision_id in [r.decision_id for r in session.pending()]
    session.submit(
        req.agent_id, req.decision_id, nom("x" * 60, 0.01)
    )  # the limits themselves are fine


# ------------------------------------------------------------------ agents that need no PB code


class NominatingRandom:
    """A RandomAgent for ballots; nominations (a string and an exclusive bound) are scripted.

    ``RandomAgent`` cannot sample the nomination schema (``string``, ``exclusiveMinimum``), and
    the brief only claims PB-free ballots, so this wrapper is the whole of the PB-specific code.
    """

    def __init__(self, seed: int, noms: Noms) -> None:
        self._random = RandomAgent(seed)
        self._noms = noms

    async def episode_start(self, ctx: EpisodeContext) -> None:
        await self._random.episode_start(ctx)

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        if request.layer == "nomination":
            return self._noms[request.agent_id]
        return await self._random.act(request)

    async def observe(self, report: ParticipantReport) -> None:
        await self._random.observe(report)

    async def episode_end(self, end: EpisodeEnd) -> None:
        await self._random.episode_end(end)


KNAPSACK_CENTS = pytest.mark.xfail(
    raises=ValueError,
    strict=True,
    reason="knapsack needs whole-cent costs and a random float cost is not (NOTES.md, M6 task 0)",
)


@pytest.mark.parametrize(
    "rule", [pytest.param(r, marks=KNAPSACK_CENTS) if r == "knapsack" else r for r in RULES]
)
def test_plain_random_agents_run_pb_end_to_end(rule: str) -> None:
    """Nominations are samplable now (strings, exclusive bounds): no PB-specific adapter needed."""
    people = ["alice", "bob", "carol", "dan", "erin"]
    p = params(proposers=["alice", "bob", "carol"], voters=people, budget=10, rule=rule)
    ports = set()
    for seed in range(8):
        session = Session.start(ENV, p, seed)
        adapters: dict[AgentId, AgentAdapter] = {
            AgentId(a): RandomAgent(seed * 10 + i) for i, a in enumerate(people)
        }
        run_test(run(session, adapters))
        assert session.finished and not session.failed
        assert session.result is not None
        ports.add(session.result.port)
        assert "action_rejected" not in trace_kinds(session)
        assert_replay_equivalent(session)
    assert "done" in ports <= {"done", "none"}  # random costs sometimes exceed the limits


@pytest.mark.parametrize("rule", RULES)
def test_random_agents_complete_pb_for_every_rule(rule: str) -> None:
    people = ["alice", "bob", "carol", "dan", "erin"]
    noms: Noms = {"alice": nom("Park", 3), "bob": nom("Pool", 4), "carol": nom("Lights", 5)}
    p = params(proposers=["alice", "bob", "carol"], voters=people, budget=10, rule=rule)
    for seed in range(5):
        session = Session.start(ENV, p, seed)
        adapters: dict[AgentId, AgentAdapter] = {
            AgentId(a): NominatingRandom(seed * 10 + i, noms) for i, a in enumerate(people)
        }
        run_test(run(session, adapters))
        assert session.finished and not session.failed
        assert session.result is not None and session.result.port == "done"
        assert session.result.record["spent"] <= 10
        kinds = trace_kinds(session)
        assert "action_rejected" not in kinds and "action_defaulted" not in kinds
        assert kinds.count("action_accepted") == 3 + 5  # nothing was repaired or timed out
        assert_replay_equivalent(session)


def test_llm_mock_agent_completes_pb() -> None:
    def proposer(request: DecisionRequest, seen: list[ParticipantReport]) -> dict[str, Any]:
        return NOMS[request.agent_id]

    p = params(proposers=["alice", "bob"], voters=["carol", "dan"])
    session = Session.start(ENV, p, 4)
    backends = {a: MockBackend([{"approve": ["p0", "p1"]}]) for a in ("carol", "dan")}
    adapters: dict[AgentId, AgentAdapter] = {
        AgentId("alice"): ScriptedAgent(proposer),
        AgentId("bob"): ScriptedAgent(proposer),
        **{AgentId(a): LLMAgent(AgentId(a), b) for a, b in backends.items()},
    }
    run_test(run(session, adapters))
    assert session.result is not None and session.result.port == "done"
    assert session.result.record["funded"] == ["p0", "p1"]
    for backend in backends.values():
        (call,) = backend.calls  # one call each: the reply was valid first time
        assert call.response_schema is not None
        assert item_ids({"properties": call.response_schema["properties"]}) == ["p0", "p1"]
        assert "Park" in call.messages[-1].content  # the observation reached the prompt
    assert "action_rejected" not in trace_kinds(session)


# ------------------------------------------------------------------ routing around voting


def test_empty_nomination_routes_to_none() -> None:
    p = params(proposers=["alice", "bob"], voters=["alice", "carol"])
    session = Session.start(ENV, p, 2)
    session.advance()
    for req in session.pending():
        session.timeout(req.decision_id)  # DropRole by default: nobody nominates
    session.advance()
    assert session.finished and not session.failed
    assert session.result is not None and session.result.port == "none"
    assert session.result.record["reason"] == "no_nominations"
    assert trace_kinds(session).count("role_dropped") == 2
    assert not [
        e for e in session.trace if getattr(e, "path", None) and "voting" in map(str, e.path)
    ]
    assert all(
        r.layer != "voting"
        for e in session.trace
        if isinstance(e, ev.DecisionRequested)
        for r in [e.request]
    )
    # a public report reached every voter and proposer, identical
    seen = {a: report_from(session, a, "nomination") for a in ("alice", "bob", "carol")}
    assert len({canonical(r.payload) for r in seen.values()}) == 1
    assert seen["alice"].payload == {"status": "none", "eligible": [], "rejected": []}
    assert_replay_equivalent(session)


def test_no_proposers_at_all_also_exits_none() -> None:
    session = Session.start(ENV, params(proposers=[]), 2)
    session.advance()
    assert session.finished and session.result is not None and session.result.port == "none"
    assert reports(session, "carol")[0].payload["status"] == "none"


def test_a_timeout_under_fail_policy_fails_the_session() -> None:
    session = Session.start(ENV, params(timeout_nomination={"kind": "fail"}), 2)
    session.advance()
    session.timeout(session.pending()[0].decision_id)
    session.advance()
    assert session.failed and session.result is None


def test_one_dropped_proposer_does_not_stop_the_institution() -> None:
    session = Session.start(ENV, params(), 2)
    session.advance()
    alice, bob = session.pending()
    session.timeout(alice.decision_id)
    session.submit(bob.agent_id, bob.decision_id, nom("Library", 5))
    session.advance()
    (project,) = session.pending()[0].observation["projects"]
    assert project == {"id": "p0", "title": "Library", "cost": 5.0, "proposer": "bob"}


def test_nothing_eligible_routes_to_none_with_a_public_report() -> None:
    noms = {"alice": nom("Huge", 9), "bob": nom("Vast", 12)}  # both above max_cost 8; bob above 10
    session = run_pb(params(), noms, {})
    assert session.result is not None and session.result.port == "none"
    assert session.result.record["reason"] == "no_eligible_projects"
    assert "voting" not in {
        r.layer for e in session.trace if isinstance(e, ev.DecisionRequested) for r in [e.request]
    }
    payloads = {
        canonical(report_from(session, a, "eligibility").payload) for a in ("alice", "bob", "carol")
    }
    assert len(payloads) == 1
    payload = report_from(session, "carol", "eligibility").payload
    assert payload["status"] == "none" and payload["eligible"] == []
    assert payload["rejected"] == [
        {"id": "p0", "reason": "over_max_cost"},
        {"id": "p1", "reason": "over_budget_and_max_cost"},
    ]


# ------------------------------------------------------------------ eligibility


def test_eligibility_filters_and_reports_publicly() -> None:
    names = ["alice", "bob", "carol", "dan"]
    noms = {
        "alice": nom("Park", 4),
        "bob": nom("Dome", 9),  # above max_cost 8
        "carol": nom("Pool", 8),  # exactly max_cost: eligible
        "dan": nom("Tower", 12),  # above both
    }
    session = run_pb(
        params(proposers=names, voters=["alice", "erin"]),
        noms,
        {"alice": ["p0", "p2"], "erin": ["p2"]},
    )
    audience = names + ["erin"]
    payloads = [report_from(session, a, "eligibility").payload for a in audience]
    assert len({canonical(x) for x in payloads}) == 1  # byte-identical for all five
    assert payloads[0] == {
        "status": "eligible",
        "eligible": [
            {"id": "p0", "title": "Park", "cost": 4.0},
            {"id": "p2", "title": "Pool", "cost": 8.0},
        ],
        "rejected": [
            {"id": "p1", "reason": "over_max_cost"},
            {"id": "p3", "reason": "over_budget_and_max_cost"},
        ],
    }
    assert session.result is not None
    # budget 10: Park (4) and Pool (8) do not both fit; Pool has more approvals, so it wins
    assert session.result.record["funded"] == ["p2"]


def test_over_budget_alone_is_reported_as_such() -> None:
    p = params(budget=5, max_cost=20, voters=["carol"])
    noms = {"alice": nom("Park", 4), "bob": nom("Library", 6)}  # 6 > budget 5, below max_cost 20
    session = run_pb(p, noms, {"carol": ["p0"]})
    payload = report_from(session, "carol", "eligibility").payload
    assert payload["rejected"] == [{"id": "p1", "reason": "over_budget"}]
    assert [x["id"] for x in payload["eligible"]] == ["p0"]


def test_project_ids_follow_sorted_proposer_order_not_arrival() -> None:
    p = params(proposers=["bob", "alice", "carol"])
    noms = {"alice": nom("Z", 1), "bob": nom("Y", 2), "carol": nom("X", 3)}
    forward = run_pb(p, noms, {a: [] for a in ("alice", "bob", "carol")})
    backward = run_pb(p, noms, {a: [] for a in ("alice", "bob", "carol")}, reverse=True)
    for session in (forward, backward):
        projects = report_from(session, "alice", "eligibility").payload["eligible"]
        assert [(x["id"], x["title"]) for x in projects] == [("p0", "Z"), ("p1", "Y"), ("p2", "X")]
    assert ev.dump(forward.trace) == ev.dump(backward.trace)  # arrival order leaves no mark


# ------------------------------------------------------------------ reports


PRIVATE = {"alice": {"p0": 3.0, "p1": 1.0}, "carol": {"p0": 2.0, "p1": 7.0, "p9": 100.0}}


def test_public_vs_private_report_split() -> None:
    p = params(voters=["alice", "carol", "dan"], private_values=PRIVATE)
    ballots = {"alice": ["p0"], "carol": ["p1"], "dan": ["p0", "p1"]}
    session = run_pb(p, NOMS, ballots)
    funding = {
        a: report_from(session, a, "funding_greedy").payload
        for a in ("alice", "bob", "carol", "dan")
    }
    assert len({canonical(x["public"]) for x in funding.values()}) == 1  # one public part
    assert funding["alice"]["public"] == {
        "rule": "greedy",
        "budget": 10.0,
        "funded": [
            {"id": "p0", "title": "Park", "cost": 4.0},
            {"id": "p1", "title": "Library", "cost": 5.0},
        ],
        "spent": 9.0,
    }  # no ballots, no counts
    assert funding["alice"]["private"] == {"own_value_of_funded": 4.0}
    assert funding["carol"]["private"] == {"own_value_of_funded": 9.0}  # p9 is not a project
    assert "private" not in funding["bob"] and "private" not in funding["dan"]
    seen = {
        a: requests(session, a)[-1].observation["own_values"] for a in ("alice", "carol", "dan")
    }
    assert seen == {"alice": {"p0": 3.0, "p1": 1.0}, "carol": {"p0": 2.0, "p1": 7.0}, "dan": None}

    # noninterference over private_values: changing carol's values changes only carol's stream
    other = {**PRIVATE, "carol": {"p0": 2.0, "p1": 8.0}}
    changed = run_pb(params(voters=["alice", "carol", "dan"], private_values=other), NOMS, ballots)
    for agent in ("alice", "bob", "dan"):
        assert stream(changed, agent) == stream(session, agent), agent
    a, b = stream(session, "carol"), stream(changed, "carol")
    layers = [a[i].get("layer") or a[i].get("request", {}).get("layer") for i in differing(a, b)]
    assert layers == ["voting", "funding_greedy"]  # her observation and her funding report
    assert b[differing(a, b)[-1]]["payload"]["private"] == {"own_value_of_funded": 10.0}
    assert changed.result == session.result  # and nothing else moved


def test_ballot_privacy() -> None:
    p = params()
    base = {"alice": ["p0", "p1"], "bob": ["p0", "p1"], "carol": ["p0", "p1"]}
    streams = {}
    for bob_ballot in (["p0", "p1"], ["p0"], []):
        session = run_pb(p, NOMS, {**base, "bob": bob_ballot})
        assert session.result is not None and session.result.record["funded"] == ["p0", "p1"]
        streams[tuple(bob_ballot)] = {a: stream(session, a) for a in ("alice", "bob", "carol")}
    first = streams[("p0", "p1")]
    for other in streams.values():
        assert other["alice"] == first["alice"] and other["carol"] == first["carol"]
    assert streams[("p0",)]["bob"] != first["bob"]  # bob's own receipt does change

    # control: with reveal_ballots the same perturbation does reach alice
    revealed = [
        stream(run_pb(params(reveal_ballots=True), NOMS, {**base, "bob": b}), "alice")
        for b in (["p0", "p1"], ["p0"])
    ]
    assert revealed[0] != revealed[1]

    # when bob's ballot decides the funded set, alice's stream differs only in the funding report
    tight = params(budget=6)
    one = run_pb(tight, NOMS, {"alice": ["p1"], "bob": ["p0"], "carol": ["p0"]})
    two = run_pb(tight, NOMS, {"alice": ["p1"], "bob": ["p1"], "carol": ["p0"]})
    a, b = stream(one, "alice"), stream(two, "alice")
    (index,) = differing(a, b)
    assert a[index]["layer"] == "funding_greedy"
    assert a[index]["payload"]["public"]["funded"] != b[index]["payload"]["public"]["funded"]
    assert "ballots" not in a[index]["payload"]["public"]


def test_ballots_appear_in_the_public_report_only_when_revealed() -> None:
    ballots = {"alice": ["p0"], "bob": ["p1", "p0"], "carol": []}
    hidden = run_pb(params(), NOMS, ballots)
    shown = run_pb(params(reveal_ballots=True), NOMS, ballots)
    assert "ballots" not in report_from(hidden, "alice", "funding_greedy").payload["public"]
    for agent in ("alice", "bob", "carol"):
        public = report_from(shown, agent, "funding_greedy").payload["public"]
        assert public["ballots"] == {"alice": ["p0"], "bob": ["p0", "p1"], "carol": []}


def test_the_voting_receipt_is_only_the_voters_own_ballot() -> None:
    session = run_pb(params(), NOMS, VOTES)
    for agent, ballot in VOTES.items():
        assert report_from(session, agent, "voting").payload == {"own_ballot": ballot}
    assert [r.layer for r in reports(session, "carol")] == [
        "eligibility",
        "voting",
        "funding_greedy",
    ]


# ------------------------------------------------------------------ swapping the funding rule


TRAP_NOMS: Noms = {"alice": nom("A", 6), "bob": nom("B", 5), "carol": nom("C", 5)}
TRAP_VOTES: Ballots = {"v1": ["p0", "p1"], "v2": ["p0", "p1"], "v3": ["p0", "p2"], "v4": ["p2"]}


def trap(rule: str) -> Session:
    p = params(
        proposers=["alice", "bob", "carol"],
        voters=["v1", "v2", "v3", "v4"],
        budget=10,
        max_cost=10,
        rule=rule,
    )
    return run_pb(p, TRAP_NOMS, TRAP_VOTES)


def last_voting_event(session: Session) -> int:
    (index,) = [
        i
        for i, e in enumerate(session.trace)
        if isinstance(e, ev.Exited) and str(e.path[-1]) == "voting"
    ]
    return index


def test_rule_swap_changes_only_funding_stage() -> None:
    greedy, knap = trap("greedy"), trap("knapsack")
    assert greedy.result is not None and knap.result is not None
    assert greedy.result.record["funded"] == ["p0"]  # 3 approvals
    assert knap.result.record["funded"] == ["p1", "p2"]  # 4 approvals
    cut = last_voting_event(greedy) + 1
    assert cut == last_voting_event(knap) + 1
    for rule in RULES:
        other = trap(rule)
        before = ev.strip_paths(ev.dump(other.trace[:cut]))
        assert before == ev.strip_paths(ev.dump(greedy.trace[:cut])), rule  # t included
        assert str(other.trace[cut].path[-1]) == f"funding_{rule}"
    assert ev.strip_paths(ev.dump(greedy.trace[cut:])) != ev.strip_paths(ev.dump(knap.trace[cut:]))
    # before the funding report each voter has exactly the same information
    for agent in ("alice", "bob", "carol", "v1", "v2", "v3", "v4"):
        a, b = stream(greedy, agent), stream(knap, agent)
        assert differing(a, b) == [len(a) - 1], agent  # only the funding report differs
        assert a[-1]["layer"] == "funding_greedy" and b[-1]["layer"] == "funding_knapsack"


def test_the_trees_differ_only_in_the_funding_layer_name() -> None:
    trees = {}
    for rule in RULES:
        process, _ = build_pb(**params(rule=rule))
        trees[rule] = json.dumps(describe(process), sort_keys=True).replace(f"funding_{rule}", "F")
    assert len(set(trees.values())) == 1


def test_a_rule_that_overspends_is_caught() -> None:
    projects = [
        Project(id="p0", title="A", cost=6, proposer=AgentId("x")),
        Project(id="p1", title="B", cost=5, proposer=AgentId("x")),
    ]
    layer = funding_layer("everything", lambda ps, b, budget: [p.id for p in ps])
    state = BallotsExit(budget=10, projects=projects, ballots={AgentId("v"): ["p0"]})
    cfg = validate_entry(layer, Config(state=state.model_dump(mode="json"), roles={}))
    with pytest.raises(ContractError, match="overspent"):
        layer.outcome(cfg, {}, None, None)  # type: ignore[arg-type]
    with pytest.raises(ContractError, match="invalid ids"):
        funding_layer("ghost", lambda ps, b, budget: ["p9"]).outcome(cfg, {}, None, None)  # type: ignore[arg-type]


# ------------------------------------------------------------------ one agent, two roles


def test_role_reassignment_proposer_and_voter_same_agent() -> None:
    p = params(proposers=["alice", "bob"], voters=["alice", "carol"])
    session = Session.start(ENV, p, 5)
    session.advance()
    first = session.pending_for(AgentId("alice"))
    assert [(r.layer, r.role) for r in first] == [("nomination", "proposer:0")]
    submit_stage(session, NOMS, {})
    session.advance()
    second = session.pending_for(AgentId("alice"))
    assert [(r.layer, r.role) for r in second] == [("voting", "voter:0")]
    assert first[0].decision_id != second[0].decision_id
    submit_stage(session, NOMS, {"alice": ["p0"], "carol": ["p1"]})
    session.advance()
    assert session.finished

    def kinds(agent: str) -> list[tuple[str, str]]:
        out = []
        for item in session.ledger.information(AgentId(agent)):
            if isinstance(item, DecisionRequest):
                out.append(("request", f"{item.layer}/{item.role}"))
            else:
                out.append(("report", item.layer))
        return out

    assert kinds("alice") == [
        ("request", "nomination/proposer:0"),
        ("report", "eligibility"),
        ("request", "voting/voter:0"),
        ("report", "voting"),
        ("report", "funding_greedy"),
    ]
    assert kinds("bob") == [
        ("request", "nomination/proposer:1"),
        ("report", "eligibility"),
        ("report", "funding_greedy"),
    ]
    assert kinds("carol") == [
        ("report", "eligibility"),
        ("request", "voting/voter:1"),
        ("report", "voting"),
        ("report", "funding_greedy"),
    ]
    assert_replay_equivalent(session)


# ------------------------------------------------------------------ replay and restore


def resume(snapshot: dict[str, Any]) -> Session:
    return Session.restore(json.loads(json.dumps(snapshot)))  # through real JSON


def test_pb_replay_and_partial_restore_between_stages() -> None:
    p = params(
        voters=["alice", "bob", "carol"],
        private_values=PRIVATE,
        reveal_ballots=True,
        rule="mes",
    )
    reference = run_pb(p, NOMS, VOTES, seed=9)
    want = ev.dump(reference.trace)
    assert_replay_equivalent(reference)
    assert resume(reference.snapshot()).result == reference.result  # restore at the end

    def finish(session: Session) -> Session:
        for _ in range(10):
            session.advance()
            if session.finished:
                return session
            submit_stage(session, NOMS, VOTES)
        raise AssertionError("did not finish")

    # restore before anything happened
    start = Session.start(ENV, p, 9)
    assert ev.dump(finish(resume(start.snapshot())).trace) == want

    # restore with the nomination requests pending, and with one nomination already in
    session = Session.start(ENV, p, 9)
    session.advance()
    assert ev.dump(finish(resume(session.snapshot())).trace) == want
    first = session.pending()[0]
    session.submit(first.agent_id, first.decision_id, NOMS[first.agent_id])
    mid_nomination = resume(session.snapshot())
    assert [r.decision_id for r in mid_nomination.pending()] == [
        r.decision_id for r in session.pending() if r.decision_id != first.decision_id
    ]
    assert ev.dump(finish(mid_nomination).trace) == want

    # restore between stages: voting requests pending, then with one ballot already in
    session = at_voting(p, NOMS, seed=9)
    between = resume(session.snapshot())
    assert [r.decision_id for r in between.pending()] == [r.decision_id for r in session.pending()]
    assert ev.dump(finish(between).trace) == want
    voter = session.pending()[1]
    session.submit(voter.agent_id, voter.decision_id, {"approve": VOTES[voter.agent_id]})
    partial = resume(session.snapshot())
    assert len(partial.pending()) == 2
    assert ev.dump(finish(partial).trace) == want
    assert partial.result == reference.result


def test_session_params_and_snapshot_are_plain_json() -> None:
    p = params(private_values=PRIVATE, timeout_nomination={"kind": "drop"}, reveal_ballots=True)
    session = run_pb(p, NOMS, VOTES)
    text = json.dumps(session.snapshot())
    assert "p9" in text and "private_values" in text  # the params travel with the snapshot
    assert resume(session.snapshot()).input_log == session.input_log
