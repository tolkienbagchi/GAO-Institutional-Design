"""Frozen pydantic data objects shared by layers, sessions and traces."""

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer

from gao_idl.core.ids import AgentId, DecisionId, Port, ReportId, RoleId
from gao_idl.core.paths import Branch, LoopIter, PathElem


def _validate_elem(v: Any) -> Any:
    """Decode a JSON path element: str, ``{"loop": i}`` or ``{"branch": name}``."""
    if isinstance(v, str | LoopIter | Branch):
        return v
    if isinstance(v, dict) and len(v) == 1:
        if "loop" in v and isinstance(v["loop"], int) and not isinstance(v["loop"], bool):
            return LoopIter(v["loop"])
        if "branch" in v and isinstance(v["branch"], str):
            return Branch(v["branch"])
    raise ValueError(f"invalid path element: {v!r}")


def _serialize_elem(e: PathElem) -> str | dict[str, Any]:
    """Encode a path element for JSON."""
    if isinstance(e, LoopIter):
        return {"loop": e.i}
    if isinstance(e, Branch):
        return {"branch": e.name}
    return e


PElem = Annotated[
    PathElem,
    BeforeValidator(_validate_elem),
    PlainSerializer(_serialize_elem, when_used="json"),
]


class FrozenModel(BaseModel):
    """Base class for immutable data objects."""

    model_config = ConfigDict(frozen=True)


class Assignment(FrozenModel):
    """Mapping of roles to agents."""

    roles: dict[RoleId, AgentId]


class Config(FrozenModel):
    """Entry configuration of a layer: JSON state plus role assignment."""

    state: dict[str, Any]
    roles: dict[RoleId, AgentId]


class DecisionRequest(FrozenModel):
    """A pending decision addressed to one agent in one role."""

    decision_id: DecisionId
    path: list[PElem]
    layer: str
    role: RoleId
    agent_id: AgentId
    observation: dict[str, Any]
    action_schema: dict[str, Any]
    deadline: float | None = None


class ParticipantReport(FrozenModel):
    """Information delivered to an agent after a layer completes."""

    report_id: ReportId
    path: list[PElem]
    layer: str
    agent_id: AgentId
    payload: dict[str, Any]


class Exit(FrozenModel):
    """The port a process exited through, with its record."""

    port: Port
    record: dict[str, Any]


class UtilityIncrement(FrozenModel):
    """A utility amount credited to an agent at logical time ``t``."""

    agent_id: AgentId
    amount: float
    path: list[PElem]
    t: int
