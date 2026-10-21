import pytest

from gao_idl.composition.primitive import Primitive
from gao_idl.environments.auction import build_bidding_only
from gao_idl.runtime.registry import resolve


def test_resolve_returns_factory() -> None:
    factory = resolve("gao_idl.environments.auction:build_bidding_only")
    assert factory is build_bidding_only
    process, cfg = factory(values={"alice": 1.0, "bob": 2.0})
    assert isinstance(process, Primitive)
    assert cfg.roles == {"bidder:0": "alice", "bidder:1": "bob"}
    assert cfg.state == {
        "values": {"alice": 1.0, "bob": 2.0},
        "rule": "second_price",
        "reveal_bids": False,
    }


@pytest.mark.parametrize("bad", ["no_colon", ":attr", "module:", ""])
def test_resolve_malformed(bad: str) -> None:
    with pytest.raises(ValueError, match="env_ref"):
        resolve(bad)


def test_resolve_missing_module_or_attribute() -> None:
    with pytest.raises(ModuleNotFoundError):
        resolve("gao_idl.does_not_exist:f")
    with pytest.raises(ValueError, match="no attribute"):
        resolve("gao_idl.environments.auction:nope")


def test_resolve_non_callable() -> None:
    with pytest.raises(ValueError, match="callable"):
        resolve("gao_idl:__version__")
