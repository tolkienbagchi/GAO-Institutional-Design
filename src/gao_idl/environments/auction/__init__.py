"""Auction environments."""

from typing import Any

from gao_idl.composition.connector import Connector
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.composition.seq import Seq
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Config
from gao_idl.environments.auction.bidding import bidding_layer
from gao_idl.environments.auction.settlement import SettlementState, settlement_layer


def _bidders(values: dict[str, float]) -> tuple[dict[str, Any], dict[RoleId, AgentId]]:
    state: dict[str, Any] = {"values": dict(values)}
    roles = {RoleId(f"bidder:{i}"): AgentId(a) for i, a in enumerate(sorted(values))}
    return state, roles


def build_bidding_only(
    values: dict[str, float], rule: str = "second_price", reveal_bids: bool = False
) -> tuple[Process, Config]:
    """Environment factory: the bidding layer alone, one role ``bidder:i`` per agent (sorted)."""
    state, roles = _bidders(values)
    state.update(rule=rule, reveal_bids=reveal_bids)
    return Primitive(bidding_layer()), Config(state=state, roles=roles)


def build_auction(
    values: dict[str, float],
    rule: str = "second_price",
    reveal_bids: bool = False,
    reserve: float | None = None,
) -> tuple[Process, Config]:
    """Environment factory: sealed-bid bidding, then settlement (the full two-layer auction).

    ``reserve`` is committed in the settlement layer: bids below it are ignored, and if none
    clears it the auction exits through ``unsold`` with no payments. The reserve is public: every
    bidder's observation carries it (``BidObs.reserve``, ``None`` without one).
    """
    state, roles = _bidders(values)
    state.update(rule=rule, reveal_bids=reveal_bids, reserve=reserve)
    to_settlement = Connector(
        lambda rec, ledger, store: Config(state=rec, roles={}), SettlementState
    )
    process = Seq(
        Primitive(bidding_layer()),
        {Port("settle"): (to_settlement, Primitive(settlement_layer(reserve)))},
        name="auction",
    )
    return process, Config(state=state, roles=roles)
