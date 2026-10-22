"""Prompts for language-model players in the repeated prisoner's dilemma.

Everything here is built from the ``DecisionRequest`` observation and from report payloads, and
nothing else. The payoff table is stated only when the observation carries one
(``build_repeated_pd(disclose_payoffs=True)``); otherwise the prompt says nothing about payoffs
beyond the player learning its own after each round.
"""

from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.llm.backend import Message
from gao_idl.llm.codec import DefaultCodec, DefaultReportRenderer


def _payoff_table(payoffs: dict[str, list[float]]) -> list[str]:
    """The four move pairs as ``your move / their move -> your payoff, their payoff``."""
    lines = ["Payoffs (your move, their move: your payoff, their payoff):"]
    for key in ("CC", "CD", "DC", "DD"):
        mine, theirs = payoffs[key]
        lines.append(f"  {key[0]}, {key[1]}: {mine:g}, {theirs:g}")
    return lines


class PDCodec(DefaultCodec):
    """Own history against this opponent only, then ask for ``{"move": "C" | "D"}``."""

    def system_prompt(self, request: DecisionRequest) -> str:
        """Who the model is and the reply format."""
        return (
            f"You are {request.agent_id}, playing a repeated two-player game. In every round "
            "you and the other player choose at the same time between C (cooperate) and "
            "D (defect). After each round you are told both moves and your own payoff. "
            "Reply with a JSON object only."
        )

    def task_prompt(self, request: DecisionRequest) -> str:
        """This round's index and what happened in earlier rounds against this opponent."""
        obs = request.observation
        lines = [f"This is round {obs['round_index']} (counting from 0)."]
        payoffs = obs.get("payoffs")
        if payoffs:
            lines += ["", *_payoff_table(payoffs), ""]
        history = obs["own_history"]
        if history:
            lines.append("Earlier rounds against this opponent (your move, their move):")
            lines += [f"  round {i}: {own}, {other}" for i, (own, other) in enumerate(history)]
        else:
            lines.append("You have not played this opponent before.")
        lines += ["", 'Choose your move: {"move": "C"} or {"move": "D"}.', "Reply with JSON only."]
        return "\n".join(lines)


class PDReportRenderer(DefaultReportRenderer):
    """The result of a round as a sentence."""

    def render_report(self, report: ParticipantReport) -> Message:
        """Both moves and the player's own payoff."""
        p = report.payload
        if not {"round_index", "own_move", "other_move", "own_payoff"} <= p.keys():
            return super().render_report(report)
        return Message(
            role="user",
            content=(
                f"Round {p['round_index']} result: you played {p['own_move']}, the other player "
                f"played {p['other_move']}, your payoff was {p['own_payoff']}."
            ),
        )
