from dataclasses import replace
from typing import Any

import pytest
from numpy.random import Generator
from pydantic import BaseModel

from gao_idl.composition.primitive import Primitive, PrimitiveFrame
from gao_idl.composition.process import Ctx, Exited, FailedResult, Pending
from gao_idl.core import events as ev
from gao_idl.core.errors import (
    ContractError,
    Infeasible,
    SchemaError,
    UnknownDecision,
    WrongAgent,
)
from gao_idl.core.ids import AgentId, DecisionId, Port, ReportId, RoleId
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.ledger import Ledger
from gao_idl.core.schemas import Config, Exit, ParticipantReport
from gao_idl.core.store import Store, StoreView
from gao_idl.core.timeout import DefaultAction, DropRole, Fail
from gao_idl.core.trace import LogicalClock

A, B = AgentId("alice"), AgentId("bob")
RA, RB = RoleId("a"), RoleId("b")
DA, DB = DecisionId("sum#a"), DecisionId("sum#b")


class State(BaseModel):
    pass


class Num(BaseModel):
    n: int


class Obs(BaseModel):
    r: float


class Total(BaseModel):
    total: int
    roles: list[str]


class Note(BaseModel):
    total: int


class ListSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)

    @property
    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]


def make_layer(**kw: Any) -> SimpleLayer:
    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        return Obs(r=float(rng.random()))

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        nums = {r: a.n for r, a in actions.items()}  # type: ignore[attr-defined]
        return Total(total=sum(nums.values()), roles=sorted(nums))

    def utilities(cfg: Config, o: BaseModel) -> dict[AgentId, float]:
        return {B: 2.0, A: 1.0}  # deliberately unsorted

    def report(cfg: Config, o: BaseModel, who: AgentId, rng: Generator) -> BaseModel | None:
        return Note(total=o.total)  # type: ignore[attr-defined]

    def export(cfg: Config, o: BaseModel) -> Exit:
        return Exit(port=Port("done"), record=o.model_dump(mode="json"))

    fields: dict[str, Any] = {
        "name": "sum",
        "entry": Contract(state_schema=State),
        "exits": {Port("done"): Total},
        "observe": observe,
        "action_schema": lambda cfg, role: Num,
        "outcome": outcome,
        "utilities": utilities,
        "audience": lambda cfg, o: {B, A, AgentId("observer")},
        "report": report,
        "export": export,
    }
    fields.update(kw)
    return SimpleLayer(**fields)


def make_ctx(seed: int = 1, ledger: Ledger | None = None) -> tuple[Ctx, ListSink]:
    sink = ListSink()
    clock = LogicalClock()
    ctx = Ctx(
        root_seed=seed,
        path=("sum",),
        ledger=ledger or Ledger(),
        store=Store(clock),
        trace=sink,
        clock=clock,
    )
    return ctx, sink


CFG = Config(state={}, roles={RA: A, RB: B})


def started(layer: SimpleLayer, seed: int = 1) -> tuple[Primitive, Any, Ctx, ListSink]:
    prim = Primitive(layer)
    ctx, sink = make_ctx(seed)
    frame, result = prim.step(prim.start(CFG, ctx), [], ctx)
    assert isinstance(result, Pending)
    return prim, frame, ctx, sink


def sub(did: DecisionId, agent: AgentId, n: int) -> ev.Submitted:
    return ev.Submitted(decision_id=did, agent_id=agent, action={"n": n})


def test_start_is_init_and_silent() -> None:
    prim = Primitive(make_layer())
    ctx, sink = make_ctx()
    frame = prim.start(CFG, ctx)
    assert frame.phase == "init"
    assert sink.events == []


def test_process_properties_delegate_to_layer() -> None:
    layer = make_layer()
    prim = Primitive(layer)
    assert (prim.name, prim.entry, prim.exits) == (layer.name, layer.entry, layer.exits)


def test_init_issues_all_requests_in_role_order() -> None:
    prim, frame, ctx, sink = started(make_layer())
    assert sink.kinds == ["layer_entered", "decision_requested", "decision_requested"]
    assert [r.decision_id for r in frame.requests.values()] == [DA, DB]
    assert [r.agent_id for r in frame.requests.values()] == [A, B]
    assert frame.requests[DA].action_schema["properties"]["n"]["type"] == "integer"
    assert [i.decision_id for i in ctx.ledger.information(A)] == [DA]  # type: ignore[union-attr]
    assert frame.actions == {}


def test_observation_rng_is_deterministic_per_seed_and_role() -> None:
    _, f1, _, _ = started(make_layer(), seed=5)
    _, f2, _, _ = started(make_layer(), seed=5)
    _, f3, _, _ = started(make_layer(), seed=6)
    assert f1.requests[DA].observation == f2.requests[DA].observation
    assert f1.requests[DA].observation != f3.requests[DA].observation
    assert f1.requests[DA].observation != f1.requests[DB].observation  # per-role streams


def test_validate_errors() -> None:
    layer = make_layer(feasible=lambda cfg, role, action: action.n >= 0)  # type: ignore[attr-defined]
    prim, frame, _, _ = started(layer)
    with pytest.raises(UnknownDecision):
        prim.validate(frame, DecisionId("nope"), A, {"n": 1})
    with pytest.raises(WrongAgent):
        prim.validate(frame, DA, B, {"n": 1})
    with pytest.raises(SchemaError):
        prim.validate(frame, DA, A, {"n": "x"})
    with pytest.raises(Infeasible):
        prim.validate(frame, DA, A, {"n": -1})
    assert prim.validate(frame, DA, A, {"n": 4}) == Num(n=4)


def test_partial_input_stays_pending_and_defers_trace() -> None:
    prim, frame, ctx, sink = started(make_layer())
    n_before = len(sink.events)
    frame, result = prim.step(frame, [sub(DA, A, 1)], ctx)
    assert isinstance(result, Pending)
    assert [r.decision_id for r in result.requests] == [DB]
    assert len(sink.events) == n_before  # nothing about a's action is disclosed yet
    assert frame.actions == {RA: {"n": 1}}


def test_settle_event_order() -> None:
    prim, frame, ctx, sink = started(make_layer())
    frame, result = prim.step(frame, [sub(DB, B, 5), sub(DA, A, 1)], ctx)
    assert isinstance(result, Exited)
    assert result.exit == Exit(port=Port("done"), record={"total": 6, "roles": ["a", "b"]})
    assert sink.kinds == [
        "layer_entered",
        "decision_requested",
        "decision_requested",
        "action_accepted",
        "action_accepted",
        "layer_completed",
        "utility_increment",
        "utility_increment",
        "report_delivered",
        "report_delivered",
        "report_delivered",
        "exited",
    ]
    accepted = [e for e in sink.events if e.kind == "action_accepted"]
    assert [e.decision_id for e in accepted] == [DA, DB]  # role order, not arrival order
    ts = [e.t for e in sink.events]
    assert ts == sorted(ts) and len(set(ts)) == len(ts)


def test_settle_utilities_and_reports() -> None:
    prim, frame, ctx, sink = started(make_layer())
    prim.step(frame, [sub(DA, A, 1), sub(DB, B, 5)], ctx)
    assert ctx.ledger.utility(A) == 1.0 and ctx.ledger.utility(B) == 2.0
    incs = [e.increment.agent_id for e in sink.events if e.kind == "utility_increment"]
    assert incs == [A, B]  # sorted by agent
    recipients = [e.report.agent_id for e in sink.events if e.kind == "report_delivered"]
    assert recipients == [A, B, AgentId("observer")]
    assert ctx.ledger.information(AgentId("observer"))[0].payload == {"total": 6}  # type: ignore[union-attr]


def test_report_ids_count_per_recipient_and_path() -> None:
    ledger = Ledger()
    ledger.record_information(
        A,
        ParticipantReport(
            report_id=ReportId("sum@alice:0"), path=["sum"], layer="sum", agent_id=A, payload={}
        ),
    )
    prim = Primitive(make_layer())
    ctx, _ = make_ctx(ledger=ledger)
    frame, _ = prim.step(prim.start(CFG, ctx), [], ctx)
    prim.step(frame, [sub(DA, A, 1), sub(DB, B, 1)], ctx)
    reports = [x for x in ledger.information(A) if isinstance(x, ParticipantReport)]
    assert [r.report_id for r in reports] == ["sum@alice:0", "sum@alice:1"]
    obs_reports = ledger.information(AgentId("observer"))
    assert obs_reports[0].report_id == "sum@observer:0"  # type: ignore[union-attr]


def test_done_is_idempotent() -> None:
    prim, frame, ctx, sink = started(make_layer())
    done, first = prim.step(frame, [sub(DA, A, 1), sub(DB, B, 1)], ctx)
    n_events = len(sink.events)
    again, second = prim.step(done, [], ctx)
    assert second == first and again == done
    assert len(sink.events) == n_events


def test_stale_input_rejected() -> None:
    prim, frame, ctx, _ = started(make_layer())
    with pytest.raises(UnknownDecision):
        prim.step(frame, [sub(DA, A, 1), sub(DA, A, 2)], ctx)


def test_timeout_fail() -> None:
    prim, frame, ctx, sink = started(make_layer())
    done, result = prim.step(frame, [ev.TimedOut(decision_id=DA)], ctx)
    assert isinstance(result, FailedResult)
    assert sink.kinds[-1] == "failed"
    n = len(sink.events)
    _, again = prim.step(done, [], ctx)
    assert again == result and len(sink.events) == n


def test_timeout_default() -> None:
    layer = make_layer(timeout=DefaultAction(), default_action=lambda cfg, role: Num(n=100))
    prim, frame, ctx, sink = started(layer)
    frame, result = prim.step(frame, [ev.TimedOut(decision_id=DA), sub(DB, B, 1)], ctx)
    assert isinstance(result, Exited)
    assert result.exit.record["total"] == 101
    assert "action_defaulted" in sink.kinds and sink.kinds.count("action_accepted") == 1
    assert frame.defaulted == [RA]


def test_timeout_drop() -> None:
    prim, frame, ctx, sink = started(make_layer(timeout=DropRole()))
    _, result = prim.step(frame, [ev.TimedOut(decision_id=DB), sub(DA, A, 3)], ctx)
    assert isinstance(result, Exited)
    assert result.exit.record == {"total": 3, "roles": ["a"]}
    assert "role_dropped" in sink.kinds


def test_all_roles_dropped_still_settles() -> None:
    prim, frame, ctx, sink = started(make_layer(timeout=DropRole()))
    _, result = prim.step(frame, [ev.TimedOut(decision_id=DA), ev.TimedOut(decision_id=DB)], ctx)
    assert isinstance(result, Exited) and result.exit.record["total"] == 0
    assert sink.kinds.count("role_dropped") == 2


def test_bad_exit_record_is_contract_error() -> None:
    layer = make_layer(export=lambda cfg, o: Exit(port=Port("done"), record={"wrong": 1}))
    prim, frame, ctx, _ = started(layer)
    with pytest.raises(ContractError):
        prim.step(frame, [sub(DA, A, 1), sub(DB, B, 1)], ctx)


def test_undeclared_exit_port_is_contract_error() -> None:
    layer = make_layer(export=lambda cfg, o: Exit(port=Port("elsewhere"), record={}))
    prim, frame, ctx, _ = started(layer)
    with pytest.raises(ContractError):
        prim.step(frame, [sub(DA, A, 1), sub(DB, B, 1)], ctx)


def test_bad_entry_state_is_contract_error() -> None:
    class Needs(BaseModel):
        k: int

    prim = Primitive(make_layer(entry=Contract(state_schema=Needs)))
    ctx, _ = make_ctx()
    with pytest.raises(ContractError):
        prim.step(prim.start(CFG, ctx), [], ctx)


def test_store_access_traced_inside_outcome() -> None:
    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        store.set("k", 7)
        return Total(total=store.get("k"), roles=[])

    layer = make_layer(
        entry=Contract(
            state_schema=State, store_reads=frozenset({"k"}), store_writes=frozenset({"k"})
        ),
        outcome=outcome,
    )
    prim, frame, ctx, sink = started(layer)
    prim.step(frame, [sub(DA, A, 1), sub(DB, B, 1)], ctx)
    i = sink.kinds.index("store_access")
    assert sink.kinds[i : i + 3] == ["store_access", "store_access", "layer_completed"]


def test_frames_round_trip_through_json() -> None:
    prim, frame, ctx, _ = started(make_layer())
    frame, _ = prim.step(frame, [sub(DA, A, 1)], ctx)
    again = PrimitiveFrame.model_validate(frame.model_dump(mode="json"))
    assert again == frame
    done, _ = prim.step(frame, [sub(DB, B, 1)], ctx)
    assert PrimitiveFrame.model_validate(done.model_dump(mode="json")) == done


def test_automatic_layer_settles_immediately() -> None:
    layer = make_layer()
    prim = Primitive(
        replace(layer, audience=lambda cfg, o: {AgentId("observer")}, utilities=lambda cfg, o: {})
    )
    ctx, sink = make_ctx()
    cfg = Config(state={}, roles={})
    _, result = prim.step(prim.start(cfg, ctx), [], ctx)
    assert isinstance(result, Exited)
    assert sink.kinds == ["layer_entered", "layer_completed", "report_delivered", "exited"]


def test_fail_is_default_policy() -> None:
    assert isinstance(make_layer().timeout, Fail)
