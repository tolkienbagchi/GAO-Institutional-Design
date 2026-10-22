"""Participatory budgeting institution: nomination -> eligibility -> approval voting -> funding."""

from typing import Any

import pytest

from gao_idl.composition.contracts import check, describe
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.environments.pb import FUNDING, RULES, build_pb
from gao_idl.runtime.session import Session
from gao_idl.testing import assert_replay_equivalent

ENV = "gao_idl.environments.pb:build_pb"
Noms = dict[str, dict[str, Any]]
Ballots = dict[str, list[str]]


def params(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "proposers": ["alice", "bob"],
        "voters": ["alice", "bob", "carol"],
        "budget": 10,
        "max_cost": 8,
        "rule": "greedy",
    }
    base.update(over)
    return base


def nom(title: str, cost: float) -> dict[str, Any]:
    return {"title": title, "cost": cost}


def answer_all(
    session: Session, noms: Noms, ballots: Ballots, *, skip: frozenset[str] = frozenset()
) -> None:
    """Answer every pending request: nominations from ``noms``, ballots from ``ballots``."""
    for req in session.pending():
        if req.agent_id in skip:
            continue
        action = (
            noms[req.agent_id] if req.layer == "nomination" else {"approve": ballots[req.agent_id]}
        )
        session.submit(req.agent_id, req.decision_id, action)


def run_pb(
    p: dict[str, Any], noms: Noms, ballots: Ballots, seed: int = 3, *, reverse: bool = False
) -> Session:
    """Start a session and answer stage by stage until it finishes."""
    session = Session.start(ENV, p, seed)
    for _ in range(10):
        session.advance()
        if session.finished:
            return session
        pending = session.pending()
        for req in reversed(pending) if reverse else pending:
            action = (
                noms[req.agent_id]
                if req.layer == "nomination"
                else {"approve": ballots[req.agent_id]}
            )
            session.submit(req.agent_id, req.decision_id, action)
    raise AssertionError("session did not finish")


def reports(session: Session, agent: str) -> list[ParticipantReport]:
    return [
        i for i in session.ledger.information(AgentId(agent)) if isinstance(i, ParticipantReport)
    ]


def requests(session: Session, agent: str) -> list[DecisionRequest]:
    return [i for i in session.ledger.information(AgentId(agent)) if isinstance(i, DecisionRequest)]


def stream(session: Session, agent: str) -> list[dict[str, Any]]:
    """The agent's whole information stream as JSON, in delivery order."""
    return [i.model_dump(mode="json") for i in session.ledger.information(AgentId(agent))]


NOMS: Noms = {"alice": nom("Park", 4), "bob": nom("Library", 5)}
VOTES: Ballots = {"alice": ["p0"], "bob": ["p0", "p1"], "carol": ["p1"]}


# ------------------------------------------------------------------ construction and smoke


@pytest.mark.parametrize("rule", RULES)
def test_every_rule_completes_the_pipeline(rule: str) -> None:
    session = run_pb(params(rule=rule), NOMS, VOTES)
    assert session.finished and not session.failed
    assert session.result is not None and session.result.port == "done"
    record = session.result.record
    assert record["rule"] == rule
    assert record["funded"] == ["p0", "p1"]  # both projects (4 + 5) fit the budget of 10
    assert record["spent"] == 9.0
    assert_replay_equivalent(session)


def test_the_process_is_well_wired() -> None:
    process, cfg = build_pb(**params())
    check(process)
    assert sorted(process.exits) == ["done", "none"]
    assert sorted(cfg.roles) == ["proposer:0", "proposer:1"]
    tree = describe(process)
    assert tree["kind"] == "seq" and tree["name"] == "pb"
    assert set(FUNDING) == set(RULES)
