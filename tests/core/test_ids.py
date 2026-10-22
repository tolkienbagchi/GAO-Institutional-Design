import pytest

from gao_idl.core.ids import role_sort_key


def test_natural_order_of_numbered_roles() -> None:
    roles = [f"bidder:{i}" for i in range(12)]
    assert sorted(reversed(roles), key=role_sort_key) == roles
    assert sorted(roles) != roles  # plain lexicographic order would put bidder:10 before bidder:2


@pytest.mark.parametrize(
    ("lo", "hi"),
    [("a2", "a10"), ("a", "a1"), ("a1b", "a1c"), ("r9", "s1"), ("1", "a"), ("x:2:3", "x:2:10")],
)
def test_pairwise_order(lo: str, hi: str) -> None:
    assert role_sort_key(lo) < role_sort_key(hi)


def test_order_is_total_and_deterministic() -> None:
    roles = ["01", "1", "001", "a", "a0", "a00", "b"]
    assert sorted(roles, key=role_sort_key) == sorted(reversed(roles), key=role_sort_key)
    assert len({role_sort_key(r) for r in roles}) == len(roles)  # distinct strings, distinct keys
