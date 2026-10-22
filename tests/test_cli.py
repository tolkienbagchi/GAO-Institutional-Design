"""``python -m gao_idl.run`` and ``python -m gao_idl.replay`` on the demo configs.

The modules are only ever run with ``-m`` (a subprocess), never imported: importing
``gao_idl.run`` would replace the function ``gao_idl.run`` (see ``tests/modules.py``).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import gao_idl

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = sorted((ROOT / "examples" / "configs").glob("*.json"))
LIMIT_SECONDS = 20


def _module(name: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    package_root = str(Path(gao_idl.__file__).resolve().parents[1])
    path = os.pathsep.join(p for p in (package_root, os.environ.get("PYTHONPATH", "")) if p)
    return subprocess.run(
        [sys.executable, "-W", "error", "-m", name, *args],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": path},
        capture_output=True,
        text=True,
        timeout=LIMIT_SECONDS,
    )


def _run(config: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return _module("gao_idl.run", str(config), "--out", str(out), cwd=out.parent)


def test_there_is_one_demo_config_per_environment() -> None:
    assert [c.stem for c in CONFIGS] == ["auction", "pb", "pd"]


@pytest.mark.parametrize("config", CONFIGS, ids=lambda c: c.stem)
def test_run_then_replay(config: Path, tmp_path: Path) -> None:
    out = tmp_path / "run.json"
    ran = _run(config, out)
    assert ran.returncode == 0, ran.stderr
    assert ran.stderr == ""  # in particular: no runpy warning about the module being imported twice
    record = json.loads(out.read_text())
    assert record["result"]["finished"] and not record["result"]["failed"]
    assert set(record["ledger"]) >= set(json.loads(config.read_text())["agents"])
    assert set(record["snapshot"]) == {"env_ref", "params", "seed", "log"}

    replayed = _module("gao_idl.replay", str(out), cwd=tmp_path)
    assert replayed.returncode == 0, replayed.stderr
    assert "replay identical" in replayed.stdout


@pytest.mark.parametrize("config", CONFIGS, ids=lambda c: c.stem)
def test_the_same_config_gives_the_same_file(config: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a" / "run.json", tmp_path / "b" / "run.json"
    for out in (first, second):
        out.parent.mkdir()
        assert _run(config, out).returncode == 0
    assert first.read_text() == second.read_text()


def test_another_seed_gives_another_run(tmp_path: Path) -> None:
    config = json.loads((ROOT / "examples" / "configs" / "pd.json").read_text())
    outs = []
    for seed in (1, 2, 3):
        path = tmp_path / f"c{seed}.json"
        path.write_text(json.dumps({**config, "seed": seed}))
        out = tmp_path / f"r{seed}.json"
        assert _run(path, out).returncode == 0
        outs.append(json.loads(out.read_text())["snapshot"]["log"])
    assert outs[0] != outs[1] or outs[1] != outs[2]


def _recorded(tmp_path: Path, name: str = "pd") -> tuple[Path, dict[str, Any]]:
    out = tmp_path / "run.json"
    assert _run(ROOT / "examples" / "configs" / f"{name}.json", out).returncode == 0
    return out, json.loads(out.read_text())


def test_replay_notices_a_changed_ledger(tmp_path: Path) -> None:
    out, record = _recorded(tmp_path)
    record["ledger"]["alice"] += 1.0
    out.write_text(json.dumps(record))
    replayed = _module("gao_idl.replay", str(out), cwd=tmp_path)
    assert replayed.returncode == 1
    assert "utility of 'alice'" in replayed.stderr


def test_replay_notices_a_changed_result(tmp_path: Path) -> None:
    out, record = _recorded(tmp_path)
    record["result"]["port"] = "elsewhere"
    out.write_text(json.dumps(record))
    replayed = _module("gao_idl.replay", str(out), cwd=tmp_path)
    assert replayed.returncode == 1
    assert "result differs" in replayed.stderr


def test_replay_notices_a_changed_input_log(tmp_path: Path) -> None:
    out, record = _recorded(tmp_path)
    log = record["snapshot"]["log"]
    submitted = next(i for i, e in enumerate(log) if e["kind"] == "submitted")
    log[submitted]["action"] = {"move": "D" if log[submitted]["action"]["move"] == "C" else "C"}
    out.write_text(json.dumps(record))
    replayed = _module("gao_idl.replay", str(out), cwd=tmp_path)
    assert replayed.returncode == 1


def test_replay_refuses_a_file_of_another_kind(tmp_path: Path) -> None:
    other = tmp_path / "x.json"
    other.write_text(json.dumps({"format": "something/else"}))
    replayed = _module("gao_idl.replay", str(other), cwd=tmp_path)
    assert replayed.returncode == 1
    assert "not a gao_idl.run/1 file" in replayed.stderr


@pytest.mark.parametrize(
    "change",
    [
        {"surprise": 1},
        {"agents": {"alice": "telepathy", "bob": "random"}},
        {"agents": {"alice": {"kind": "mock_llm"}, "bob": "random"}},
        {"agents": {"alice": "random"}},  # bob is never answered
    ],
    ids=["unknown-key", "unknown-adapter", "bad-mock", "missing-agent"],
)
def test_a_bad_config_is_reported_not_run(change: dict[str, Any], tmp_path: Path) -> None:
    config = json.loads((ROOT / "examples" / "configs" / "pd.json").read_text())
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({**config, **change}))
    ran = _run(path, tmp_path / "run.json")
    assert ran.returncode == 2, ran.stdout + ran.stderr
    assert "Traceback" not in ran.stderr
    assert not (tmp_path / "run.json").exists()


def test_the_package_does_not_import_the_entry_points() -> None:
    code = (
        "import sys, gao_idl; "
        "assert 'gao_idl.run' not in sys.modules and 'gao_idl.replay' not in sys.modules; "
        "assert callable(gao_idl.run)"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    subprocess.run([sys.executable, "-c", code], check=True, env=env)
