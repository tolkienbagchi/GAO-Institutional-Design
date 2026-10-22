"""Codecs that let a tabular buyer (``gao_idl.learning.TableAgent``) play the per-project market.

The generic ``SchemaActCodec`` cannot enumerate a buyer's per-project orders (nested objects and
real-valued prices), and a table keyed by raw prices would never generalize from one round to the
next. ``MarkdownActCodec`` therefore makes the learner's choice small and meaningful: how far to
mark down, below the reported quality, the projects of one verifier; every other project is bid at
its reported quality, and the demand is spread evenly over the projects (as ``naive_project_buyer``
does). ``RecordObsCodec`` reduces the track record of that verifier to what a buyer can act on:
nothing shown, never audited, audited and never wrong, or caught being wrong. Both implement the
``ObsCodec`` / ``ActCodec`` protocols of ``gao_idl.learning`` and read only what a buyer is given.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json
from collections.abc import Hashable, Sequence
from typing import Any

from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.environments.vcm.scripted import split_demand

DEFAULT_MARKDOWNS = (0.0, 0.1, 0.2, 0.3, 0.4)


class RecordObsCodec:
    """State = what the buyer knows about one verifier's track record.

    ``("hidden",)`` when the observation carries no record; otherwise
    ``("record", caught, audited)`` with ``caught = 1`` if any audited report was wrong and
    ``audited = 1`` if any report was audited (both capped at 1).
    """

    def __init__(self, verifier: str) -> None:
        self.verifier = verifier

    def config(self) -> dict[str, Any]:
        """The constructor arguments (written into checkpoints to identify the codec)."""
        return {"verifier": self.verifier}

    def encode(self, request: DecisionRequest, since_last: list[ParticipantReport]) -> Hashable:
        """The state key for ``request``."""
        record = (request.observation.get("reputation") or {}).get(self.verifier)
        if record is None:
            return ("hidden",)
        return ("record", min(int(record["wrong"]), 1), min(int(record["audited"]), 1))


class MarkdownActCodec:
    """Actions = a markdown ``m`` from ``markdowns`` applied to ``verifier``'s projects.

    The order for a project is ``value * clip(reported - m)`` (``m = 0`` for the other verifiers'
    projects) for an even share of the demand. The table key of an action is its markdown; the
    codec remembers which markdown produced which action in ``actions`` (``encode`` is only valid
    for actions returned by the latest ``actions`` call of this instance). Actions that coincide
    (a markdown that changes no order) keep the smallest markdown.
    """

    def __init__(self, verifier: str, markdowns: Sequence[float] = DEFAULT_MARKDOWNS) -> None:
        self.verifier = verifier
        self.markdowns = [float(m) for m in markdowns]
        self._key: dict[str, float] = {}

    def config(self) -> dict[str, Any]:
        """The constructor arguments (written into checkpoints to identify the codec)."""
        return {"verifier": self.verifier, "markdowns": self.markdowns}

    def actions(self, request: DecisionRequest) -> list[dict[str, Any]]:
        """One order set per markdown, in the order of ``markdowns``."""
        obs = request.observation
        split = split_demand(obs)
        seen: dict[str, float] = {}
        out: list[dict[str, Any]] = []
        for m in self.markdowns:
            orders: dict[str, Any] = {}
            for p in obs["projects"]:
                cut = m if p["verifier"] == self.verifier else 0.0
                quality = min(1.0, max(0.0, p["verified_quality"] - cut))
                if split[p["project"]] > 0:
                    orders[p["project"]] = {
                        "price": round(obs["value"] * quality, 4),
                        "qty": split[p["project"]],
                    }
            key = json.dumps(orders, sort_keys=True)
            if key not in seen:
                seen[key] = m
                out.append(orders)
        self._key = seen
        return out

    def encode(self, action: dict[str, Any]) -> Hashable:
        """The markdown that produced ``action``."""
        return self._key[json.dumps(action, sort_keys=True)]
