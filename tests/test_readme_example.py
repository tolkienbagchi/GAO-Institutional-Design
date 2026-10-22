import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parent.parent / "README.md"


def test_readme_example_runs(capsys: pytest.CaptureFixture[str]) -> None:
    blocks = re.findall(r"```python\n(.*?)```", README.read_text(), flags=re.DOTALL)
    assert len(blocks) == 1
    assert len(blocks[0].strip().splitlines()) <= 20 + 2  # the brief's "20-line example"
    exec(compile(blocks[0], str(README), "exec"), {"__name__": "__readme__"})
    out = capsys.readouterr().out.splitlines()
    assert out == ["settle", "{'status': 'bid accepted', 'own_bid': 5.0}"]
