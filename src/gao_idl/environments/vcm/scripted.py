"""Scripted players for the carbon market: an honest verifier, a truthful seller, two buyers.

Plain ``(request, reports) -> action`` policies for ``ScriptedAgent``. They read only the
observation, like any other adapter.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from typing import Any

from gao_idl.core.schemas import DecisionRequest, ParticipantReport


def honest_verifier(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Report exactly what was measured."""
    return {p: float(x) for p, x in request.observation["measurements"].items()}


def truthful_seller(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Offer the whole quantity at cost, the lowest price at which selling does not lose money."""
    obs = request.observation
    return {"price": float(obs["cost"]), "qty": int(obs["quantity"])}


def _basket_quality(obs: dict[str, Any], discount: bool) -> float:
    """Quantity-weighted mean quality of the listed projects, optionally net of the verifiers'
    average past over-statement (the ``mean_error`` of their track record, 0 if unknown)."""
    track = obs.get("reputation") or {}
    total = sum(p["quantity"] for p in obs["projects"])
    weighted = 0.0
    for p in obs["projects"]:
        bias = track.get(p["verifier"], {}).get("mean_error", 0.0) if discount else 0.0
        weighted += p["quantity"] * min(1.0, max(0.0, p["verified_quality"] - bias))
    return float(weighted / total)


def naive_buyer(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Bid ``value`` times the reported quality of the listed projects for the whole demand."""
    obs = request.observation
    return {
        "price": round(obs["value"] * _basket_quality(obs, False), 4),
        "qty": int(obs["demand"]),
    }


def skeptical_buyer(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Like ``naive_buyer`` but subtracts each verifier's past mean over-statement, if shown."""
    obs = request.observation
    return {"price": round(obs["value"] * _basket_quality(obs, True), 4), "qty": int(obs["demand"])}
