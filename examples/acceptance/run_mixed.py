"""Reference answer to step 5b: scripted + table + mock-LLM (+ random) in one session."""

import json
from pathlib import Path
from typing import Any

from gao_idl import AgentAdapter, RandomAgent, ScriptedAgent, Session, TableAgent, run_sync
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.learning.codecs import FieldObsCodec, SchemaActCodec
from gao_idl.llm import LLMAgent, MockBackend

AGENTS = ["alice", "bob", "carol", "dan"]


def tit_for_tat(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Scripted: copy the other player's last move; rate the game 4."""
    if request.layer == "rate":
        return {"score": 4}
    return {"move": reports[-1].payload["other_move"] if reports else "C"}


def mock_reply(messages: list[Any]) -> dict[str, Any]:
    """The mock backend sees the prompt, which contains the action schema: answer accordingly."""
    return {"score": 5} if '"score"' in messages[-1].content else {"move": "C"}


def main() -> None:
    checkpoint = json.loads(Path("bob.json").read_text())
    table = TableAgent.from_checkpoint(
        checkpoint, FieldObsCodec([]), SchemaActCodec(), learning=False
    )
    adapters: dict[AgentId, AgentAdapter] = {
        AgentId("alice"): ScriptedAgent(tit_for_tat),
        AgentId("bob"): table,
        AgentId("carol"): LLMAgent(AgentId("carol"), MockBackend(mock_reply)),
        AgentId("dan"): RandomAgent(seed=4),
    }
    session = run_sync(
        Session.start("my_env:build", {"agents": AGENTS, "rounds": 3}, seed=1), adapters
    )
    session.check_determinism()
    snapshot = session.snapshot()
    Path("session.json").write_text(json.dumps(snapshot))
    again = Session.restore(json.loads(Path("session.json").read_text()))
    assert again.result == session.result

    assert session.result is not None
    print("exit:", session.result.port, session.result.record["scores"])
    for agent in AGENTS:
        print(f"{agent:<6}{session.ledger.utility(AgentId(agent)):6.1f}")
    print("replay identical")


if __name__ == "__main__":
    main()
