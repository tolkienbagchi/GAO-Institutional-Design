"""Tutorial 6: check your own layer with the helpers in ``gao_idl.testing``.

The functions are written as pytest tests; the script runs them one after another.
"""

import dataclasses
import importlib
import math
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from gao_idl import Config, RandomAgent, Session, run_sync
from gao_idl.core.ids import AgentId, RoleId
from gao_idl.testing import (
    assert_arrival_invariant,
    assert_incremental_equals_fold,
    assert_noninterference,
    assert_replay_equivalent,
    assert_substitution_equivalent,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
pg = importlib.import_module("01_define_a_layer")
compose = importlib.import_module("02_compose")

TEAMS = [["ann", "bo"], ["cy", "di"]]
ACTIONS = {
    AgentId(a): {"amount": x} for a, x in [("ann", 4.0), ("bo", 2.0), ("cy", 5.0), ("di", 0.0)]
}


def build_unequal(rich_group_endowment: float) -> tuple[Any, Config]:
    """The tournament in which the second team starts with a different endowment.

    It is also an ``env_ref`` entry point: sessions find environments by name.
    """
    loop, cfg = compose.build_pg_tournament(TEAMS, rounds=2)
    state = dict(cfg.state)
    state["endowments"] = {
        a: (10.0 if a in TEAMS[0] else rich_group_endowment) for a in sum(TEAMS, [])
    }
    return loop, Config(state=state, roles=cfg.roles)


def test_replay_and_fold() -> None:
    """Whatever happened in a session, replaying its input log gives the same trace."""
    session = Session.start("02_compose:build_repeated_pg", {"agents": ["ann", "bo"]}, seed=4)
    run_sync(session, {AgentId("ann"): RandomAgent(1), AgentId("bo"): RandomAgent(2)})
    assert_replay_equivalent(session)
    assert_incremental_equals_fold(session)


def test_arrival_order_does_not_matter() -> None:
    """Agents answer in every possible order; outcomes and information must not change."""
    assert_arrival_invariant(
        "02_compose:build_pg_tournament", {"groups": TEAMS, "rounds": 2}, 3, ACTIONS
    )


def test_teams_cannot_see_each_others_endowment() -> None:
    """Team one's information is the same whatever team two starts with ..."""
    _assert_same_information_for([AgentId("ann"), AgentId("bo")], 10.0, 25.0)


def test_the_check_has_teeth() -> None:
    """... and the same check does fail for someone who is told their own endowment."""
    try:
        _assert_same_information_for([AgentId("cy")], 10.0, 25.0)
    except AssertionError:
        return
    raise AssertionError("expected a leak: cy sees its own endowment")


def _assert_same_information_for(agents: list[AgentId], a: float, b: float) -> None:
    assert_noninterference(
        "06_information_tests:build_unequal",
        {"rich_group_endowment": a},
        {"rich_group_endowment": b},
        seed=3,
        actions=ACTIONS,
        agents=agents,
    )


def test_a_rewritten_layer_changes_nothing() -> None:
    """Swap in a different implementation of the same layer; nothing observable may change."""
    original = pg.public_goods_layer(2)

    def outcome(cfg: Config, actions: dict[RoleId, BaseModel], rng: Any, store: Any) -> Any:
        s = pg.PGState.model_validate(cfg.state)
        given = {r: actions[RoleId(r)].model_dump()["amount"] for r in s.endowments}
        share = s.multiplier * math.fsum(given.values()) / len(given)  # a different summation
        payoffs = {r: s.endowments[r] - given[r] + share for r in s.endowments}
        return pg.PGOutcome(
            contributions=given, share=share, payoffs=payoffs, endowments=s.endowments,
            multiplier=s.multiplier, round=s.round, rounds=s.rounds,
        )  # fmt: skip

    rewritten = dataclasses.replace(original, outcome=outcome)
    old, cfg = compose.build_repeated_pg(["ann", "bo"], rounds=3)
    new, _ = compose.build_repeated_pg(["ann", "bo"], rounds=3, layer=rewritten)
    assert_substitution_equivalent(old, new, cfg, 5, {AgentId("ann"): {"amount": 4.0},
                                                     AgentId("bo"): {"amount": 2.0}})  # fmt: skip


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
