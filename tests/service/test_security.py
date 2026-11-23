"""The service's safety rules: nothing a client names is imported, users never see each other's
rows, caps hold, hostile text stays data, and pages reach no other host."""

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from wsgiref.types import WSGIApplication

import pytest

from gao_idl.service import load_library, resolve_params
from gao_idl.service.app import make_app
from gao_idl.service.library import Library
from gao_idl.service.runs import MAX_DECISIONS, Budget, CapExceeded, play_cast
from gao_idl.service.store import Store
from tests.service.helpers import LIBRARY, FakeClock, call, library, store

SRC = Path(__file__).resolve().parents[2] / "src" / "gao_idl" / "service"
HOSTILE = 'Robert\'); DROP TABLE users;-- <script>alert("x")</script> & "q" \'q\''
SENTINEL = "gao_sentinel_m11"


@pytest.fixture(scope="module")
def lib() -> Library:
    return library()


@pytest.fixture(scope="module")
def world(lib: Library, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    s: Store = store(tmp_path_factory.mktemp("db") / "service.db")
    _, a = s.add_user("ann-m11")
    _, b = s.add_user("bo-m11")
    yield {"app": make_app(lib, s), "a": a, "b": b, "store": s}


def _app(world: dict[str, Any]) -> WSGIApplication:
    return world["app"]  # type: ignore[no-any-return]


# ------------------------------------------------------------------ W7: nothing a client names


@pytest.fixture
def sentinel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A module on ``sys.path`` that leaves a file behind if it is ever imported."""
    marker = tmp_path / "imported"
    (tmp_path / f"{SENTINEL}.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n"
        "def boom(*a, **k):\n    raise SystemExit('ran')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(SENTINEL, None)
    return marker


def test_nothing_a_client_names_is_imported(world: dict[str, Any], sentinel: Path) -> None:
    app, token = _app(world), world["a"]
    ref = f"{SENTINEL}:boom"
    runs = "/api/environments/auction/runs"
    requests: list[tuple[str, str, Any, int]] = [
        ("POST", runs, {"env_ref": ref, "cast": "truthful", "seeds": [1]}, 422),
        ("POST", runs, {"cast": ref, "seeds": [1]}, 422),
        ("POST", runs, {"cast": "truthful", "seeds": [1], "params": {ref: 1}}, 422),
        ("POST", runs, {"cast": "truthful", "seeds": [1], "params": {"rule": ref}}, 422),
        ("POST", runs, {"cast": "truthful", "seeds": [1], "params": {"env_ref": ref}}, 422),
        ("POST", f"/api/environments/{ref}/runs", {"cast": "truthful", "seeds": [1]}, 404),
        ("GET", f"/api/environments/{ref}", None, 404),
        ("GET", f"/api/environments/{ref}/bundle", None, 404),
        ("GET", f"/environments/{ref}/review", None, 404),
        (
            "POST",
            "/api/environments/auction/verdicts",
            {"id": ref, "verdict": "confirmed", "outcome_hash": ref},
            422,
        ),
    ]
    for method, path, body, status in requests:
        answer = call(app, method, path, token, body=body)
        assert answer.status == status, (path, body, answer.text)
    assert SENTINEL not in sys.modules
    assert not sentinel.exists()


# ------------------------------------------------------------------ users never mix


def test_one_user_cannot_read_or_change_anothers_runs_or_verdicts(world: dict[str, Any]) -> None:
    app, a, b = _app(world), world["a"], world["b"]
    made = call(
        app, "POST", "/api/environments/auction/runs", a, body={"cast": "truthful", "seeds": [1, 2]}
    ).json["runs"]
    a_ids = [r["id"] for r in made]
    view = call(app, "GET", "/api/environments/auction/bundle", a).json["cards"][0]
    verdict = {
        "id": view["card"]["id"],
        "verdict": "rejected",
        "note": "alice's note",
        "outcome_hash": view["outcome_hash"],
    }
    assert call(app, "POST", "/api/environments/auction/verdicts", a, body=verdict).status == 200

    # bob sees none of it, by listing or by guessing ids around alice's
    assert [r for r in call(app, "GET", "/api/runs", b).json["runs"] if r["id"] in a_ids] == []
    for guess in range(max(a_ids) + 3):
        answer = call(app, "GET", f"/api/runs/{guess}", b)
        assert answer.status == 404, guess
        assert "ann-m11" not in answer.text
    assert call(app, "GET", "/api/environments/auction/verdicts", b).json["cards"] == []
    bob_bundle = call(app, "GET", "/api/environments/auction/bundle", b).json
    assert bob_bundle["cards"][0]["card"]["status"] == "proposed"
    assert "alice's note" not in json.dumps(bob_bundle)
    assert "alice's note" not in call(app, "GET", "/environments/auction/review", b).text

    # bob's verdict on the same card changes only bob's
    mine = {**verdict, "verdict": "confirmed", "note": ""}
    assert call(app, "POST", "/api/environments/auction/verdicts", b, body=mine).status == 200
    alice = call(app, "GET", "/api/environments/auction/verdicts", a).json["cards"]
    assert [(c["verdict"], c["note"]) for c in alice] == [("rejected", "alice's note")]
    for run_id in a_ids:
        assert call(app, "GET", f"/api/runs/{run_id}", a).status == 200


# ------------------------------------------------------------------ the three run caps


def _pd_library(tmp_path: Path) -> Library:
    """The shipped library, but the PD's rounds may go up to 1000 (4 decisions a round)."""
    data = json.loads(LIBRARY.read_text())
    for entry in data["entries"]:
        entry["spec"] = str((LIBRARY.parent / entry["spec"]).resolve())
        entry["cards"] = str((LIBRARY.parent / entry["cards"]).resolve())
        if entry["slug"] == "prisoners-dilemma":
            entry["params"][0]["maximum"] = 1000
    path = tmp_path / "library.json"
    path.write_text(json.dumps(data))
    return load_library(path)


def test_the_decision_cap(tmp_path: Path) -> None:
    s = store(tmp_path / "s.db")
    _, token = s.add_user("cap")
    app = make_app(_pd_library(tmp_path), s)
    path = "/api/environments/prisoners-dilemma/runs"
    ok = {"cast": "defectors", "seeds": [1], "params": {"rounds": 3}}
    assert call(app, "POST", path, token, body=ok).status == 200
    over = {"cast": "defectors", "seeds": [1, 2], "params": {"rounds": MAX_DECISIONS // 4 + 1}}
    answer = call(app, "POST", path, token, body=over)
    assert answer.status == 422 and answer.json["error"] == "cap"
    assert "2000 decisions" in answer.json["message"]
    assert len(call(app, "GET", "/api/runs", token).json["runs"]) == 1  # only the first request


def test_the_decision_cap_is_exact(lib: Library) -> None:
    entry = lib.entry("prisoners-dilemma")
    spec = lib.spec_of(entry)
    params = resolve_params(entry, spec, {"rounds": 5})  # 20 decisions
    exact = Budget(FakeClock(), max_decisions=20)
    assert len(play_cast(lib, spec, params, entry.casts[0], [1], exact)) == 1
    with pytest.raises(CapExceeded, match="more than 19 decisions"):
        play_cast(lib, spec, params, entry.casts[0], [1], Budget(FakeClock(), max_decisions=19))


def test_the_time_cap(tmp_path: Path, lib: Library) -> None:
    s = store(tmp_path / "s.db")
    _, token = s.add_user("slow")
    app = make_app(lib, s, clock=FakeClock(step=1.0))  # each reading of the clock is a second
    body = {"cast": "cooperators", "seeds": [1, 2, 3], "params": {"rounds": 5}}
    answer = call(app, "POST", "/api/environments/prisoners-dilemma/runs", token, body=body)
    assert answer.status == 422 and answer.json["error"] == "cap"
    assert "10 seconds" in answer.json["message"]
    assert call(app, "GET", "/api/runs", token).json["runs"] == []
    quick = make_app(lib, s, clock=FakeClock(step=0.0))
    answer = call(quick, "POST", "/api/environments/prisoners-dilemma/runs", token, body=body)
    assert answer.status == 200 and len(answer.json["runs"]) == 3


def test_the_seed_cap(world: dict[str, Any]) -> None:
    app, token = _app(world), world["a"]
    body = {"cast": "truthful", "seeds": list(range(11))}
    answer = call(app, "POST", "/api/environments/auction/runs", token, body=body)
    assert answer.status == 422 and answer.json["error"] == "cap"
    body["seeds"] = list(range(10))
    assert call(app, "POST", "/api/environments/auction/runs", token, body=body).status == 200


# ------------------------------------------------------------------ untrusted text


def test_hostile_names_and_notes_stay_data(tmp_path: Path, lib: Library) -> None:
    s = store(tmp_path / "s.db")
    _, token = s.add_user(HOSTILE)
    app = make_app(lib, s)
    bundle = call(app, "GET", "/api/environments/auction/bundle", token).json
    view = bundle["cards"][0]
    verdict = {
        "id": view["card"]["id"],
        "verdict": "rejected",
        "note": HOSTILE,
        "outcome_hash": view["outcome_hash"],
    }
    assert (
        call(app, "POST", "/api/environments/auction/verdicts", token, body=verdict).status == 200
    )
    text = bundle["spec"]["assumptions"][0]["text"]
    choice = {"index": 0, "text": text, "verdict": "disputed", "note": HOSTILE}
    assert call(app, "POST", "/api/environments/auction/verdicts", token, body=choice).status == 200

    mine = call(app, "GET", "/api/environments/auction/verdicts", token).json
    assert mine["reviewer"] == HOSTILE
    assert mine["cards"][0]["note"] == HOSTILE and mine["assumptions"][0]["note"] == HOSTILE
    overlaid = call(app, "GET", "/api/environments/auction/bundle", token).json
    assert overlaid["cards"][0]["card"]["note"] == HOSTILE.strip()
    assert overlaid["cards"][0]["card"]["reviewer"] == HOSTILE
    with s.open() as db:  # the tables are all still there
        assert db.user_by_name(HOSTILE) is not None

    for path in ("/environments/auction/review", "/", "/environments/auction/run", "/login"):
        page = call(app, "GET", path, token).text
        assert "<script>alert" not in page, path
        assert "DROP TABLE" not in page or path == "/environments/auction/review", path
    review = call(app, "GET", "/environments/auction/review", token).text
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in review  # the escaped note


def test_service_scripts_put_client_text_in_with_text_content_only() -> None:
    from gao_idl.service import pages

    scripts = pages.COMMON_JS + pages.INDEX_JS + pages.LOGIN_JS + pages.RUN_JS + pages.REVIEW_SHIM
    for name in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert name not in scripts, name
    assert "textContent" in scripts
    assert scripts.count("DOMParser") == 1  # only for the server-rendered run view


def test_no_page_reaches_another_host(world: dict[str, Any], lib: Library) -> None:
    app, token = _app(world), world["a"]
    paths = ["/", "/login"]
    for entry in lib.entries:
        paths += [f"/environments/{entry.slug}/review", f"/environments/{entry.slug}/run"]
    for path in paths:
        for t in (None, token):
            page = call(app, "GET", path, t).text
            assert "http://" not in page and "https://" not in page, path
            assert "default-src 'none'" in page and "connect-src 'self'" in page, path
            assert "src=" not in page and "@import" not in page and "url(" not in page, path
    made = call(
        app,
        "POST",
        "/api/environments/carbon-market/runs",
        token,
        body={"cast": "naive-buyers", "seeds": [1]},
    ).json["runs"][0]
    html = call(app, "GET", f"/api/runs/{made['id']}", token).json["html"]
    assert "http://" not in html and "https://" not in html and "<script" not in html


def test_page_routes_carry_no_user_data_without_a_token(world: dict[str, Any]) -> None:
    app, token = _app(world), world["a"]
    call(
        app,
        "POST",
        "/api/environments/auction/runs",
        token,
        body={"cast": "truthful", "seeds": [5]},
    )
    for path in ("/", "/environments/auction/run", "/environments/auction/review"):
        page = call(app, "GET", path).text
        assert "ann-m11" not in page, path  # no user name, no run
    # the anonymous review page is the library's page, the same for everyone
    assert (
        call(app, "GET", "/environments/auction/review").text
        == call(app, "GET", "/environments/auction/review").text
    )


# ------------------------------------------------------------------ the code itself


def test_the_service_imports_no_model_and_builds_sql_from_no_strings() -> None:
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    code = (
        "import sys, gao_idl.service.app, gao_idl.cli; "
        "assert 'gao_idl.llm' not in sys.modules and 'httpx' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=env, timeout=30)
    for path in sorted(SRC.glob("*.py")):
        text = path.read_text()
        assert "gao_idl.llm" not in text and "import random" not in text, path.name
        assert not re.search(r"execute\(\s*f[\"']", text), path.name  # parameterised SQL only
        assert ".format(" not in text, path.name
        assert "traceback" not in text.lower() or path.name == "app.py", path.name


def test_tokens_and_bodies_are_never_logged(
    world: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    app, token = _app(world), world["a"]
    secret_note = "a-note-that-must-not-be-logged"
    with caplog.at_level("DEBUG", logger="gao_idl"):
        call(app, "GET", "/api/environments", token)
        call(app, "GET", "/api/environments", "wrong-token-value")
        call(
            app,
            "POST",
            "/api/environments/auction/verdicts",
            token,
            body={"id": "x", "verdict": "rejected", "note": secret_note, "outcome_hash": "h"},
        )
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "/api/environments" in logged
    assert token not in logged and "wrong-token-value" not in logged and secret_note not in logged


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on the path")
def test_the_page_scripts_parse(world: dict[str, Any], tmp_path: Path) -> None:
    app = _app(world)
    for path in ("/", "/login", "/environments/auction/run", "/environments/auction/review"):
        page = call(app, "GET", path).text
        for i, script in enumerate(re.findall(r"<script>(.*?)</script>", page, re.S)):
            file = tmp_path / f"s{i}.js"
            file.write_text(script)
            subprocess.run(["node", "--check", str(file)], check=True, timeout=30)
