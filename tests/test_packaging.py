"""Install hygiene: what ``import gao_idl`` pulls in, and the self-test a fresh install runs."""

import os
import subprocess
import sys

import pytest

import gao_idl

FORBIDDEN = ("httpx", "hypothesis", "pytest")


def _isolated(code: str) -> str:
    """Run ``code`` in a fresh interpreter that sees the same import path as this test."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    done = subprocess.run(
        [sys.executable, "-c", code], check=True, env=env, capture_output=True, text=True
    )
    return done.stdout


def test_import_gao_idl_does_not_import_optional_or_test_dependencies() -> None:
    code = (
        "import sys, gao_idl\n"
        f"bad = [m for m in {FORBIDDEN!r} if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    _isolated(code)


def test_importing_the_llm_package_does_not_need_httpx() -> None:
    code = (
        "import sys, gao_idl.llm\n"
        f"bad = [m for m in {FORBIDDEN!r} if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    _isolated(code)


def test_selftest_runs_in_a_fresh_interpreter() -> None:
    out = _isolated("import gao_idl; gao_idl.selftest()")
    lines = out.splitlines()
    assert lines[0] == f"gao_idl {gao_idl.__version__} selftest"
    assert lines[1].startswith("auction  exit=done")
    assert "'alice': 2.0, 'bob': 7.0" in lines[2] and lines[2].startswith("pd       exit=done")
    assert lines[-1] == "ok"


def test_selftest_prints_the_same_totals_every_time(capsys: pytest.CaptureFixture[str]) -> None:
    gao_idl.selftest()
    first = capsys.readouterr().out
    gao_idl.selftest()
    assert capsys.readouterr().out == first
