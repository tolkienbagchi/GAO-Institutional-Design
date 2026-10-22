"""Bounded waiting for async tests: a lost runner must fail a test, never hang it."""

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")
LIMIT = 5.0  # seconds; every wait on runner progress is far shorter when things work


async def soon(awaitable: Awaitable[T], limit: float = LIMIT) -> T:
    """Await ``awaitable`` but raise ``TimeoutError`` after ``limit`` seconds."""
    return await asyncio.wait_for(awaitable, limit)


def run_test(coro: Coroutine[Any, Any, T], limit: float = 2 * LIMIT) -> T:
    """``asyncio.run`` with an overall time limit for the whole test body."""
    return asyncio.run(soon(coro, limit))
