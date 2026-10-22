"""Live smoke test against a real OpenAI-compatible endpoint; skipped unless configured.

    GAO_LLM_BASE_URL=http://localhost:8000/v1 GAO_LLM_MODEL=<model> \
        [GAO_LLM_API_KEY=<key>] pytest tests/llm/test_live.py

It only asserts that a session with one live model bidder runs to the end through the real HTTP
transport and that its replay needs no model. The model's bid itself is not checked.
"""

import os

import pytest
from pydantic import SecretStr

from gao_idl.core import events as ev
from gao_idl.environments.auction.llm import AuctionCodec, AuctionReportRenderer
from gao_idl.llm.agent import LLMAgent
from gao_idl.llm.openai_compat import OpenAICompatBackend
from gao_idl.llm.replay import ReplayBackend
from tests.llm.helpers import ALICE, AUCTION, BOB, bidders, play, truthful

pytestmark = pytest.mark.skipif(
    not os.environ.get("GAO_LLM_BASE_URL"), reason="set GAO_LLM_BASE_URL to run the live smoke test"
)


def test_live_auction_round_and_replay_without_inference() -> None:
    pytest.importorskip("httpx")
    key = os.environ.get("GAO_LLM_API_KEY")
    backend = OpenAICompatBackend(
        os.environ["GAO_LLM_BASE_URL"],
        os.environ.get("GAO_LLM_MODEL", "default"),
        credential=(lambda: SecretStr(key)) if key else None,
    )
    agent = LLMAgent(
        ALICE, backend, AuctionCodec(), report_renderer=AuctionReportRenderer(), max_tokens=64
    )
    session = play(AUCTION, bidders(alice=10.0, bob=6.0), {ALICE: agent, BOB: truthful()})
    assert session.finished
    assert agent.metadata(), "the model was never called"
    replayed = play(
        AUCTION,
        bidders(alice=10.0, bob=6.0),
        {ALICE: LLMAgent(ALICE, ReplayBackend(session.input_log), AuctionCodec()), BOB: truthful()},
    )
    assert ev.dump(replayed.trace) == ev.dump(session.trace)
    if key:
        assert key not in str(session.snapshot())
