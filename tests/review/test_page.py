"""The page as text: self-contained, escaped, small enough, and complete. No browser is used."""

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
    for label in ("Yes, this is what I meant", ">No<", ">Skip<", ">Back<", "Download verdicts"):
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
    positions = [page.index(f"Card {i}; in the file") for i in ids]
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
