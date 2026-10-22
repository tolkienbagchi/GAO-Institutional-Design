import json
from typing import Any

import pytest
from pydantic import ValidationError

from gao_idl.core import events as ev
from gao_idl.core.ids import AgentId, DecisionId, Port, ReportId, RoleId
from gao_idl.core.paths import Branch, LoopIter
from gao_idl.core.schemas import DecisionRequest, ParticipantReport, UtilityIncrement
from gao_idl.core.timeout import DefaultAction, DropRole, Fail

PATH: list[Any] = ["tournament", LoopIter(3), Branch("pair_1"), "pd"]
A = AgentId("alice")
D = DecisionId("tournament/loop[3]/branch[pair_1]/pd#r")

REQUEST = DecisionRequest(
    decision_id=D,
    path=PATH,
    layer="pd",
    role=RoleId("r"),
    agent_id=A,
    observation={"x": 1},
    action_schema={"type": "object"},
)
REPORT = ParticipantReport(
    report_id=ReportId("p@alice:0"), path=PATH, layer="pd", agent_id=A, payload={"k": "v"}
)

ALL_EVENTS: list[Any] = [
    ev.Started(env_ref="m:f", params={"a": [1, 2]}, seed=3),
    ev.Submitted(decision_id=D, agent_id=A, action={"amount": 1.5}),
    ev.TimedOut(decision_id=D),
    ev.ExternalResponse(decision_id=D, payload={"z": None}),
    ev.Aborted(reason="why"),
    ev.LayerEntered(t=1, path=PATH),
    ev.DecisionRequested(t=2, path=PATH, request=REQUEST),
    ev.ActionAccepted(t=3, path=PATH, decision_id=D, agent_id=A, action={"amount": 1}),
    ev.ActionRejected(t=3, path=PATH, decision_id=D, agent_id=A, reason="SchemaError"),
    ev.ActionDefaulted(t=4, path=PATH, decision_id=D, action={"amount": 0}),
    ev.RoleDropped(t=5, path=PATH, decision_id=D),
    ev.LayerCompleted(t=6, path=PATH, layer="pd", actions={"r": {"a": 1}}, outcome={"o": 2}),
    ev.UtilityIncrementEvent(
        t=7, path=PATH, increment=UtilityIncrement(agent_id=A, amount=2.5, path=PATH, t=7)
    ),
    ev.ReportDelivered(t=8, path=PATH, report=REPORT),
    ev.StoreAccess(t=9, path=PATH, key="k", mode="read"),
    ev.Exited(t=10, path=PATH, port=Port("settle"), record={"r": 1}),
    ev.Truncated(t=11, path=PATH, reason="budget"),
    ev.Failed(t=12, path=PATH, reason="timeout"),
]


@pytest.mark.parametrize("event", ALL_EVENTS, ids=lambda e: e.kind)
def test_event_round_trips(event: Any) -> None:
    dumped = ev.dump([event])
    assert json.loads(json.dumps(dumped)) == dumped  # JSON-able
    (loaded,) = ev.load(json.loads(json.dumps(dumped)))
    assert loaded == event
    assert type(loaded) is type(event)


def test_whole_list_round_trip_preserves_order() -> None:
    assert ev.load(ev.dump(ALL_EVENTS)) == ALL_EVENTS


def test_path_elements_json_encoding() -> None:
    (d,) = ev.dump([ev.LayerEntered(t=1, path=PATH)])
    assert d["path"] == ["tournament", {"loop": 3}, {"branch": "pair_1"}, "pd"]
    (loaded,) = ev.load([d])
    assert isinstance(loaded, ev.LayerEntered)
    assert loaded.path == PATH
    assert isinstance(loaded.path[1], LoopIter) and isinstance(loaded.path[2], Branch)


def test_nested_paths_round_trip() -> None:
    (d,) = ev.dump([ev.DecisionRequested(t=2, path=PATH, request=REQUEST)])
    assert d["request"]["path"] == ["tournament", {"loop": 3}, {"branch": "pair_1"}, "pd"]


@pytest.mark.parametrize(
    "bad", [{"loop": "x"}, {"branch": 1}, {"other": 1}, {"loop": 1, "branch": "a"}, 5]
)
def test_bad_path_elements_rejected(bad: Any) -> None:
    with pytest.raises(ValidationError):
        ev.LayerEntered(t=1, path=[bad])


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        ev.load([{"kind": "nope"}])


def test_events_are_frozen() -> None:
    e = ev.TimedOut(decision_id=D)
    with pytest.raises(ValidationError):
        e.decision_id = DecisionId("other")  # type: ignore[misc]


def test_timeout_policies_round_trip() -> None:
    for p in (Fail(), DefaultAction(), DropRole()):
        assert type(p).model_validate(p.model_dump(mode="json")) == p


def test_strip_paths_removes_paths_ids_and_optionally_t() -> None:
    req = DecisionRequest(
        decision_id=DecisionId("a/loop[1]/b#bidder:0"),
        path=["a", LoopIter(1), "b"],
        layer="b",
        role=RoleId("bidder:0"),
        agent_id=AgentId("x"),
        observation={"path": "user data stays"},
        action_schema={},
    )
    rep = ParticipantReport(
        report_id=ReportId("a/b@x:2"), path=["a", "b"], layer="b", agent_id=AgentId("x"), payload={}
    )
    inc = UtilityIncrement(agent_id=AgentId("x"), amount=1.0, path=["a"], t=9)
    trace = [
        ev.DecisionRequested(t=1, path=["a", LoopIter(1), "b"], request=req),
        ev.ReportDelivered(t=2, path=["a", "b"], report=rep),
        ev.UtilityIncrementEvent(t=3, path=["a"], increment=inc),
    ]
    stripped = ev.strip_paths(ev.dump(trace))
    assert all("path" not in d for d in stripped)
    assert stripped[0]["request"] == {
        "decision_id": "bidder:0",
        "layer": "b",
        "role": "bidder:0",
        "agent_id": "x",
        "observation": {"path": "user data stays"},
        "action_schema": {},
        "deadline": None,
    }
    assert stripped[1]["report"]["report_id"] == "x:2" and "path" not in stripped[1]["report"]
    assert stripped[2]["increment"]["t"] == 9 and stripped[0]["t"] == 1  # t kept by default
    no_t = ev.strip_paths(ev.dump(trace), drop_t=True)
    assert all("t" not in d for d in no_t) and "t" not in no_t[2]["increment"]
