"""The page as text: self-contained, escaped, small enough, and complete. No browser is used."""

import html
import json
import re
from pathlib import Path

import pytest

from gao_idl.authoring import Card, load_cards, load_spec
from gao_idl.review import Bundle, dump_bundle, make_bundle, render_page, save_bundle
from gao_idl.review.page import FIXED_SENTENCE, asset
from tests.test_cli import _cli

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "examples" / "specs"
CARDS = ROOT / "examples" / "cards"
EVIL = 'Evil <script>alert("pwned")</script> & "quotes"'


def _bundle(name: str) -> Bundle:
    return make_bundle(load_spec(SPECS / f"{name}.json"), load_cards(CARDS / f"{name}.json"))


@pytest.fixture(scope="module")
def auction_page() -> str:
    return render_page(_bundle("auction"))


def _embedded(page: str, element_id: str) -> object:
    match = re.search(
        rf'<script type="application/json" id="{element_id}">(.*?)</script>', page, re.S
    )
    assert match is not None
    return json.loads(match.group(1))


def test_the_assets_ship_with_the_package() -> None:
    for name in ("page.html", "page.css", "page.js"):
        assert len(asset(name)) > 500, name
    assert "{{JS}}" in asset("page.html") and "{{BUNDLE}}" in asset("page.html")


def test_the_assets_are_declared_for_the_wheel() -> None:
    text = (ROOT / "pyproject.toml").read_text()
    for name in ("page.html", "page.css", "page.js"):
        assert f'"src/gao_idl/review/{name}"' in text


def test_the_page_makes_no_network_request(auction_page: str) -> None:
    for forbidden in ("http://", "https://", "src=", "href=", "@import", "url(", "//cdn"):
        assert forbidden not in auction_page, forbidden
    assert "connect-src 'none'" in auction_page and "default-src 'none'" in auction_page
    assert auction_page.count("<script") == 3  # the bundle, the config and page.js
    assert "fetch(" in auction_page  # present, but used only when an api is given
    assert _embedded(auction_page, "gao-config")["api"] is None  # type: ignore[index]


def test_the_page_has_five_screens_and_every_card(auction_page: str) -> None:
    for i in range(5):
        assert f'<section class="screen" id="screen-{i}"' in auction_page
    assert FIXED_SENTENCE in auction_page
    assert "Card 1 of 13" in auction_page and "Card 13 of 13" in auction_page
    assert auction_page.count('<article class="card"') == 13
    assert auction_page.count('<li class="assumption"') == 7
    for label in ("Yes, this is what I meant", ">No…<", ">Skip<", ">Back<", "Download verdicts"):
        assert label in auction_page
    assert "<button " in auction_page and "<a " not in auction_page
    assert auction_page.count('<div class="picture"><svg') == 14  # the flow and 13 timelines
    assert auction_page.count("<svg ") == 14
    assert len(re.findall(r"<svg [^>]*>\n<title>[^<]+</title>", auction_page)) == 14
    assert "Who knows what" in auction_page and "What must stay secret" in auction_page


def test_the_embedded_bundle_is_the_bundle(auction_page: str) -> None:
    bundle = _bundle("auction")
    data = _embedded(auction_page, "gao-bundle")
    assert Bundle.model_validate(data) == bundle
    key = _embedded(auction_page, "gao-config")["key"]  # type: ignore[index]
    assert re.fullmatch(r"[0-9a-f]{64}", key)


def test_rendering_is_deterministic(auction_page: str) -> None:
    assert render_page(_bundle("auction")) == auction_page


def test_untrusted_text_never_appears_unescaped() -> None:
    spec = load_spec(SPECS / "auction.json")
    spec = spec.model_copy(update={"title": EVIL, "summary": EVIL})
    cards = load_cards(CARDS / "auction.json")[:2]
    cards[0] = cards[0].model_copy(update={"title": EVIL, "note": EVIL, "why": EVIL})
    page = render_page(make_bundle(spec, cards))
    assert page.count("<script") == 3
    assert "<script>alert" not in page
    assert 'alert("pwned")' not in page
    assert '"quotes"' not in page
    assert "&lt;script&gt;alert(&quot;pwned&quot;)&lt;/script&gt;" in page  # HTML and SVG
    assert '\\u003cscript\\u003ealert(\\"pwned\\")' in page  # the embedded JSON
    assert "</script> &" not in page


def test_a_stale_card_says_so_and_cannot_be_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    from gao_idl.authoring import freeze
    from tests.authoring import envs
    from tests.authoring.test_cards import _tie
    from tests.review.test_bundle import tiny_auction_spec

    card: Card = freeze(_tie())
    monkeypatch.setattr(envs, "TIE", "last")
    page = render_page(make_bundle(tiny_auction_spec(), [card]))
    assert "This card is out of date." in page
    assert 'data-act="yes" aria-pressed="false" disabled>' in page


def test_the_carbon_market_page_is_under_two_megabytes() -> None:
    page = render_page(_bundle("vcm"))
    assert len(page.encode("utf-8")) < 2_000_000


def test_with_an_api_the_page_may_post_there_only() -> None:
    bundle = _bundle("pd")
    page = render_page(bundle, api="/verdicts")
    assert "connect-src 'self'" in page
    assert _embedded(page, "gao-config")["api"] == "/verdicts"  # type: ignore[index]
    with pytest.raises(ValueError, match="api"):
        render_page(bundle, api="javascript:alert(1)")
    with pytest.raises(ValueError, match="api"):
        render_page(bundle, api="//elsewhere.example/x")
    with pytest.raises(ValueError, match="api"):
        render_page(bundle, api='/v"><script>')


def test_cli_review_page_from_a_spec_and_from_a_bundle(tmp_path: Path) -> None:
    out = tmp_path / "review.html"
    spec, cards = str(SPECS / "pd.json"), str(CARDS / "pd.json")
    done = _cli("review", "page", spec, "--cards", cards, "--out", str(out), cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    bundle_path = tmp_path / "bundle.json"
    save_bundle(bundle_path, _bundle("pd"))
    again = tmp_path / "again.html"
    done = _cli("review", "page", "--bundle", str(bundle_path), "--out", str(again), cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert again.read_text() == out.read_text()
    assert _cli("review", "page", "--out", str(again), cwd=tmp_path).returncode == 2
    assert _cli("review", "page", spec, "--out", str(again), cwd=tmp_path).returncode == 2


def test_bundle_text_and_page_agree_on_the_card_order() -> None:
    bundle = _bundle("pd")
    page = render_page(bundle)
    ids = [v.card.id for v in bundle.cards]
    positions = [page.index(f"Card name: {i}.") for i in ids]
    assert positions == sorted(positions)
    assert json.loads(dump_bundle(bundle))["bundle_version"] == 1


def test_the_example_page_script_writes_three_pages(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_example_pages", ROOT / "scripts" / "make_example_pages.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--fuzz", "0", "--out", str(tmp_path)]) == 0
    pages = sorted(p.name for p in tmp_path.glob("*.html"))
    assert pages == ["auction.html", "pd.html", "vcm.html"]
    assert "build/review/" in (ROOT / ".gitignore").read_text().split()


# ------------------------------------------------------------------ M10 fix-up: shorter cards


def _articles(page: str) -> list[str]:
    return re.findall(r'<article class="card".*?</article>', page, re.S)


def test_a_public_report_is_shown_once_not_once_per_recipient() -> None:
    bundle = _bundle("vcm")
    page = render_page(bundle)
    (view,) = [v for v in bundle.cards if v.card.id == "audited"]
    (audit,) = [s for s in view.story.steps if s.layer == "audit"]
    revealed = audit.told[0].payload["revealed"]
    text = html.escape(json.dumps(revealed, sort_keys=True), quote=True)
    article = next(a for a in _articles(page) if "Card name: audited." in a)
    picture, words = article.split("</svg>", 1)
    assert words.count(text) == 1  # the told list: once, for everyone
    assert picture.count(text) == 1  # the everyone-row's full text, in its <title>
    assert len(audit.told) == 9  # nine recipients in M10 meant nine copies


def test_the_carbon_market_page_stays_a_reasonable_size() -> None:
    # 978 KB in M10; the run view by participant added about 60 KB to the fix-up's page
    assert len(render_page(_bundle("vcm")).encode("utf-8")) < 1200 * 1024


def test_the_full_record_is_folded_shut_on_every_card() -> None:
    auction = _articles(render_page(_bundle("auction")))
    assert all('<details class="fold" open>' not in a for a in auction)
    assert all(a.count('<details class="fold">') == 3 for a in auction)
    vcm = _articles(render_page(_bundle("vcm")))
    folded = [a for a in vcm if '<details class="fold">' in a]
    assert folded and all(a.count('<details class="fold">') == 3 for a in folded)
    assert all("message" in a and "step" in a for a in vcm)


def test_the_answer_bar_is_sticky_and_inside_every_card(auction_page: str) -> None:
    css = asset("page.css")
    assert re.search(r"\.verdict \{[^}]*position: sticky;[^}]*bottom: 0;", css, re.S)
    assert re.search(r"\.verdict \{[^}]*background: var\(--bg\);", css, re.S)
    for article in _articles(auction_page):
        assert article.count('<div class="verdict only-js"') == 1
        assert article.index("<h3>What happened</h3>") < article.index("<h4>Final payoffs</h4>")
        assert article.index("<h4>Final payoffs</h4>") < article.index("Detailed diagram")
        assert article.index("Detailed diagram") < article.index("<svg")
        assert article.index("<svg") < article.index("The full record")
        assert article.index("Everything each participant was told") < article.index(
            'class="verdict'
        )
        # the note box stays shut until "No…" asks for it, and has its own Submit and Cancel
        panel = article[article.index('<div class="note-panel"') :]
        assert re.match(r'<div class="note-panel" id="panel-c\d+" hidden>', panel)
        assert "data-submit-note" in panel and "data-cancel-note" in panel
        assert 'class="note"' in panel and "<label for=" in panel


def test_narrow_navigation_keeps_full_names_for_screen_readers(auction_page: str) -> None:
    names = ["Checks", "Design", "Choices", "Cards", "Finish"]
    for i, name in enumerate(names):
        assert (
            f'data-screen="{i}" aria-label="{i + 1}. {name}" title="{name}">'
            f'<span class="num">{i + 1}</span>'
        ) in auction_page
    css = asset("page.css")
    assert re.search(
        r"@media \(max-width: 30rem\) \{[^@]*nav\.screens \.name \{ display: none; \}", css
    )
    assert re.search(r"(?m)^label \{ display: block; margin: [^}]+\}", css)


def test_every_choice_has_a_note_box_with_a_submit_button(auction_page: str) -> None:
    items = re.findall(r'<li class="assumption".*?</li>', auction_page, re.S)
    assert items
    for item in items:
        assert 'data-verdict="agreed"' in item and "data-open-note" in item
        assert 'data-verdict="disputed"' not in item  # disagreeing goes through the box
        assert re.search(r'<div class="note-panel only-js" id="panel-a\d+" hidden>', item)
        assert "data-submit-note" in item and "data-cancel-note" in item


def test_a_card_shows_the_run_by_participant_and_keeps_the_diagram_folded() -> None:
    (article,) = [a for a in _articles(render_page(_bundle("auction"))) if "name: tie-top." in a]
    run = article[article.index("<h3>What happened</h3>") : article.index("Detailed diagram")]
    assert "<h4>Bidding</h4>" in run and "<h4>Settlement</h4>" in run
    assert run.count('<li class="tile">') == 6  # three bidders in each of two steps
    assert '<p class="who">alice chose</p><p class="what">amount</p><p class="did">8</p>' in run
    assert "Everyone was told the same" in run and "told privately" in run
    assert "<dt>winner</dt><dd>alice</dd>" in run and "<dt>winning payment</dt><dd>8</dd>" in run
    assert "own_bid" not in run and " = " not in run  # field names and pairs: record only
    assert "<svg" not in run
    bars = run[run.index("<h4>Final payoffs</h4>") :]
    assert bars.count('<span class="bar pos"') == 3 and "The run finished." in run
    folded = article[article.index("Detailed diagram") :]
    assert folded.index("<svg") < folded.index("The full record")


def test_payoff_bars_share_one_scale_and_mark_losses() -> None:
    bundle = _bundle("auction")
    top = max(abs(u) for v in bundle.cards for u in v.story.utilities.values())
    articles = _articles(render_page(bundle))
    (lost,) = [a for a in articles if "name: overbid." in a]
    assert '<span class="bar neg" style="width:' in lost  # bob overbids and ends below zero
    assert top == 10 and '<span class="bar pos" style="width:100.0%"' in "".join(articles)
