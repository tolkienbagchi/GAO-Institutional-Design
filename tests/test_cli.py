"""``python -m gao_idl.cli run`` and ``... replay`` on the demo configs."""

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


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    package_root = str(Path(gao_idl.__file__).resolve().parents[1])
    path = os.pathsep.join(p for p in (package_root, os.environ.get("PYTHONPATH", "")) if p)
    return subprocess.run(
        [sys.executable, "-W", "error", "-m", "gao_idl.cli", *args],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": path},
        capture_output=True,
        text=True,
        timeout=LIMIT_SECONDS,
    )


def _run(config: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return _cli("run", str(config), "--out", str(out), cwd=out.parent)


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

    replayed = _cli("replay", str(out), cwd=tmp_path)
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
    replayed = _cli("replay", str(out), cwd=tmp_path)
    assert replayed.returncode == 1
    assert "utility of 'alice'" in replayed.stderr


def test_replay_notices_a_changed_result(tmp_path: Path) -> None:
    out, record = _recorded(tmp_path)
    record["result"]["port"] = "elsewhere"
    out.write_text(json.dumps(record))
    replayed = _cli("replay", str(out), cwd=tmp_path)
    assert replayed.returncode == 1
    assert "result differs" in replayed.stderr


def test_replay_notices_a_changed_input_log(tmp_path: Path) -> None:
    out, record = _recorded(tmp_path)
    log = record["snapshot"]["log"]
    submitted = next(i for i, e in enumerate(log) if e["kind"] == "submitted")
    log[submitted]["action"] = {"move": "D" if log[submitted]["action"]["move"] == "C" else "C"}
    out.write_text(json.dumps(record))
    replayed = _cli("replay", str(out), cwd=tmp_path)
    assert replayed.returncode == 1


def test_replay_refuses_a_file_of_another_kind(tmp_path: Path) -> None:
    other = tmp_path / "x.json"
    other.write_text(json.dumps({"format": "something/else"}))
    replayed = _cli("replay", str(other), cwd=tmp_path)
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


def test_importing_the_cli_module_leaves_run_a_function() -> None:
    code = (
        "import inspect, gao_idl, gao_idl.cli; "
        "assert inspect.isfunction(gao_idl.run) and not hasattr(gao_idl.cli, 'nonexistent'); "
        "assert callable(gao_idl.cli.main)"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_installed_script_name_points_at_main() -> None:
    import tomllib

    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts == {"gao-idl": "gao_idl.cli:main"}


def test_no_subcommand_is_a_usage_error(tmp_path: Path) -> None:
    assert _cli(cwd=tmp_path).returncode == 2


# ------------------------------------------------------------------ spec and cards (M8)

SPEC = ROOT / "examples" / "specs" / "auction.json"
CARDS = ROOT / "examples" / "cards" / "auction.json"


def test_spec_check_passes_on_the_shipped_auction(tmp_path: Path) -> None:
    done = _cli("spec", "check", str(SPEC), "--cards", str(CARDS), cwd=tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "0 mismatches over 13 runs" in done.stdout
    assert "assumptions not yet confirmed" in done.stdout


def test_spec_check_without_cards_claims_nothing(tmp_path: Path) -> None:
    done = _cli("spec", "check", str(SPEC), cwd=tmp_path)
    assert done.returncode == 1
    assert "not_exercised" in done.stdout and "undeclared" not in done.stdout


def test_spec_check_reports_a_wrong_spec(tmp_path: Path) -> None:
    spec = json.loads(SPEC.read_text())
    spec["steps"][0]["sees"]["bidder"].remove("bidders")
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    done = _cli("spec", "check", str(path), "--cards", str(CARDS), cwd=tmp_path)
    assert done.returncode == 1
    assert "undeclared_sees" in done.stdout and "bidders" in done.stdout


def test_spec_render_writes_the_design(tmp_path: Path) -> None:
    out = tmp_path / "design.md"
    done = _cli("spec", "render", str(SPEC), "--cards", str(CARDS), "--out", str(out), cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    text = out.read_text()
    assert text.startswith("# Sealed-bid auction") and "## Who knows what" in text
    bad = _cli("spec", "render", str(tmp_path / "nope.json"), "--out", str(out), cwd=tmp_path)
    assert bad.returncode == 2 and "Traceback" not in bad.stderr


def test_cards_confirm_reject_verify(tmp_path: Path) -> None:
    cards = tmp_path / "cards.json"
    cards.write_text(CARDS.read_text())
    shown = _cli(
        "cards", "show", str(cards), "--status", "proposed", "--spec", str(SPEC), cwd=tmp_path
    )
    assert shown.returncode == 0 and "### Everyone bids their value" in shown.stdout
    assert _cli("cards", "verify", str(cards), cwd=tmp_path).returncode == 0
    done = _cli("cards", "confirm", str(cards), "truthful", "tie-top", cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    verified = _cli("cards", "verify", str(cards), cwd=tmp_path)
    assert (
        verified.returncode == 0 and "2 confirmed, 0 rejected, 11 still proposed" in verified.stdout
    )
    rejected = _cli(
        "cards",
        "reject",
        str(cards),
        "no-bid",
        "--note",
        "a missing bid should be a zero bid",
        cwd=tmp_path,
    )
    assert rejected.returncode == 0
    verified = _cli("cards", "verify", str(cards), cwd=tmp_path)
    assert verified.returncode == 1 and "no-bid: rejected outcome is still" in verified.stderr
    data = {c["id"]: c for c in json.loads(cards.read_text())}
    assert data["no-bid"]["note"] == "a missing bid should be a zero bid"
    assert _cli("cards", "confirm", str(cards), "nope", cwd=tmp_path).returncode == 2


# ------------------------------------------------------------- fuzzing and divergence (M9)


def test_spec_check_with_fuzzing_runs_the_invariants(tmp_path: Path) -> None:
    done = _cli("spec", "check", str(SPEC), "--cards", str(CARDS), "--fuzz", "40", cwd=tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "0 mismatches over 53 runs" in done.stdout
    assert "note" in done.stdout and "2 invariants not checked" in done.stdout


def test_diverge_writes_one_question_per_disagreement(tmp_path: Path) -> None:
    spec = {
        "title": "Tiny reserve auction",
        "summary": "x",
        "env_ref": "tests.authoring.envs:build_auction_a",
        "params": {"values": {"alice": 10.0, "bob": 7.0, "carol": 4.0}, "reserve": 2.0},
        "roles": [{"name": "bidder", "description": "bids", "many": True}],
        "steps": [
            {
                "name": "bidding",
                "kind": "decision",
                "description": "Everyone bids.",
                "chooses": {"bidder": ["amount"]},
                "told": {"bidder": ["winner", "price"]},
                "on_no_action": "fail",
            }
        ],
    }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    out = tmp_path / "choices.json"
    same = _cli(
        "diverge",
        str(path),
        "tests.authoring.envs:build_auction_a2",
        "--n",
        "60",
        "--out",
        str(out),
        cwd=ROOT,
    )
    assert same.returncode == 0, same.stderr
    assert json.loads(out.read_text()) == []
    assert "endings and payoffs agreed on every script" in same.stderr
    differ = _cli(
        "diverge",
        str(path),
        "tests.authoring.envs:build_auction_c",
        "--n",
        "200",
        "--seed",
        "11",
        "--out",
        str(out),
        cwd=ROOT,
    )
    assert differ.returncode == 1, differ.stderr
    assert "they disagree on utilities" in differ.stderr
    assert "Which did you mean?" in differ.stdout and "build_auction" not in differ.stdout
    (card,) = json.loads(out.read_text())
    assert [o["label"] for o in card["options"]] == ["A", "B"] and card["chosen"] is None
