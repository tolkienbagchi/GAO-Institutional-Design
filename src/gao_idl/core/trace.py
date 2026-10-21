"""Trace sink protocol and logical clock (kept in core so the store can emit trace events)."""

from typing import Protocol

from gao_idl.core.events import TraceEvent


class TraceSink(Protocol):
    """Receives trace events in emission order."""

    def emit(self, event: TraceEvent) -> None:
        """Record one trace event."""
        ...


class LogicalClock:
    """Monotone integer clock; ``tick()`` stamps the next event, never wall time."""

    def __init__(self) -> None:
        self._t = 0

    def tick(self) -> int:
        """Advance the clock and return the new time."""
        self._t += 1
        return self._t

    @property
    def now(self) -> int:
        """Time of the most recent tick (0 before any)."""
        return self._t
