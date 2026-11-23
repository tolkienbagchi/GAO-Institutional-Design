"""The WSGI application of the library service: JSON API routes and the service's pages.

``make_app(library, store)`` answers every request from the library (the only source of
environments and policies) and the store (one connection per request). Every ``/api/...`` route
needs ``Authorization: Bearer <token>``; ``/health`` needs none, and page routes answer with a
page that carries no user data whether or not a token is sent (a page fetches the user's data
from ``/api/...`` itself). Errors are ``{"error": code, "message": plain words}`` and never carry
a stack trace. Bodies over ``MAX_BODY`` bytes are refused unread. Nothing a client sends is ever
imported or run: a client names an entry by slug and a cast by name.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from wsgiref.types import StartResponse, WSGIApplication, WSGIEnvironment

from pydantic import ValidationError

from gao_idl.review.bundle import Bundle, make_bundle
from gao_idl.review.draw import timeline_svg
from gao_idl.review.page import render_run
from gao_idl.review.verdicts import AssumptionVerdict, CardVerdict, Verdicts
from gao_idl.service import pages
from gao_idl.service.library import Entry, Library, resolve_params
from gao_idl.service.runs import (
    MAX_SEEDS,
    Budget,
    CapExceeded,
    RunError,
    play_cast,
    replay_run,
)
from gao_idl.service.store import Db, Store, User

logger = logging.getLogger("gao_idl.service")

MAX_BODY = 1_000_000  # bytes: "1 MB", read as 10**6
MAX_SEED = 2**32 - 1
REASONS = {
    200: "OK",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    413: "Content Too Large",
    422: "Unprocessable Content",
    500: "Internal Server Error",
}
_SLUG = r"(?P<slug>[^/]+)"


class HttpError(Exception):
    """An answer other than 200: status, a short code and a message in plain words."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


@dataclass
class _Request:
    method: str
    path: str
    environ: WSGIEnvironment
    match: dict[str, str]
    user: User | None = None
    db: Db | None = None


@dataclass
class _Response:
    status: int
    body: bytes
    content_type: str


def _json(status: int, data: Any) -> _Response:
    body = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return _Response(status, body.encode("utf-8"), "application/json; charset=utf-8")


def _html(status: int, text: str) -> _Response:
    return _Response(status, text.encode("utf-8"), "text/html; charset=utf-8")


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in JSON")


def _short_errors(e: ValidationError) -> str:
    """A validation error in plain words, without echoing the input."""
    parts = []
    for err in e.errors()[:5]:
        where = ".".join(str(x) for x in err["loc"]) or "body"
        parts.append(f"{where}: {err['msg']}")
    return "; ".join(parts)


def user_verdicts(db: Db, user: User, slug: str, spec_title: str) -> Verdicts:
    """This user's latest verdicts on one entry, in the M10 ``Verdicts`` format."""
    return Verdicts(
        bundle_version=1,
        spec_title=spec_title,
        reviewer=user.name,
        cards=[
            CardVerdict(
                id=v.card_id,
                verdict=v.verdict,
                note=v.note,
                outcome_hash=v.outcome_hash,
            )
            for v in db.card_verdicts(user, slug)
        ],
        assumptions=[
            AssumptionVerdict(
                index=v.index,
                text=v.text,
                verdict=v.verdict,
                note=v.note,
            )
            for v in db.assumption_verdicts(user, slug)
        ],
    )


def overlay(bundle: Bundle, verdicts: Verdicts) -> Bundle:
    """A copy of ``bundle`` with one person's verdicts shown on card status and choices.

    A card verdict shows only if its ``outcome_hash`` is the card's hash now (and a confirmation
    only if the card is not stale); an agreed choice shows as confirmed only if its text matches.
    The counts in ``status`` follow. Nothing else changes.
    """
    latest = {v.id: v for v in verdicts.cards}
    views = []
    for view in bundle.cards:
        v = latest.get(view.card.id)
        if (
            v is not None
            and v.outcome_hash == view.outcome_hash
            and not (view.stale and v.verdict == "confirmed")
        ):
            card = view.card.model_copy(
                update={"status": v.verdict, "note": v.note.strip(), "reviewer": verdicts.reviewer}
            )
            view = view.model_copy(update={"card": card})
        views.append(view)
    assumptions = list(bundle.spec.assumptions)
    for a in verdicts.assumptions:
        if (
            a.verdict == "agreed"
            and 0 <= a.index < len(assumptions)
            and assumptions[a.index].text == a.text
        ):
            assumptions[a.index] = assumptions[a.index].model_copy(update={"confirmed": True})
    spec = bundle.spec.model_copy(update={"assumptions": assumptions})
    counts = {
        s: sum(v.card.status == s for v in views) for s in ("proposed", "confirmed", "rejected")
    }
    status = bundle.status.model_copy(
        update={"cards": counts, "assumptions_open": sum(not a.confirmed for a in assumptions)}
    )
    return bundle.model_copy(update={"cards": views, "spec": spec, "status": status})


class _Service:
    def __init__(self, library: Library, store: Store, clock: Callable[[], float]) -> None:
        self.library = library
        self.store = store
        self.clock = clock
        self._bundles: dict[str, Bundle] = {}
        self._pages: dict[str, str] = {}
        self._lock = threading.RLock()
        self.routes: list[tuple[str, re.Pattern[str], Callable[[_Request], _Response], str]] = []
        for method, pattern, handler, kind in [
            ("GET", r"/health", self.health, "open"),
            ("GET", r"/api/environments", self.environments, "api"),
            ("GET", rf"/api/environments/{_SLUG}", self.environment, "api"),
            ("GET", rf"/api/environments/{_SLUG}/bundle", self.bundle_route, "api"),
            ("GET", rf"/api/environments/{_SLUG}/verdicts", self.get_verdicts, "api"),
            ("POST", rf"/api/environments/{_SLUG}/verdicts", self.post_verdicts, "api"),
            ("POST", rf"/api/environments/{_SLUG}/runs", self.post_runs, "api"),
            ("GET", r"/api/runs", self.list_runs, "api"),
            ("GET", r"/api/runs/(?P<run>[^/]+)", self.get_run, "api"),
            ("GET", r"/", self.index, "page"),
            ("GET", r"/login", self.login, "page"),
            ("GET", rf"/environments/{_SLUG}/review", self.review, "page"),
            ("GET", rf"/environments/{_SLUG}/run", self.run_page, "page"),
        ]:
            self.routes.append((method, re.compile(pattern), handler, kind))

    # ------------------------------------------------------------ plumbing

    def entry(self, slug: str) -> Entry:
        try:
            return self.library.entry(slug)
        except KeyError:
            raise HttpError(404, "not_found", "There is no environment with that name.") from None

    def bundle(self, entry: Entry) -> Bundle:
        """The entry's bundle at its spec's parameters, built once and kept in memory."""
        with self._lock:
            if entry.slug not in self._bundles:
                spec = self.library.spec_of(entry)
                self._bundles[entry.slug] = make_bundle(spec, self.library.cards_of(entry))
            return self._bundles[entry.slug]

    def _page(self, key: str, make: Callable[[], str]) -> str:
        with self._lock:
            if key not in self._pages:
                self._pages[key] = make()
            return self._pages[key]

    @staticmethod
    def body(req: _Request) -> Any:
        environ = req.environ
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            raise HttpError(
                422, "bad_request", "The Content-Length header is not a number."
            ) from None
        if length > MAX_BODY:
            raise HttpError(413, "too_large", f"Request bodies are limited to {MAX_BODY} bytes.")
        raw = environ["wsgi.input"].read(length) if length > 0 else b""
        try:
            return json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError):
            raise HttpError(422, "bad_json", "The request body is not valid JSON.") from None

    # ------------------------------------------------------------ open and page routes

    def health(self, req: _Request) -> _Response:
        return _json(200, {"status": "ok"})

    def index(self, req: _Request) -> _Response:
        return _html(200, self._page("index", lambda: pages.index_page(self.library)))

    def login(self, req: _Request) -> _Response:
        return _html(200, self._page("login", pages.login_page))

    def review(self, req: _Request) -> _Response:
        entry = self.entry(req.match["slug"])
        if req.user is None or req.db is None:
            return _html(
                200,
                self._page(
                    f"review:{entry.slug}",
                    lambda: pages.review_page(self.bundle(entry), entry.slug),
                ),
            )
        spec = self.library.spec_of(entry)
        mine = overlay(self.bundle(entry), user_verdicts(req.db, req.user, entry.slug, spec.title))
        return _html(200, pages.review_page(mine, entry.slug))

    def run_page(self, req: _Request) -> _Response:
        entry = self.entry(req.match["slug"])
        spec = self.library.spec_of(entry)
        return _html(200, self._page(f"run:{entry.slug}", lambda: pages.run_page(entry, spec)))

    # ------------------------------------------------------------ environments

    def environments(self, req: _Request) -> _Response:
        out = []
        for entry in self.library.entries:
            spec = self.library.spec_of(entry)
            out.append({"slug": entry.slug, "title": entry.title, "summary": spec.summary})
        return _json(200, {"environments": out})

    def environment(self, req: _Request) -> _Response:
        entry = self.entry(req.match["slug"])
        spec = self.library.spec_of(entry)
        cards = self.library.cards_of(entry)
        counts = {
            s: sum(c.status == s for c in cards) for s in ("proposed", "confirmed", "rejected")
        }
        return _json(
            200,
            {
                "slug": entry.slug,
                "title": entry.title,
                "summary": spec.summary,
                "params": [
                    {**p.model_dump(mode="json"), "value": spec.params.get(p.name)}
                    for p in entry.params
                ],
                "casts": [
                    {
                        "name": c.name,
                        "label": c.label,
                        "description": c.description,
                        "roles": sorted(c.policies),
                    }
                    for c in entry.casts
                ],
                "cards": {"total": len(cards), **counts},
                "assumptions": len(spec.assumptions),
            },
        )

    def bundle_route(self, req: _Request) -> _Response:
        assert req.user is not None and req.db is not None
        entry = self.entry(req.match["slug"])
        spec = self.library.spec_of(entry)
        mine = overlay(self.bundle(entry), user_verdicts(req.db, req.user, entry.slug, spec.title))
        return _json(200, mine.model_dump(mode="json"))

    # ------------------------------------------------------------ verdicts

    def get_verdicts(self, req: _Request) -> _Response:
        assert req.user is not None and req.db is not None
        entry = self.entry(req.match["slug"])
        spec = self.library.spec_of(entry)
        verdicts = user_verdicts(req.db, req.user, entry.slug, spec.title)
        return _json(200, verdicts.model_dump(mode="json"))

    def post_verdicts(self, req: _Request) -> _Response:
        assert req.user is not None and req.db is not None
        entry = self.entry(req.match["slug"])
        spec = self.library.spec_of(entry)
        data = self.body(req)
        if not isinstance(data, dict):
            raise HttpError(422, "bad_verdict", "Send one verdict or a whole verdicts file.")
        model: type[CardVerdict | AssumptionVerdict | Verdicts]
        if "bundle_version" in data:
            model = Verdicts
        elif "index" in data:
            model = AssumptionVerdict
        else:
            model = CardVerdict
        try:
            parsed = model.model_validate(data)
        except ValidationError as e:
            raise HttpError(422, "bad_verdict", f"Not a verdict: {_short_errors(e)}") from None
        if isinstance(parsed, Verdicts):
            if parsed.spec_title != spec.title:
                raise HttpError(422, "wrong_spec", "These verdicts are for another environment.")
            cards, choices = parsed.cards, parsed.assumptions
        elif isinstance(parsed, CardVerdict):
            cards, choices = [parsed], []
        else:
            cards, choices = [], [parsed]
        views = {v.card.id: v for v in self.bundle(entry).cards}
        problems: list[str] = []
        ids = [c.id for c in cards]
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            problems.append(f"card {dup} has more than one verdict")
        for c in cards:
            if c.id not in views:
                problems.append(f"there is no card {c.id}")
            elif c.verdict == "rejected" and not c.note.strip():
                problems.append(f"card {c.id}: a No needs a note")
        indexes = [a.index for a in choices]
        for twice in sorted({i for i in indexes if indexes.count(i) > 1}):
            problems.append(f"choice {twice} has more than one answer")
        for a in choices:
            if not 0 <= a.index < len(spec.assumptions) or spec.assumptions[a.index].text != a.text:
                problems.append(f"there is no choice {a.index} with that text")
            elif a.verdict == "disputed" and not a.note.strip():
                problems.append(f"choice {a.index}: Disagree needs a note")
        if problems:
            raise HttpError(422, "bad_verdict", "Nothing was stored: " + "; ".join(problems) + ".")
        stale = sorted(
            c.id
            for c in cards
            if c.outcome_hash != views[c.id].outcome_hash
            or (views[c.id].stale and c.verdict == "confirmed")
        )
        db, user = req.db, req.user
        with db.transaction():  # a verdicts file is stored whole or not at all
            for c in cards:
                if c.id not in stale:
                    db.add_card_verdict(
                        user, entry.slug, c.id, c.verdict, c.note, c.outcome_hash, spec.title
                    )
            for a in choices:
                db.add_assumption_verdict(
                    user, entry.slug, a.index, a.text, a.verdict, a.note, spec.title
                )
        if stale:
            raise HttpError(
                409,
                "stale",
                "What these cards do has changed since that page was made, so these verdicts "
                f"were not stored: {', '.join(stale)}. Reload the review page.",
                stale=stale,
                stored={"cards": len(cards) - len(stale), "assumptions": len(choices)},
            )
        return _json(200, {"stored": {"cards": len(cards), "assumptions": len(choices)}})

    # ------------------------------------------------------------ runs

    def post_runs(self, req: _Request) -> _Response:
        assert req.user is not None and req.db is not None
        entry = self.entry(req.match["slug"])
        spec = self.library.spec_of(entry)
        data = self.body(req)
        if not isinstance(data, dict):
            raise HttpError(
                422, "bad_run", 'Send {"params": {...}, "cast": "...", "seeds": [...]}.'
            )
        unknown = sorted(str(k) for k in data if k not in ("params", "cast", "seeds"))
        if unknown:
            raise HttpError(
                422, "bad_run", f"Unknown field {unknown[0][:60]!r}: only params, cast and seeds."
            )
        casts = {c.name: c for c in entry.casts}
        cast_name = data.get("cast")
        if not isinstance(cast_name, str) or cast_name not in casts:
            raise HttpError(
                422, "bad_run", "Field 'cast' must name one of this environment's casts."
            )
        seeds = data.get("seeds")
        if (
            not isinstance(seeds, list)
            or not seeds
            or not all(
                isinstance(s, int) and not isinstance(s, bool) and 0 <= s <= MAX_SEED for s in seeds
            )
        ):
            raise HttpError(
                422,
                "bad_run",
                f"Field 'seeds' must be a list of whole numbers from 0 to {MAX_SEED}.",
            )
        if len(seeds) > MAX_SEEDS:
            raise HttpError(422, "cap", f"Field 'seeds': at most {MAX_SEEDS} seeds per request.")
        try:
            params = resolve_params(entry, spec, data.get("params", {}))
        except ValueError as e:
            raise HttpError(422, "bad_params", str(e)) from None
        try:
            played = play_cast(
                self.library, spec, params, casts[cast_name], seeds, Budget(self.clock)
            )
        except CapExceeded as e:
            raise HttpError(422, "cap", f"Nothing was stored: {e}.") from None
        except RunError as e:
            raise HttpError(422, "cannot_run", str(e)) from None
        ids = req.db.add_runs(req.user, entry.slug, params, cast_name, played)
        return _json(
            200,
            {
                "runs": [
                    {"id": i, "seed": seed, "summary": summary}
                    for i, (seed, _snap, summary) in zip(ids, played, strict=True)
                ]
            },
        )

    def _title(self, slug: str, cast: str, seed: int) -> str:
        try:
            entry = self.library.entry(slug)
        except KeyError:
            return f"{slug}: {cast}, seed {seed}"
        label = next((c.label for c in entry.casts if c.name == cast), cast)
        return f"{entry.title}: {label}, seed {seed}"

    def list_runs(self, req: _Request) -> _Response:
        assert req.user is not None and req.db is not None
        return _json(
            200,
            {
                "runs": [
                    {
                        "id": r.id,
                        "slug": r.slug,
                        "title": self._title(r.slug, r.cast, r.seed),
                        "cast": r.cast,
                        "seed": r.seed,
                        "params": r.params,
                        "summary": r.summary,
                        "created": r.created,
                    }
                    for r in req.db.runs(req.user)
                ]
            },
        )

    def get_run(self, req: _Request) -> _Response:
        assert req.user is not None and req.db is not None
        text = req.match["run"]
        row = req.db.run(req.user, int(text)) if re.fullmatch(r"[0-9]{1,18}", text) else None
        if row is None:
            raise HttpError(404, "not_found", "You have no run with that number.")
        try:
            entry = self.library.entry(row.slug)
        except KeyError:
            raise HttpError(
                404, "not_found", "That run's environment is no longer in the library."
            ) from None
        spec = self.library.spec_of(entry)
        try:
            story = replay_run(spec, row.params, row.seed, row.snapshot["log"])
        except Exception:  # an environment that changed since: say so, without a trace
            logger.warning("run %d of %s could not be replayed", row.id, row.slug)
            raise HttpError(
                409, "cannot_replay", "This run cannot be replayed by the current environment."
            ) from None
        title = self._title(row.slug, row.cast, row.seed)
        return _json(
            200,
            {
                "id": row.id,
                "slug": row.slug,
                "title": title,
                "cast": row.cast,
                "seed": row.seed,
                "params": row.params,
                "summary": row.summary,
                "created": row.created,
                "story": story.model_dump(mode="json"),
                "html": render_run(story, title),
                "timeline_svg": timeline_svg(story, title),
            },
        )

    # ------------------------------------------------------------ dispatch

    def authenticate(self, environ: WSGIEnvironment, db: Db) -> User | None:
        """The user of the bearer token; ``None`` if no token was sent; 401 if a wrong one was."""
        header = environ.get("HTTP_AUTHORIZATION")
        if header is None:
            return None
        scheme, _, token = header.partition(" ")
        token = token.strip()
        user = db.user_by_token(token) if scheme.lower() == "bearer" and token else None
        if user is None:
            raise HttpError(401, "unauthorized", "The token is missing or not valid.")
        return user

    def handle(self, environ: WSGIEnvironment) -> tuple[_Response, User | None]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "") or "/")
        found = None
        path_known = False
        for route_method, pattern, handler, kind in self.routes:
            m = pattern.fullmatch(path)
            if m is not None:
                path_known = True
                if route_method == method:
                    found = (handler, kind, m.groupdict())
                    break
        is_api = path == "/api" or path.startswith("/api/")
        with self.store.open() as db:
            req = _Request(method, path, environ, found[2] if found else {}, None, db)
            if is_api or (found is not None and found[1] == "page"):
                req.user = self.authenticate(environ, db)
                if is_api and req.user is None:
                    raise HttpError(401, "unauthorized", "The token is missing or not valid.")
            if found is None:
                what = "That method is not used on this path." if path_known else "No such page."
                raise HttpError(404, "not_found", what)
            if method == "POST":
                origin = environ.get("HTTP_ORIGIN")
                if origin is not None and urlsplit(origin).netloc != environ.get("HTTP_HOST"):
                    raise HttpError(403, "forbidden", "Requests from other sites are refused.")
            return found[0](req), req.user


def make_app(
    library: Library, store: Store, *, clock: Callable[[], float] = time.monotonic
) -> WSGIApplication:
    """The WSGI application serving ``library`` with ``store``; ``clock`` times the run caps."""
    service = _Service(library, store, clock)

    def app(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET"))
        path = str(environ.get("PATH_INFO", "") or "/")
        user: User | None = None
        try:
            response, user = service.handle(environ)
        except HttpError as e:
            response = _json(e.status, {"error": e.code, "message": e.message, **e.extra})
        except Exception:
            # the trace goes to the server's log only (it carries no token: tokens are never
            # passed to anything that could raise with them); the client gets plain words
            logger.exception("internal error on %s %s", method, path[:200])
            response = _json(
                500, {"error": "internal", "message": "Something went wrong on the server."}
            )
        logger.info(
            "%s %s %d user=%s",
            method,
            path[:200],
            response.status,
            "-" if user is None else user.id,
        )
        headers = [
            ("Content-Type", response.content_type),
            ("Content-Length", str(len(response.body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            ("Content-Security-Policy", "frame-ancestors 'none'"),
        ]
        start_response(f"{response.status} {REASONS.get(response.status, 'Error')}", headers)
        return [response.body]

    return app
