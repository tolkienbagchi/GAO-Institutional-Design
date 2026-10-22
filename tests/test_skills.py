"""The agent skills: front matter, size, runnable examples, and the fixture checklist."""

import re
from pathlib import Path

import pytest

from gao_idl import testing

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))
MAX_LINES = 400
EXPECTED = {"design-environment", "write-llm-codec", "explain-environment"}


def _runnable_blocks(text: str) -> list[str]:
    blocks = re.findall(r"```python\n(# runnable\n.*?)```", text, flags=re.S)
    return blocks


def test_the_three_skills_exist() -> None:
    assert {p.parent.name for p in SKILLS} == EXPECTED


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_front_matter_and_size(path: Path) -> None:
    text = path.read_text()
    assert len(text.splitlines()) <= MAX_LINES
    match = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", text)
    assert match, "front matter must be exactly name and description"
    assert match.group(1) == path.parent.name
    assert 20 < len(match.group(2)) <= 1024 and "<" not in match.group(2)


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_each_skill_has_a_runnable_example_and_it_runs(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = _runnable_blocks(path.read_text())
    assert blocks, f"{path.parent.name} needs a '# runnable' python block"
    monkeypatch.chdir(ROOT)
    monkeypatch.syspath_prepend(str(ROOT / "examples"))
    for i, block in enumerate(blocks):
        code = compile(block, f"{path.parent.name}[{i}]", "exec")
        exec(code, {"__name__": f"skill_{i}"})  # noqa: S102 (our own documentation)


def test_the_design_skill_names_every_generic_fixture() -> None:
    fixtures = sorted(n for n in dir(testing) if n.startswith("assert_"))
    assert len(fixtures) >= 5
    text = (ROOT / "skills" / "design-environment" / "SKILL.md").read_text()
    missing = [n for n in fixtures if n not in text]
    assert not missing, missing


def test_the_design_skill_asks_the_nine_questions_in_order() -> None:
    text = (ROOT / "skills" / "design-environment" / "SKILL.md").read_text()
    rows = re.findall(r"^\| (\d) \| ", text, flags=re.M)
    assert rows == [str(i) for i in range(1, 10)]


def test_llms_md_mentions_every_public_symbol() -> None:
    import gao_idl
    import gao_idl.llm

    text = (ROOT / "docs" / "for-agents" / "llms.md").read_text()
    assert len(text.splitlines()) <= 1500
    missing = [n for n in (*gao_idl.__all__, *gao_idl.llm.__all__) if n not in text]
    assert not missing, missing
    for skill in EXPECTED:
        assert f"skills/{skill}/SKILL.md" in text
