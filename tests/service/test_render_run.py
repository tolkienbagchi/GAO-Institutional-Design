"""``render_run`` shows a run as a card does, and ``render_page``'s output is pinned.

The pinned hashes (``golden/pages.json``) were last rewritten on purpose by the reviewer after
M11, when the page learnt to ask its server for earlier answers. A change to them must be meant.
"""

import hashlib
import json
from pathlib import Path

import pytest

from gao_idl.authoring import load_cards, load_spec
from gao_idl.review import Bundle, make_bundle, render_page, render_run, timeline_svg
from gao_idl.review.draw import esc

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((Path(__file__).parent / "golden" / "pages.json").read_text())
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
def test_render_page_output_is_pinned(bundles: dict[str, Bundle], name: str) -> None:
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
    from gao_idl.review.page import PAGE_SCALE, RUN_SCALE

    alone = render_run(story, "t")  # the same bars; only the line under them differs
    assert alone.replace(esc(RUN_SCALE), PAGE_SCALE) == render_run(story, "t", largest)
    assert "width:100.0%" in render_run(story, "t")
    assert "width:100.0%" not in render_run(story, "t", largest * 4)


def test_hostile_titles_are_escaped(bundles: dict[str, Bundle]) -> None:
    view = bundles["auction"].cards[0]
    fragment = render_run(view.story, '<script>alert("x")</script>')
    assert "<script>" not in fragment and "&lt;script&gt;" in fragment


def test_a_run_on_its_own_says_its_bars_are_scaled_to_itself(bundles: dict[str, Bundle]) -> None:
    view = bundles["auction"].cards[0]
    alone = render_run(view.story, view.card.title)
    assert "Bars are scaled to this run&#x27;s largest payoff." in alone
    assert "every card on this page" not in alone
    shared = render_run(view.story, view.card.title, 10.0)
    assert "Bars use one scale for every card on this page." in shared
