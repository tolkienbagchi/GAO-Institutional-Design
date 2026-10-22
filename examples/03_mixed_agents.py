"""Tutorial 3: one game, three kinds of participant (scripted, tabular, language model)."""

import importlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from gao_idl import AgentAdapter, ScriptedAgent, Session, TableAgent, run_sync
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.learning.codecs import FieldObsCodec, SchemaActCodec
from gao_idl.learning.opponents import default_opponent_factory
from gao_idl.learning.qlearning import QConfig, RewardExtractor
from gao_idl.learning.trainer import TrainSpec, train
from gao_idl.llm import LLMAgent, MockBackend

sys.path.insert(0, str(Path(__file__).resolve().parent))
importlib.import_module("01_define_a_layer")  # make sure tutorial 1 is importable by name
ENV = "02_compose:build_repeated_pg"
AGENTS = ["ann", "bo", "cy"]


def free_rider(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """A scripted policy is a plain function of the request and the reports seen so far."""
    return {"amount": 0.0}


def new_learner(agent: str, seed: int = 0, learning: bool = True) -> TableAgent:
    """A tabular Q-learner that sees the round number and picks from three contribution levels."""
    return TableAgent(
        AgentId(agent),
        FieldObsCodec(["round"]),
        SchemaActCodec(grid={"amount": [0.0, 5.0, 10.0]}),
        RewardExtractor(fields=["own_payoff"]),
        QConfig(alpha=0.2, gamma=0.9, epsilon=0.3),
        seed,
        learning=learning,
    )


def make_checkpoint(path: Path, episodes: int) -> None:
    """Train ``cy`` against two free riders and write the checkpoint as JSON."""
    spec = TrainSpec(
        env_ref=ENV,
        params={"agents": AGENTS},
        learner=AgentId("cy"),
        episodes=episodes,
        seed=0,
        opponents={AgentId("ann"): "scripted:free_rider", AgentId("bo"): "scripted:free_rider"},
    )
    result = train(spec, new_learner("cy"), default_opponent_factory({"free_rider": free_rider}))
    path.write_text(json.dumps(result.checkpoint))


def load_learner(path: Path) -> TableAgent:
    """A frozen (greedy, non-learning) agent restored from the checkpoint file."""
    ckpt = json.loads(path.read_text())
    return TableAgent.from_checkpoint(
        ckpt,
        FieldObsCodec(["round"]),
        SchemaActCodec(grid={"amount": [0.0, 5.0, 10.0]}),
        learning=False,
    )


def main(episodes: int = 60) -> None:
    ckpt = Path(tempfile.mkdtemp()) / "cy.json"
    make_checkpoint(ckpt, episodes)

    # the mock backend answers every prompt with the same JSON object; a script (a list of
    # replies, or a function of the messages) is how tests drive an LLMAgent without a network
    backend = MockBackend(lambda messages: {"amount": 5.0})
    adapters: dict[AgentId, AgentAdapter] = {
        AgentId("ann"): ScriptedAgent(free_rider),
        AgentId("bo"): LLMAgent(AgentId("bo"), backend),
        AgentId("cy"): load_learner(ckpt),
    }
    session = run_sync(Session.start(ENV, {"agents": AGENTS}, seed=11), adapters)
    session.check_determinism()  # replaying the input log reproduces the session exactly

    for agent in AGENTS:
        print(f"{agent}: {session.ledger.utility(AgentId(agent)):6.2f}")
    print("model calls:", len(backend.calls))


if __name__ == "__main__":
    import os

    main(episodes=8 if os.environ.get("GAO_EXAMPLE_FAST") else 60)
