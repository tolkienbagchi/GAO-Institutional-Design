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
