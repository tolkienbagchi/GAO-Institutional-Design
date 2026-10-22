"""Scripted players for the congestion environment: a myopic commuter and a greedy regulator.

Both are plain ``(request, reports) -> action`` policies for ``ScriptedAgent``. They read only
the observation and the reports they were sent, like any other adapter.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from typing import Any

from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.environments.congestion.schemas import TOLL_MENU

_ORDER = ("tolled", "bypass", "transit")


def myopic_commuter(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Take the cheapest feasible route at last period's times and this period's toll.

    Cost is ``value_of_time * time + toll`` on ``tolled``, ``value_of_time * time`` on
    ``bypass`` and ``value_of_time * time + fare`` on ``transit``; ties go to the earlier of
    ``tolled``, ``bypass``, ``transit``. Without a car only ``transit`` is considered.
    """
    obs = request.observation
    extra = {"tolled": obs["toll"], "bypass": 0.0, "transit": obs["fare"]}
    options = [r for r in _ORDER if r == "transit" or obs["car_available"]]
    cost = {r: obs["value_of_time"] * obs["route_times"][r] + extra[r] for r in options}
    return {"route": min(options, key=lambda r: (cost[r], _ORDER.index(r)))}


def greedy_regulator(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Try each toll on the menu once, in order, then repeat the one with the lowest objective.

    The objective is read from the report the regulator was sent (which already reflects the
    reporting regime); a toll tried twice counts with its latest value; ties go to the lower
    toll.
    """
    menu = list(request.observation["menu"])
    seen: dict[int, float] = {}
    for r in reports:
        if {"toll", "objective"} <= r.payload.keys():
            seen[int(r.payload["toll"])] = float(r.payload["objective"])
    untried = [t for t in menu if t not in seen]
    if untried:
        return {"toll": untried[0]}
    return {"toll": min(menu, key=lambda t: (seen[t], t))}


__all__ = ["TOLL_MENU", "greedy_regulator", "myopic_commuter"]
