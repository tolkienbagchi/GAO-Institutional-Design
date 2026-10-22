"""Shared builders for the learning tests."""

from typing import Any

from gao_idl.core.ids import AgentId
from gao_idl.learning import (
    FieldObsCodec,
    QConfig,
    RewardExtractor,
    SchemaActCodec,
    TableAgent,
    TrainSpec,
)

PD = "gao_idl.environments.pd:build_repeated_pd"
AUCTION = "gao_idl.environments.auction:build_auction"
ALICE, BOB = AgentId("alice"), AgentId("bob")


def pd_spec(
    rounds: int = 3,
    episodes: int = 20,
    seed: int = 1,
    opponent: str = "scripted:always_cooperate",
    **params: Any,
) -> TrainSpec:
    return TrainSpec(
        env_ref=PD,
        params={"agents": ["alice", "bob"], "rounds": rounds, "matching": "fixed", **params},
        learner=ALICE,
        episodes=episodes,
        seed=seed,
        opponents={BOB: opponent},
    )


def pd_learner(
    seed: int = 0, cfg: QConfig | None = None, record: bool = False, learning: bool = True
) -> TableAgent:
    return TableAgent(
        ALICE,
        FieldObsCodec(["round_index"]),
        SchemaActCodec(),
        RewardExtractor(fields=["own_payoff"]),
        cfg or QConfig(alpha=0.2, gamma=0.9, epsilon=0.2),
        seed,
        learning=learning,
        record_transitions=record,
    )
