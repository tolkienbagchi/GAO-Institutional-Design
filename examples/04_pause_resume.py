"""Tutorial 4: pause a session in the middle of a decision and finish it in another process."""

import asyncio
import contextlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from gao_idl import AgentAdapter, HumanTransportAgent, ScriptedAgent, Session, run
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport

sys.path.insert(0, str(Path(__file__).resolve().parent))
importlib.import_module("02_compose")  # the environment lives in tutorial 2
ENV = "02_compose:build_repeated_pg"
HUMAN, BOT = AgentId("hu"), AgentId("bot")


def free_rider(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    return {"amount": 0.0}


async def play(session: Session, stop_after: int | None) -> tuple[dict[str, Any] | None, int]:
    """Run ``session`` with a human and a bot; the "human" always contributes 5.

    With ``stop_after=n`` the run is abandoned, as if the process died, while the human is looking
    at its (n+1)-th decision; the snapshot taken at that moment is returned. Otherwise the
    session is played to the end and ``None`` is returned. The second value counts the decisions
    the human answered in this call.
    """
    human = HumanTransportAgent()
    adapters: dict[AgentId, AgentAdapter] = {HUMAN: human, BOT: ScriptedAgent(free_rider)}
    runner = asyncio.create_task(run(session, adapters))
    answered = 0
    while True:
        waiting = asyncio.create_task(human.wait_outstanding())
        await asyncio.wait({runner, waiting}, return_when=asyncio.FIRST_COMPLETED)
        if runner.done():
            waiting.cancel()
            await runner  # re-raises if the run failed
            return None, answered
        (request,) = waiting.result()
        if stop_after is not None and answered == stop_after:
            snapshot = session.snapshot()  # plain JSON-able data: env_ref, params, seed, input log
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner
            return snapshot, answered
        human.provide(request.decision_id, {"amount": 5.0})
        answered += 1


def totals(session: Session) -> dict[str, float]:
    return {str(a): round(session.ledger.utility(a), 6) for a in (HUMAN, BOT)}


def resume(path: str) -> None:
    """The second process: restore from the file and finish. Prints one JSON line."""
    session = Session.restore(json.loads(Path(path).read_text()))
    _, answered = asyncio.run(play(session, stop_after=None))
    print(json.dumps({"answered": answered, "totals": totals(session)}))


def main(rounds: int) -> None:
    params = {"agents": [str(HUMAN), str(BOT)], "rounds": rounds}
    session = Session.start(ENV, params, seed=5)
    snapshot, answered = asyncio.run(play(session, stop_after=1))
    assert snapshot is not None
    path = Path(tempfile.mkdtemp()) / "paused.json"
    path.write_text(json.dumps(snapshot))
    print(f"paused after {answered} answered decision(s); snapshot: {path.stat().st_size} bytes")

    done = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--resume", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    resumed = json.loads(done.stdout.strip().splitlines()[-1])
    print(f"fresh process answered {resumed['answered']} more; totals {resumed['totals']}")

    straight = Session.start(ENV, params, seed=5)  # the same game without the interruption
    asyncio.run(play(straight, stop_after=None))
    assert resumed["totals"] == totals(straight), "resumed run differs from the straight run"
    assert resumed["answered"] == rounds - answered
    print("identical to an uninterrupted run")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--resume":
        resume(sys.argv[2])
    else:
        main(rounds=2 if os.environ.get("GAO_EXAMPLE_FAST") else 4)
