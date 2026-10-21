import numpy as np

from gao_idl.core.ids import AgentId, RoleId
from gao_idl.core.paths import Branch, LoopIter, Path, child, decision_id, path_str, report_id

LETTERS = "abcdefghijklmnopqrstuvwxyz_"


def test_path_str_nested() -> None:
    p: Path = ("tournament", LoopIter(3), Branch("pair_1"), "pd_round")
    assert path_str(p) == "tournament/loop[3]/branch[pair_1]/pd_round"


def test_path_str_empty_and_single() -> None:
    assert path_str(()) == ""
    assert path_str(("bidding",)) == "bidding"


def test_child_appends() -> None:
    p: Path = ("a", LoopIter(0))
    assert child(p, "b") == ("a", LoopIter(0), "b")
    assert child(p, Branch("x")) == ("a", LoopIter(0), Branch("x"))
    assert p == ("a", LoopIter(0))  # original untouched


def test_decision_and_report_id_format() -> None:
    p: Path = ("bidding",)
    assert decision_id(p, RoleId("bidder:0")) == "bidding#bidder:0"
    assert report_id(p, AgentId("alice"), 2) == "bidding@alice:2"


def _random_path(rng: np.random.Generator) -> Path:
    elems: list[str | LoopIter | Branch] = []
    for _ in range(int(rng.integers(0, 5))):
        kind = int(rng.integers(0, 3))
        word = "".join(rng.choice(list(LETTERS), size=int(rng.integers(1, 6))))
        if kind == 0:
            elems.append(word)
        elif kind == 1:
            elems.append(LoopIter(int(rng.integers(0, 6))))
        else:
            elems.append(Branch(word))
    return tuple(elems)


def test_ids_injective_randomized() -> None:
    """Plain-pytest version of the injectivity property (no hypothesis needed)."""
    rng = np.random.default_rng(0)
    seen_d: dict[str, tuple[Path, str]] = {}
    seen_r: dict[str, tuple[Path, str, int]] = {}
    for _ in range(3000):
        p = _random_path(rng)
        role = "".join(rng.choice(list(LETTERS + ":0123"), size=int(rng.integers(1, 6))))
        n = int(rng.integers(0, 4))
        d = decision_id(p, RoleId(role))
        r = report_id(p, AgentId(role), n)
        assert seen_d.setdefault(d, (p, role)) == (p, role)
        assert seen_r.setdefault(r, (p, role, n)) == (p, role, n)
