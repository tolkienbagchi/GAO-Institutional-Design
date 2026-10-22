"""Rebuild a session from a file written by ``gao_idl.run`` and check that it is reproduced.

``python -m gao_idl.replay run.json``. The snapshot's input log is replayed from scratch (no
adapters are needed: whatever the agents did, including the replies of a language model, is in the
log), the replay is checked against the log's own trace, and the result and ledger totals are
compared with the ones the file recorded. Exit status 0 means identical; 1 means they differ.

Stability: internal; a command-line entry point.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gao_idl.core.errors import GaoError
from gao_idl.core.ids import AgentId
from gao_idl.runtime.session import Session

FORMAT = "gao_idl.run/1"


def replay_file(data: dict[str, Any]) -> list[str]:
    """Replay ``data`` (parsed output of ``gao_idl.run``); returns the differences (empty: ok)."""
    if data.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} file (format={data.get('format')!r})")
    session = Session.restore(data["snapshot"])
    session.check_determinism()
    problems: list[str] = []
    result = session.result
    seen = {
        "finished": session.finished,
        "failed": session.failed,
        "port": None if result is None else str(result.port),
        "record": None if result is None else result.record,
    }
    if seen != data["result"]:
        problems.append(f"result differs: recorded {data['result']!r}, replayed {seen!r}")
    for agent, total in sorted(data["ledger"].items()):
        replayed = session.ledger.utility(AgentId(agent))
        if replayed != total:
            problems.append(f"utility of {agent!r}: recorded {total!r}, replayed {replayed!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the exit status."""
    parser = argparse.ArgumentParser(prog="python -m gao_idl.replay", description=__doc__)
    parser.add_argument("run", type=Path, help="file written by python -m gao_idl.run")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.run.read_text())
        problems = replay_file(data)
    except (OSError, ValueError, KeyError, GaoError) as e:
        print(f"replay failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if problems:
        print("replay differs from the recording:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"replay identical: {len(data['snapshot']['log'])} inputs, exit={data['result']['port']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
