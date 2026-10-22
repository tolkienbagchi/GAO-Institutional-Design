"""Memory kinds: what they keep and how they bound it."""

import pytest

from gao_idl.llm.backend import Message
from gao_idl.llm.memory import FullTranscript, NoMemory, Window


def msgs(n: int, start: int = 0) -> list[Message]:
    return [Message(role="user", content=str(i)) for i in range(start, start + n)]


def test_full_transcript_keeps_everything_in_order_and_returns_copies() -> None:
    m = FullTranscript()
    m.append(msgs(3))
    m.append(msgs(2, 3))
    assert [x.content for x in m.messages()] == ["0", "1", "2", "3", "4"]
    m.messages().clear()
    assert len(m.messages()) == 5


def test_window_keeps_the_last_k_messages() -> None:
    w = Window(3)
    w.append(msgs(2))
    assert [x.content for x in w.messages()] == ["0", "1"]
    w.append(msgs(4, 2))
    assert [x.content for x in w.messages()] == ["3", "4", "5"]
    assert len(w._items) == 3  # bounded storage, not just a bounded view


def test_window_zero_and_validation() -> None:
    w = Window(0)
    w.append(msgs(5))
    assert w.messages() == []
    for bad in (-1, 1.5, True, "3"):
        with pytest.raises(ValueError, match="k >= 0"):
            Window(bad)  # type: ignore[arg-type]


def test_no_memory_is_stateless() -> None:
    m = NoMemory()
    m.append(msgs(3))
    assert m.messages() == []


@pytest.mark.parametrize("memory", [FullTranscript(), Window(2), NoMemory()])
def test_clear_and_fresh(memory: FullTranscript | Window | NoMemory) -> None:
    memory.append(msgs(4))
    twin = memory.fresh()
    assert twin is not memory and twin.messages() == []
    assert type(twin) is type(memory)
    memory.clear()
    assert memory.messages() == []
    twin.append(msgs(1))
    assert memory.messages() == []  # a fresh memory does not share storage


def test_fresh_window_keeps_its_size() -> None:
    assert Window(7).fresh().k == 7
