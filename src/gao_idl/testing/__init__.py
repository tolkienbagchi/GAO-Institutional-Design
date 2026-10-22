"""Reusable invariant checks for environments (replay, arrival order, non-interference,
substitution, branch independence).

These helpers raise ``AssertionError`` and have no pytest dependency, so milestones can reuse them.
They submit ``actions[agent]`` to every pending decision of that agent (so they assume an agent
wants to give the same action to each of its decisions; the runtime itself has no such limit).

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import itertools
import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from gao_idl.composition.process import Process
from gao_idl.core import events
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import Config
from gao_idl.runtime.session import Session

MAX_PERMUTED_AGENTS = 5
MAX_ROUNDS = 1000


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
    """Run to completion; in every round each agent in ``order`` answers all its pending decisions.

    All answers of a round are submitted before the next ``advance()``. In the first round every
    agent in ``order`` must have a pending decision; in later rounds agents without one are skipped.
    """
    session = Session.start(env_ref, params, seed)
    for round_ in range(MAX_ROUNDS):
        session.advance()
        if session.finished:
            return session
        submitted = False
        for agent in order:
            pending = session.pending_for(agent)
            if not pending:
                if round_ == 0:
                    raise AssertionError(f"agent {agent!r} has no pending decision to act on")
                continue
            for request in pending:
                session.submit(agent, request.decision_id, actions[agent])
            submitted = True
        if not submitted:
            waiting = sorted({r.agent_id for r in session.pending()})
            raise AssertionError(
                f"session is not finished and no agent in actions can act: {waiting}"
            )
    raise AssertionError(f"session did not finish within {MAX_ROUNDS} rounds")


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


def _branch_root(path: list[Any]) -> str | None:
    """JSON key of the prefix ending in the path's last ``Branch`` element (None: no Branch)."""
    last = None
    for i, elem in enumerate(path):
        if isinstance(elem, dict) and set(elem) == {"branch"}:
            last = i
    return None if last is None else json.dumps(path[: last + 1])


def _projections(session: Session) -> dict[str | None, list[dict[str, Any]]]:
    """Trace events (modulo ``t``) grouped by branch root; key None holds the Branch-free events.

    Every event belongs to exactly one group, so an event under nested Pars is grouped by its
    innermost branch and events of an outer branch outside any inner one form that branch's group.
    Within a group the relative order is preserved.
    """
    groups: dict[str | None, list[dict[str, Any]]] = {None: []}
    for d in _trace_without_t(session):
        groups.setdefault(_branch_root(d["path"]), []).append(d)
    return groups


def _snapshot(session: Session, *, drop_t: bool) -> dict[str, Any]:
    """The ledger snapshot; ``drop_t`` removes the logical time of utility increments."""
    snap = session.ledger.snapshot()
    if drop_t:
        for incs in snap["increments"].values():
            for inc in incs:
                inc.pop("t", None)
    return snap


def assert_arrival_invariant(
    env_ref: str, params: dict[str, Any], seed: int, actions: dict[AgentId, dict[str, Any]]
) -> None:
    """Outcomes must not depend on the order in which agents submit (projection invariance).

    The environment is run to completion once per submission order (all permutations of the agents
    in ``actions`` up to five agents, otherwise the sorted order, its reverse and every rotation);
    within a round every agent answers all its pending decisions in that order. Against the first
    run, every other run must have

    1. identical ``ledger.information(agent)`` for every agent (exact: these carry no ``t``);
    2. an identical ``ledger.snapshot()`` (exact, except that when the trace has events under a
       ``Branch`` the ``t`` of utility increments is ignored: it is the global logical clock);
    3. for every branch root (the path prefix ending in an event's last ``Branch`` element), an
       identical subsequence of trace events under that root, modulo ``t``;
    4. an identical subsequence of the events whose path contains no ``Branch``, modulo ``t``.

    Whole-trace equality is deliberately not required: a ``Par`` applies inputs in log order, so the
    interleaving of events of different branches legitimately follows the submission order, while
    each branch (and everything outside the Pars) must not. For an environment without ``Par``
    check 3 is vacuous and check 4 is the whole trace. ``t`` is dropped from trace events because
    the logical clock is global.
    """
    agents = sorted(actions)
    reference: Session | None = None
    ref_proj: dict[str | None, list[dict[str, Any]]] = {}
    has_branches = False
    for order in _orders(agents):
        session = _run(env_ref, params, seed, actions, order)
        proj = _projections(session)
        if reference is None:
            reference, ref_proj = session, proj
            has_branches = len(proj) > 1  # some event sits under a Branch: the clock interleaves
            continue
        for agent in sorted(set(_agents_in(session)) | set(_agents_in(reference))):
            if _information(session, agent) != _information(reference, agent):
                raise AssertionError(f"information for {agent!r} depends on order {order}")
        if _snapshot(session, drop_t=has_branches) != _snapshot(reference, drop_t=has_branches):
            raise AssertionError(f"ledger snapshot depends on order {order}")
        for root in sorted(set(proj) | set(ref_proj), key=lambda r: (r is not None, r or "")):
            if proj.get(root, []) != ref_proj.get(root, []):
                where = "outside every branch" if root is None else f"under branch root {root}"
                raise AssertionError(f"trace events {where} depend on arrival order {order}")


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
