from typing import Any

from gao_idl.composition.connector import Connector
from gao_idl.composition.loop import Loop
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Continue, FailedResult, StepResult
from gao_idl.core import events as ev
from gao_idl.core.ids import Port
from gao_idl.core.paths import Branch, LoopIter
from gao_idl.core.schemas import Config, Exit
from gao_idl.runtime.session import Session
from tests.composition import envs
from tests.composition.layers import N, counter_layer

FINISHED = Port("finished")


def run(env: str, params: dict[str, Any] | None = None) -> Session:
    s = Session.start(env, params or {}, 1)
    s.advance()
    return s


def accesses(s: Session) -> list[tuple[Any, ...]]:
    return [(e.path, e.key, e.mode) for e in s.trace if isinstance(e, ev.StoreAccess)]


def test_store_persists_across_loop_iterations() -> None:
    s = run(envs.LOOP_STASH, {"limit": 4})
    assert s.result == Exit(port=FINISHED, record={"n": 4, "hist": [0, 1, 2, 3]})
    assert accesses(s) == [
        (["loop", LoopIter(0)], "hist", "write"),
        *[(["loop", LoopIter(i)], "hist", mode) for i in (1, 2, 3) for mode in ("read", "write")],
    ]
    s.check_determinism()


def test_store_scope_violation_raises() -> None:
    """A layer touching an undeclared key fails the session (not an escaped exception)."""
    for mode, error in (("read", "KeyError"), ("write", "PermissionError")):
        s = run(envs.SNOOP, {"mode": mode})
        assert s.finished and s.failed and s.result is None
        (failed,) = [e for e in s.trace if isinstance(e, ev.Failed)]
        assert error in failed.reason and "snoop" in failed.reason and "secret" in failed.reason
        assert failed.path == ["snoop"]
        assert not any(e.kind == "exited" for e in s.trace)  # nothing exited after the violation
        s.check_determinism()


def test_store_scope_violation_in_a_connector_fails_the_session() -> None:
    for mode, error in (("read", "KeyError"), ("write", "PermissionError")):
        s = run(envs.SEQ_STORE_CONNECTOR, {"mode": mode})
        assert s.finished and s.failed
        (failed,) = [e for e in s.trace if isinstance(e, ev.Failed)]
        assert "connector" in failed.reason and error in failed.reason
        assert failed.path == ["seq"]
        assert [e.path for e in s.trace if e.kind == "layer_entered"] == [["seq", "choose"]]


def test_store_scope_violation_in_a_feedback_connector_fails_the_session() -> None:
    def fn(rec: dict[str, Any], ledger: Any, store: Any) -> Config:
        store.get("secret")
        return Config(state={"n": rec["n"]}, roles={})

    loop = Loop(
        Primitive(counter_layer(limit=3)),
        {Port("again"): Connector(fn, N)},
        {Port("stop"): FINISHED},
        max_iter=5,
        on_limit="fail",
    )
    s = Session.start(envs.LOOP_COUNTER, {}, 1)  # only used for a ready-made Ctx
    frame = loop.start(Config(state={"n": 0}, roles={}), s._ctx)
    result: StepResult = Continue()
    for _ in range(10):
        frame, result = loop.step(frame, [], s._ctx)
        if isinstance(result, FailedResult):
            break
    assert isinstance(result, FailedResult) and "feedback connector" in result.reason
    assert any(isinstance(e, ev.Failed) for e in s.trace)


def test_par_cross_branch_read_before_write_fails_in_sorted_order() -> None:
    """Documents the hazard of the shared store: branches run in sorted-name order, so a reader
    named before its writer reads an unset key. That is a KeyError, hence a failed session."""
    s = run(envs.PAR_SHARED_STORE, {"writer": "B", "reader": "A"})
    assert s.failed
    (failed,) = [e for e in s.trace if isinstance(e, ev.Failed)]
    assert failed.path == ["par", Branch("A")] and "KeyError" in failed.reason


def test_par_branches_share_the_store() -> None:
    s = run(envs.PAR_SHARED_STORE, {"writer": "A", "reader": "B"})
    assert s.result == Exit(port=Port("done"), record={"A": {"n": 42}, "B": {"n": 42}})
    assert accesses(s) == [
        (["par", Branch("A")], "shared", "write"),
        (["par", Branch("B")], "shared", "read"),
    ]
    s.check_determinism()
