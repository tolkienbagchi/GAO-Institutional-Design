"""Hypothesis versions of the randomized rule tests (the same checks as in test_pb_rules.py).

Kept in their own module because ``hypothesis`` is a dev dependency: where it is missing, the rest
of the rule tests still collect (CI installs it, so these run there).
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.environments.test_pb_rules import (
    check_all_rules_within_budget,
    check_knapsack_matches_bruteforce,
)


@st.composite
def pb_instances(
    draw: st.DrawFn, max_projects: int, max_voters: int
) -> tuple[list[int], list[list[int]], int]:
    costs = draw(st.lists(st.integers(1, 20), min_size=1, max_size=max_projects))
    m = len(costs)
    approvals = draw(
        st.lists(
            st.lists(st.integers(0, m - 1), unique=True),
            min_size=1,
            max_size=max_voters,
        )
    )
    return costs, approvals, draw(st.integers(0, 45))


@settings(max_examples=300, deadline=None)
@given(pb_instances(max_projects=8, max_voters=6))
def test_knapsack_matches_bruteforce(instance: tuple[list[int], list[list[int]], int]) -> None:
    check_knapsack_matches_bruteforce(*instance)


@settings(max_examples=300, deadline=None)
@given(pb_instances(max_projects=8, max_voters=6))
def test_all_rules_within_budget(instance: tuple[list[int], list[list[int]], int]) -> None:
    check_all_rules_within_budget(*instance)
