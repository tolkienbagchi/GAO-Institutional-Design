import pytest

from gao_idl.runtime.session import MAX_CONSECUTIVE_CONTINUES, Session

CONTINUE_N = "tests.runtime.envs:continue_n"


def test_continue_forever_raises() -> None:
    s = Session.start(CONTINUE_N, {"limit": None}, 1)
    with pytest.raises(RuntimeError, match="did not reach quiescence"):
        s.advance()
    assert not s.finished


def test_guard_boundary() -> None:
    ok = Session.start(CONTINUE_N, {"limit": MAX_CONSECUTIVE_CONTINUES - 1}, 1)
    ok.advance()
    assert ok.finished and not ok.failed
    bad = Session.start(CONTINUE_N, {"limit": MAX_CONSECUTIVE_CONTINUES}, 1)
    with pytest.raises(RuntimeError, match="did not reach quiescence"):
        bad.advance()
