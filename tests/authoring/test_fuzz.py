"""Edge-biased scripts: deterministic, replayable, valid on every shipped environment."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gao_idl import Session
from gao_idl.authoring import edge_scripts, load_spec, play, sample_action

ROOT = Path(__file__).resolve().parents[2]
AUCTION_PARAMS = {"values": {"alice": 10.0, "bob": 7.0, "carol": 4.0}, "reserve": 2.0}


def _envs() -> dict[str, tuple[str, dict[str, Any]]]:
    pb = json.loads((ROOT / "examples" / "configs" / "pb.json").read_text())["params"]
    vcm = load_spec(ROOT / "examples" / "specs" / "vcm.json").params
    return {
        "auction": ("gao_idl.environments.auction:build_auction", AUCTION_PARAMS),
        "pd": ("gao_idl.environments.pd:build_repeated_pd", {"agents": list("abcd"), "rounds": 3}),
        "pb": ("gao_idl.environments.pb:build_pb", pb),
        "vcm": ("gao_idl.environments.vcm:build_vcm", vcm),
        "congestion": ("gao_idl.environments.congestion:build_congestion", {"periods": 3}),
    }


def _all_replay(name: str, n: int) -> None:
    ref, params = _envs()[name]
    scripts = edge_scripts(ref, params, 7, n)
    assert len(scripts) == n
    finished = 0
    for script in scripts:
        session = play(ref, params, 7, script)  # raises on any action the environment rejects
        assert session.finished
        finished += not session.failed
        if session.failed:  # only a declared "the run stops" timeout may end a run early
            assert any(a is None for entries in script.values() for a in entries), name
    assert finished >= n // 2, "most runs should get to the end"


@pytest.mark.parametrize("name", ["auction", "pd", "pb", "vcm", "congestion"])
def test_scripts_run_on_every_shipped_environment(name: str) -> None:
    _all_replay(name, 30)


@pytest.mark.slow
@pytest.mark.parametrize("name", ["auction", "pd", "pb", "vcm", "congestion"])
def test_two_hundred_scripts_run_on_every_shipped_environment(name: str) -> None:
    _all_replay(name, 200)


def test_same_arguments_same_scripts() -> None:
    ref, params = _envs()["auction"]
    assert edge_scripts(ref, params, 3, 20) == edge_scripts(ref, params, 3, 20)
    assert edge_scripts(ref, params, 3, 20) != edge_scripts(ref, params, 4, 20)
    assert edge_scripts(ref, params, 3, 25)[:20] == edge_scripts(ref, params, 3, 20)


def test_scripts_lean_on_the_edges() -> None:
    ref, params = _envs()["auction"]
    scripts = edge_scripts(ref, params, 7, 200)
    bids = [[e[0]["amount"] if e[0] else None for e in s.values()] for s in scripts]
    assert any(None in b for b in bids), "somebody stays silent in some script"
    assert sum(None in b for b in bids) < 60, "but most scripts have no silence"
    played = [[x for x in b if x is not None] for b in bids]
    assert any(len(set(b)) < len(b) and max(b) > 2 for b in played), "ties above the reserve"
    assert any(2.0 in b for b in played), "a bid exactly at the reserve the bidder was shown"
    assert any(0.0 in b for b in played), "a bid at the lower bound"
    assert any(max(b) > 10 for b in played if b), "a bid above every value"


def test_sampler_handles_nested_schemas() -> None:
    ref, params = _envs()["vcm"]
    session = Session.start(ref, params, 1)
    session.advance()
    while not any(r.role.startswith("buyer") for r in session.pending()):
        for r in sorted(session.pending(), key=lambda r: str(r.decision_id)):
            session.submit(
                r.agent_id, r.decision_id, dict.fromkeys(r.action_schema["properties"], 0.5)
            )
        session.advance()
    buyer = next(r for r in session.pending() if r.role.startswith("buyer"))
    assert "$defs" in buyer.action_schema  # the case RandomAgent cannot play
    rng = np.random.default_rng(0)
    draws = [sample_action(buyer.action_schema, rng, [3.0]) for _ in range(200)]
    orders = [o for d in draws for o in d.values() if o is not None]
    assert orders and all(set(o) == {"price", "qty"} for o in orders)
    assert any(o["price"] == 3.0 for o in orders), "numbers from the pool are used"
    assert any(not d for d in draws) or any(None in d.values() for d in draws)
    plain = sample_action(buyer.action_schema, rng, [], plain=True)
    assert all(v is None for v in plain.values())
