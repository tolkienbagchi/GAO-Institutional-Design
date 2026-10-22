"""The documentation tree is complete and consistent with the code.

* every non-internal module has an API-reference entry, and every public name is listed;
* the navigation points at files that exist and every page is in it;
* ``mkdocs build --strict`` succeeds (skipped when mkdocs is not installed).
"""

import importlib
import importlib.util
import pkgutil
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import gao_idl
import gao_idl.llm

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _documented_modules() -> list[str]:
    """Every module whose docstring does not declare it internal."""
    names = []
    for info in pkgutil.walk_packages(gao_idl.__path__, "gao_idl."):
        module = importlib.import_module(info.name)
        if not re.search(r"Stability: internal", module.__doc__ or ""):
            names.append(info.name)
    return names


def _api_directives() -> set[str]:
    found: set[str] = set()
    for page in (DOCS / "api").glob("*.md"):
        found.update(re.findall(r"^::: (\S+)$", page.read_text(), flags=re.MULTILINE))
    return found


def test_every_documented_module_has_an_api_entry() -> None:
    directives = _api_directives()
    # a package that only re-exports its children is covered by them
    reexport_only = {"gao_idl.agents", "gao_idl.environments", "gao_idl.learning", "gao_idl.llm"}
    reexport_only |= {"gao_idl.environments.toy", "gao_idl.runtime", "gao_idl.core"}
    missing = [m for m in _documented_modules() if m not in directives and m not in reexport_only]
    assert missing == []


def test_every_api_entry_names_a_documented_module() -> None:
    documented = set(_documented_modules())
    assert sorted(d for d in _api_directives() if d not in documented) == []


def test_every_public_name_is_listed_with_its_defining_module() -> None:
    index = (DOCS / "api" / "index.md").read_text()
    directives = _api_directives()
    for package in (gao_idl, gao_idl.llm):
        for name in package.__all__:
            assert f"`from {package.__name__} import {name}`" in index, name
            defined_in = getattr(getattr(package, name), "__module__", None)
            assert defined_in in directives, (name, defined_in)


def _nav_files(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [f for item in node for f in _nav_files(item)]
    if isinstance(node, dict):
        return [f for value in node.values() for f in _nav_files(value)]
    return []


def test_navigation_matches_the_files() -> None:
    config = yaml.safe_load((ROOT / "mkdocs.yml").read_text())
    in_nav = set(_nav_files(config["nav"]))
    on_disk = {p.relative_to(DOCS).as_posix() for p in DOCS.rglob("*.md")}
    assert in_nav - on_disk == set()
    assert on_disk - in_nav == set()


def test_site_directory_is_ignored() -> None:
    assert "site/" in (ROOT / ".gitignore").read_text().split()


def test_mkdocs_build_is_strict(tmp_path: Path) -> None:
    pytest.importorskip("mkdocs")
    config = yaml.safe_load((ROOT / "mkdocs.yml").read_text())
    if importlib.util.find_spec("mkdocstrings") is None:  # the API pages then stay plain text
        config["plugins"] = ["search"]
    config_path = ROOT / "_mkdocs_test.yml"
    config_path.write_text(yaml.safe_dump(config))
    try:
        done = subprocess.run(
            [sys.executable, "-m", "mkdocs", "build", "--strict", "-f", str(config_path)]
            + ["-d", str(tmp_path / "site")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=25,
        )
    finally:
        config_path.unlink()
    assert done.returncode == 0, (done.stdout + done.stderr)[-2000:]
