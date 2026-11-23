"""The SQLite store: migrations, tokens kept only as hashes, and users' rows never mixing."""

import itertools
import sqlite3
from pathlib import Path

import pytest

from gao_idl.service.store import SCHEMA_VERSION, Store, init_db, new_token, token_hash


def _clock() -> "itertools.count[float]":
    return itertools.count(1000.0)


def _store(tmp_path: Path) -> Store:
    ticks = _clock()
    tokens = (f"token-{i}" for i in itertools.count())
    return Store(tmp_path / "s.db", clock=lambda: next(ticks), token_source=lambda: next(tokens))


def test_a_fresh_database_migrates_once(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    assert init_db(path) == list(range(1, SCHEMA_VERSION + 1))
    assert init_db(path) == []  # reopening does not migrate again
    store = Store(path)
    assert store.migrated == []
    conn = sqlite3.connect(path)
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")]
    assert versions == list(range(1, SCHEMA_VERSION + 1))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"users", "runs", "card_verdicts", "assumption_verdicts", "schema_version"} <= tables
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_a_newer_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "new.db"
    init_db(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO schema_version VALUES (?, 0)", (SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="schema version"):
        init_db(path)


def test_tokens_are_stored_only_as_hashes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user, token = store.add_user("alice")
    assert token == "token-0" and user.name == "alice"
    raw = (tmp_path / "s.db").read_bytes() + (
        (tmp_path / "s.db-wal").read_bytes() if (tmp_path / "s.db-wal").exists() else b""
    )
    assert b"token-0" not in raw
    conn = sqlite3.connect(tmp_path / "s.db")
    assert conn.execute("SELECT token_sha256 FROM users").fetchall() == [(token_hash(token),)]
    conn.close()
    with store.open() as db:
        assert db.user_by_token(token) == user
        assert db.user_by_token("token-1") is None
        assert db.user_by_token("") is None
        assert db.user_by_name("alice") == user
    with pytest.raises(ValueError, match="already"):
        store.add_user("alice")
    with pytest.raises(ValueError, match="name"):
        store.add_user("  ")


def test_new_tokens_are_long_and_different() -> None:
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 43


def test_two_users_rows_never_mix(tmp_path: Path) -> None:
    store = _store(tmp_path)
    alice, _ = store.add_user("alice")
    bob, _ = store.add_user("bob")
    with store.open() as db:
        (a_run,) = db.add_runs(alice, "auction", {"reserve": 1.0}, "truthful", [(0, {"s": 1}, {})])
        (b_run,) = db.add_runs(bob, "auction", {"reserve": 2.0}, "shading", [(0, {"s": 2}, {})])
        db.add_card_verdict(alice, "auction", "tie-top", "confirmed", "", "h1")
        db.add_card_verdict(bob, "auction", "tie-top", "rejected", "bob's note", "h1")
        db.add_assumption_verdict(alice, "auction", 0, "text", "agreed", "")
        db.add_assumption_verdict(bob, "auction", 0, "text", "disputed", "no")

        assert [r.id for r in db.runs(alice)] == [a_run]
        assert [r.id for r in db.runs(bob)] == [b_run]
        assert db.run(alice, b_run) is None and db.run(bob, a_run) is None
        own = db.run(alice, a_run)
        assert own is not None and own.snapshot == {"s": 1} and own.params == {"reserve": 1.0}
        assert [(v.verdict, v.note) for v in db.card_verdicts(alice, "auction")] == [
            ("confirmed", "")
        ]
        assert [(v.verdict, v.note) for v in db.card_verdicts(bob, "auction")] == [
            ("rejected", "bob's note")
        ]
        assert [v.verdict for v in db.assumption_verdicts(alice, "auction")] == ["agreed"]
        assert [v.verdict for v in db.assumption_verdicts(bob, "auction")] == ["disputed"]
        assert db.card_verdicts(alice, "pd") == [] and db.runs(alice, "pd") == []


def test_the_latest_verdict_wins_and_runs_are_newest_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    alice, _ = store.add_user("alice")
    with store.open() as db:
        db.add_card_verdict(alice, "auction", "b", "confirmed", "", "h")
        db.add_card_verdict(alice, "auction", "a", "confirmed", "", "h")
        db.add_card_verdict(alice, "auction", "b", "rejected", "changed my mind", "h")
        db.add_assumption_verdict(alice, "auction", 2, "t", "agreed", "")
        db.add_assumption_verdict(alice, "auction", 2, "t", "disputed", "now no")
        latest = db.card_verdicts(alice, "auction")
        assert [(v.card_id, v.verdict) for v in latest] == [("a", "confirmed"), ("b", "rejected")]
        assert [v.note for v in db.assumption_verdicts(alice, "auction")] == ["now no"]
        first = db.add_runs(alice, "auction", {}, "c", [(1, {}, {}), (2, {}, {})])
        second = db.add_runs(alice, "pd", {}, "c", [(3, {}, {})])
        assert [r.id for r in db.runs(alice)] == second + first[::-1]
        assert [r.seed for r in db.runs(alice, "auction")] == [2, 1]


def test_hostile_text_is_stored_as_data(tmp_path: Path) -> None:
    store = _store(tmp_path)
    name = "Robert'); DROP TABLE users;--"
    user, _ = store.add_user(name)
    note = "x'); DELETE FROM card_verdicts; -- <script>alert(1)</script>"
    with store.open() as db:
        db.add_card_verdict(user, "auction", "c'; --", "rejected", note, "h")
        assert db.user_by_name(name) == user
        (v,) = db.card_verdicts(user, "auction")
        assert v.note == note and v.card_id == "c'; --"
