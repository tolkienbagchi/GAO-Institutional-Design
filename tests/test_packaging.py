"""Install hygiene: what ``import gao_idl`` pulls in, and the self-test a fresh install runs."""

import importlib.util
import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

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


ROOT = Path(__file__).resolve().parents[1]
PAGE_FILES = ("gao_idl/review/page.html", "gao_idl/review/page.css", "gao_idl/review/page.js")


def _wheel(tmp: Path) -> Path | None:
    """The wheel for this version: ``dist/`` if built there, else one built now without network."""
    with open(ROOT / "pyproject.toml", "rb") as f:
        version = tomllib.load(f)["project"]["version"]
    built = sorted((ROOT / "dist").glob(f"gao_idl-{version}-*.whl"))
    if len(built) == 1:
        return built[0]
    if importlib.util.find_spec("build") and importlib.util.find_spec("hatchling"):
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(tmp)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=120,
        )
        return next(tmp.glob("gao_idl-*.whl"))
    return None


def test_the_wheel_contains_the_review_page_files(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path)
    if wheel is None:
        pytest.skip(
            "no wheel in dist/ and no build+hatchling to make one (clean_install.sh checks)"
        )
    names = set(zipfile.ZipFile(wheel).namelist())
    missing = [n for n in PAGE_FILES if n not in names]
    assert not missing, missing
