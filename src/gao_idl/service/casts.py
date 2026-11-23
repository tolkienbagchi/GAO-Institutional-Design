"""Small scripted policies for the library's casts: auction bidders and PD players.

Each is a plain ``(request, reports) -> action`` function for ``ScriptedAgent`` and reads only
what the request and its own reports carry, like any other adapter. The carbon market's casts
use the policies in ``gao_idl.environments.vcm.scripted``.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from typing import Any

from gao_idl.core.schemas import DecisionRequest, ParticipantReport


def bid_value(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Auction: bid exactly your own value."""
    return {"amount": float(request.observation["value"])}


def bid_80_percent(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Auction: bid 80% of your own value (rounded to cents)."""
    return {"amount": round(0.8 * float(request.observation["value"]), 2)}


def always_cooperate(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Prisoner's dilemma: cooperate in every round."""
    return {"move": "C"}


def always_defect(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Prisoner's dilemma: defect in every round."""
    return {"move": "D"}


def tit_for_tat(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Prisoner's dilemma: cooperate first, then play what this opponent played last round."""
    history = request.observation.get("own_history") or []
    if not history:
        return {"move": "C"}
    return {"move": str(history[-1][1])}
