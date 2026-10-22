"""Hypothesis property tests: ids derived from paths are injective."""

from hypothesis import given
from hypothesis import strategies as st

from gao_idl.core.ids import AgentId, RoleId
from gao_idl.core.paths import (
    Branch,
    LoopIter,
    Path,
    PathElem,
    decision_id,
    path_str,
    report_id,
)

# Layer/branch names must not contain the reserved characters / # @ [ ] (and ':' for ids' tails).
names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=5)
elems: st.SearchStrategy[PathElem] = st.one_of(
    names,
    st.integers(min_value=0, max_value=9).map(LoopIter),
    names.map(Branch),
)
paths: st.SearchStrategy[Path] = st.lists(elems, max_size=4).map(tuple)
# Roles and agents may contain ':' and digits (e.g. "bidder:0") but never '#', '@' or '/'.
roles = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_:0123", min_size=1, max_size=6)


@given(paths, roles, paths, roles)
def test_decision_id_injective(p1: Path, r1: str, p2: Path, r2: str) -> None:
    if (p1, r1) != (p2, r2):
        assert decision_id(p1, RoleId(r1)) != decision_id(p2, RoleId(r2))


@given(paths, roles, st.integers(0, 5), paths, roles, st.integers(0, 5))
def test_report_id_injective(p1: Path, a1: str, n1: int, p2: Path, a2: str, n2: int) -> None:
    if (p1, a1, n1) != (p2, a2, n2):
        assert report_id(p1, AgentId(a1), n1) != report_id(p2, AgentId(a2), n2)


@given(paths, paths)
def test_path_str_injective(p1: Path, p2: Path) -> None:
    if p1 != p2:
        assert path_str(p1) != path_str(p2)
