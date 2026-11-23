"""The service over a real socket: ``gao-idl service serve`` with wsgiref (slow)."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from gao_idl.service.store import Store
from tests.service.helpers import LIBRARY


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str, token: str | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 (localhost)
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.mark.slow
def test_serve_over_a_socket(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _, token = Store(db).add_user("sock")
    port = _free_port()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)
    command = [sys.executable, "-m", "gao_idl.cli", "service", "serve", "--library", str(LIBRARY)]
    command += ["--db", str(db), "--port", str(port)]
    server = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(opener)
    try:
        for _ in range(100):
            try:
                if _get(f"{base}/health")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("the server did not start")
        assert _get(f"{base}/api/environments")[0] == 401
        status, body = _get(f"{base}/api/environments", token)
        assert status == 200
        assert len(json.loads(body)["environments"]) == 3
        status, body = _get(f"{base}/environments/auction/review")
        assert status == 200 and b"/api/environments/auction/verdicts" in body
        data = json.dumps({"cast": "truthful", "seeds": [1]}).encode()
        request = urllib.request.Request(f"{base}/api/environments/auction/runs", data=data)
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            run_id = json.loads(response.read())["runs"][0]["id"]
        status, body = _get(f"{base}/api/runs/{run_id}", token)
        assert status == 200 and "What happened" in json.loads(body)["html"]
    finally:
        server.terminate()
        _out, err = server.communicate(timeout=10)
        urllib.request.install_opener(urllib.request.build_opener())
    assert "development only" in err
    assert token not in err and token not in _out
