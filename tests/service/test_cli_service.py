"""``gao-idl service init | add-user | export-verdicts`` (``serve`` is in the slow socket test)."""

import json
import sqlite3
from pathlib import Path

import pytest

from gao_idl.cli import main
from gao_idl.review.verdicts import load_verdicts
from gao_idl.service.app import make_app
from gao_idl.service.store import MIGRATIONS, SCHEMA_VERSION, Store, init_db
from tests.service.helpers import LIBRARY, call, library


def test_init_creates_then_reports_current(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "s.db"
    assert main(["service", "init", "--db", str(db)]) == 0
    assert "applied" in capsys.readouterr().out
    assert main(["service", "init", "--db", str(db)]) == 0
    assert "already current" in capsys.readouterr().out


def test_add_user_prints_the_token_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "s.db"
    assert main(["service", "add-user", "ana", "--db", str(db)]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert len(token) >= 43
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT name FROM users").fetchall() == [("ana",)]
    conn.close()
    assert token.encode() not in db.read_bytes()
    app = make_app(library(), Store(db))
    assert call(app, "GET", "/api/environments", token).status == 200
    assert main(["service", "add-user", "ana", "--db", str(db)]) == 2
    assert "already" in capsys.readouterr().err


def test_export_verdicts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "s.db"
    store = Store(db)
    user, token = store.add_user("ben")
    out = tmp_path / "v.json"
    args = ["service", "export-verdicts", "auction", "--user", "ben", "--db", str(db)]
    assert main([*args, "--out", str(out)]) == 2  # no verdicts and no library: no title
    assert "--library" in capsys.readouterr().err
    assert main([*args, "--out", str(out), "--library", str(LIBRARY)]) == 0
    empty = load_verdicts(out)
    assert empty.cards == [] and empty.reviewer == "ben"
    assert empty.spec_title == "Sealed-bid auction with a public reserve price"

    app = make_app(library(), store)
    view = call(app, "GET", "/api/environments/auction/bundle", token).json["cards"][0]
    verdict = {
        "id": view["card"]["id"],
        "verdict": "confirmed",
        "outcome_hash": view["outcome_hash"],
    }
    assert (
        call(app, "POST", "/api/environments/auction/verdicts", token, body=verdict).status == 200
    )
    assert main([*args, "--out", str(out)]) == 0  # the title now comes from the stored verdict
    done = load_verdicts(out)
    assert [c.model_dump() for c in done.cards] == [{**verdict, "note": ""}]
    nobody = ["service", "export-verdicts", "auction", "--user", "nobody", "--db", str(db)]
    assert main([*nobody, "--out", str(out)]) == 2
    unknown = ["service", "export-verdicts", "nope", "--user", "ben", "--db", str(db)]
    assert main([*unknown, "--out", str(out), "--library", str(LIBRARY)]) == 2
    assert json.loads(out.read_text())["bundle_version"] == 1


def test_a_version_1_database_migrates_to_the_current_version(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied REAL NOT NULL)")
    for statement in MIGRATIONS[0].split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("INSERT INTO schema_version VALUES (1, 0)")
    conn.execute("INSERT INTO users (name, token_sha256, created) VALUES ('old', 'h', 0)")
    conn.execute(
        "INSERT INTO card_verdicts (user_id, slug, card_id, verdict, note, outcome_hash, created) "
        "VALUES (1, 'auction', 'c', 'confirmed', '', 'x', 0)"
    )
    conn.commit()
    conn.close()
    assert init_db(db) == list(range(2, SCHEMA_VERSION + 1))
    store = Store(db)
    with store.open() as s:
        user = s.user_by_name("old")
        assert user is not None
        assert [v.card_id for v in s.card_verdicts(user, "auction")] == ["c"]
        assert s.spec_title(user, "auction") is None
