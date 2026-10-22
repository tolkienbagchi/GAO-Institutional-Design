import tomllib
from pathlib import Path

import gao_idl

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_version() -> None:
    assert gao_idl.__version__ == "0.1.0"


def test_package_version_matches_pyproject() -> None:
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    assert project["version"] == gao_idl.__version__
    assert project["requires-python"] == ">=3.11"
    extras = project["optional-dependencies"]
    assert {"llm", "dev", "docs"} <= set(extras)
    assert extras["llm"] == ["httpx"]
    assert set(project["dependencies"]) == {"pydantic>=2.7", "numpy>=1.26"}  # core stays minimal


def test_license_and_author_metadata() -> None:
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    assert project["license"] == "Apache-2.0"  # SPDX expression, not a classifier
    assert project["license-files"] == ["LICENSE"]
    assert {"name": "Tolkien Bagchi", "email": "tb549@cornell.edu"} in project["authors"]
    assert "mkdocs>=1.6,<2" in project["optional-dependencies"]["docs"]
    assert (PYPROJECT.parent / "src" / "gao_idl" / "py.typed").is_file()
    assert (PYPROJECT.parent / "LICENSE").read_text().count("Copyright 2026 Tolkien Bagchi") == 1
