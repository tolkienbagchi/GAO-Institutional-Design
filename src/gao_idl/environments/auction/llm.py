"""Prompts for language-model bidders in the sealed-bid auction.

Everything here is built from the ``DecisionRequest`` observation and from report payloads, and
nothing else; a test greps this file's source to keep it that way.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json

from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.llm.backend import Message
from gao_idl.llm.codec import DefaultCodec, DefaultReportRenderer

RULES = {
    "first_price": "the highest bid wins and the winner pays their own bid",
    "second_price": "the highest bid wins and the winner pays the second-highest bid",
}
DISCLOSURE = {
    "all_bids_after_settlement": "every bidder's bid is revealed once the auction is settled",
    "winning_price_only": "only the winner and the price paid are revealed once it is settled",
}


def reserve_sentence(reserve: object) -> str:
    """The reserve price as one sentence; ``None`` means there is none."""
    if reserve is None:
        return "There is no reserve price."
    return f"Reserve price {reserve}: bids below {reserve} are ignored."


TIE_BREAKS = {"lowest_agent_id": "ties go to the bidder whose name sorts first"}


class AuctionCodec(DefaultCodec):
    """Readable prompt: own value, the rule, who bids, the disclosure policy, the reply format."""

    def system_prompt(self, request: DecisionRequest) -> str:
        """Who the model is and the reply format."""
        return (
            f"You are {request.agent_id}, a bidder in a sealed-bid auction for one item. "
            "All bidders submit a single bid at the same time and nobody sees another bid "
            "before the auction closes. Reply with a JSON object only."
        )

    def task_prompt(self, request: DecisionRequest) -> str:
        """The observation in words, then how to answer."""
        obs = request.observation
        rule = str(obs["rule"])
        others = [b for b in obs["bidders"] if b != request.agent_id]
        return "\n".join(
            [
                f"Your private value for the item is {obs['value']}.",
                f"Auction rule ({rule}): {RULES.get(rule, 'see the rule name')}.",
                f"Other bidders: {', '.join(others) if others else 'none'}.",
                reserve_sentence(obs.get("reserve")),
                f"Tie-break: {TIE_BREAKS.get(str(obs['tie_break']), str(obs['tie_break']))}.",
                "After the auction: "
                f"{DISCLOSURE.get(str(obs['bid_disclosure']), str(obs['bid_disclosure']))}.",
                "",
                'Submit your bid as {"amount": number}, a number that is at least 0.',
                "Reply with JSON only.",
            ]
        )


class AuctionReportRenderer(DefaultReportRenderer):
    """Reports as sentences; an unknown payload falls back to the default JSON rendering."""

    def render_report(self, report: ParticipantReport) -> Message:
        """A message about a bid receipt, an auction result or an unsold item."""
        p = report.payload
        if p.get("status") == "bid accepted":
            text = f"Your bid of {p['own_bid']} was accepted."
        elif p.get("status") == "unsold":
            text = (
                f"The item was not sold: nobody reached the reserve of {p['reserve']}. "
                f"Your bid was {p['own_bid']}."
            )
        elif "winner" in p and "own_payment" in p:
            text = (
                f"Result: {p['winner']} won and paid {p['winning_payment']}. "
                f"Your bid was {p['own_bid']}, you paid {p['own_payment']}, "
                f"your utility is {p['own_utility']}."
            )
            if p.get("all_bids") is not None:
                text += f" All bids: {json.dumps(p['all_bids'], sort_keys=True)}."
        else:
            return super().render_report(report)
        return Message(role="user", content=text)
