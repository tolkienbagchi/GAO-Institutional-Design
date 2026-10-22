"""Reference answer to step 5a: train a tabular agent for ``bob`` and write ``bob.json``."""

import json
import os
from pathlib import Path
from typing import Any

from gao_idl import TableAgent
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.learning.codecs import FieldObsCodec, SchemaActCodec
from gao_idl.learning.opponents import default_opponent_factory
from gao_idl.learning.qlearning import QConfig, RewardExtractor
from gao_idl.learning.trainer import TrainSpec, train

AGENTS = ["alice", "bob", "carol", "dan"]


def cooperator(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Scripted opponent that has to answer both layers: look at ``request.layer``."""
    return {"score": 3} if request.layer == "rate" else {"move": "C"}


def main(episodes: int) -> None:
    spec = TrainSpec(
        env_ref="my_env:build",
        params={"agents": AGENTS, "rounds": 3},
        learner=AgentId("bob"),
        episodes=episodes,
        seed=0,
        opponents={AgentId(a): "scripted:cooperator" for a in AGENTS if a != "bob"},
    )
    learner = TableAgent(
        AgentId("bob"),
        FieldObsCodec([]),  # one state: no observation field is shared by both layers
        SchemaActCodec(),  # the PD moves and the 1..5 scores are both enumerable
        RewardExtractor(fields=["own_payoff"]),
        QConfig(alpha=0.2, gamma=0.9, epsilon=0.3),
        seed=0,
    )
    result = train(spec, learner, default_opponent_factory({"cooperator": cooperator}))
    Path("bob.json").write_text(json.dumps(result.checkpoint))
    print(f"trained bob for {episodes} episodes; failed episodes: {result.failed_episodes}")


if __name__ == "__main__":
    main(episodes=10 if os.environ.get("GAO_EXAMPLE_FAST") else 200)
