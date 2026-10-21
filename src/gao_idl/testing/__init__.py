"""Reusable invariant checks for environments (replay, arrival order, non-interference).

These helpers raise ``AssertionError`` and have no pytest dependency, so milestones can reuse them.
They assume each acting agent holds one pending decision and submit ``actions[agent]`` to it.
"""

import itertools
from collections.abc import Iterator, Sequence
from typing import Any

from gao_idl.core import events
from gao_idl.core.ids import AgentId
from gao_idl.runtime.session import Session

MAX_PERMUTED_AGENTS = 5


def assert_replay_equivalent(session: Session) -> None:
    """Replaying the session's input log must reproduce its trace exactly."""
    session.check_determinism()


def assert_incremental_equals_fold(session: Session) -> None:
    """Cached session state equals the fold of its input log (alias of replay check in M0)."""
    session.check_determinism()


def _orders(agents: Sequence[AgentId]) -> Iterator[tuple[AgentId, ...]]:
    """All submission orders for up to 5 agents; otherwise sorted, reversed and every rotation."""
    if len(agents) <= MAX_PERMUTED_AGENTS:
        yield from itertools.permutations(agents)
        return
    base = tuple(agents)
    yield base
    yield base[::-1]
    for k in range(1, len(base)):
        yield base[k:] + base[:k]


def _run(
    env_ref: str,
    params: dict[str, Any],
    seed: int,
    actions: dict[AgentId, dict[str, Any]],
    order: Sequence[AgentId],
) -> Session:
    session = Session.start(env_ref, params, seed)
    session.advance()
    for agent in order:
        pending = session.pending_for(agent)
        if not pending:
            raise AssertionError(f"agent {agent!r} has no pending decision to act on")
        for request in pending:
            session.submit(agent, request.decision_id, actions[agent])
    session.advance()
    return session


def _trace_without_t(session: Session) -> list[dict[str, Any]]:
    out = events.dump(session.trace)
    for d in out:
        d.pop("t", None)
        inc = d.get("increment")
        if isinstance(inc, dict):
            inc.pop("t", None)
    return out


def _information(session: Session, agent: AgentId) -> list[dict[str, Any]]:
    return [x.model_dump(mode="json") for x in session.ledger.information(agent)]


def assert_arrival_invariant(
    env_ref: str, params: dict[str, Any], seed: int, actions: dict[AgentId, dict[str, Any]]
) -> None:
    """Traces (modulo ``t``) and information streams must not depend on submission order."""
    agents = sorted(actions)
    reference: Session | None = None
    for order in _orders(agents):
        session = _run(env_ref, params, seed, actions, order)
        if reference is None:
            reference = session
            continue
        if _trace_without_t(session) != _trace_without_t(reference):
            raise AssertionError(f"trace depends on arrival order; differing order: {order}")
        for agent in agents:
            if _information(session, agent) != _information(reference, agent):
                raise AssertionError(f"information for {agent!r} depends on order {order}")


def assert_noninterference(
    env_ref: str,
    params_a: dict[str, Any],
    params_b: dict[str, Any],
    seed: int,
    actions: dict[AgentId, dict[str, Any]],
    agents: list[AgentId],
) -> None:
    """Information delivered to ``agents`` must be identical under ``params_a`` and ``params_b``."""
    order = sorted(actions)
    a = _run(env_ref, params_a, seed, actions, order)
    b = _run(env_ref, params_b, seed, actions, order)
    for agent in agents:
        if _information(a, agent) != _information(b, agent):
            raise AssertionError(
                f"information for {agent!r} differs between the two parameter sets"
            )
