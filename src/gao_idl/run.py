"""Play an environment from a JSON config and write the session snapshot and ledger totals.

``python -m gao_idl.run CONFIG [--out run.json]``. The config names the environment factory
(``env_ref``), its ``params``, the session ``seed`` and one adapter per agent. An adapter is a
description string understood by the learning package (``"random"``, ``"scripted:<policy>"``,
``"table:<checkpoint path>"``) or ``{"kind": "mock_llm", "reply": {...}}``: a language-model agent
on a mock backend that answers every prompt with the same JSON object. Random agents are seeded
from the session seed, so the same config always produces the same run.

Stability: internal; a command-line entry point. Run it with `-m` and do not import it: a module
named `gao_idl.run` would replace the function `run` that `gao_idl` exports under the same name.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, ValidationError

from gao_idl.agents.base import AgentAdapter
from gao_idl.core.errors import GaoError
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import FrozenModel
from gao_idl.learning.opponents import default_opponent_factory
from gao_idl.runtime.runner import run_sync
from gao_idl.runtime.session import Session

FORMAT = "gao_idl.run/1"


class RunConfig(FrozenModel):
    """A run: environment, parameters, seed and the adapter of every agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    env_ref: str
    params: dict[str, Any] = {}
    seed: int
    agents: dict[str, str | dict[str, Any]]


def build_adapter(agent: str, spec: str | dict[str, Any], seed: int) -> AgentAdapter:
    """The adapter described by ``spec`` for ``agent`` (``seed`` is the session seed)."""
    if isinstance(spec, str):
        return default_opponent_factory()(AgentId(agent), spec, seed)
    if spec.get("kind") == "mock_llm" and set(spec) == {"kind", "reply"}:
        from gao_idl.llm import LLMAgent, MockBackend  # only when asked for: keeps the CLI light

        reply = spec["reply"]
        return LLMAgent(AgentId(agent), MockBackend(lambda messages: reply))
    raise ValueError(f"agent {agent!r}: unknown adapter {spec!r}")


def play(config: RunConfig) -> dict[str, Any]:
    """Run the session to the end; return the record that is written to the output file."""
    session = Session.start(config.env_ref, config.params, config.seed)
    adapters = {
        AgentId(a): build_adapter(a, spec, config.seed) for a, spec in sorted(config.agents.items())
    }
    run_sync(session, adapters)
    return describe_run(config, session)


def describe_run(config: RunConfig, session: Session) -> dict[str, Any]:
    """The output record: the config, the outcome, the ledger totals and the snapshot."""
    agents = sorted(set(config.agents) | set(session.ledger.snapshot()["increments"]))
    result = session.result
    return {
        "format": FORMAT,
        "config": config.model_dump(mode="json"),
        "result": {
            "finished": session.finished,
            "failed": session.failed,
            "port": None if result is None else str(result.port),
            "record": None if result is None else result.record,
        },
        "ledger": {a: session.ledger.utility(AgentId(a)) for a in agents},
        "snapshot": session.snapshot(),
    }


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the exit status."""
    parser = argparse.ArgumentParser(prog="python -m gao_idl.run", description=__doc__)
    parser.add_argument("config", type=Path, help="JSON config (see examples/configs/)")
    parser.add_argument("--out", type=Path, default=Path("run.json"), help="output file")
    args = parser.parse_args(argv)
    try:
        config = RunConfig.model_validate_json(args.config.read_text())
    except (OSError, ValidationError) as e:
        print(f"cannot read the config {args.config}: {e}", file=sys.stderr)
        return 2
    try:
        record = play(config)
    except (ValueError, RuntimeError, GaoError) as e:
        print(f"the run failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    outcome = record["result"]
    print(f"{config.env_ref} seed={config.seed}: exit={outcome['port']} -> {args.out}")
    for agent, total in record["ledger"].items():
        print(f"  {agent:<10}{total:>10.4f}")
    return 0 if outcome["finished"] and not outcome["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
