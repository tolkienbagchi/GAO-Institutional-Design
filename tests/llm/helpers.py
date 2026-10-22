"""Shared builders for the LLM tests (mock backends only, no network)."""

from typing import Any

from gao_idl.agents import AgentAdapter, ScriptedAgent
from gao_idl.core import events as ev
from gao_idl.core.ids import AgentId
from gao_idl.learning.opponents import SCRIPTED
from gao_idl.llm.agent import LLMAgent
from gao_idl.llm.mock import MockBackend
from gao_idl.runtime.runner import run
from gao_idl.runtime.session import Session
from tests.aio import run_test

AUCTION = "gao_idl.environments.auction:build_auction"
CAPPED = "tests.runtime.envs:bidding_capped"
PD = "gao_idl.environments.pd:build_repeated_pd"
ALICE, BOB = AgentId("alice"), AgentId("bob")


def bidders(**values: float) -> dict[str, Any]:
    return {"values": values}


def truthful() -> ScriptedAgent:
    return ScriptedAgent(SCRIPTED["truthful_bidder"])


def llm(script: list[Any], agent: str = "alice", **kw: Any) -> tuple[LLMAgent, MockBackend]:
    backend = MockBackend(script)
    return LLMAgent(AgentId(agent), backend, **kw), backend


def play(
    env_ref: str,
    params: dict[str, Any],
    adapters: dict[AgentId, AgentAdapter],
    seed: int = 0,
) -> Session:
    session = Session.start(env_ref, params, seed)
    run_test(run(session, adapters))
    return session


def kinds(session: Session) -> list[str]:
    return [e.kind for e in session.trace]


def submitted(session: Session) -> list[ev.Submitted]:
    return [e for e in session.input_log if isinstance(e, ev.Submitted)]
