"""The Process protocol: a resumable unit of execution, with step results and runtime context.

Stability: internal; it may change without notice. Use the names exported by ``gao_idl`` where
there is one.
"""

from dataclasses import dataclass, replace
from typing import Any, Protocol

from pydantic import BaseModel

from gao_idl.core.events import Submitted, TimedOut
from gao_idl.core.ids import AgentId, DecisionId, Port
from gao_idl.core.layer import Contract
from gao_idl.core.ledger import Ledger
from gao_idl.core.paths import Path
from gao_idl.core.schemas import Config, DecisionRequest, Exit, FrozenModel
from gao_idl.core.store import Store
from gao_idl.core.trace import LogicalClock, TraceSink

__all__ = [
    "Continue",
    "Ctx",
    "Exited",
    "FailedResult",
    "Frame",
    "LogicalClock",
    "Pending",
    "Process",
    "StepResult",
    "TraceSink",
]

Frame = BaseModel  # any frozen, JSON-round-trippable pydantic model; composites nest child frames


class Pending(FrozenModel):
    """The process is waiting on these decisions."""

    requests: list[DecisionRequest]


class Exited(FrozenModel):
    """The process finished through ``exit``."""

    exit: Exit


class FailedResult(FrozenModel):
    """The process failed."""

    reason: str


class Continue(FrozenModel):
    """Progress was made; step again without new inputs. Primitives never return this."""


StepResult = Pending | Continue | Exited | FailedResult


@dataclass(frozen=True)
class Ctx:
    """Runtime-supplied context for one process instance (``path`` is that instance's path)."""

    root_seed: int
    path: Path
    ledger: Ledger
    store: Store
    trace: TraceSink
    clock: LogicalClock

    def with_path(self, path: Path) -> "Ctx":
        """Same context for a different instance path."""
        return replace(self, path=path)


class Process(Protocol):
    """A resumable process. Frames hold data only and must round-trip through JSON."""

    @property
    def name(self) -> str: ...
    @property
    def entry(self) -> Contract: ...
    @property
    def exits(self) -> dict[Port, type[BaseModel]]: ...

    def start(self, cfg: Config, ctx: Ctx) -> Frame:
        """Initial frame; emits nothing."""
        ...

    def validate(
        self, frame: Frame, decision_id: DecisionId, agent_id: AgentId, action: dict[str, Any]
    ) -> BaseModel:
        """Parse an action for a pending decision.

        Raises UnknownDecision | WrongAgent | SchemaError | Infeasible; returns the parsed action.
        """
        ...

    def step(
        self, frame: Frame, inputs: list[Submitted | TimedOut], ctx: Ctx
    ) -> tuple[Frame, StepResult]:
        """Consume inputs and run until the process must wait, exits, or fails."""
        ...
