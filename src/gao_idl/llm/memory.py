"""Agent-side conversation memory.

Contents are only ever the agent's own request prompts, its own emitted actions and the reports
addressed to it, appended by ``LLMAgent``. A memory never sees anything else.
"""

from typing import Protocol

from gao_idl.llm.backend import Message


class Memory(Protocol):
    """What an ``LLMAgent`` keeps between decisions."""

    def append(self, messages: list[Message]) -> None:
        """Remember ``messages`` (oldest first)."""
        ...

    def messages(self) -> list[Message]:
        """The remembered messages, oldest first (a copy)."""
        ...

    def clear(self) -> None:
        """Forget everything."""
        ...

    def fresh(self) -> "Memory":
        """An empty memory of the same kind (so a shared default instance is only a template)."""
        ...


class FullTranscript:
    """Remembers every message."""

    def __init__(self) -> None:
        self._items: list[Message] = []

    def append(self, messages: list[Message]) -> None:
        """Remember ``messages``."""
        self._items.extend(messages)

    def messages(self) -> list[Message]:
        """All remembered messages."""
        return list(self._items)

    def clear(self) -> None:
        """Forget everything."""
        self._items.clear()

    def fresh(self) -> "FullTranscript":
        """An empty transcript."""
        return FullTranscript()


class Window:
    """Remembers the last ``k`` messages (``k = 0`` remembers nothing)."""

    def __init__(self, k: int) -> None:
        if isinstance(k, bool) or not isinstance(k, int) or k < 0:
            raise ValueError(f"Window(k) needs an integer k >= 0, got {k!r}")
        self.k = k
        self._items: list[Message] = []

    def append(self, messages: list[Message]) -> None:
        """Remember ``messages``, dropping the oldest beyond ``k``."""
        self._items.extend(messages)
        del self._items[: max(0, len(self._items) - self.k)]

    def messages(self) -> list[Message]:
        """The last ``k`` messages."""
        return list(self._items)

    def clear(self) -> None:
        """Forget everything."""
        self._items.clear()

    def fresh(self) -> "Window":
        """An empty window of the same size."""
        return Window(self.k)


class NoMemory:
    """Stateless: every decision is asked from scratch (the ``None`` memory of the brief)."""

    def append(self, messages: list[Message]) -> None:
        """Remember nothing."""

    def messages(self) -> list[Message]:
        """Always empty."""
        return []

    def clear(self) -> None:
        """Nothing to forget."""

    def fresh(self) -> "NoMemory":
        """The same stateless memory."""
        return NoMemory()
