"""``gao_idl.review`` calls no model and draws no randomness, time or object identity."""

import os
import re
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "gao_idl" / "review"


def test_importing_review_does_not_import_the_llm_package() -> None:
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    code = (
        "import sys, gao_idl.review; "
        "assert 'gao_idl.llm' not in sys.modules and 'httpx' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=env, timeout=20)


def test_no_randomness_clock_identity_or_model_in_the_sources() -> None:
    forbidden = re.compile(
        r"\bimport (random|time|uuid)\b|\bfrom (random|time|uuid)\b|\bid\(|gao_idl\.llm"
    )
    for path in sorted(SRC.glob("*.py")):
        assert not forbidden.search(path.read_text()), path.name
    script = (SRC / "page.js").read_text()
    for name in (
        "Math.random",
        "Date.now",
        "new Date",
        "innerHTML",
        "outerHTML",
        "eval(",
        "document.write",
    ):
        assert name not in script, name
