from typing import Any

import pytest
from pydantic import BaseModel

from gao_idl.composition.connector import Connector, LedgerReadOnly, ReadOnlyLedger
from gao_idl.composition.process import Ctx
from gao_idl.core.errors import ContractError
from gao_idl.core.events import StoreAccess, TraceEvent
from gao_idl.core.ids import AgentId
from gao_idl.core.ledger import Ledger
from gao_idl.core.schemas import Config, UtilityIncrement
from gao_idl.core.store import Store, StoreView
from gao_idl.core.trace import LogicalClock


class Out(BaseModel):
    n: int


class Sink:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)


def make_ctx(initial: dict[str, Any] | None = None) -> tuple[Ctx, Ledger, Sink]:
    clock, ledger, sink = LogicalClock(), Ledger(), Sink()
    ctx = Ctx(
        root_seed=0,
        path=("root",),
        ledger=ledger,
        store=Store(clock, initial),
        trace=sink,
        clock=clock,
    )
    return ctx, ledger, sink


def test_apply_runs_fn_and_validates_output() -> None:
    ctx, ledger, _ = make_ctx()
    ledger.add_increment(UtilityIncrement(agent_id=AgentId("a"), amount=2.5, path=["x"], t=1))

    def fn(rec: dict[str, Any], led: LedgerReadOnly, store: StoreView) -> Config:
        return Config(state={"n": rec["n"] + int(led.utility(AgentId("a")))}, roles={})

    cfg = Connector(fn, Out).apply({"n": 1}, ctx)
    assert cfg.state == {"n": 3}


def test_apply_rejects_output_that_violates_schema() -> None:
    ctx, _, _ = make_ctx()
    bad = Connector(lambda rec, led, store: Config(state={"n": "x"}, roles={}), Out)
    with pytest.raises(ContractError, match="Out"):
        bad.apply({}, ctx)
    wrong_type = Connector(lambda rec, led, store: {"n": 1}, Out)  # type: ignore[arg-type,return-value]
    with pytest.raises(ContractError, match="not Config"):
        wrong_type.apply({}, ctx)


def test_apply_copies_the_record() -> None:
    ctx, _, _ = make_ctx()
    record: dict[str, Any] = {"n": 1, "xs": [1]}

    def fn(rec: dict[str, Any], led: LedgerReadOnly, store: StoreView) -> Config:
        rec["xs"].append(2)
        return Config(state={"n": 1}, roles={})

    Connector(fn, Out).apply(record, ctx)
    assert record["xs"] == [1]


def test_store_scope_is_enforced_and_traced() -> None:
    ctx, _, sink = make_ctx({"k": 7, "secret": 1})

    def fn(rec: dict[str, Any], led: LedgerReadOnly, store: StoreView) -> Config:
        store.set("out", store.get("k"))
        return Config(state={"n": 0}, roles={})

    conn = Connector(fn, Out, store_reads=frozenset({"k"}), store_writes=frozenset({"out"}))
    conn.apply({}, ctx)
    assert [e.key for e in sink.events if isinstance(e, StoreAccess)] == ["k", "out"]
    snoop = Connector(
        lambda rec, led, store: Config(state={"n": store.get("secret")}, roles={}), Out
    )
    with pytest.raises(KeyError):
        snoop.apply({}, ctx)


def test_connector_cannot_mutate_the_ledger() -> None:
    ledger = Ledger()
    ro = ReadOnlyLedger(ledger)
    assert not hasattr(ro, "add_increment") and not hasattr(ro, "record_information")
    assert ro.utility(AgentId("nobody")) == 0.0
    assert ro.increments(AgentId("nobody")) == [] and ro.information(AgentId("nobody")) == []
