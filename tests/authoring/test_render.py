"""Rendering is deterministic and comes from the built environment: golden files and tables.

Regenerate the golden files with ``GAO_UPDATE_GOLDEN=1 pytest tests/authoring/test_render.py``
and read the diff before committing it.
"""

import os
from pathlib import Path

import pytest

from gao_idl.authoring import knowledge_table, load_cards, load_spec, render_design, to_mermaid
from gao_idl.authoring.play import play
from gao_idl.authoring.render import has_loop, primitives, to_outline
from gao_idl.composition.contracts import describe
from gao_idl.runtime.registry import resolve
from tests.authoring.envs import ULTIMATUM

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parent / "golden"


def _golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("GAO_UPDATE_GOLDEN"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
    assert path.read_text() == text


def _design(name: str) -> tuple[str, str, str]:
    spec = load_spec(ROOT / "examples" / "specs" / f"{name}.json")
    cards = load_cards(ROOT / "examples" / "cards" / f"{name}.json")
    sessions = [play(c.env_ref, c.params, c.seed, c.script) for c in cards]
    process, _ = resolve(spec.env_ref)(**spec.params)
    tree = describe(process)
    return (
        to_mermaid(tree),
        to_outline(tree),
        render_design(spec, process, knowledge_table(sessions)),
    )


@pytest.mark.parametrize("name", ["auction", "pd"])
def test_golden_diagram_outline_and_design(name: str) -> None:
    mermaid, outline, design = _design(name)
    _golden(f"{name}.mmd", mermaid)
    _golden(f"{name}.outline.txt", outline)
    _golden(f"{name}.design.md", design)
    assert _design(name) == (mermaid, outline, design)  # same arguments, same text


def test_mermaid_shape() -> None:
    process, _ = resolve("gao_idl.environments.pd:build_repeated_pd")(
        agents=["a", "b", "c", "d"], rounds=3
    )
    text = to_mermaid(describe(process))
    assert text.startswith("flowchart TD\n")
    lines = [line.strip() for line in text.splitlines()]
    assert lines.count("end") == sum(line.startswith("subgraph") for line in lines) == 2
    assert '"repeat: pd' in text and '"in parallel: round"' in text
    assert "-->|again|" in text and "-->|pair_0|" in text and '(["ends: done"])' in text


def test_tree_helpers() -> None:
    process, _ = resolve("gao_idl.environments.vcm:build_vcm")(rounds=2)
    tree = describe(process)
    assert [n["name"] for n in primitives(tree)] == ["verification", "market", "clearing", "audit"]
    assert has_loop(tree)
    single, _ = resolve("gao_idl.environments.auction:build_bidding_only")(values={"a": 1.0})
    assert not has_loop(describe(single))


def test_knowledge_table_reads_what_roles_saw_chose_and_were_told() -> None:
    session = play(ULTIMATUM, {}, 0, {"ann": [{"offer": 4.0}], "ben": [{"accept": True}]})
    table = knowledge_table([session])
    assert table.step_order == ["offer", "response"]
    assert table.timeouts == {"offer": "fail", "response": "drop"}
    offer = table.row("offer", "proposer")
    assert offer is not None and offer.sees == ["cost", "pie"] and offer.chooses == ["offer"]
    response = table.row("response", "responder")
    assert response is not None and response.sees == ["offer", "pie"]
    assert response.told == ["accepted", "own_payoff"]
    told_only = table.row("response", "proposer")  # held no role in the step, still told
    assert told_only is not None and told_only.chooses == [] and told_only.told == response.told


def test_vcm_buyers_never_see_true_quality() -> None:
    cards = load_cards(ROOT / "examples" / "cards" / "vcm.json")
    unaudited = [c for c in cards if c.params["p_audit"] == 0.0]
    assert unaudited
    table = knowledge_table([play(c.env_ref, c.params, c.seed, c.script) for c in unaudited])
    for row in table.rows:
        if row.role in ("buyer", "seller"):
            assert "theta" not in row.sees + row.told and "true_quality" not in row.sees + row.told
    market = table.row("market", "buyer")
    assert market is not None and "reputation" in market.sees
    assert table.row("verification", "verifier") is not None


def test_design_lists_unconfirmed_assumptions_and_uses_built_timeouts() -> None:
    _, _, design = _design("auction")
    assert "## Choices nobody has confirmed yet" in design
    assert "- [ ] If two bidders tie for the highest bid" in design
    assert "- **bidding**: the run stops." in design
    assert "```mermaid" in design
