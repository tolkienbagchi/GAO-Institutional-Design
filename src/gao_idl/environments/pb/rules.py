"""Funding rules for approval-based participatory budgeting, as pure functions.

All four rules share one signature ``rule(projects, ballots, budget) -> list[str]`` and return the
funded project ids in ascending id order (natural order: ``p2`` before ``p10``), never above
``budget``. Common conventions:

* ``ballots`` maps *every* voter (also abstainers, with ``[]``) to the ids they approve; the number
  of voters ``n`` is ``len(ballots)``. A ballot may not repeat an id or name an unknown id
  (``ValueError``: nothing is silently ignored).
* A project is a *candidate* only if somebody approves it and its cost is at most the budget.
  Unapproved projects are never funded, so no rule spends money on projects nobody wants.
* All ties are broken by project id ascending (natural order). Costs and budgets are compared as
  exact decimal fractions (``Fraction(repr(x))``), so ``0.1 + 0.2 <= 0.3`` holds.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from gao_idl.core.ids import AgentId, role_sort_key
from gao_idl.environments.pb.schemas import Project

MAX_KNAPSACK_PROJECTS = 60
MAX_KNAPSACK_CELLS = 50_000_000
MAX_ADD1_ROUNDS = 100_000


def _id_key(project_id: str) -> tuple[tuple[tuple[int, int, str], ...], str]:
    """Natural-order sort key for project ids (``p2`` < ``p10``)."""
    return role_sort_key(project_id)


def _q(x: float | int) -> Fraction:
    """Exact decimal reading of a finite number (``0.1`` is one tenth, not its binary expansion)."""
    if isinstance(x, bool) or not math.isfinite(x):
        raise ValueError(f"expected a finite number, got {x!r}")
    return Fraction(repr(float(x)))


@dataclass(frozen=True)
class _Instance:
    """Validated, exact view of ``(projects, ballots, budget)``."""

    budget: Fraction
    voters: tuple[AgentId, ...]
    cost: dict[str, Fraction]
    supporters: dict[str, tuple[AgentId, ...]]
    candidates: tuple[str, ...]  # ascending id order

    def count(self, pid: str) -> int:
        """Number of voters approving ``pid``."""
        return len(self.supporters[pid])


def _prepare(
    projects: Sequence[Project], ballots: Mapping[AgentId, Sequence[str]], budget: float
) -> _Instance:
    b = _q(budget)
    if b < 0:
        raise ValueError(f"budget must be >= 0, got {budget!r}")
    cost: dict[str, Fraction] = {}
    for p in projects:
        if p.id in cost:
            raise ValueError(f"duplicate project id {p.id!r}")
        c = _q(p.cost)
        if c <= 0:
            raise ValueError(f"project {p.id!r} has non-positive cost {p.cost!r}")
        cost[p.id] = c
    voters = tuple(sorted(ballots))
    supporters: dict[str, list[AgentId]] = {pid: [] for pid in cost}
    for v in voters:
        approved = list(ballots[v])
        if len(set(approved)) != len(approved):
            raise ValueError(f"ballot of {v!r} repeats a project")
        for pid in approved:
            if pid not in cost:
                raise ValueError(f"ballot of {v!r} approves unknown project {pid!r}")
            supporters[pid].append(v)
    candidates = tuple(
        sorted((pid for pid in cost if supporters[pid] and cost[pid] <= b), key=_id_key)
    )
    return _Instance(
        budget=b,
        voters=voters,
        cost=cost,
        supporters={pid: tuple(vs) for pid, vs in supporters.items()},
        candidates=candidates,
    )


def _total(inst: _Instance, funded: Sequence[str]) -> Fraction:
    return sum((inst.cost[p] for p in funded), Fraction(0))


def total_cost(projects: Sequence[Project], funded: Sequence[str]) -> Fraction:
    """Exact (decimal) total cost of the projects with the given ids."""
    by_id = {p.id: _q(p.cost) for p in projects}
    return sum((by_id[i] for i in funded), Fraction(0))


def within_budget(projects: Sequence[Project], funded: Sequence[str], budget: float) -> bool:
    """Whether the funded ids cost at most ``budget``, compared exactly."""
    return total_cost(projects, funded) <= _q(budget)


def _greedy_order(inst: _Instance) -> list[str]:
    """Candidates by approval count desc, cost asc, id asc."""
    return sorted(inst.candidates, key=lambda p: (-inst.count(p), inst.cost[p], _id_key(p)))


def _greedy_fill(inst: _Instance, funded: list[str]) -> list[str]:
    """Add candidates in greedy order while they fit the money left; returns a new list."""
    out = list(funded)
    left = inst.budget - _total(inst, out)
    for p in _greedy_order(inst):
        if p not in out and inst.cost[p] <= left:
            out.append(p)
            left -= inst.cost[p]
    return out


# ------------------------------------------------------------------ greedy


def greedy(
    projects: Sequence[Project], ballots: Mapping[AgentId, Sequence[str]], budget: float
) -> list[str]:
    """Greedy by approvals: scan by approval count desc, cost asc, id asc; fund if it still fits.

    Zero-approval projects are never funded. Ties are broken by project id ascending.
    """
    inst = _prepare(projects, ballots, budget)
    return sorted(_greedy_fill(inst, []), key=_id_key)


# ------------------------------------------------------------------ exact knapsack


def knapsack(
    projects: Sequence[Project], ballots: Mapping[AgentId, Sequence[str]], budget: float
) -> list[str]:
    """Maximise the total number of approvals within the budget (exact DP over integer costs).

    Costs are scaled by 100 and must be exact multiples of 0.01 (``ValueError`` otherwise); the
    budget is floored to a whole number of cents, which loses nothing because costs are whole
    cents. Refuses more than 60 projects, and instances whose DP table would exceed
    50 million cells. Among all optimal sets the one with the lexicographically smallest
    ascending id list wins (equivalently: scanning ids ascending, include a project whenever an
    optimal solution still exists with it). Zero-approval projects are never funded.
    """
    if len(projects) > MAX_KNAPSACK_PROJECTS:
        raise ValueError(
            f"knapsack refuses {len(projects)} projects (limit {MAX_KNAPSACK_PROJECTS})"
        )
    inst = _prepare(projects, ballots, budget)
    items = list(inst.candidates)
    weight: dict[str, int] = {}
    for pid in items:
        cents = inst.cost[pid] * 100
        if cents.denominator != 1:
            raise ValueError(f"cost of {pid!r} is not a whole number of cents: {inst.cost[pid]}")
        weight[pid] = int(cents)
    cap = min(int(inst.budget * 100 // 1), sum(weight.values()))
    if (len(items) + 1) * (cap + 1) > MAX_KNAPSACK_CELLS:
        raise ValueError("knapsack instance too large for the exact DP")
    n = len(items)
    # best[i][c]: maximum approvals using items[i:] with capacity c
    best = [np.zeros(cap + 1, dtype=np.int64) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        w, a = weight[items[i]], inst.count(items[i])
        row = best[i + 1].copy()
        if w <= cap:
            row[w:] = np.maximum(row[w:], best[i + 1][: cap + 1 - w] + a)
        best[i] = row
    funded: list[str] = []
    c = cap
    for i, pid in enumerate(items):
        w, a = weight[pid], inst.count(pid)
        if w <= c and a + best[i + 1][c - w] == best[i][c]:
            funded.append(pid)
            c -= w
    return funded


# ------------------------------------------------------------------ Method of Equal Shares


def _rho(cost: Fraction, budgets: Sequence[Fraction]) -> Fraction | None:
    """Smallest ``rho`` with ``sum(min(b, rho)) >= cost`` over the budgets; None if impossible."""
    if sum(budgets, Fraction(0)) < cost:
        return None
    left, k = cost, len(budgets)
    for b in sorted(budgets):
        if b * k >= left:
            return left / k
        left -= b
        k -= 1
    return None  # unreachable: the total was checked above


def _equal_shares(
    inst: _Instance, endowment: Fraction
) -> tuple[list[tuple[str, Fraction]], dict[AgentId, Fraction]]:
    """One run of MES: each voter holds ``endowment``; returns the steps and the final budgets."""
    budgets = dict.fromkeys(inst.voters, endowment)
    remaining = list(inst.candidates)
    steps: list[tuple[str, Fraction]] = []
    while True:
        pick: tuple[Fraction, str] | None = None
        for pid in remaining:  # ascending id order, strict '<': ties go to the smaller id
            rho = _rho(inst.cost[pid], [budgets[v] for v in inst.supporters[pid]])
            if rho is not None and (pick is None or rho < pick[0]):
                pick = (rho, pid)
        if pick is None:
            return steps, budgets
        rho, pid = pick
        for v in inst.supporters[pid]:
            budgets[v] -= min(budgets[v], rho)
        remaining.remove(pid)
        steps.append((pid, rho))


def equal_shares_steps(
    projects: Sequence[Project],
    ballots: Mapping[AgentId, Sequence[str]],
    budget: float,
    *,
    endowment: float | None = None,
) -> list[tuple[str, Fraction]]:
    """One plain MES run (no completion): the funded ids in selection order with their ``rho``.

    ``endowment`` is each voter's initial money; the default is ``budget / n``. ``rho`` is the
    price per supporter at which the project was bought (cardinality utilities: every approved
    project is worth 1 to each approver).
    """
    inst = _prepare(projects, ballots, budget)
    if not inst.voters:
        return []
    start = inst.budget / len(inst.voters) if endowment is None else _q(endowment)
    return _equal_shares(inst, start)[0]


def _exhaustive(inst: _Instance, funded: Sequence[str]) -> bool:
    left = inst.budget - _total(inst, funded)
    return not any(p not in funded and inst.cost[p] <= left for p in inst.candidates)


def mes(
    projects: Sequence[Project], ballots: Mapping[AgentId, Sequence[str]], budget: float
) -> list[str]:
    """Method of Equal Shares (Peters & Skowron) with approval utilities, completed.

    Variant, exactly:

    1. *Core run* (Rule X / MES with cardinality utilities). Every voter starts with ``budget / n``.
       Repeatedly, a project ``c`` with supporters ``N_c`` is *rho-affordable* if
       ``sum(min(b_i, rho) for i in N_c) >= cost(c)``; buy the project with the smallest ``rho``
       (ties: smaller id), each supporter paying ``min(b_i, rho)``. Stop when nothing is
       affordable. Exact rational arithmetic throughout.
    2. *Add1 completion.* If the outcome is not exhaustive (some unfunded candidate still fits the
       money left), raise every voter's initial money by 1 currency unit and rerun from scratch;
       repeat until the outcome is exhaustive, or until a rerun would cost more than ``budget``,
       in which case the last run within budget is kept.
    3. *Greedy completion.* Whatever still fits is added in greedy order (approvals desc, cost
       asc, id asc).

    Ties anywhere are broken by project id ascending. With no voters nothing is funded.
    """
    inst = _prepare(projects, ballots, budget)
    if not inst.voters:
        return []
    endowment = inst.budget / len(inst.voters)
    kept: list[str] = []
    for round_ in range(MAX_ADD1_ROUNDS):
        steps, _ = _equal_shares(inst, endowment + round_)
        funded = [pid for pid, _ in steps]
        if _total(inst, funded) > inst.budget:
            break  # the previous run (round_ >= 1: round 0 never overspends) is the answer
        kept = funded
        if _exhaustive(inst, funded):
            break
    else:
        raise ValueError("Add1 completion did not terminate; costs are too large for unit steps")
    return sorted(_greedy_fill(inst, kept), key=_id_key)


# ------------------------------------------------------------------ sequential Phragmén


def phragmen_steps(
    projects: Sequence[Project], ballots: Mapping[AgentId, Sequence[str]], budget: float
) -> tuple[list[tuple[str, Fraction]], dict[AgentId, Fraction]]:
    """Sequential Phragmén: the funded ids in selection order with their purchase times, and loads.

    Voters earn money at rate 1 from time 0 and a project is bought as soon as its supporters
    jointly hold its cost; its supporters then spend everything they hold. With ``load_i`` the
    total money voter ``i`` has spent so far, a project's purchase time is
    ``(cost + sum of load_i over supporters) / |supporters|``; the project with the smallest time
    is considered next (ties: smaller id) and all its supporters get that time as their load.
    A project that does not fit the money left is discarded for good (the rule continues with the
    others). The result is the order of purchase and the final loads of all voters.
    """
    inst = _prepare(projects, ballots, budget)
    loads = dict.fromkeys(inst.voters, Fraction(0))
    remaining = list(inst.candidates)
    left = inst.budget
    steps: list[tuple[str, Fraction]] = []
    while remaining:
        pick: tuple[Fraction, str] | None = None
        for pid in remaining:  # ascending id order, strict '<': ties go to the smaller id
            sup = inst.supporters[pid]
            t = (inst.cost[pid] + sum((loads[v] for v in sup), Fraction(0))) / len(sup)
            if pick is None or t < pick[0]:
                pick = (t, pid)
        assert pick is not None
        t, pid = pick
        remaining.remove(pid)
        if inst.cost[pid] > left:
            continue
        left -= inst.cost[pid]
        for v in inst.supporters[pid]:
            loads[v] = t
        steps.append((pid, t))
    return steps, loads


def phragmen(
    projects: Sequence[Project], ballots: Mapping[AgentId, Sequence[str]], budget: float
) -> list[str]:
    """Sequential Phragmén with continuous loads (see :func:`phragmen_steps`), tie-break by id."""
    steps, _ = phragmen_steps(projects, ballots, budget)
    return sorted((pid for pid, _ in steps), key=_id_key)


RULE_FUNCTIONS = {"greedy": greedy, "knapsack": knapsack, "mes": mes, "phragmen": phragmen}
