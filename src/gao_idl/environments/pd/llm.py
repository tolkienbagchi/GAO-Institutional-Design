"""Prompts for language-model players in the repeated prisoner's dilemma.

Everything here is built from the ``DecisionRequest`` observation and from report payloads, and
nothing else. The prompt does not state a payoff table: the observation does not contain one, and
the player learns its own payoff after each round from the report.
"""

from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.llm.backend import Message
from gao_idl.llm.codec import DefaultCodec, DefaultReportRenderer


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
