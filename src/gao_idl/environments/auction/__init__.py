"""Auction environments."""

from typing import Any

from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.core.ids import AgentId, RoleId
from gao_idl.core.schemas import Config
from gao_idl.environments.auction.bidding import bidding_layer


def build_bidding_only(
    values: dict[str, float], rule: str = "second_price", reveal_bids: bool = False
) -> tuple[Process, Config]:
    """Environment factory: the bidding layer alone, one role ``bidder:i`` per agent (sorted)."""
    state: dict[str, Any] = {"values": dict(values), "rule": rule, "reveal_bids": reveal_bids}
    roles = {RoleId(f"bidder:{i}"): AgentId(a) for i, a in enumerate(sorted(values))}
    return Primitive(bidding_layer()), Config(state=state, roles=roles)
