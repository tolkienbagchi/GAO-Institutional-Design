"""Calling the service's WSGI application directly, without sockets."""

import io
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from wsgiref.types import WSGIApplication
from wsgiref.util import setup_testing_defaults

from gao_idl.service import load_library
from gao_idl.service.library import Library
from gao_idl.service.store import Store

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "examples" / "library" / "library.json"


@dataclass
class Answer:
    """What the application answered."""

    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")


def call(
    app: WSGIApplication,
    method: str,
    path: str,
    token: str | None = None,
    body: Any = None,
    raw: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Answer:
    """One request; ``body`` is sent as JSON, ``raw`` as given."""
    data = raw if raw is not None else b"" if body is None else json.dumps(body).encode()
    environ: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "wsgi.input": io.BytesIO(data),
        "CONTENT_LENGTH": str(len(data)) if data else "",
        "HTTP_HOST": "testserver",
    }
    if data:
        environ["CONTENT_TYPE"] = "application/json"
    if token is not None:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    for key, value in (headers or {}).items():
        environ[key] = value
    setup_testing_defaults(environ)
    seen: dict[str, Any] = {}

    def start_response(status: str, response_headers: list[tuple[str, str]]) -> Any:
        seen["status"] = int(status.split()[0])
        seen["headers"] = dict(response_headers)
        return lambda chunk: None

    chunks = app(environ, start_response)
    return Answer(seen["status"], seen["headers"], b"".join(chunks))


class FakeClock:
    """A clock that moves ``step`` seconds each time it is read."""

    def __init__(self, step: float = 0.0, start: float = 1000.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def library() -> Library:
    return load_library(LIBRARY)


def store(path: Path) -> Store:
    ticks = itertools.count(1_700_000_000.0)
    tokens = (f"tok{i:04d}-{'x' * 40}" for i in itertools.count())
    return Store(path, clock=lambda: next(ticks), token_source=lambda: next(tokens))
