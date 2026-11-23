"""Each route's happy path and each error code, by calling the WSGI application directly."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from wsgiref.types import WSGIApplication

import pytest

from gao_idl.service.app import MAX_BODY, make_app
from gao_idl.service.library import Library
from gao_idl.service.store import Store
from tests.service.helpers import FakeClock, call, library, store

API_ROUTES = [
    ("GET", "/api/environments"),
    ("GET", "/api/environments/auction"),
    ("GET", "/api/environments/auction/bundle"),
    ("GET", "/api/environments/auction/verdicts"),
    ("POST", "/api/environments/auction/verdicts"),
    ("POST", "/api/environments/auction/runs"),
    ("GET", "/api/runs"),
    ("GET", "/api/runs/1"),
]


@pytest.fixture(scope="module")
def lib() -> Library:
    return library()


@pytest.fixture(scope="module")
def world(lib: Library, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    s: Store = store(tmp_path_factory.mktemp("db") / "service.db")
    _, alice = s.add_user("alice")
    _, bob = s.add_user("bob")
    yield {"app": make_app(lib, s), "alice": alice, "bob": bob, "store": s}


def _app(world: dict[str, Any]) -> WSGIApplication:
    return world["app"]  # type: ignore[no-any-return]


def test_health_needs_no_token(world: dict[str, Any]) -> None:
    answer = call(_app(world), "GET", "/health")
    assert answer.status == 200 and answer.json == {"status": "ok"}
    assert answer.headers["Content-Type"].startswith("application/json")
    assert answer.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(("method", "path"), API_ROUTES)
def test_every_api_route_needs_a_valid_token(world: dict[str, Any], method: str, path: str) -> None:
    app = _app(world)
    for headers in ({}, {"HTTP_AUTHORIZATION": "Bearer wrong"}, {"HTTP_AUTHORIZATION": "Basic x"}):
        answer = call(app, method, path, body={} if method == "POST" else None, headers=headers)
        assert answer.status == 401, (path, headers)
        assert answer.json["error"] == "unauthorized"
        assert "Traceback" not in answer.text


def test_list_and_describe_environments(world: dict[str, Any]) -> None:
    app, token = _app(world), world["alice"]
    listed = call(app, "GET", "/api/environments", token).json["environments"]
    assert [e["slug"] for e in listed] == ["auction", "prisoners-dilemma", "carbon-market"]
    assert all(e["title"] and e["summary"] for e in listed)
    one = call(app, "GET", "/api/environments/auction", token).json
    assert one["slug"] == "auction"
    assert [p["name"] for p in one["params"]] == ["reserve", "rule", "reveal_bids"]
    assert one["params"][0]["value"] == 2.0 and one["params"][0]["kind"] == "float"
    assert [c["name"] for c in one["casts"]] == ["truthful", "shading"]
    assert one["casts"][0]["roles"] == ["bidder"]
    assert "policies" not in one["casts"][0]  # code references stay on the server
    assert one["cards"] == {"total": 13, "proposed": 13, "confirmed": 0, "rejected": 0}


def test_unknown_slug_and_route_are_404(world: dict[str, Any]) -> None:
    app, token = _app(world), world["alice"]
    for method, path in [
        ("GET", "/api/environments/nope"),
        ("GET", "/api/environments/nope/bundle"),
        ("GET", "/api/environments/nope/verdicts"),
        ("POST", "/api/environments/nope/verdicts"),
        ("POST", "/api/environments/nope/runs"),
        ("GET", "/api/nothing"),
        ("DELETE", "/api/runs"),
        ("GET", "/environments/nope/review"),
        ("GET", "/environments/nope/run"),
        ("GET", "/no/such/page"),
    ]:
        answer = call(app, method, path, token, body={} if method == "POST" else None)
        assert answer.status == 404, path
        assert answer.json["error"] == "not_found"


def _bundle(world: dict[str, Any], token: str) -> dict[str, Any]:
    answer = call(_app(world), "GET", "/api/environments/auction/bundle", token)
    assert answer.status == 200
    return answer.json  # type: ignore[no-any-return]


def test_bundle_and_verdicts_round_trip(world: dict[str, Any]) -> None:
    app, alice = _app(world), world["bob"]  # a user with no verdicts yet
    bundle = _bundle(world, alice)
    assert bundle["bundle_version"] == 1 and len(bundle["cards"]) == 13
    assert bundle["status"]["cards"] == {"proposed": 13, "confirmed": 0, "rejected": 0}
    first, second = bundle["cards"][0], bundle["cards"][1]
    yes = {"id": first["card"]["id"], "verdict": "confirmed", "outcome_hash": first["outcome_hash"]}
    answer = call(app, "POST", "/api/environments/auction/verdicts", alice, body=yes)
    assert answer.status == 200 and answer.json["stored"] == {"cards": 1, "assumptions": 0}
    choice = bundle["spec"]["assumptions"][0]
    agree = {"index": 0, "text": choice["text"], "verdict": "agreed"}
    assert call(app, "POST", "/api/environments/auction/verdicts", alice, body=agree).status == 200
    whole = {
        "bundle_version": 1,
        "spec_title": bundle["spec"]["title"],
        "reviewer": "ignored",
        "cards": [
            {
                "id": second["card"]["id"],
                "verdict": "rejected",
                "note": "should be unsold",
                "outcome_hash": second["outcome_hash"],
            }
        ],
        "assumptions": [],
    }
    answer = call(app, "POST", "/api/environments/auction/verdicts", alice, body=whole)
    assert answer.status == 200 and answer.json["stored"] == {"cards": 1, "assumptions": 0}

    mine = call(app, "GET", "/api/environments/auction/verdicts", alice).json
    assert mine["bundle_version"] == 1 and mine["reviewer"] == "bob"
    assert mine["spec_title"] == bundle["spec"]["title"]
    assert sorted((c["id"], c["verdict"]) for c in mine["cards"]) == sorted(
        [(first["card"]["id"], "confirmed"), (second["card"]["id"], "rejected")]
    )
    assert [(a["index"], a["verdict"]) for a in mine["assumptions"]] == [(0, "agreed")]

    overlaid = _bundle(world, alice)
    by_id = {v["card"]["id"]: v["card"] for v in overlaid["cards"]}
    assert by_id[first["card"]["id"]]["status"] == "confirmed"
    assert by_id[second["card"]["id"]]["status"] == "rejected"
    assert by_id[second["card"]["id"]]["note"] == "should be unsold"
    assert by_id[second["card"]["id"]]["reviewer"] == "bob"
    assert overlaid["status"]["cards"] == {"proposed": 11, "confirmed": 1, "rejected": 1}
    assert overlaid["spec"]["assumptions"][0]["confirmed"] is True
    assert overlaid["status"]["assumptions_open"] == bundle["status"]["assumptions_open"] - 1


def test_a_stale_verdict_is_409_and_not_stored(world: dict[str, Any]) -> None:
    app, token = _app(world), world["alice"]
    bundle = _bundle(world, token)
    a, b = bundle["cards"][2], bundle["cards"][3]
    whole = {
        "bundle_version": 1,
        "spec_title": bundle["spec"]["title"],
        "reviewer": "alice",
        "cards": [
            {"id": a["card"]["id"], "verdict": "confirmed", "outcome_hash": "0" * 64},
            {"id": b["card"]["id"], "verdict": "confirmed", "outcome_hash": b["outcome_hash"]},
        ],
        "assumptions": [],
    }
    answer = call(app, "POST", "/api/environments/auction/verdicts", token, body=whole)
    assert answer.status == 409
    assert answer.json["error"] == "stale" and answer.json["stale"] == [a["card"]["id"]]
    assert a["card"]["id"] in answer.json["message"]
    stored = call(app, "GET", "/api/environments/auction/verdicts", token).json["cards"]
    assert [c["id"] for c in stored] == [b["card"]["id"]]


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"id": "no-such-card", "verdict": "confirmed", "outcome_hash": "x"},
        {"id": "tie-top", "verdict": "maybe", "outcome_hash": "x"},
        {"id": "tie-top", "verdict": "rejected", "note": "  ", "outcome_hash": "x"},
        {"index": 99, "text": "x", "verdict": "agreed"},
        {"index": 0, "text": "not the text", "verdict": "agreed"},
        {
            "bundle_version": 1,
            "spec_title": "Other",
            "reviewer": "",
            "cards": [],
            "assumptions": [],
        },
        {"bundle_version": 2},
    ],
)
def test_bad_verdicts_are_422(world: dict[str, Any], body: Any) -> None:
    answer = call(
        _app(world), "POST", "/api/environments/auction/verdicts", world["alice"], body=body
    )
    assert answer.status == 422, body
    assert answer.json["error"] in ("bad_verdict", "wrong_spec")


def test_a_dispute_needs_a_note(world: dict[str, Any]) -> None:
    app, token = _app(world), world["alice"]
    text = _bundle(world, token)["spec"]["assumptions"][1]["text"]
    body = {"index": 1, "text": text, "verdict": "disputed", "note": ""}
    assert call(app, "POST", "/api/environments/auction/verdicts", token, body=body).status == 422
    body["note"] = "the reserve should be secret"
    assert call(app, "POST", "/api/environments/auction/verdicts", token, body=body).status == 200


def test_runs_are_played_stored_and_shown(world: dict[str, Any]) -> None:
    app, token = _app(world), world["alice"]
    body = {"params": {"reserve": 5.0, "rule": "first_price"}, "cast": "shading", "seeds": [3, 4]}
    answer = call(app, "POST", "/api/environments/auction/runs", token, body=body)
    assert answer.status == 200, answer.text
    runs = answer.json["runs"]
    assert [r["seed"] for r in runs] == [3, 4]
    assert runs[0]["summary"] == {
        "exit_port": "done",
        "failed": False,
        "finished": True,
        "payoffs": {"alice": 2.0, "bob": 0.0, "carol": 0.0},  # pays own bid 8.0, value 10
    }
    listed = call(app, "GET", "/api/runs", token).json["runs"]
    assert [r["id"] for r in listed[:2]] == [runs[1]["id"], runs[0]["id"]]  # newest first
    assert listed[0]["params"]["reserve"] == 5.0 and listed[0]["cast"] == "shading"
    one = call(app, "GET", f"/api/runs/{runs[0]['id']}", token).json
    assert one["seed"] == 3 and one["summary"] == runs[0]["summary"]
    assert one["title"].endswith("Everyone bids 80% of their value, seed 3")
    assert one["story"]["exit_port"] == "done" and one["story"]["steps"]
    assert one["html"].startswith("<h3>What happened</h3>") and "Detailed diagram" in one["html"]
    assert one["timeline_svg"].startswith("<svg ") and one["timeline_svg"] in one["html"]


def test_runs_are_deterministic(world: dict[str, Any]) -> None:
    app, token = _app(world), world["alice"]
    body = {"cast": "tit-for-tat-vs-defector", "seeds": [7], "params": {"rounds": 4}}
    a = call(app, "POST", "/api/environments/prisoners-dilemma/runs", token, body=body).json
    b = call(app, "POST", "/api/environments/prisoners-dilemma/runs", token, body=body).json
    assert a["runs"][0]["summary"] == b["runs"][0]["summary"]
    assert a["runs"][0]["summary"]["payoffs"] == {"a": 3.0, "b": 8.0, "c": 3.0, "d": 8.0}
    sa = call(app, "GET", f"/api/runs/{a['runs'][0]['id']}", token).json
    sb = call(app, "GET", f"/api/runs/{b['runs'][0]['id']}", token).json
    assert sa["story"] == sb["story"] and sa["html"] == sb["html"]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"cast": "truthful", "seeds": [1], "env_ref": "os:system"}, "bad_run"),
        ({"cast": "nobody", "seeds": [1]}, "bad_run"),
        ({"seeds": [1]}, "bad_run"),
        ({"cast": "truthful", "seeds": []}, "bad_run"),
        ({"cast": "truthful", "seeds": [1.5]}, "bad_run"),
        ({"cast": "truthful", "seeds": [-1]}, "bad_run"),
        ({"cast": "truthful", "seeds": [True]}, "bad_run"),
        ({"cast": "truthful", "seeds": "1"}, "bad_run"),
        ({"cast": "truthful", "seeds": list(range(11))}, "cap"),
        ({"cast": "truthful", "seeds": [1], "params": {"reserve": 16}}, "bad_params"),
        ({"cast": "truthful", "seeds": [1], "params": {"values": {}}}, "bad_params"),
        ({"cast": "truthful", "seeds": [1], "params": []}, "bad_params"),
        ([1, 2], "bad_run"),
    ],
)
def test_bad_run_requests_are_422(world: dict[str, Any], body: Any, code: str) -> None:
    app, token = _app(world), world["alice"]
    before = len(call(app, "GET", "/api/runs", token).json["runs"])
    answer = call(app, "POST", "/api/environments/auction/runs", token, body=body)
    assert answer.status == 422 and answer.json["error"] == code, answer.text
    assert len(call(app, "GET", "/api/runs", token).json["runs"]) == before


def test_bodies_over_the_limit_are_413_unread(world: dict[str, Any]) -> None:
    app, token = _app(world), world["alice"]
    big = b'{"cast": "truthful", "seeds": [1], "pad": "' + b"x" * MAX_BODY + b'"}'
    for path in ("/api/environments/auction/runs", "/api/environments/auction/verdicts"):
        answer = call(app, "POST", path, token, raw=big)
        assert answer.status == 413 and answer.json["error"] == "too_large"
    answer = call(
        app, "POST", "/api/environments/auction/runs", token, headers={"CONTENT_LENGTH": "2000000"}
    )
    assert answer.status == 413


def test_bad_json_is_422(world: dict[str, Any]) -> None:
    app, token = _app(world), world["alice"]
    for raw in (b"{", b"\xff\xfe", b'{"seeds": [NaN]}', b""):
        answer = call(app, "POST", "/api/environments/auction/runs", token, raw=raw)
        assert answer.status == 422, raw


def test_posts_from_another_site_are_403(world: dict[str, Any]) -> None:
    app, token = _app(world), world["alice"]
    body = {"cast": "truthful", "seeds": [1]}
    other = {"HTTP_ORIGIN": "https://evil.example"}
    answer = call(app, "POST", "/api/environments/auction/runs", token, body=body, headers=other)
    assert answer.status == 403 and answer.json["error"] == "forbidden"
    same = {"HTTP_ORIGIN": "http://testserver"}
    answer = call(app, "POST", "/api/environments/auction/runs", token, body=body, headers=same)
    assert answer.status == 200


def test_pages_answer_without_a_token(world: dict[str, Any]) -> None:
    app = _app(world)
    for path in ("/", "/login", "/environments/auction/review", "/environments/auction/run"):
        answer = call(app, "GET", path)
        assert answer.status == 200, path
        assert answer.headers["Content-Type"].startswith("text/html")
        assert answer.text.startswith("<!DOCTYPE html>")
        assert "default-src 'none'" in answer.text
    index = call(app, "GET", "/").text
    assert "/environments/carbon-market/review" in index and "/environments/auction/run" in index
    run = call(app, "GET", "/environments/auction/run").text
    for label in ("Reserve price", "Pricing rule", "Publish all bids afterwards", "Seeds"):
        assert label in run
    assert 'type="number" id="param-reserve" step="any" min="0" max="15"' in run
    assert "Everyone bids 80% of their value" in run
    review = call(app, "GET", "/environments/auction/review").text
    assert "/api/environments/auction/verdicts" in review and "Authorization" in review
    assert "connect-src 'self'" in review


def test_a_page_with_a_wrong_token_is_401(world: dict[str, Any]) -> None:
    assert call(_app(world), "GET", "/environments/auction/review", "wrong").status == 401


def test_the_review_page_with_a_token_shows_that_users_verdicts(world: dict[str, Any]) -> None:
    app, s = _app(world), world["store"]
    _, carol = s.add_user("carol")
    view = _bundle(world, carol)["cards"][0]
    body = {"id": view["card"]["id"], "verdict": "confirmed", "outcome_hash": view["outcome_hash"]}
    assert call(app, "POST", "/api/environments/auction/verdicts", carol, body=body).status == 200
    mine = call(app, "GET", "/environments/auction/review", carol).text
    anonymous = call(app, "GET", "/environments/auction/review").text
    assert "12 waiting for a verdict, 1 confirmed" in mine
    assert "13 waiting for a verdict, 0 confirmed" in anonymous


def test_a_run_the_library_cannot_replay_is_409(world: dict[str, Any], lib: Library) -> None:
    app, token, s = _app(world), world["alice"], world["store"]
    with s.open() as db:
        user = db.user_by_token(token)
        assert user is not None
        (run_id,) = db.add_runs(user, "auction", {"values": {}}, "truthful", [(0, {"log": []}, {})])
    answer = call(app, "GET", f"/api/runs/{run_id}", token)
    assert answer.status == 409 and answer.json["error"] == "cannot_replay"
    assert "Traceback" not in answer.text


def test_unknown_run_ids_are_404(world: dict[str, Any]) -> None:
    app, token = _app(world), world["alice"]
    for run_id in ("999999", "abc", "1e3", "-1", "99999999999999999999999"):
        assert call(app, "GET", f"/api/runs/{run_id}", token).status == 404, run_id


def test_an_internal_error_answers_500_without_a_trace(tmp_path: Path, lib: Library) -> None:
    s = store(tmp_path / "x.db")
    _, token = s.add_user("dora")
    app = make_app(lib, s, clock=FakeClock())
    (tmp_path / "x.db").unlink()  # the database disappears under the service
    (tmp_path / "x.db").mkdir()
    answer = call(app, "GET", "/api/environments", token)
    assert answer.status == 500 and answer.json["error"] == "internal"
    assert "Traceback" not in answer.text and "sqlite" not in answer.text.lower()
