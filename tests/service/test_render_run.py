"""``render_run`` shows a run as a card does, and ``render_page`` is byte-identical to M10."""

import hashlib
import json
from pathlib import Path

import pytest

from gao_idl.authoring import load_cards, load_spec
from gao_idl.review import Bundle, make_bundle, render_page, render_run, timeline_svg

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((Path(__file__).parent / "golden" / "m10_pages.json").read_text())
NAMES = ("auction", "pd", "vcm")


@pytest.fixture(scope="module")
def bundles() -> dict[str, Bundle]:
    return {
        n: make_bundle(
            load_spec(ROOT / "examples" / "specs" / f"{n}.json"),
            load_cards(ROOT / "examples" / "cards" / f"{n}.json"),
        )
        for n in NAMES
    }


@pytest.mark.parametrize("name", NAMES)
def test_render_page_is_byte_identical_to_m10(bundles: dict[str, Bundle], name: str) -> None:
    page = render_page(bundles[name]).encode("utf-8")
    assert {"sha256": hashlib.sha256(page).hexdigest(), "bytes": len(page)} == GOLDEN[name]
    api = render_page(bundles[name], f"/api/environments/{name}/verdicts").encode("utf-8")
    assert {"sha256": hashlib.sha256(api).hexdigest(), "bytes": len(api)} == GOLDEN[f"{name}+api"]


@pytest.mark.parametrize("name", NAMES)
def test_every_card_shows_exactly_render_run(bundles: dict[str, Bundle], name: str) -> None:
    bundle = bundles[name]
    page = render_page(bundle)
    top = max((abs(u) for v in bundle.cards for u in v.story.utilities.values()), default=0.0)
    for view in bundle.cards:
        fragment = render_run(view.story, view.card.title, top)
        assert fragment in page, view.card.id
        assert view.timeline_svg in fragment


def test_the_fragment_has_the_run_view_and_a_folded_diagram(bundles: dict[str, Bundle]) -> None:
    view = bundles["auction"].cards[0]
    fragment = render_run(view.story, "A title")
    assert fragment.startswith("<h3>What happened</h3>")
    assert "Final payoffs" in fragment and '<ul class="bars">' in fragment
    assert '<details class="fold"><summary><h3>Detailed diagram</h3>' in fragment
    assert timeline_svg(view.story, "A title") in fragment
    assert "<script" not in fragment and "http" not in fragment


def test_top_scales_the_bars(bundles: dict[str, Bundle]) -> None:
    story = next(v.story for v in bundles["auction"].cards if any(v.story.utilities.values()))
    largest = max(abs(u) for u in story.utilities.values())
    assert render_run(story, "t") == render_run(story, "t", largest)
    assert "width:100.0%" in render_run(story, "t")
    assert "width:100.0%" not in render_run(story, "t", largest * 4)


def test_hostile_titles_are_escaped(bundles: dict[str, Bundle]) -> None:
    view = bundles["auction"].cards[0]
    fragment = render_run(view.story, '<script>alert("x")</script>')
    assert "<script>" not in fragment and "&lt;script&gt;" in fragment
