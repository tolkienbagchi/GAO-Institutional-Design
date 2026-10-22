"""Per-agent utility increments and the ordered stream of information delivered to each agent.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import math
from typing import Any

from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport, UtilityIncrement

Information = DecisionRequest | ParticipantReport


class Ledger:
    """Accumulates utility and information per agent; contents are a pure fold of the input log."""

    def __init__(self) -> None:
        self._increments: dict[AgentId, list[UtilityIncrement]] = {}
        self._information: dict[AgentId, list[Information]] = {}

    def add_increment(self, inc: UtilityIncrement) -> None:
        """Credit ``inc`` to its agent."""
        self._increments.setdefault(inc.agent_id, []).append(inc)

    def utility(self, agent_id: AgentId) -> float:
        """Total utility credited to ``agent_id`` (0.0 if none)."""
        return math.fsum(i.amount for i in self._increments.get(agent_id, []))

    def increments(self, agent_id: AgentId) -> list[UtilityIncrement]:
        """Increments credited to ``agent_id`` in credit order."""
        return list(self._increments.get(agent_id, []))

    def record_information(self, agent_id: AgentId, item: Information) -> None:
        """Append ``item`` to the information delivered to ``agent_id``."""
        self._information.setdefault(agent_id, []).append(item)

    def information(self, agent_id: AgentId) -> list[Information]:
        """Everything delivered to ``agent_id``, in delivery order."""
        return list(self._information.get(agent_id, []))

    def snapshot(self) -> dict[str, Any]:
        """JSON-able snapshot."""
        return {
            "increments": {
                a: [i.model_dump(mode="json") for i in incs] for a, incs in self._increments.items()
            },
            "information": {
                a: [
                    {
                        "type": "request" if isinstance(x, DecisionRequest) else "report",
                        "item": x.model_dump(mode="json"),
                    }
                    for x in items
                ]
                for a, items in self._information.items()
            },
        }

    @classmethod
    def from_snapshot(cls, d: dict[str, Any]) -> "Ledger":
        """Inverse of :meth:`snapshot`."""
        ledger = cls()
        for agent, incs in d["increments"].items():
            for raw in incs:
                ledger.add_increment(UtilityIncrement.model_validate(raw))
            ledger._increments.setdefault(AgentId(agent), [])
        for agent, items in d["information"].items():
            ledger._information.setdefault(AgentId(agent), [])
            for entry in items:
                model = DecisionRequest if entry["type"] == "request" else ParticipantReport
                ledger.record_information(AgentId(agent), model.model_validate(entry["item"]))
        return ledger
