"""Tutorial 5: train a tabular learner on the public-goods game and evaluate it."""

import importlib
import os
import statistics
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gao_idl import AgentAdapter, RandomAgent, ScriptedAgent, Session, run_sync
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.learning.opponents import default_opponent_factory
from gao_idl.learning.trainer import TrainSpec, evaluate, train

sys.path.insert(0, str(Path(__file__).resolve().parent))
mixed = importlib.import_module("03_mixed_agents")  # the learner and the free rider
ENV, AGENTS = mixed.ENV, mixed.AGENTS
ANN, BO, CY = AgentId("ann"), AgentId("bo"), AgentId("cy")


def always_contribute(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    return {"amount": 10.0}


def baseline(make: Callable[[int], AgentAdapter], episodes: int) -> list[float]:
    """Mean-return data for a fixed policy in the learner's seat, against the same free riders."""
    returns = []
    for e in range(episodes):
        session = Session.start(ENV, {"agents": AGENTS}, seed=9_000 + e)
        adapters: dict[AgentId, AgentAdapter] = {
            ANN: ScriptedAgent(mixed.free_rider),
            BO: ScriptedAgent(mixed.free_rider),
            CY: make(e),
        }
        run_sync(session, adapters)
        returns.append(session.ledger.utility(CY))
    return returns


def main(train_episodes: int, eval_episodes: int) -> None:
    spec = TrainSpec(
        env_ref=ENV,
        params={"agents": AGENTS},
        learner=CY,
        episodes=train_episodes,
        seed=1,
        opponents={ANN: "scripted:free_rider", BO: "scripted:free_rider"},
    )
    factory = default_opponent_factory({"free_rider": mixed.free_rider})
    trained = train(spec, mixed.new_learner("cy", seed=1), factory)
    k = max(1, train_episodes // 5)
    print(f"training return, first {k} episodes: {statistics.mean(trained.returns[:k]):6.2f}")
    print(f"training return, last  {k} episodes: {statistics.mean(trained.returns[-k:]):6.2f}")

    # evaluation never touches the training agent: it builds a frozen copy from the checkpoint
    result = evaluate(spec, trained.checkpoint, factory, episodes=eval_episodes, seed=2)
    rows = {
        "trained (greedy)": (result.mean, result.std),
        "random": _stats(baseline(lambda e: RandomAgent(e), eval_episodes)),
        "always contribute": _stats(baseline(lambda e: ScriptedAgent(always_contribute), 3)),
        "free rider": _stats(baseline(lambda e: ScriptedAgent(mixed.free_rider), 3)),
    }
    print(f"\n{'policy':<20}{'mean':>8}{'std':>8}   (return of cy over 5 rounds)")
    for name, (mean, std) in rows.items():
        print(f"{name:<20}{mean:>8.2f}{std:>8.2f}")


def _stats(returns: list[float]) -> tuple[float, float]:
    return statistics.mean(returns), statistics.pstdev(returns)


if __name__ == "__main__":
    fast = bool(os.environ.get("GAO_EXAMPLE_FAST"))
    main(train_episodes=20 if fast else 300, eval_episodes=10 if fast else 50)
