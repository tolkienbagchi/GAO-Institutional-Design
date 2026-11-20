"""Pictures: golden files, one box per step, one lane per participant, limits, escaping, size.

Regenerate the golden files with ``GAO_UPDATE_GOLDEN=1 pytest tests/review/test_draw.py`` and read
the diff before committing it (or open the ``.svg`` files: they are standalone pictures).
"""

import os
import re
from pathlib import Path
from typing import Any

import pytest

from gao_idl.authoring import Card, load_cards, load_spec, tell_card
from gao_idl.authoring.cards import Move, StepView, Story, Told
from gao_idl.composition.contracts import describe
from gao_idl.review.draw import flow_svg, timeline_svg
from gao_idl.runtime.registry import resolve

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parent / "golden"
TIMELINES = {"auction": ["tie-top", "no-bid"], "pd": ["two-pairs", "no-move-default"]}


def _golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("GAO_UPDATE_GOLDEN"):
        path.write_text(text)
    assert path.read_text() == text


def _tree(name: str) -> dict[str, Any]:
    spec = load_spec(ROOT / "examples" / "specs" / f"{name}.json")
    process, _ = resolve(spec.env_ref)(**spec.params)
    return describe(process)


def _card(name: str, card_id: str) -> Card:
    cards = load_cards(ROOT / "examples" / "cards" / f"{name}.json")
    return next(c for c in cards if c.id == card_id)


def _primitive_nodes(node: dict[str, Any]) -> int:
    kind = node.get("kind")
    if kind == "primitive":
        return 1
    if kind == "seq":
        return _primitive_nodes(node["first"]) + sum(
            _primitive_nodes(r["then"]) for r in node["routes"].values()
        )
    if kind == "par":
        return sum(_primitive_nodes(b) for b in node["branches"].values())
    if kind == "loop":
        return _primitive_nodes(node["body"])
    return 0


@pytest.mark.parametrize("name", ["auction", "pd"])
def test_flow_golden(name: str) -> None:
    svg = flow_svg(_tree(name))
    _golden(f"{name}.flow.svg", svg)
    assert flow_svg(_tree(name)) == svg


@pytest.mark.parametrize(
    ("name", "card_id"), [(n, c) for n, ids in sorted(TIMELINES.items()) for c in ids]
)
def test_timeline_golden(name: str, card_id: str) -> None:
    spec = load_spec(ROOT / "examples" / "specs" / f"{name}.json")
    card = _card(name, card_id)
    svg = timeline_svg(tell_card(card, spec), card.title)
    _golden(f"{name}.{card_id}.svg", svg)
    assert timeline_svg(tell_card(card, spec), card.title) == svg


@pytest.mark.parametrize("name", ["auction", "pd", "vcm"])
def test_one_step_box_per_primitive_and_frames_for_loops_and_branches(name: str) -> None:
    tree = _tree(name)
    svg = flow_svg(tree)
    assert svg.count('<g class="step">') == _primitive_nodes(tree)
    assert svg.count('<g class="frame loop">') == (1 if name in ("pd", "vcm") else 0)
    assert svg.count('<g class="frame par">') == (1 if name == "pd" else 0)
    if name == "pd":
        assert "repeat: pd (limit 4)" in svg and ">pair_0<" in svg and ">pair_1<" in svg
    ends = re.findall(r">ends: (\w+)<", svg)
    assert ends == (["done", "unsold"] if name == "auction" else ["done"])


@pytest.mark.parametrize(("name", "card_id"), [("pd", "two-pairs"), ("auction", "tie-top")])
def test_one_lane_per_participant_and_one_for_the_rules(name: str, card_id: str) -> None:
    story = tell_card(_card(name, card_id))
    svg = timeline_svg(story, "t")
    people = set(story.utilities)
    assert svg.count('<g class="lane">') == len(people)
    assert svg.count('<g class="lane rules">') == 1
    assert svg.count('<g class="payoff">') == len(people)
    moves = sum(len(s.moves) for s in story.steps)
    told = sum(len(s.told) for s in story.steps)
    assert svg.count('<g class="move ') == moves and svg.count('<g class="told">') == told


def test_long_actions_are_cut_with_the_full_text_in_a_title() -> None:
    long = {"note": "x" * 60}
    story = Story(
        steps=[
            StepView(
                place="",
                layer="say",
                description="",
                moves=[Move(agent="ann", kind="chose", action=long)],
                told=[],
                stopped=None,
            )
        ],
        utilities={"ann": 1.0},
        exit_port="done",
        failed=False,
    )
    svg = timeline_svg(story, "t")
    assert "<title>ann chooses: note = " + "x" * 60 + "</title>" in svg
    assert ">note = " + "x" * 20 + "…<" in svg  # 28 characters shown


def _crowded(people: int, steps: int) -> Story:
    names = [f"p{i}" for i in range(people)]
    return Story(
        steps=[
            StepView(
                place=f"round {k + 1}",
                layer="act",
                description="",
                moves=[Move(agent=a, kind="chose", action={"x": k}) for a in names],
                told=[Told(agent=a, payload={"y": k}) for a in names],
                stopped=None,
            )
            for k in range(steps)
        ],
        utilities={a: 1.0 for a in names},
        exit_port="done",
        failed=False,
    )


def test_beyond_eight_participants_or_twelve_steps_the_rest_is_announced() -> None:
    svg = timeline_svg(_crowded(10, 14), "crowded")
    assert svg.count('<g class="lane">') == 8
    assert "+2 more participants not drawn" in svg
    assert svg.count('<g class="band">') == 12
    assert "+2 more steps not drawn" in svg
    assert svg.count("+4 more arrows for participants not drawn") == 12
    assert ">p9<" not in svg and ">p7<" in svg
    small = timeline_svg(_crowded(8, 12), "fits")
    assert "more" not in small


def test_untrusted_text_is_escaped_and_nothing_points_outside() -> None:
    evil = '<script>alert("x")</script>'
    story = Story(
        steps=[
            StepView(
                place=evil,
                layer=evil,
                description=evil,
                moves=[Move(agent=evil, kind="chose", action={evil: evil})],
                told=[Told(agent=evil, payload={"q": '"'})],
                stopped=evil,
            )
        ],
        utilities={evil: 1.0},
        exit_port=None,
        failed=True,
    )
    svg = timeline_svg(story, evil)
    assert "<script" not in svg and 'alert("' not in svg
    assert "&lt;script&gt;" in svg
    for picture in (svg, flow_svg(_tree("pd"))):
        assert "http" not in picture and "xmlns" not in picture and "url(" not in picture
        assert " id=" not in picture and "href" not in picture
        assert picture.startswith('<svg class="') and "<title>" in picture


def test_the_two_round_carbon_market_timeline_stays_small() -> None:
    spec = load_spec(ROOT / "examples" / "specs" / "vcm.json")
    card = _card("vcm", "reputation-shown")
    svg = timeline_svg(tell_card(card, spec), card.title)
    assert len(svg.encode()) < 200_000
    assert "+1 more participant not drawn" in svg  # nine participants
