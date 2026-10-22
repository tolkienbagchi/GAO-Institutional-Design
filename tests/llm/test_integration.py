"""Whole-system properties: secrets, mixed populations, concurrent decisions, import direction."""

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

import gao_idl
from gao_idl.agents import RandomAgent, ScriptedAgent
from gao_idl.core import events as ev
from gao_idl.core.ids import AgentId
from gao_idl.environments.pd.llm import PDCodec, PDReportRenderer
from gao_idl.learning import FieldObsCodec, QConfig, RewardExtractor, SchemaActCodec, TableAgent
from gao_idl.llm.agent import LLMAgent
from gao_idl.llm.backend import BackendError, Message, Response, current_decision
from gao_idl.llm.mock import MockBackend
from gao_idl.llm.openai_compat import HttpReply, OpenAICompatBackend
from gao_idl.llm.replay import ReplayBackend
from tests.llm.helpers import ALICE, AUCTION, BOB, CAPPED, PD, bidders, play, truthful

SECRET = "sk-TESTSECRET"
CAROL, DAVE = AgentId("carol"), AgentId("dave")


# ---------------------------------------------------------------------- secrets


class Transport:
    """A fake HTTP layer whose failures echo the request headers, as a careless client would."""

    def __init__(self, replies: list[HttpReply | Exception]) -> None:
        self.replies = list(replies)

    async def __call__(
        self, url: str, headers: dict[str, str], body: dict[str, Any], timeout_s: float
    ) -> HttpReply:
        item = self.replies.pop(0) if self.replies else RuntimeError(f"headers={headers}")
        if isinstance(item, Exception):
            raise item
        return item


async def no_sleep(delay: float) -> None:
    return None


def secret_backend(replies: list[HttpReply | Exception]) -> OpenAICompatBackend:
    return OpenAICompatBackend(
        "http://x/v1",
        "m",
        credential=lambda: SecretStr(SECRET),
        sleep=no_sleep,
        transport=Transport(replies),
    )


def reply(content: str) -> HttpReply:
    return HttpReply(
        200, json.dumps({"model": "m", "choices": [{"message": {"content": content}}]})
    )


def test_no_secret_in_logs_or_snapshot() -> None:
    failing = secret_backend([])  # every request raises with the Authorization header in its text
    with pytest.raises(BackendError) as info:
        asyncio.run(
            failing.complete(
                [Message(role="user", content="hi")], None, temperature=0, max_tokens=1, timeout_s=1
            )
        )
    assert SECRET not in str(info.value) and SECRET not in repr(info.value)

    agent = LLMAgent(ALICE, failing, record_text=True)
    session = play(CAPPED, bidders(alice=4.0, bob=3.0), {ALICE: agent, BOB: truthful()})
    assert "agent_failed" in [e.kind for e in session.trace]  # the transport error did happen
    assert SECRET not in json.dumps(session.snapshot())
    assert SECRET not in json.dumps([r.model_dump() for r in agent.metadata()])
    assert SECRET not in json.dumps(ev.dump(session.trace))
    assert SECRET not in json.dumps(agent.drain_external())

    ok = LLMAgent(
        ALICE,
        secret_backend([reply('{"amount": 2}')]),
        record_text=True,
        record_raw=True,
    )
    session = play(AUCTION, bidders(alice=4.0, bob=3.0), {ALICE: ok, BOB: truthful()})
    assert session.finished and not session.failed
    assert SECRET not in json.dumps(session.snapshot())
    assert SECRET not in json.dumps([r.model_dump() for r in ok.metadata()])


def test_a_credential_that_leaks_into_a_server_reply_is_scrubbed_from_the_log() -> None:
    echo = HttpReply(
        200,
        json.dumps(
            {
                "model": "m",
                "choices": [{"message": {"content": '{"amount": 2}'}}],
                "debug": {"auth": f"Authorization: Bearer {SECRET}"},
            }
        ),
    )
    agent = LLMAgent(ALICE, secret_backend([echo]), record_raw=True)
    session = play(AUCTION, bidders(alice=4.0, bob=3.0), {ALICE: agent, BOB: truthful()})
    assert SECRET not in json.dumps(session.snapshot())
    raw = [e for e in session.input_log if isinstance(e, ev.ExternalResponse)][0].payload["raw"]
    assert raw["debug"]["auth"] != f"Authorization: Bearer {SECRET}"


# ----------------------------------------------------------------- mixed population


def test_mixed_population_llm_table_scripted_pd() -> None:
    """An LLM, a learning table agent, a scripted agent and a random agent play 3 rounds."""
    mock = MockBackend(lambda messages: {"move": "C"})
    llm_agent = LLMAgent(
        ALICE, mock, PDCodec(), report_renderer=PDReportRenderer(), record_text=True
    )
    table = TableAgent(
        BOB,
        FieldObsCodec(["round_index"]),
        SchemaActCodec(),
        RewardExtractor(fields=["own_payoff"]),
        QConfig(alpha=0.2, gamma=0.9, epsilon=0.3),
        seed=4,
    )
    scripted = ScriptedAgent(lambda request, reports: {"move": "D"})
    params = {"agents": ["alice", "bob", "carol", "dave"], "rounds": 3, "matching": "round_robin"}
    session = play(
        PD, params, {ALICE: llm_agent, BOB: table, CAROL: scripted, DAVE: RandomAgent(9)}, seed=2
    )
    assert session.finished and not session.failed
    session.check_determinism()
    assert len(mock.calls) == 3

    moves: dict[str, list[tuple[str, float]]] = {}
    for agent in (ALICE, BOB, CAROL, DAVE):
        reports = session.reports_for(agent)
        assert len(reports) == 3
        moves[agent] = [(r.payload["own_move"], r.payload["own_payoff"]) for r in reports]
        # the ledger agrees with what the agent was told, to the cent
        assert session.ledger.utility(agent) == pytest.approx(sum(p for _, p in moves[agent]))
    # all payoffs handed out are accounted for by the ledger
    total = sum(session.ledger.utility(a) for a in (ALICE, BOB, CAROL, DAVE))
    assert total == pytest.approx(sum(p for ms in moves.values() for _, p in ms))
    assert [m for m, _ in moves[ALICE]] == ["C", "C", "C"] and [m for m, _ in moves[CAROL]] == [
        "D"
    ] * 3
    # the LLM agent's own record: three calls, no repairs
    assert [(c.attempt, c.failure) for c in llm_agent.metadata()] == [(1, None)] * 3


def test_a_replayed_mixed_population_is_identical() -> None:
    params = {"agents": ["alice", "bob", "carol", "dave"], "rounds": 2, "matching": "fixed"}

    def adapters(backend: Any) -> dict[AgentId, Any]:
        return {
            ALICE: LLMAgent(ALICE, backend, PDCodec()),
            BOB: ScriptedAgent(lambda request, reports: {"move": "C"}),
            CAROL: LLMAgent(CAROL, backend, PDCodec()),
            DAVE: RandomAgent(1),
        }

    # one mock serves both LLM agents; its script is chosen by which decision is asked about
    live_backend = MockBackend(
        lambda messages: {"move": "D" if "carol" in messages[0].content else "C"}
    )
    live = play(PD, params, adapters(live_backend), seed=8)
    again = play(PD, params, adapters(ReplayBackend(live.input_log)), seed=8)
    assert ev.dump(again.trace) == ev.dump(live.trace)
    assert ev.dump(again.input_log) == ev.dump(live.input_log)


# ------------------------------------------------------------ concurrent decisions


class Yielding:
    """A backend that gives up the event loop mid-call and answers by which decision is asked."""

    def __init__(self) -> None:
        self.asked: list[str | None] = []
        self.phases: list[str] = []

    async def complete(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None,
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> Response:
        did = current_decision.get()
        self.asked.append(did)
        self.phases.append("start")
        await asyncio.sleep(0)  # let the agent's other decision run in between
        x = 1 if did and did.endswith("player:0") else 2
        await asyncio.sleep(0)
        self.phases.append("end")
        return Response(text=json.dumps({"x": x}), parsed={"x": x}, model="y")


def test_one_agent_with_two_decisions_in_flight_keeps_them_apart() -> None:
    env = "tests.composition.envs:par_players"
    params = {"agents": {"A": ["alice", "alice"]}}  # one agent, two roles: two decisions at once
    backend = Yielding()
    agent = LLMAgent(ALICE, backend)
    session = play(env, params, {ALICE: agent}, seed=1)
    assert session.finished and not session.failed
    assert (
        len(backend.asked) == 2 and len(set(backend.asked)) == 2
    )  # both asked, each with its own id
    submitted = {e.decision_id: e.action for e in session.input_log if isinstance(e, ev.Submitted)}
    assert sorted(a["x"] for a in submitted.values()) == [1, 2]
    for did, action in submitted.items():
        assert action["x"] == (1 if did.endswith("player:0") else 2)
    # every raw reply is logged under the decision it answered, before that decision's submission
    log = session.input_log
    for did in submitted:
        ext = [
            i
            for i, e in enumerate(log)
            if isinstance(e, ev.ExternalResponse) and e.decision_id == did
        ]
        sub = [i for i, e in enumerate(log) if isinstance(e, ev.Submitted) and e.decision_id == did]
        assert len(ext) == 1 and ext[0] < sub[0]
    # and the whole thing replays without a model
    again = play(env, params, {ALICE: LLMAgent(ALICE, ReplayBackend(session.input_log))}, seed=1)
    assert ev.dump(again.input_log) == ev.dump(session.input_log)
    assert ev.dump(again.trace) == ev.dump(session.trace)


# --------------------------------------------------------------- import direction

SRC = Path(gao_idl.__file__).parent


def imports_of(path: Path) -> list[str]:
    return re.findall(r"^\s*(?:from|import) ([\w.]+)", path.read_text(), re.M)


def test_nothing_below_the_llm_package_imports_it() -> None:
    for sub in ("core", "composition", "runtime", "agents", "learning", "testing"):
        for path in (SRC / sub).rglob("*.py"):
            assert not [m for m in imports_of(path) if m.startswith("gao_idl.llm")], path
    allowed = {
        SRC / "environments" / "auction" / "llm.py",
        SRC / "environments" / "congestion" / "llm.py",
        SRC / "environments" / "vcm" / "llm.py",
        SRC / "environments" / "pd" / "llm.py",
    }
    for path in (SRC / "environments").rglob("*.py"):
        if path not in allowed:
            assert not [m for m in imports_of(path) if m.startswith("gao_idl.llm")], path


def test_the_llm_package_never_reaches_for_the_session_the_ledger_or_the_store() -> None:
    banned = ("gao_idl.runtime", "gao_idl.composition", "gao_idl.core.ledger", "gao_idl.core.store")
    for path in (SRC / "llm").rglob("*.py"):
        bad = [m for m in imports_of(path) if m.startswith(banned)]
        assert not bad, (path, bad)


def test_the_gate_import_check_core_and_runtime_do_not_pull_in_httpx() -> None:
    code = "import gao_idl.core, gao_idl.runtime, sys; assert 'httpx' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, env=dict(os.environ))
    code = (
        "import sys, gao_idl.llm.agent, gao_idl.llm.replay, gao_idl.environments.auction.llm, "
        "gao_idl.environments.pd.llm; assert 'httpx' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=dict(os.environ))
