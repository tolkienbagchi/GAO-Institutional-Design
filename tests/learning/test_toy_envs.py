"""The sanity environments, played by hand through the Session API."""

import itertools
from typing import Any

import pytest

from gao_idl.core.errors import ContractError, SchemaError
from gao_idl.core.ids import AgentId
from gao_idl.environments.toy import build_bandit, build_chain
from gao_idl.runtime.session import Session

BANDIT = "gao_idl.environments.toy:build_bandit"
CHAIN = "gao_idl.environments.toy:build_chain"
ME = AgentId("agent")


def play(session: Session, moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Answer every pending decision in turn; return the observations seen."""
    seen = []
    for action in moves:
        session.advance()
        (req,) = session.pending()
        seen.append(req.observation)
        session.submit(req.agent_id, req.decision_id, action)
    session.advance()
    return seen


# ------------------------------------------------------------------ bandit


def test_bandit_request_report_and_ledger() -> None:
    s = Session.start(BANDIT, {"means": [0.0, 1.0, 0.5]}, 1)
    s.advance()
    (req,) = s.pending()
    assert (req.layer, req.role, req.agent_id) == ("pull", "player", ME)
    assert req.observation == {"n_arms": 3}  # the means are private
    assert req.action_schema["properties"]["arm"] == {
        "maximum": 2,
        "minimum": 0,
        "title": "Arm",
        "type": "integer",
    }
    s.submit(ME, req.decision_id, {"arm": 1})
    s.advance()
    assert s.finished and not s.failed
    (rep,) = s.reports_for(ME)
    assert rep.layer == "settle" and rep.payload == {"arm": 1, "own_reward": 1.0}
    assert s.ledger.utility(ME) == 1.0
    s.check_determinism()


def test_bandit_extreme_arms_are_deterministic_and_means_are_respected() -> None:
    rewards = {0.0: 0, 1.0: 0}
    for seed in range(20):
        for mean in rewards:
            s = Session.start(BANDIT, {"means": [mean]}, seed)
            play(s, [{"arm": 0}])
            rewards[mean] += int(s.ledger.utility(ME))
    assert rewards == {0.0: 0, 1.0: 20}
    hits = 0
    for seed in range(400):
        s = Session.start(BANDIT, {"means": [0.3]}, seed)
        play(s, [{"arm": 0}])
        hits += int(s.ledger.utility(ME))
    assert 90 < hits < 150  # Bernoulli(0.3): mean 120, sd about 9


def test_bandit_reward_depends_only_on_session_seed() -> None:
    def pull(seed: int) -> float:
        s = Session.start(BANDIT, {"means": [0.5, 0.5]}, seed)
        play(s, [{"arm": 1}])
        return s.ledger.utility(ME)

    assert [pull(s) for s in range(30)] == [pull(s) for s in range(30)]
    assert len({pull(s) for s in range(30)}) == 2


def test_bandit_rejects_bad_arms_and_bad_means() -> None:
    s = Session.start(BANDIT, {"means": [0.2, 0.8]}, 1)
    s.advance()
    (req,) = s.pending()
    for bad in ({"arm": 2}, {"arm": -1}, {"arm": 0.5}, {"arm": "0"}, {}):
        with pytest.raises(SchemaError):
            s.submit(ME, req.decision_id, bad)
    for means in ([], [1.5], [-0.1], [float("nan")], [True]):
        with pytest.raises(ContractError):
            build_bandit(means)  # type: ignore[arg-type]


def test_bandit_agent_id_is_a_parameter() -> None:
    s = Session.start(BANDIT, {"means": [1.0], "agent": "solo"}, 1)
    s.advance()
    assert s.pending()[0].agent_id == "solo"


# ------------------------------------------------------------------ chain


def returns_of(moves: list[str]) -> float:
    s = Session.start(CHAIN, {}, 1)
    play(s, [{"move": m} for m in moves])
    assert s.finished and not s.failed
    return s.ledger.utility(ME)


def test_chain_observation_is_the_state_index() -> None:
    s = Session.start(CHAIN, {}, 1)
    seen = play(s, [{"move": m} for m in ("right", "right", "left")])
    assert seen == [{"state": 0}, {"state": 1}, {"state": 2}]


def test_chain_rewards_for_every_policy_of_three_moves() -> None:
    table = {}
    for moves in itertools.product(("left", "right"), repeat=3):
        table[moves] = returns_of(list(moves))
    # Reward 1 exactly on the transition 1 --right--> 2; "left" at state 0 stays in state 0.
    assert max(table.values()) == 1.0  # never more than once within 3 steps
    assert [m for m, v in table.items() if v == 1.0] == [
        ("left", "right", "right"),
        ("right", "right", "left"),
        ("right", "right", "right"),  # staying in state 2 pays nothing more
    ]


def test_chain_reports_each_step_and_ends_normally_after_the_horizon() -> None:
    s = Session.start(CHAIN, {}, 1)
    play(s, [{"move": "right"}, {"move": "right"}, {"move": "right"}])
    assert [r.payload for r in s.reports_for(ME)] == [
        {"own_reward": 0.0, "position": 1},
        {"own_reward": 1.0, "position": 2},
        {"own_reward": 0.0, "position": 2},
    ]
    assert s.result is not None and s.result.port == "done"  # not "truncated"
    s.check_determinism()


def test_chain_decision_ids_are_path_based() -> None:
    s = Session.start(CHAIN, {}, 1)
    ids = []
    for _ in range(3):
        s.advance()
        (req,) = s.pending()
        ids.append(str(req.decision_id))
        s.submit(ME, req.decision_id, {"move": "left"})
    assert ids == ["chain/loop[0]#player", "chain/loop[1]#player", "chain/loop[2]#player"]


def test_chain_horizon_and_validation() -> None:
    s = Session.start(CHAIN, {"horizon": 1}, 1)
    play(s, [{"move": "right"}])
    assert s.finished and s.ledger.utility(ME) == 0.0
    for bad in (0, -1, True):
        with pytest.raises(ContractError):
            build_chain(horizon=bad)  # type: ignore[arg-type]
    with pytest.raises(SchemaError):
        s = Session.start(CHAIN, {}, 1)
        s.advance()
        s.submit(ME, s.pending()[0].decision_id, {"move": "up"})
