"""Input-log events (source of truth) and trace events (derived record), with JSON dump/load."""

from collections.abc import Iterable
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from gao_idl.core.ids import AgentId, DecisionId, Port
from gao_idl.core.schemas import (
    DecisionRequest,
    FrozenModel,
    ParticipantReport,
    PElem,
    UtilityIncrement,
)

# ---------------------------------------------------------------- input log


class Started(FrozenModel):
    """Session start: environment reference, parameters and root seed."""

    kind: Literal["started"] = "started"
    env_ref: str
    params: dict[str, Any]
    seed: int


class Submitted(FrozenModel):
    """An accepted action submission."""

    kind: Literal["submitted"] = "submitted"
    decision_id: DecisionId
    agent_id: AgentId
    action: dict[str, Any]


class TimedOut(FrozenModel):
    """A decision whose deadline passed."""

    kind: Literal["timed_out"] = "timed_out"
    decision_id: DecisionId


class ExternalResponse(FrozenModel):
    """A response from an external service recorded for a decision."""

    kind: Literal["external"] = "external"
    decision_id: DecisionId
    payload: dict[str, Any]


class Aborted(FrozenModel):
    """The session was aborted."""

    kind: Literal["aborted"] = "aborted"
    reason: str


_InputUnion = Started | Submitted | TimedOut | ExternalResponse | Aborted
InputEvent = Annotated[_InputUnion, Field(discriminator="kind")]
INPUT_EVENT_TYPES = (Started, Submitted, TimedOut, ExternalResponse, Aborted)

# -------------------------------------------------------------------- trace


class TraceBase(FrozenModel):
    """Fields common to every trace event."""

    t: int
    path: list[PElem]


class LayerEntered(TraceBase):
    """A layer instance was entered."""

    kind: Literal["layer_entered"] = "layer_entered"


class DecisionRequested(TraceBase):
    """A decision request was issued."""

    kind: Literal["decision_requested"] = "decision_requested"
    request: DecisionRequest


class ActionAccepted(TraceBase):
    """A submitted action was accepted into the layer."""

    kind: Literal["action_accepted"] = "action_accepted"
    decision_id: DecisionId
    agent_id: AgentId
    action: dict[str, Any]


class ActionRejected(TraceBase):
    """A submission was rejected; it never enters the input log."""

    kind: Literal["action_rejected"] = "action_rejected"
    decision_id: DecisionId
    agent_id: AgentId
    reason: str


class ActionDefaulted(TraceBase):
    """A timed-out decision was resolved with the layer's default action."""

    kind: Literal["action_defaulted"] = "action_defaulted"
    decision_id: DecisionId
    action: dict[str, Any]


class RoleDropped(TraceBase):
    """A timed-out decision's role was dropped from the layer."""

    kind: Literal["role_dropped"] = "role_dropped"
    decision_id: DecisionId


class LayerCompleted(TraceBase):
    """A layer computed its outcome."""

    kind: Literal["layer_completed"] = "layer_completed"
    layer: str
    actions: dict[str, dict[str, Any]]
    outcome: dict[str, Any]


class UtilityIncrementEvent(TraceBase):
    """A utility increment was credited."""

    kind: Literal["utility_increment"] = "utility_increment"
    increment: UtilityIncrement


class ReportDelivered(TraceBase):
    """A participant report was delivered."""

    kind: Literal["report_delivered"] = "report_delivered"
    report: ParticipantReport


class StoreAccess(TraceBase):
    """A layer read or wrote a store key."""

    kind: Literal["store_access"] = "store_access"
    key: str
    mode: Literal["read", "write"]


class Exited(TraceBase):
    """A process exited through a port."""

    kind: Literal["exited"] = "exited"
    port: Port
    record: dict[str, Any]


class Truncated(TraceBase):
    """A process was cut short (reserved for later milestones)."""

    kind: Literal["truncated"] = "truncated"
    reason: str


class Failed(TraceBase):
    """A process failed."""

    kind: Literal["failed"] = "failed"
    reason: str


_TraceUnion = (
    LayerEntered
    | DecisionRequested
    | ActionAccepted
    | ActionRejected
    | ActionDefaulted
    | RoleDropped
    | LayerCompleted
    | UtilityIncrementEvent
    | ReportDelivered
    | StoreAccess
    | Exited
    | Truncated
    | Failed
)
TraceEvent = Annotated[_TraceUnion, Field(discriminator="kind")]

# --------------------------------------------------------------- dump / load

_ADAPTER: TypeAdapter[Any] = TypeAdapter(
    list[Annotated[_InputUnion | _TraceUnion, Field(discriminator="kind")]]
)


def dump(events: Iterable[InputEvent | TraceEvent]) -> list[dict[str, Any]]:
    """Serialize events to JSON-able dicts (``LoopIter(i)`` -> ``{"loop": i}``, etc.)."""
    return [e.model_dump(mode="json") for e in events]


def load(data: Iterable[dict[str, Any]]) -> list[InputEvent | TraceEvent]:
    """Inverse of :func:`dump`."""
    out: list[InputEvent | TraceEvent] = _ADAPTER.validate_python(list(data))
    return out
