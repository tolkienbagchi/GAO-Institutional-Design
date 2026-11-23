"""From an empty database to verdicts imported into a copy of the cards, and a carbon-market run."""

import shutil
from pathlib import Path

import pytest

from gao_idl.authoring import load_cards, load_spec
from gao_idl.cli import main
from gao_idl.review.verdicts import load_verdicts
from gao_idl.service import load_library
from gao_idl.service.app import make_app
from gao_idl.service.store import Store
from tests.service.helpers import LIBRARY, ROOT, call


def test_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "service.db"
    examples = {p: p.read_bytes() for p in (ROOT / "examples").rglob("*.json")}

    # an administrator prepares the database and issues a token
    assert main(["service", "init", "--db", str(db)]) == 0
    assert main(["service", "add-user", "Eve Reviewer", "--db", str(db)]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    app = make_app(load_library(LIBRARY), Store(db))

    # the reviewer lists the library and fetches the auction's bundle
    listed = call(app, "GET", "/api/environments", token)
    assert listed.status == 200
    assert "auction" in [e["slug"] for e in listed.json["environments"]]
    bundle = call(app, "GET", "/api/environments/auction/bundle", token).json
    first, second = bundle["cards"][0], bundle["cards"][1]

    # and gives verdicts on two cards, as the review page posts them, plus one choice
    for body in (
        {
            "id": first["card"]["id"],
            "verdict": "confirmed",
            "note": "",
            "outcome_hash": first["outcome_hash"],
        },
        {
            "id": second["card"]["id"],
            "verdict": "rejected",
            "note": "The reserve should apply.",
            "outcome_hash": second["outcome_hash"],
        },
        {
            "index": 0,
            "text": bundle["spec"]["assumptions"][0]["text"],
            "verdict": "agreed",
            "note": "",
        },
    ):
        assert (
            call(app, "POST", "/api/environments/auction/verdicts", token, body=body).status == 200
        )

    # the administrator exports them and the author imports them into a copy of the cards
    verdicts = tmp_path / "verdicts.json"
    export = ["service", "export-verdicts", "auction", "--user", "Eve Reviewer", "--db", str(db)]
    assert main([*export, "--out", str(verdicts)]) == 0
    exported = load_verdicts(verdicts)
    assert exported.reviewer == "Eve Reviewer" and len(exported.cards) == 2
    cards = tmp_path / "cards.json"
    spec = tmp_path / "spec.json"
    shutil.copy(ROOT / "examples" / "cards" / "auction.json", cards)
    shutil.copy(ROOT / "examples" / "specs" / "auction.json", spec)
    capsys.readouterr()
    assert main(["cards", "import", str(cards), str(verdicts), "--spec", str(spec)]) == 0
    assert "2 cards updated" in capsys.readouterr().out
    by_id = {c.id: c for c in load_cards(cards)}
    assert by_id[first["card"]["id"]].status == "confirmed"
    assert by_id[first["card"]["id"]].reviewer == "Eve Reviewer"
    assert by_id[second["card"]["id"]].status == "rejected"
    assert by_id[second["card"]["id"]].note == "The reserve should apply."
    assert sum(c.status == "proposed" for c in by_id.values()) == len(by_id) - 2
    assert load_spec(spec).assumptions[0].confirmed is True
    capsys.readouterr()
    assert main(["cards", "verify", str(cards)]) == 1  # the rejected card is work for the author
    assert "1 confirmed, 1 rejected" in capsys.readouterr().out

    # the reviewer runs the carbon market with a cast over three seeds and reads one run
    body = {"cast": "skeptical-buyers", "seeds": [1, 2, 3], "params": {"p_audit": 0.5}}
    made = call(app, "POST", "/api/environments/carbon-market/runs", token, body=body)
    assert made.status == 200, made.text
    runs = made.json["runs"]
    assert [r["seed"] for r in runs] == [1, 2, 3]
    assert all(r["summary"]["finished"] and not r["summary"]["failed"] for r in runs)
    one = call(app, "GET", f"/api/runs/{runs[1]['id']}", token).json
    assert one["params"]["p_audit"] == 0.5 and one["cast"] == "skeptical-buyers"
    layers = {s["layer"] for s in one["story"]["steps"]}
    assert {"verification", "market", "clearing", "audit"} <= layers
    assert set(one["story"]["utilities"]) == set(one["summary"]["payoffs"])
    assert "What happened" in one["html"] and "Detailed diagram" in one["html"]

    # nothing under examples/ was written
    assert {p: p.read_bytes() for p in (ROOT / "examples").rglob("*.json")} == examples
