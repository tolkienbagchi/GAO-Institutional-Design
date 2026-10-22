import json
from typing import Any

import pytest

from gao_idl.core import events as ev
from gao_idl.core.ids import AgentId, DecisionId, ReportId, RoleId
from gao_idl.core.ledger import Ledger
from gao_idl.core.paths import LoopIter, Path
from gao_idl.core.schemas import DecisionRequest, ParticipantReport, UtilityIncrement
from gao_idl.core.store import Store, StoreReadDenied, StoreWriteDenied
from gao_idl.core.trace import LogicalClock

A, B = AgentId("alice"), AgentId("bob")
PATH: Path = ("root", LoopIter(1), "layer")


class ListSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


def inc(agent: AgentId, amount: float, t: int = 1) -> UtilityIncrement:
    return UtilityIncrement(agent_id=agent, amount=amount, path=list(PATH), t=t)


def request(n: int) -> DecisionRequest:
    return DecisionRequest(
        decision_id=DecisionId(f"d{n}"),
        path=list(PATH),
        layer="l",
        role=RoleId("r"),
        agent_id=A,
        observation={"n": n},
        action_schema={},
    )


def report(n: int) -> ParticipantReport:
    return ParticipantReport(
        report_id=ReportId(f"r{n}"), path=list(PATH), layer="l", agent_id=A, payload={"n": n}
    )


def test_increments_sum_per_agent() -> None:
    led = Ledger()
    led.add_increment(inc(A, 1.5))
    led.add_increment(inc(A, 2.0, t=2))
    led.add_increment(inc(B, -1.0))
    assert led.utility(A) == 3.5
    assert led.utility(B) == -1.0
    assert led.utility(AgentId("nobody")) == 0.0
    assert [i.amount for i in led.increments(A)] == [1.5, 2.0]


def test_information_order_preserved() -> None:
    led = Ledger()
    items = [request(0), report(1), request(2), report(3)]
    for it in items:
        led.record_information(A, it)
    assert led.information(A) == items
    assert led.information(B) == []


def test_ledger_returns_copies() -> None:
    led = Ledger()
    led.record_information(A, request(0))
    led.information(A).clear()
    assert len(led.information(A)) == 1


def test_ledger_snapshot_round_trip() -> None:
    led = Ledger()
    led.add_increment(inc(A, 1.0))
    led.record_information(A, request(0))
    led.record_information(A, report(1))
    led.record_information(B, report(2))
    snap = json.loads(json.dumps(led.snapshot()))
    again = Ledger.from_snapshot(snap)
    assert again.snapshot() == led.snapshot()
    assert again.information(A) == led.information(A)
    assert again.utility(A) == 1.0


def test_store_scope_enforcement() -> None:
    sink = ListSink()
    store = Store(LogicalClock(), {"a": 1, "b": 2})
    view = store.view(frozenset({"a"}), frozenset({"b"}), sink, PATH)
    assert view.get("a") == 1
    with pytest.raises(StoreReadDenied):
        view.get("b")  # not in reads
    with pytest.raises(StoreWriteDenied):
        view.set("a", 5)  # not in writes
    view.set("b", 9)
    assert store.view(frozenset({"b"}), frozenset(), sink, PATH).get("b") == 9


def test_store_emits_access_events() -> None:
    sink = ListSink()
    clock = LogicalClock()
    store = Store(clock, {"a": 1})
    view = store.view(frozenset({"a"}), frozenset({"a"}), sink, PATH)
    view.get("a")
    view.set("a", 2)
    kinds = [(e.kind, e.mode, e.key, e.t) for e in sink.events]
    assert kinds == [("store_access", "read", "a", 1), ("store_access", "write", "a", 2)]
    assert all(isinstance(e, ev.StoreAccess) and e.path == list(PATH) for e in sink.events)


def test_store_copies_on_set_and_get() -> None:
    sink = ListSink()
    store = Store(LogicalClock())
    view = store.view(frozenset({"k"}), frozenset({"k"}), sink, PATH)
    value = {"xs": [1, 2]}
    view.set("k", value)
    value["xs"].append(3)  # mutating the original must not leak in
    got = view.get("k")
    assert got == {"xs": [1, 2]}
    got["xs"].append(99)  # mutating a read must not leak in either
    assert view.get("k") == {"xs": [1, 2]}


def test_store_rejects_non_json_values() -> None:
    store = Store(LogicalClock())
    view = store.view(frozenset(), frozenset({"k"}), ListSink(), PATH)
    with pytest.raises(ValueError, match="JSON"):
        view.set("k", object())
    with pytest.raises(ValueError, match="JSON"):
        view.set("k", float("nan"))


def test_clock() -> None:
    clock = LogicalClock()
    assert clock.now == 0
    assert clock.tick() == 1
    assert clock.tick() == 2
    assert clock.now == 2
