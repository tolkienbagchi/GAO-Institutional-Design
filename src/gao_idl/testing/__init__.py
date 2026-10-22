"""Reusable invariant checks for environments (replay, arrival order, non-interference,
substitution, branch independence).

These helpers raise ``AssertionError`` and have no pytest dependency, so milestones can reuse them.
They submit ``actions[agent]`` to every pending decision of that agent (so they assume an agent
wants to give the same action to each of its decisions; the runtime itself has no such limit).
"""

import itertools
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from gao_idl.composition.process import Process
from gao_idl.core import events
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import Config
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


# ------------------------------------------------------------- ad-hoc process sessions
#
# Session resolves environments by ``env_ref``. To run a Process object that is not importable by
# name (two structures being compared, a Par built on the fly), it is registered here for the
# duration of a ``with`` block and addressed through ``_adhoc_factory``. The key only lives in the
# ``Started`` event of these throwaway sessions; use sessions only inside the block.

_ADHOC: dict[str, tuple[Process, Config]] = {}
_ADHOC_KEYS = itertools.count()


def _adhoc_factory(key: str) -> tuple[Process, Config]:
    return _ADHOC[key]


@contextmanager
def _registered(process: Process, cfg: Config) -> Iterator[tuple[str, dict[str, Any]]]:
    key = f"adhoc-{next(_ADHOC_KEYS)}"
    _ADHOC[key] = (process, cfg)
    try:
        yield "gao_idl.testing:_adhoc_factory", {"key": key}
    finally:
        del _ADHOC[key]


MAX_ROUNDS = 1000


def _drive(session: Session, actions: dict[AgentId, dict[str, Any]]) -> None:
    """Advance, answer every pending decision with ``actions[agent]``, repeat until finished."""
    for _ in range(MAX_ROUNDS):
        session.advance()
        if session.finished:
            return
        pending = session.pending()
        if not pending:
            raise AssertionError("session is neither finished nor waiting on any decision")
        for request in pending:
            if request.agent_id not in actions:
                raise AssertionError(
                    f"no action given for {request.agent_id!r} ({request.decision_id})"
                )
            session.submit(request.agent_id, request.decision_id, actions[request.agent_id])
    raise AssertionError(f"session did not finish within {MAX_ROUNDS} rounds")


def _agents_in(session: Session) -> list[AgentId]:
    """Every agent that appears in the session's trace, sorted."""
    found: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "agent_id" and isinstance(v, str):
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(events.dump(session.trace))
    return sorted(AgentId(a) for a in found)


def _information_stripped(session: Session, agent: AgentId, drop_t: bool = False) -> list[Any]:
    items = [x.model_dump(mode="json") for x in session.ledger.information(agent)]
    return events.strip_paths(items, drop_t=drop_t)


def _run_env(
    process: Process, cfg: Config, seed: int, actions: dict[AgentId, dict[str, Any]]
) -> Session:
    """Run an ad-hoc process to completion. The session is for inspection only afterwards
    (replay needs the registry entry, which is removed on return)."""
    with _registered(process, cfg) as (ref, params):
        session = Session.start(ref, params, seed)
        _drive(session, actions)
    return session


def _first_difference(a: list[Any], b: list[Any], what: str) -> None:
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            raise AssertionError(f"{what} differ at index {i}: {x} != {y}")
    if len(a) != len(b):
        raise AssertionError(f"{what} have different lengths: {len(a)} vs {len(b)}")


def assert_substitution_equivalent(
    process_a: Process,
    process_b: Process,
    cfg: Config,
    seed: int,
    actions: dict[AgentId, dict[str, Any]],
) -> None:
    """Two structurally different processes must be indistinguishable apart from paths.

    Both are run on ``cfg`` with ``actions`` (until finished). Their results, per-agent utility,
    per-agent information streams and traces (including ``t``) must be equal after
    ``events.strip_paths``. Only deterministic layers qualify: random streams are keyed by path, so
    a layer that draws randomness sees a different stream once its path changes.
    """
    a = _run_env(process_a, cfg, seed, actions)
    b = _run_env(process_b, cfg, seed, actions)
    if (a.result, a.failed) != (b.result, b.failed):
        raise AssertionError(f"results differ: {a.result} (failed={a.failed}) vs {b.result}")
    for agent in sorted(set(_agents_in(a)) | set(_agents_in(b))):
        if a.ledger.utility(agent) != b.ledger.utility(agent):
            raise AssertionError(f"utility of {agent!r} differs")
        _first_difference(
            _information_stripped(a, agent),
            _information_stripped(b, agent),
            f"information streams of {agent!r}",
        )
    _first_difference(
        events.strip_paths(events.dump(a.trace)), events.strip_paths(events.dump(b.trace)), "traces"
    )


def assert_branch_rng_independence(
    build_par_env: Callable[[tuple[str, ...]], tuple[Process, Config]],
    seed: int,
    *,
    actions: dict[AgentId, dict[str, Any]] | None = None,
    branch: str = "A",
    other: str = "B",
) -> None:
    """A branch's trace must not depend on whether a sibling branch exists.

    ``build_par_env(names)`` builds a Par over exactly those branch names. The environment is run
    with ``(branch, other)`` and with ``(branch,)`` alone; the events under ``Branch(branch)`` and
    the information of the agents acting in it must be equal after ``strip_paths(drop_t=True)``
    (``t`` is dropped because the sibling's events interleave and shift the logical clock).
    """
    acts = actions or {}
    with_sibling = _run_env(*build_par_env((branch, other)), seed, acts)
    alone = _run_env(*build_par_env((branch,)), seed, acts)

    def of_branch(s: Session) -> list[dict[str, Any]]:
        mine = [d for d in events.dump(s.trace) if {"branch": branch} in d["path"]]
        return events.strip_paths(mine, drop_t=True)

    reference = of_branch(alone)
    if not reference:
        raise AssertionError(f"branch {branch!r} produced no events; nothing to compare")
    _first_difference(of_branch(with_sibling), reference, f"events of branch {branch!r}")
    for agent in _agents_in(alone):
        _first_difference(
            _information_stripped(with_sibling, agent, True),
            _information_stripped(alone, agent, True),
            f"information streams of {agent!r}",
        )
