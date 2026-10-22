"""The reference answer to ``docs/ACCEPTANCE.md`` runs: the one-hour exercise is possible.

``examples/acceptance/`` is what a participant is expected to produce (a new layer played after
the repeated prisoner's dilemma, a trained table agent, and a session with scripted, table,
mock-LLM and random agents). The participant does not see it unless they are stuck.
"""

import os
import subprocess
import sys
from pathlib import Path

import gao_idl
from gao_idl.composition.contracts import check, describe

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "examples" / "acceptance"


def _python(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    package_root = str(Path(gao_idl.__file__).resolve().parents[1])
    path = os.pathsep.join(p for p in (package_root, os.environ.get("PYTHONPATH", "")) if p)
    env = {**os.environ, "PYTHONPATH": path, "GAO_EXAMPLE_FAST": "1"}
    return subprocess.run(
        [sys.executable, str(REFERENCE / script)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_reference_answer_trains_runs_and_replays(tmp_path: Path) -> None:
    trained = _python("train_table.py", tmp_path)
    assert trained.returncode == 0, trained.stderr
    assert "failed episodes: 0" in trained.stdout
    assert (tmp_path / "bob.json").exists()

    played = _python("run_mixed.py", tmp_path)
    assert played.returncode == 0, played.stderr
    lines = played.stdout.splitlines()
    assert lines[0].startswith("exit: done {'rater:0'")
    assert lines[-1] == "replay identical"
    assert [line.split()[0] for line in lines[1:-1]] == ["alice", "bob", "carol", "dan"]
    assert (tmp_path / "session.json").exists()


def test_reference_layer_is_a_well_formed_composition() -> None:
    sys.path.insert(0, str(REFERENCE))
    try:
        import my_env  # the participant's module has the same name

        process, _ = my_env.build(["alice", "bob"], 2)
        check(process)
        assert describe(process)["kind"] == "seq"
    finally:
        sys.path.remove(str(REFERENCE))
        sys.modules.pop("my_env", None)
