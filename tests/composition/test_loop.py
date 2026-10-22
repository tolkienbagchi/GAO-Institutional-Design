from typing import Any

import pytest
from pydantic import BaseModel

from gao_idl.composition.connector import Connector
from gao_idl.composition.loop import TRUNCATED, Loop, TruncatedExit
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Continue, Exited, StepResult
from gao_idl.core import events as ev
from gao_idl.core.errors import ContractError, UnknownDecision
from gao_idl.core.ids import AgentId, DecisionId, Port
from gao_idl.core.paths import LoopIter
from gao_idl.core.schemas import Config, Exit
from gao_idl.runtime.session import Session
from gao_idl.testing import assert_replay_equivalent
from tests.composition import envs
from tests.composition.layers import N, Rec, counter_layer, n_feedback, step_layer

ALICE = AgentId("alice")
AGAIN, STOP, FINISHED = Port("again"), Port("stop"), Port("finished")


def run(env: str, params: dict[str, Any]) -> Session:
    s = Session.start(env, params, 1)
    s.advance()
    return s


def test_loop_feedback_and_until() -> None:
    s = run(envs.LOOP_COUNTER, {"max_iter": 10, "limit": 3})
    assert s.result == Exit(port=FINISHED, record={"n": 3})  # mapped by `until`
    entered = [e.path for e in s.trace if e.kind == "layer_entered"]
    assert entered == [["loop", LoopIter(i)] for i in range(3)]  # iterations 0, 1, 2
    assert not any(e.kind == "truncated" for e in s.trace)
    loop, _ = envs.loop_counter()
    assert TRUNCATED in loop.exits and FINISHED in loop.exits  # reserved port is always declared
    assert_replay_equivalent(s)


def test_loop_stops_without_truncation_when_the_last_allowed_iteration_exits_normally() -> None:
    s = run(envs.LOOP_COUNTER, {"max_iter": 3, "limit": 3})  # iteration 2 is the last allowed
    assert s.result == Exit(port=FINISHED, record={"n": 3})
    assert not any(e.kind == "truncated" for e in s.trace)


def test_loop_truncation_port() -> None:
    s = run(envs.LOOP_COUNTER, {"max_iter": 2, "limit": 100})
    assert s.result == Exit(port=TRUNCATED, record={"iterations": 2, "last_exit": {"n": 2}})
    assert not s.failed
    cut = [e for e in s.trace if e.kind == "truncated"]
    assert len(cut) == 1 and cut[0].path == ["loop"]
    assert [e.path for e in s.trace if e.kind == "layer_entered"] == [
        ["loop", LoopIter(0)],
        ["loop", LoopIter(1)],
    ]  # no iteration 2 was started
    loop, _ = envs.loop_counter()
    assert "truncated" not in loop.until.values()
    assert loop.exits[TRUNCATED] is TruncatedExit


def test_loop_fail_on_limit() -> None:
    s = run(envs.LOOP_COUNTER, {"max_iter": 2, "limit": 100, "on_limit": "fail"})
    assert s.finished and s.failed and s.result is None
    failed = [e for e in s.trace if e.kind == "failed"]
    assert len(failed) == 1 and "max_iter=2" in failed[0].reason  # type: ignore[union-attr]
    assert not any(e.kind == "truncated" for e in s.trace)
    s.check_determinism()


def test_limit_does_not_run_the_feedback_connector() -> None:
    calls: list[int] = []

    def fn(rec: dict[str, Any], ledger: Any, store: Any) -> Config:
        calls.append(rec["n"])
        return Config(state={"n": rec["n"]}, roles={})

    loop = Loop(
        Primitive(counter_layer(limit=100)),
        {AGAIN: Connector(fn, N)},
        {STOP: FINISHED},
        max_iter=2,
        on_limit="truncate",
    )
    s = Session.start(envs.LOOP_COUNTER, {}, 1)  # only used for a ready-made Ctx
    ctx = s._ctx
    frame = loop.start(Config(state={"n": 0}, roles={}), ctx)
    result: StepResult = Continue()
    for _ in range(50):
        frame, result = loop.step(frame, [], ctx)
        if isinstance(result, Exited):
            break
    assert isinstance(result, Exited) and result.exit.port == TRUNCATED
    assert calls == [1]  # fed back once (into iteration 1), never for the would-be iteration 2


def test_loop_with_decisions_uses_iteration_paths_in_ids() -> None:
    s = run(envs.LOOP_DECISIONS, {"threshold": 10})
    seen: list[str] = []
    for _ in range(3):
        (req,) = s.pending()
        seen.append(req.decision_id)
        s.submit(ALICE, req.decision_id, {"x": 4})
        s.advance()
    assert seen == ["loop/loop[0]#player", "loop/loop[1]#player", "loop/loop[2]#player"]
    assert s.result == Exit(port=FINISHED, record={"payload": 12})
    assert s.ledger.utility(ALICE) == 3.0  # one increment per iteration
    assert_replay_equivalent(s)


def test_loop_snapshot_restore_mid_iteration() -> None:
    s = run(envs.LOOP_DECISIONS, {"threshold": 10})
    (req,) = s.pending()
    s.submit(ALICE, req.decision_id, {"x": 4})
    s.advance()
    restored = Session.restore(s.snapshot())
    assert [r.decision_id for r in restored.pending()] == ["loop/loop[1]#player"]
    assert ev.dump(restored.trace) == ev.dump(s.trace)
    # a decision from an iteration that is over is unknown
    with pytest.raises(UnknownDecision):
        restored.submit(ALICE, DecisionId("loop/loop[0]#player"), {"x": 1})


def test_loop_wiring_errors() -> None:
    body = Primitive(counter_layer())

    def build(feedback: dict[Port, Connector], until: dict[Port, Port], **kw: Any) -> Loop:
        args: dict[str, Any] = {"max_iter": 3, "on_limit": "truncate", **kw}
        return Loop(body, feedback, until, **args)

    ok_fb, ok_until = {AGAIN: n_feedback()}, {STOP: FINISHED}
    build(ok_fb, ok_until)  # sanity
    with pytest.raises(ContractError, match="both feedback and until"):
        build({AGAIN: n_feedback(), STOP: n_feedback()}, {STOP: FINISHED})
    with pytest.raises(ContractError, match="neither feedback nor until"):
        build(ok_fb, {})
    with pytest.raises(ContractError, match="not exits"):
        build({**ok_fb, Port("zzz"): n_feedback()}, ok_until)
    with pytest.raises(ContractError, match="reserved"):
        build(ok_fb, {STOP: TRUNCATED})
    with pytest.raises(ContractError, match="max_iter"):
        build(ok_fb, ok_until, max_iter=0)
    with pytest.raises(ContractError, match="max_iter"):
        build(ok_fb, ok_until, max_iter=True)
    with pytest.raises(ContractError, match="on_limit"):
        build(ok_fb, ok_until, on_limit="wrap")
    wrong = Connector(lambda rec, led, store: Config(state={}, roles={}), Rec)
    with pytest.raises(ContractError, match="outputs Rec but 'count' expects N"):
        build({AGAIN: wrong}, ok_until)


def test_loop_rejects_conflicting_until_targets() -> None:
    class Other(BaseModel):
        k: int = 0

    from gao_idl.core.layer import Contract, SimpleLayer

    layer = SimpleLayer(
        name="two",
        entry=Contract(state_schema=N),
        exits={Port("x"): N, Port("y"): Other, Port("again"): N},
        export=lambda cfg, out: Exit(port=Port("x"), record={"n": 0}),
    )
    with pytest.raises(ContractError, match="conflicting schemas"):
        Loop(
            Primitive(layer),
            {AGAIN: n_feedback()},
            {Port("x"): FINISHED, Port("y"): FINISHED},
            max_iter=2,
            on_limit="fail",
        )


def test_loop_requires_every_body_port_to_be_routed() -> None:
    # step_layer exits only through "done"; a loop must route that port somewhere
    with pytest.raises(ContractError, match="neither"):
        Loop(Primitive(step_layer()), {}, {}, max_iter=2, on_limit="fail")
