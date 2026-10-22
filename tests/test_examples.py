"""The tutorials are scripts in ``examples/``: each one must run, fast, and stay honest.

* every example runs to completion in under 20 seconds with ``GAO_EXAMPLE_FAST=1``;
* an example imports only public or supporting modules of ``gao_idl`` (never an internal one);
* every example is mirrored into its tutorial page with a ``--8<--`` include.
"""

import ast
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import gao_idl

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("[0-9][0-9]_*.py"))
LIMIT_SECONDS = 20

EXPECTED_OUTPUT = {
    "01_define_a_layer": "exit: done",
    "02_compose": "tournament shape: loop(par(primitive, primitive))",
    "03_mixed_agents": "model calls: 5",
    "04_pause_resume": "identical to an uninterrupted run",
    "05_train_and_evaluate": "trained (greedy)",
    "06_information_tests": "ok   test_the_check_has_teeth",
    "10_congestion": "step 7 model calls: 3",
}


def _env() -> dict[str, str]:
    """The environment of an example: fast mode, and the same ``gao_idl`` as this test session."""
    package_root = str(Path(gao_idl.__file__).resolve().parents[1])
    path = os.pathsep.join(p for p in (package_root, os.environ.get("PYTHONPATH", "")) if p)
    return {**os.environ, "GAO_EXAMPLE_FAST": "1", "PYTHONPATH": path}


def test_the_tutorials_exist() -> None:
    assert [p.stem for p in EXAMPLES] == sorted(EXPECTED_OUTPUT)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_runs_to_completion(path: Path, tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(path)],
        cwd=tmp_path,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=LIMIT_SECONDS,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert EXPECTED_OUTPUT[path.stem] in done.stdout, done.stdout[-1000:]


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return {m for m in modules if m == "gao_idl" or m.startswith("gao_idl.")}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_imports_no_internal_module(path: Path) -> None:
    for name in sorted(_imported_modules(path)):
        assert not any(part.startswith("_") for part in name.split(".")), name
        module = importlib.import_module(name)
        assert not re.search(r"Stability: internal", module.__doc__ or ""), name


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_is_mirrored_in_its_tutorial_page(path: Path) -> None:
    # tutorials 1-6 are pages named like their script; later ones drop the number (the M7 brief
    # names ``docs/tutorials/congestion.md`` for ``examples/10_congestion.py``)
    pages = [
        ROOT / "docs" / "tutorials" / f"{n}.md" for n in (path.stem, path.stem.split("_", 1)[1])
    ]
    page = next(p for p in pages if p.exists()).read_text()
    assert f'--8<-- "examples/{path.name}"' in page
