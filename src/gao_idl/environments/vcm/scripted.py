"""Scripted players for the carbon market: an honest verifier, a truthful seller, buyers.

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


def split_demand(obs: dict[str, Any], weights: dict[str, float] | None = None) -> dict[str, int]:
    """Spread the demand over the listed projects, evenly or in proportion to ``weights``.

    Whole credits by the largest-remainder method (ties to the earlier project), each capped at
    the project's quantity; credits a cap cuts off are not moved elsewhere.
    """
    projects = [p["project"] for p in obs["projects"]]
    demand = int(obs["demand"])
    w = {p: 1.0 for p in projects} if weights is None else {p: weights[p] for p in projects}
    total = sum(w.values())
    if total <= 0:
        return {p: 0 for p in projects}
    exact = {p: demand * w[p] / total for p in projects}
    qty = {p: int(exact[p]) for p in projects}
    leftover = demand - sum(qty.values())
    for p in sorted(projects, key=lambda p: (-(exact[p] - qty[p]), projects.index(p)))[:leftover]:
        qty[p] += 1
    cap = {p["project"]: int(p["quantity"]) for p in obs["projects"]}
    return {p: min(qty[p], cap[p]) for p in projects}


def _project_orders(obs: dict[str, Any], discount: bool) -> dict[str, Any]:
    track = obs.get("reputation") or {}
    quality: dict[str, float] = {}
    for p in obs["projects"]:
        bias = track.get(p["verifier"], {}).get("mean_error", 0.0) if discount else 0.0
        quality[p["project"]] = min(1.0, max(0.0, p["verified_quality"] - bias))
    split = split_demand(obs, quality)
    return {
        p: {"price": round(obs["value"] * quality[p], 4), "qty": q}
        for p, q in split.items()
        if q > 0
    }


def naive_project_buyer(
    request: DecisionRequest, reports: list[ParticipantReport]
) -> dict[str, Any]:
    """Per-project form: bid ``value`` times each reported quality; demand split in proportion."""
    return _project_orders(request.observation, False)


def skeptical_project_buyer(
    request: DecisionRequest, reports: list[ParticipantReport]
) -> dict[str, Any]:
    """Like ``naive_project_buyer`` but each project's quality is net of its verifier's past
    mean over-statement, if the track record is shown; this moves both bids and the split."""
    return _project_orders(request.observation, True)
