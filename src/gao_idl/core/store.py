"""Scoped JSON key-value store shared between layers; every access is traced."""

import copy
import json
from typing import Any

from gao_idl.core import events as ev
from gao_idl.core.errors import GaoError
from gao_idl.core.paths import Path
from gao_idl.core.trace import LogicalClock, TraceSink


class StoreScopeError(GaoError):
    """A store access outside the view's declared scope (a contract violation by the accessor)."""


class StoreReadDenied(StoreScopeError, KeyError):
    """A read of a key that is not in the view's read scope.

    Also a ``KeyError`` so that code written against the plain store keeps working; a ``KeyError``
    for an in-scope key that is merely unset stays a plain ``KeyError`` (a bug in the layer).
    """


class StoreWriteDenied(StoreScopeError, PermissionError):
    """A write to a key that is not in the view's write scope."""


class StoreView:
    """A layer's view of the store, restricted to declared read and write keys."""

    def __init__(
        self,
        data: dict[str, Any],
        clock: LogicalClock,
        reads: frozenset[str],
        writes: frozenset[str],
        trace: TraceSink,
        path: Path,
    ) -> None:
        self._data = data
        self._clock = clock
        self._reads = reads
        self._writes = writes
        self._trace = trace
        self._path = path

    def get(self, key: str) -> Any:
        """Return a copy of the value at ``key``.

        Raises StoreReadDenied if ``key`` is out of scope, a plain KeyError if in scope but unset.
        """
        if key not in self._reads:
            raise StoreReadDenied(f"store key {key!r} is not in this view's read scope")
        value = copy.deepcopy(self._data[key])
        self._trace.emit(
            ev.StoreAccess(t=self._clock.tick(), path=list(self._path), key=key, mode="read")
        )
        return value

    def set(self, key: str, value: Any) -> None:
        """Store a deep copy of the JSON-able ``value``; StoreWriteDenied if out of scope."""
        if key not in self._writes:
            raise StoreWriteDenied(f"store key {key!r} is not in this view's write scope")
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as e:
            raise ValueError(f"value for store key {key!r} is not JSON-able: {e}") from e
        self._data[key] = copy.deepcopy(value)
        self._trace.emit(
            ev.StoreAccess(t=self._clock.tick(), path=list(self._path), key=key, mode="write")
        )


class Store:
    """Shared state across layers; accessed only through scoped views."""

    def __init__(self, clock: LogicalClock, initial: dict[str, Any] | None = None) -> None:
        self._clock = clock
        self._data: dict[str, Any] = copy.deepcopy(initial) if initial else {}

    def view(
        self, reads: frozenset[str], writes: frozenset[str], trace: TraceSink, path: Path
    ) -> StoreView:
        """Create a view limited to ``reads`` / ``writes`` that traces to ``trace``."""
        return StoreView(self._data, self._clock, reads, writes, trace, path)
