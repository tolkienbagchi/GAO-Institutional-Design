import numpy as np

from gao_idl.core.paths import Branch, LoopIter, Path
from gao_idl.core.rng import stream

P: Path = ("tournament", LoopIter(1), "pd")


def draws(seed: int, path: Path, purpose: str) -> np.ndarray:
    return stream(seed, path, purpose).random(4)


def test_same_inputs_same_draws() -> None:
    assert np.array_equal(draws(7, P, "outcome"), draws(7, P, "outcome"))


def test_changing_seed_changes_draws() -> None:
    assert not np.array_equal(draws(7, P, "outcome"), draws(8, P, "outcome"))


def test_changing_path_changes_draws() -> None:
    other: Path = ("tournament", LoopIter(2), "pd")
    assert not np.array_equal(draws(7, P, "outcome"), draws(7, other, "outcome"))
    assert not np.array_equal(
        draws(7, P, "outcome"), draws(7, ("tournament", Branch("x")), "outcome")
    )


def test_changing_purpose_changes_draws() -> None:
    assert not np.array_equal(draws(7, P, "outcome"), draws(7, P, "observe:a"))


def test_order_independence() -> None:
    before = draws(3, P, "outcome")
    other: Path = ("elsewhere",)
    _ = stream(3, other, "outcome").random(10)  # create/consume an unrelated stream first
    during = draws(3, P, "outcome")
    _ = stream(3, other, "outcome").random(10)  # and after
    after = draws(3, P, "outcome")
    assert np.array_equal(before, during)
    assert np.array_equal(before, after)
