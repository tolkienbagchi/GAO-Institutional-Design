"""Storage for the library service: users, runs and verdicts in one SQLite file.

``Store(path)`` migrates the file to the current schema (``init_db``) and hands out one
connection per request (``Store.open``), each a ``Db`` whose methods are the only SQL in the
service. Every statement is parameterised. Every query about runs or verdicts is filtered by the
user it is asked for, so one user's rows never reach another. A token is shown once, when the user
is added, and only its SHA-256 is stored. Verdicts are kept as given, oldest first; the latest
per user and card (or choice) is the one that counts.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIGRATIONS: tuple[str, ...] = (
    # 1: the first schema
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        token_sha256 TEXT NOT NULL UNIQUE,
        created REAL NOT NULL
    );
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        slug TEXT NOT NULL,
        params TEXT NOT NULL,
        cast_name TEXT NOT NULL,
        seed INTEGER NOT NULL,
        snapshot TEXT NOT NULL,
        summary TEXT NOT NULL,
        created REAL NOT NULL
    );
    CREATE INDEX runs_by_user ON runs (user_id, id);
    CREATE TABLE card_verdicts (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        slug TEXT NOT NULL,
        card_id TEXT NOT NULL,
        verdict TEXT NOT NULL CHECK (verdict IN ('confirmed', 'rejected')),
        note TEXT NOT NULL,
        outcome_hash TEXT NOT NULL,
        created REAL NOT NULL
    );
    CREATE INDEX card_verdicts_by_user ON card_verdicts (user_id, slug, card_id, id);
    CREATE TABLE assumption_verdicts (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        slug TEXT NOT NULL,
        idx INTEGER NOT NULL,
        text TEXT NOT NULL,
        verdict TEXT NOT NULL CHECK (verdict IN ('agreed', 'disputed')),
        note TEXT NOT NULL,
        created REAL NOT NULL
    );
    CREATE INDEX assumption_verdicts_by_user ON assumption_verdicts (user_id, slug, idx, id);
    """,
    # 2: the spec title a verdict was given under, so a verdicts file can be exported without
    # the library (`gao-idl service export-verdicts`)
    """
    ALTER TABLE card_verdicts ADD COLUMN spec_title TEXT NOT NULL DEFAULT '';
    ALTER TABLE assumption_verdicts ADD COLUMN spec_title TEXT NOT NULL DEFAULT '';
    """,
)
SCHEMA_VERSION = len(MIGRATIONS)


def token_hash(token: str) -> str:
    """The SHA-256 (hex) of a token: the only form in which a token is stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """A new token: 32 random bytes, URL-safe base64 (``secrets.token_urlsafe``)."""
    return secrets.token_urlsafe(32)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db(path: Path, clock: Callable[[], float] = time.time) -> list[int]:
    """Create or migrate the database at ``path``; returns the migrations applied (none if current).

    WAL journal mode; a ``schema_version`` table lists the applied versions; ``MIGRATIONS`` are
    applied in order, each in its own transaction. A database newer than this code is refused.
    """
    conn = _connect(Path(path))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY, applied REAL NOT NULL)"
        )
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = int(row[0] or 0)
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"the database is at schema version {current}; this code knows {SCHEMA_VERSION}"
            )
        applied = []
        for version in range(current + 1, SCHEMA_VERSION + 1):
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in MIGRATIONS[version - 1].split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_version (version, applied) VALUES (?, ?)",
                    (version, clock()),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            applied.append(version)
        return applied
    finally:
        conn.close()


@dataclass(frozen=True)
class User:
    """A user of the service (never with the token)."""

    id: int
    name: str
    created: float


@dataclass(frozen=True)
class RunRow:
    """One stored run."""

    id: int
    slug: str
    params: dict[str, Any]
    cast: str
    seed: int
    snapshot: dict[str, Any]
    summary: dict[str, Any]
    created: float


@dataclass(frozen=True)
class CardVerdictRow:
    """The latest verdict of one user on one card."""

    card_id: str
    verdict: str
    note: str
    outcome_hash: str
    created: float


@dataclass(frozen=True)
class AssumptionVerdictRow:
    """The latest answer of one user to one choice (``index`` into the spec's assumptions)."""

    index: int
    text: str
    verdict: str
    note: str
    created: float


class Db:
    """One connection, for one request. Every method that reads user data takes the user."""

    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], float]) -> None:
        self._conn = conn
        self._clock = clock

    # ---------------------------------------------------------------- users

    def add_user(self, name: str, token: str) -> User:
        """Add a user with the SHA-256 of ``token``; ``ValueError`` if the name is taken."""
        name = name.strip()
        if not name:
            raise ValueError("a user needs a name")
        created = self._clock()
        try:
            cur = self._conn.execute(
                "INSERT INTO users (name, token_sha256, created) VALUES (?, ?, ?)",
                (name, token_hash(token), created),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"there is already a user called {name!r}") from e
        return User(id=int(cur.lastrowid or 0), name=name, created=created)

    def user_by_token(self, token: str) -> User | None:
        """The user whose token is ``token``, or ``None``."""
        row = self._conn.execute(
            "SELECT id, name, created FROM users WHERE token_sha256 = ?", (token_hash(token),)
        ).fetchone()
        return None if row is None else User(row["id"], row["name"], row["created"])

    def user_by_name(self, name: str) -> User | None:
        """The user called ``name``, or ``None``."""
        row = self._conn.execute(
            "SELECT id, name, created FROM users WHERE name = ?", (name,)
        ).fetchone()
        return None if row is None else User(row["id"], row["name"], row["created"])

    # ---------------------------------------------------------------- runs

    def add_runs(
        self,
        user: User,
        slug: str,
        params: dict[str, Any],
        cast: str,
        runs: list[tuple[int, dict[str, Any], dict[str, Any]]],
    ) -> list[int]:
        """Store ``(seed, snapshot, summary)`` runs of one request in one transaction; their ids."""
        created = self._clock()
        ids = []
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for seed, snapshot, summary in runs:
                cur = self._conn.execute(
                    "INSERT INTO runs (user_id, slug, params, cast_name, seed, snapshot, summary, "
                    "created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user.id,
                        slug,
                        json.dumps(params, sort_keys=True),
                        cast,
                        seed,
                        json.dumps(snapshot, sort_keys=True),
                        json.dumps(summary, sort_keys=True),
                        created,
                    ),
                )
                ids.append(int(cur.lastrowid or 0))
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return ids

    @staticmethod
    def _run(row: sqlite3.Row, with_snapshot: bool) -> RunRow:
        return RunRow(
            id=row["id"],
            slug=row["slug"],
            params=json.loads(row["params"]),
            cast=row["cast_name"],
            seed=row["seed"],
            snapshot=json.loads(row["snapshot"]) if with_snapshot else {},
            summary=json.loads(row["summary"]),
            created=row["created"],
        )

    def runs(self, user: User, slug: str | None = None, limit: int = 200) -> list[RunRow]:
        """This user's runs, newest first (optionally of one entry), without their snapshots."""
        sql = (
            "SELECT id, slug, params, cast_name, seed, summary, created FROM runs WHERE user_id = ?"
        )
        args: list[Any] = [user.id]
        if slug is not None:
            sql += " AND slug = ?"
            args.append(slug)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [self._run(r, False) for r in self._conn.execute(sql, args).fetchall()]

    def run(self, user: User, run_id: int) -> RunRow | None:
        """One of this user's runs, with its snapshot; ``None`` if it is not this user's."""
        row = self._conn.execute(
            "SELECT * FROM runs WHERE id = ? AND user_id = ?", (run_id, user.id)
        ).fetchone()
        return None if row is None else self._run(row, True)

    # ---------------------------------------------------------------- verdicts

    def add_card_verdict(
        self,
        user: User,
        slug: str,
        card_id: str,
        verdict: str,
        note: str,
        outcome_hash: str,
        spec_title: str = "",
    ) -> None:
        """Record a card verdict as given (the latest per user and card counts)."""
        self._conn.execute(
            "INSERT INTO card_verdicts (user_id, slug, card_id, verdict, note, outcome_hash, "
            "created, spec_title) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user.id, slug, card_id, verdict, note, outcome_hash, self._clock(), spec_title),
        )

    def add_assumption_verdict(
        self,
        user: User,
        slug: str,
        index: int,
        text: str,
        verdict: str,
        note: str,
        spec_title: str = "",
    ) -> None:
        """Record an answer to a choice as given (the latest per user and index counts)."""
        self._conn.execute(
            "INSERT INTO assumption_verdicts (user_id, slug, idx, text, verdict, note, created, "
            "spec_title) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user.id, slug, index, text, verdict, note, self._clock(), spec_title),
        )

    def spec_title(self, user: User, slug: str) -> str | None:
        """The spec title of this user's most recent verdict on one entry, or ``None``."""
        row = self._conn.execute(
            "SELECT spec_title, created, kind, id FROM ("
            "SELECT spec_title, created, 0 AS kind, id FROM card_verdicts "
            "WHERE user_id = ? AND slug = ? AND spec_title != '' UNION ALL "
            "SELECT spec_title, created, 1 AS kind, id FROM assumption_verdicts "
            "WHERE user_id = ? AND slug = ? AND spec_title != '') "
            "ORDER BY created DESC, kind DESC, id DESC LIMIT 1",
            (user.id, slug, user.id, slug),
        ).fetchone()
        return None if row is None else str(row["spec_title"])

    def card_verdicts(self, user: User, slug: str) -> list[CardVerdictRow]:
        """This user's latest verdict on each card of one entry, by card id."""
        rows = self._conn.execute(
            "SELECT card_id, verdict, note, outcome_hash, created FROM card_verdicts "
            "WHERE id IN (SELECT MAX(id) FROM card_verdicts WHERE user_id = ? AND slug = ? "
            "GROUP BY card_id) ORDER BY card_id",
            (user.id, slug),
        ).fetchall()
        return [CardVerdictRow(*tuple(r)) for r in rows]

    def assumption_verdicts(self, user: User, slug: str) -> list[AssumptionVerdictRow]:
        """This user's latest answer to each choice of one entry, by index."""
        rows = self._conn.execute(
            "SELECT idx, text, verdict, note, created FROM assumption_verdicts "
            "WHERE id IN (SELECT MAX(id) FROM assumption_verdicts WHERE user_id = ? AND slug = ? "
            "GROUP BY idx) ORDER BY idx",
            (user.id, slug),
        ).fetchall()
        return [AssumptionVerdictRow(*tuple(r)) for r in rows]


class Store:
    """The service's database file; ``open()`` gives one connection (a ``Db``) per request."""

    def __init__(
        self,
        path: Path,
        clock: Callable[[], float] = time.time,
        token_source: Callable[[], str] = new_token,
    ) -> None:
        self.path = Path(path)
        self.clock = clock
        self._token_source = token_source
        self.migrated = init_db(self.path, clock)

    @contextmanager
    def open(self) -> Iterator[Db]:
        """A connection for one request, closed afterwards."""
        conn = _connect(self.path)
        try:
            yield Db(conn, self.clock)
        finally:
            conn.close()

    def add_user(self, name: str) -> tuple[User, str]:
        """Add a user with a new token; returns the user and the token (shown once, never kept)."""
        token = self._token_source()
        with self.open() as db:
            return db.add_user(name, token), token
