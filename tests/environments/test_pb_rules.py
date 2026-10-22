"""Participatory-budgeting funding rules as pure functions: worked examples and properties.

Fixtures marked LITERATURE come from a published example; each was also re-derived by hand from
the rule definitions in ``environments/pb/rules.py`` (see NOTES.md for how they were obtained and
what could not be verified). Fixtures marked HAND are mine.

The hypothesis versions of the randomized tests live in ``test_pb_rules_property.py``.
"""

import itertools
from collections.abc import Mapping, Sequence
from fractions import Fraction as F

import numpy as np
import pytest

from gao_idl.core.ids import AgentId
from gao_idl.environments.pb.rules import (
    RULE_FUNCTIONS,
    equal_shares_steps,
    greedy,
    knapsack,
    mes,
    phragmen,
    phragmen_steps,
)
from gao_idl.environments.pb.schemas import Project

Ballots = Mapping[AgentId, Sequence[str]]


def projects(*costs: float, ids: Sequence[str] | None = None) -> list[Project]:
    names = list(ids) if ids is not None else [f"p{i}" for i in range(len(costs))]
    return [
        Project(id=n, title=f"title {n}", cost=c, proposer=AgentId("x"))
        for n, c in zip(names, costs, strict=True)
    ]


def ballots(**by_voter: Sequence[str]) -> dict[AgentId, list[str]]:
    return {AgentId(v): list(ps) for v, ps in by_voter.items()}


# ------------------------------------------------------------------ pure-function basics


def test_greedy_feasible_and_deterministic() -> None:
    projs = projects(6, 5, 5)  # p0 is the most popular but blocks the other two
    b = ballots(v1=["p0", "p1"], v2=["p0", "p1"], v3=["p0", "p2"], v4=["p2"])
    assert greedy(projs, b, 10) == ["p0"]
    # the result does not depend on the order of the inputs
    assert greedy(list(reversed(projs)), dict(reversed(list(b.items()))), 10) == ["p0"]
    # ... nor on the order inside a ballot
    shuffled = ballots(v1=["p1", "p0"], v2=["p1", "p0"], v3=["p2", "p0"], v4=["p2"])
    assert greedy(projs, shuffled, 10) == ["p0"]
    for budget in (0, 4.99, 5, 10, 11, 16, 100):
        funded = greedy(projs, b, budget)
        assert sum(p.cost for p in projs if p.id in funded) <= budget


def test_greedy_orders_by_approvals_then_cost_then_id() -> None:
    # p0, p1, p2 all have 2 approvals; the cheapest goes first, then the smaller id.
    projs = projects(3, 2, 2, ids=["p0", "p1", "p2"])
    b = ballots(v1=["p0", "p1", "p2"], v2=["p0", "p1", "p2"])
    assert greedy(projs, b, 4) == ["p1", "p2"]  # p1 (cost 2, id p1) then p2; p0 does not fit
    assert greedy(projs, b, 2) == ["p1"]  # the tie between p1 and p2 goes to p1


def test_ids_are_ordered_naturally() -> None:
    projs = projects(*([1] * 11))
    everyone = [p.id for p in projs]
    out = greedy(projs, ballots(v=everyone), 11)
    assert out == [f"p{i}" for i in range(11)]  # p2 before p10
    # with room for one project only, the tie goes to p0 rather than lexicographic p0 < p1 < p10
    assert greedy(projs, ballots(v=everyone), 1) == ["p0"]


def test_decimal_costs_are_compared_exactly() -> None:
    projs = projects(0.1, 0.2)
    b = ballots(v=["p0", "p1"])
    assert greedy(projs, b, 0.3) == ["p0", "p1"]  # 0.1 + 0.2 > 0.3 in binary floats
    assert phragmen(projs, b, 0.3) == ["p0", "p1"]
    assert mes(projs, b, 0.3) == ["p0", "p1"]
    assert knapsack(projs, b, 0.3) == ["p0", "p1"]


def test_zero_approval_projects_are_never_funded() -> None:
    projs = projects(1, 1, 1)
    b = ballots(v1=["p0"], v2=[])
    for rule in RULE_FUNCTIONS.values():
        assert rule(projs, b, 100) == ["p0"]
        assert rule(projs, ballots(v1=[], v2=[]), 100) == []
        assert rule(projs, {}, 100) == []


def test_malformed_ballots_are_refused() -> None:
    projs = projects(1, 1)
    for rule in RULE_FUNCTIONS.values():
        with pytest.raises(ValueError, match="unknown project"):
            rule(projs, ballots(v=["p9"]), 5)
        with pytest.raises(ValueError, match="repeats"):
            rule(projs, ballots(v=["p0", "p0"]), 5)


# ------------------------------------------------------------------ knapsack


def brute_force(projs: Sequence[Project], b: Ballots, budget: int) -> list[str]:
    """Best total approvals; ties: lexicographically smallest ascending id list. (HAND)"""
    count = {p.id: sum(p.id in ballot for ballot in b.values()) for p in projs}
    cost = {p.id: int(p.cost) for p in projs}
    ids = sorted((p.id for p in projs if count[p.id] > 0), key=lambda s: int(s[1:]))
    best: tuple[int, list[str]] = (0, [])
    for r in range(len(ids) + 1):
        for combo in itertools.combinations(ids, r):
            if sum(cost[i] for i in combo) > budget:
                continue
            value = sum(count[i] for i in combo)
            key = [int(i[1:]) for i in combo]
            if value > best[0] or (value == best[0] and key < [int(i[1:]) for i in best[1]]):
                best = (value, list(combo))
    return best[1]


def instance_from(
    costs: Sequence[int], approvals: Sequence[Sequence[int]]
) -> tuple[list[Project], dict[AgentId, list[str]]]:
    """Projects ``p0..`` with the given costs; voter ``i`` approves the indices ``approvals[i]``."""
    projs = projects(*costs)
    return projs, {AgentId(f"v{i}"): [f"p{j}" for j in a] for i, a in enumerate(approvals)}


def check_knapsack_matches_bruteforce(
    costs: Sequence[int], approvals: Sequence[Sequence[int]], budget: int
) -> None:
    projs, b = instance_from(costs, approvals)
    assert knapsack(projs, b, budget) == brute_force(projs, b, budget)


def check_all_rules_within_budget(
    costs: Sequence[int], approvals: Sequence[Sequence[int]], budget: int
) -> None:
    projs, b = instance_from(costs, approvals)
    cost = {p.id: p.cost for p in projs}
    for name, rule in RULE_FUNCTIONS.items():
        funded = rule(projs, b, budget)
        assert sum(cost[i] for i in funded) <= budget, name
        assert funded == sorted(set(funded), key=lambda s: int(s[1:])), name
        assert rule(projs, b, budget) == funded, name  # deterministic


def random_instance(
    rng: np.random.Generator, max_projects: int, max_cost: int, max_voters: int
) -> tuple[list[int], list[list[int]], int]:
    m = int(rng.integers(1, max_projects + 1))
    n = int(rng.integers(1, max_voters + 1))
    costs = [int(c) for c in rng.integers(1, max_cost + 1, size=m)]
    approvals = [[j for j in range(m) if rng.random() < 0.5] for _ in range(n)]
    return costs, approvals, int(rng.integers(0, 2 * max_cost + 3))


def test_knapsack_matches_bruteforce_randomized() -> None:
    rng = np.random.default_rng(5)
    for _ in range(400):
        check_knapsack_matches_bruteforce(*random_instance(rng, 8, 20, 6))


def test_knapsack_beats_greedy_where_greedy_is_trapped() -> None:
    # HAND: p0 (cost 6) has 3 approvals; p1, p2 (cost 5) have 2 each; budget 10.
    projs = projects(6, 5, 5)
    b = ballots(v1=["p0", "p1"], v2=["p0", "p1"], v3=["p0", "p2"], v4=["p2"])
    assert greedy(projs, b, 10) == ["p0"]  # 3 approvals
    assert knapsack(projs, b, 10) == ["p1", "p2"]  # 4 approvals


def test_knapsack_tie_break_is_smallest_id_list() -> None:
    projs = projects(2, 2, 2)  # one approval each, room for two
    b = ballots(v1=["p0", "p1", "p2"])
    assert knapsack(projs, b, 4) == ["p0", "p1"]
    assert knapsack(projs, ballots(v1=["p1", "p2"], v2=["p0"]), 4) == ["p0", "p1"]


def test_knapsack_guards() -> None:
    many = projects(*([1] * 61))
    with pytest.raises(ValueError, match="refuses 61 projects"):
        knapsack(many, ballots(v=[p.id for p in many]), 100)
    sixty = projects(*([1] * 60))
    assert len(knapsack(sixty, ballots(v=[p.id for p in sixty]), 60)) == 60
    with pytest.raises(ValueError, match="whole number of cents"):
        knapsack(projects(1.005), ballots(v=["p0"]), 5)
    assert knapsack(projects(1.25, 2.5), ballots(v=["p0", "p1"]), 3.75) == ["p0", "p1"]
    # a budget that is not a whole number of cents is floored, which is exact for whole-cent costs
    assert knapsack(projects(1.25), ballots(v=["p0"]), 1.2599) == ["p0"]
    assert knapsack(projects(1.25), ballots(v=["p0"]), 1.2499) == []


# ------------------------------------------------------------------ Method of Equal Shares


def test_mes_matches_worked_examples() -> None:
    # LITERATURE: Peters & Skowron, "Proportionality and the Limits of Welfarism" (EC 2020,
    # arXiv:1911.11747), Example 2 (profile of Example 1): n = 15, k = 4 unit-cost candidates.
    # c1, c2: v1-v12; c3: v1-v10; c4, c5: v6-v15. The paper has price n/k with unit budgets; here
    # every voter holds k/n = 4/15 and a project costs 1, so the paper's q is our rho * n/k.
    # The paper breaks the step-3 tie (c3, c4, c5 are all 15/40-affordable) in favour of c3.
    voters = [f"v{i}" for i in range(1, 16)]
    approve = {
        "p1": voters[0:12],
        "p2": voters[0:12],
        "p3": voters[0:10],
        "p4": voters[5:15],
        "p5": voters[5:15],
    }
    b = {AgentId(v): [p for p, vs in approve.items() if v in vs] for v in voters}
    projs = projects(1, 1, 1, 1, 1, ids=["p1", "p2", "p3", "p4", "p5"])
    steps = equal_shares_steps(projs, b, 4)
    assert steps == [("p1", F(1, 12)), ("p2", F(1, 12)), ("p3", F(1, 10)), ("p4", F(4, 15))]
    # paper: q = 15/48, 15/48, 15/40, 1; rho = q * k/n
    assert [rho * F(15, 4) for _, rho in steps] == [F(15, 48), F(15, 48), F(15, 40), F(1)]
    assert mes(projs, b, 4) == ["p1", "p2", "p3", "p4"]

    # LITERATURE: Rey & Maly, "The (Computational) Social Choice Take on Indivisible Participatory
    # Budgeting" (arXiv:2303.00621), the MES example with cardinality satisfaction: b = 10, four
    # voters (2.5 each); their outcome is {p1, p2, p3}, bought in the order p2, p1, p3.
    rm_projs = projects(1, 1, 3, 6, 9, ids=["p1", "p2", "p3", "p4", "p5"])
    rm = ballots(
        a1=["p2", "p4", "p5"], a2=["p1", "p2", "p3", "p5"], a3=["p2", "p5"], a4=["p3", "p5"]
    )
    rm_steps = equal_shares_steps(rm_projs, rm, 10)
    assert rm_steps == [("p2", F(1, 3)), ("p1", F(1)), ("p3", F(11, 6))]  # re-derived by hand
    assert mes(rm_projs, rm, 10) == ["p1", "p2", "p3"]


def test_mes_completion_variants() -> None:
    # HAND: budget 6, three voters (2 each). X (cost 3) and Y (cost 3) each have one supporter and
    # are not affordable at 2; Z (cost 1) is. The plain run buys only Z, which leaves 5 unspent.
    projs = projects(3, 3, 1, ids=["x", "y", "z"])
    b = ballots(v1=["x"], v2=["y"], v3=["z"])
    assert [p for p, _ in equal_shares_steps(projs, b, 6)] == ["z"]
    # Add1 raises the endowment to 3: z, x, y would cost 7 > 6, so the previous run (just z)
    # is kept, and the greedy completion then adds x (ties: smaller id) but not y (left 2).
    assert mes(projs, b, 6) == ["x", "z"]


def test_mes_add1_can_beat_greedy_completion() -> None:
    # HAND (found by a random search, then re-derived): budget 6, voters v0 {p2} and v1 {p0, p1},
    # costs 4, 1, 4 (endowment 3 each). Plain run: only p1 is affordable (v1 pays 1); p0 and p2 cost
    # 4 > 3. Greedy completion alone would then give the leftover to p0 ({p0, p1}, all for v1).
    # Add1 raises the endowment to 4: p1 (rho 1), then p2 (v0 pays 4); p0 is out of v1's reach
    # (3 left), and the run costs 5 <= 6 and is exhaustive, so it is the answer: one project each.
    projs = projects(4, 1, 4)
    b = ballots(v0=["p2"], v1=["p0", "p1"])
    assert [p for p, _ in equal_shares_steps(projs, b, 6)] == ["p1"]
    assert [p for p, _ in equal_shares_steps(projs, b, 6, endowment=4)] == ["p1", "p2"]
    assert greedy(projs, b, 6) == ["p0", "p1"]
    assert mes(projs, b, 6) == ["p1", "p2"]


def test_mes_add1_stops_when_the_next_run_overspends() -> None:
    # HAND: with endowment 2 + 1 the extra run buys more than the budget, so it is not used.
    projs = projects(3, 3, 1, ids=["x", "y", "z"])
    b = ballots(v1=["x"], v2=["y"], v3=["z"])
    over = equal_shares_steps(projs, b, 6, endowment=3)
    assert sum(projs[["x", "y", "z"].index(p)].cost for p, _ in over) == 7 > 6
    assert "y" not in mes(projs, b, 6)


# ------------------------------------------------------------------ sequential Phragmén


def test_phragmen_matches_worked_examples() -> None:
    # LITERATURE: Brill, Freeman, Janson, Lackner, "Phragmén's Voting Methods and Justified
    # Representation" (AAAI 2017; Math. Programming 2024), Examples 4.3 / 4.6: C = {a, b, c, d},
    # k = 3, voters {a} {b} {b,c} {a,b,c} {d}. Paper: round values s(1)_b = 1/3, then a at 2/3,
    # and a tie between c and d at 1 ("depending on the tie-breaking rule"): here c (smaller id).
    ex43 = projects(1, 1, 1, 1, ids=["a", "b", "c", "d"])
    b43 = ballots(v1=["a"], v2=["b"], v3=["b", "c"], v4=["a", "b", "c"], v5=["d"])
    steps, loads = phragmen_steps(ex43, b43, 3)
    assert steps == [("b", F(1, 3)), ("a", F(2, 3)), ("c", F(1))]
    assert loads == {"v1": F(2, 3), "v2": F(1, 3), "v3": F(1), "v4": F(1), "v5": F(0)}
    assert phragmen(ex43, b43, 3) == ["a", "b", "c"]

    # LITERATURE: same paper, Example 6.3 (k = 4, ties broken alphabetically): seq-Phragmén
    # selects e, f, a, b with final loads 3/4, 3/4, 0, 0, 3/4, 3/4, 1/2, 1/2.
    ex63 = projects(*([1] * 6), ids=list("abcdef"))
    b63 = ballots(
        v1=["a"],
        v2=["b"],
        v3=["c"],
        v4=["d"],
        v5=["a", "e", "f"],
        v6=["b", "e", "f"],
        v7=["c", "e", "f"],
        v8=["d", "e", "f"],
    )
    steps, loads = phragmen_steps(ex63, b63, 4)
    assert [p for p, _ in steps] == ["e", "f", "a", "b"]
    assert [t for _, t in steps] == [F(1, 4), F(1, 2), F(3, 4), F(3, 4)]
    assert [loads[AgentId(f"v{i}")] for i in range(1, 9)] == [
        F(3, 4), F(3, 4), F(0), F(0), F(3, 4), F(3, 4), F(1, 2), F(1, 2)
    ]  # fmt: skip

    # LITERATURE: Rey & Maly (arXiv:2303.00621), the sequential-Phragmén example: b = 10, costs
    # 4, 6, 8, 10, 15, four voters. Paper: p1 first (load 4/3), then p2 (about 4.33); the budget
    # is then spent, so the outcome is {p1, p2}.
    rm_projs = projects(4, 6, 8, 10, 15, ids=["p1", "p2", "p3", "p4", "p5"])
    rm = ballots(
        a1=["p1", "p4", "p5"],
        a2=["p5"],
        a3=["p1", "p2", "p4", "p5"],
        a4=["p1", "p2", "p3", "p4", "p5"],
    )
    steps, _ = phragmen_steps(rm_projs, rm, 10)
    assert steps == [("p1", F(4, 3)), ("p2", F(13, 3))]
    assert phragmen(rm_projs, rm, 10) == ["p1", "p2"]


def test_phragmen_skips_what_does_not_fit_and_goes_on() -> None:
    # HAND: budget 4. A (cost 3, voters v1-v4, time 3/4) is bought; B (cost 2, v5-v6, time 1)
    # no longer fits the remaining 1 and is discarded; C (cost 1, v7, time 1) still fits.
    projs = projects(3, 2, 1, ids=["a", "b", "c"])
    b = ballots(v1=["a"], v2=["a"], v3=["a"], v4=["a"], v5=["b"], v6=["b"], v7=["c"])
    steps, _ = phragmen_steps(projs, b, 4)
    assert steps == [("a", F(3, 4)), ("c", F(1))]


# ------------------------------------------------------------------ cross-rule properties


def test_greedy_and_friends_on_the_rey_maly_instance() -> None:
    # HAND, on the instance of the Phragmén example above (b = 10): greedy scans p1 (3
    # approvals, cost 4), p4 (3, cost 10, does not fit), p2 (2, cost 6, fits) -> {p1, p2};
    # the optimum is also 3 + 2 = 5 approvals with {p1, p2}.
    rm_projs = projects(4, 6, 8, 10, 15, ids=["p1", "p2", "p3", "p4", "p5"])
    rm = ballots(
        a1=["p1", "p4", "p5"],
        a2=["p5"],
        a3=["p1", "p2", "p4", "p5"],
        a4=["p1", "p2", "p3", "p4", "p5"],
    )
    assert greedy(rm_projs, rm, 10) == ["p1", "p2"]
    assert knapsack(rm_projs, rm, 10) == ["p1", "p2"]


def test_all_rules_within_budget_randomized() -> None:
    rng = np.random.default_rng(11)
    for _ in range(200):
        check_all_rules_within_budget(*random_instance(rng, 8, 20, 6))


def test_rules_identical_on_unanimous_profile() -> None:
    rng = np.random.default_rng(3)
    for _ in range(60):
        m = int(rng.integers(1, 7))
        costs = [int(c) for c in rng.integers(1, 6, size=m)]
        projs = projects(*costs)
        chosen = [p.id for p in projs if rng.random() < 0.6]
        total = sum(c for p, c in zip(projs, costs, strict=True) if p.id in chosen)
        n = int(rng.integers(1, 6))
        b = {AgentId(f"v{i}"): list(chosen) for i in range(n)}
        budget = total + int(rng.integers(0, 3))  # everything chosen is affordable
        for name, rule in RULE_FUNCTIONS.items():
            assert rule(projs, b, budget) == chosen, name


# ------------------------------------------------------------------ EJR for unit costs


def satisfies_ejr(ballot_sets: Sequence[frozenset[int]], funded: frozenset[int], k: int) -> bool:
    """Extended justified representation, unit costs, committee size ``k`` (standard definition).

    For every ell >= 1 and every group S with |S| >= ell * n / k whose members jointly approve at
    least ell common projects, some member of S has at least ell approved projects in ``funded``.
    """
    n = len(ballot_sets)
    for ell in range(1, k + 1):
        for size in range(1, n + 1):
            if size * k < ell * n:
                continue
            for group in itertools.combinations(range(n), size):
                common = frozenset.intersection(*(ballot_sets[i] for i in group))
                if len(common) >= ell and all(len(ballot_sets[i] & funded) < ell for i in group):
                    return False
    return True


def run_ejr_instance(ballot_sets: Sequence[frozenset[int]], m: int, k: int) -> None:
    projs = projects(*([1] * m))
    b = {AgentId(f"v{i}"): [f"p{j}" for j in sorted(s)] for i, s in enumerate(ballot_sets)}
    plain = frozenset(int(p[1:]) for p, _ in equal_shares_steps(projs, b, k))
    completed = frozenset(int(p[1:]) for p in mes(projs, b, k))
    assert len(plain) <= k and len(completed) <= k
    assert plain <= completed
    assert satisfies_ejr(ballot_sets, plain, k), (ballot_sets, k, "plain MES")
    assert satisfies_ejr(ballot_sets, completed, k), (ballot_sets, k, "MES with completion")


def all_ballots(m: int) -> list[frozenset[int]]:
    return [frozenset(j for j in range(m) if mask >> j & 1) for mask in range(2**m)]


def test_mes_ejr_on_small_instances() -> None:
    # Voters are interchangeable, so enumerate multisets of ballots: every profile with up to 4
    # voters and 4 unit-cost projects, for budgets 1, 2, 3 ...
    for m in (1, 2, 3, 4):
        pool = all_ballots(m)
        for n in range(1, 5):
            for combo in itertools.combinations_with_replacement(pool, n):
                for k in (1, 2, 3):
                    run_ejr_instance(combo, m, k)
    # ... plus a seeded sample of the 5-voter, 5-project profiles.
    rng = np.random.default_rng(1)
    pool = all_ballots(5)
    for _ in range(1500):
        combo = tuple(pool[int(i)] for i in rng.integers(0, len(pool), size=5))
        run_ejr_instance(combo, 5, int(rng.integers(1, 4)))


@pytest.mark.slow
def test_mes_ejr_exhaustive_five_voters() -> None:
    for m in (4, 5):
        pool = all_ballots(m)
        n_voters = 5 if m == 4 else 3
        for combo in itertools.combinations_with_replacement(pool, n_voters):
            for k in (1, 2, 3):
                run_ejr_instance(combo, m, k)
