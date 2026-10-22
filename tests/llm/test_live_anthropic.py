"""Live smoke test against the real Messages API; skipped unless configured.

    GAO_ANTHROPIC_API_KEY=<key> [GAO_ANTHROPIC_MODEL=<model>] [GAO_ANTHROPIC_BASE_URL=<url>] \
        pytest tests/llm/test_live_anthropic.py

It only asserts that a session with one live model bidder runs to the end through the real HTTP
transport with structured output, and that its replay needs no model. The bid itself is not checked.
"""

import os

import pytest
from pydantic import SecretStr

from gao_idl.core import events as ev
from gao_idl.environments.auction.llm import AuctionCodec, AuctionReportRenderer
from gao_idl.llm.agent import LLMAgent
from gao_idl.llm.anthropic import AnthropicBackend
from gao_idl.llm.replay import ReplayBackend
from tests.llm.helpers import ALICE, AUCTION, BOB, bidders, play, truthful

pytestmark = pytest.mark.skipif(
    not os.environ.get("GAO_ANTHROPIC_API_KEY"),
    reason="set GAO_ANTHROPIC_API_KEY to run the live Anthropic smoke test",
)


def test_live_auction_round_and_replay_without_inference() -> None:
    pytest.importorskip("httpx")
    key = os.environ["GAO_ANTHROPIC_API_KEY"]
    backend = AnthropicBackend(
        os.environ.get("GAO_ANTHROPIC_MODEL", "claude-haiku-4-5"),
        lambda: SecretStr(key),
        base_url=os.environ.get("GAO_ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
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
    assert key not in str(session.snapshot())
