"""Sealed-bid bidding layer: every bidder submits one bid simultaneously."""

from typing import Any, Literal

from numpy.random import Generator
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config, Exit
from gao_idl.core.store import StoreView
from gao_idl.core.timeout import Fail

TIE_BREAK = "lowest_agent_id"


class BiddingState(BaseModel):
    """Entry state: each bidder's private value and the auction rule."""

    values: dict[AgentId, float]
    rule: Literal["first_price", "second_price"]
    reveal_bids: bool


class BidObs(BaseModel):
    """What a bidder sees: only their own value, never the others'."""

    value: float
    rule: str
    bidders: list[AgentId]
    tie_break: str
    bid_disclosure: str


class Bid(BaseModel):
    """A sealed bid: a finite, non-negative number (bools are rejected explicitly)."""

    model_config = ConfigDict(frozen=True)

    amount: float = Field(ge=0, allow_inf_nan=False, strict=True)

    @field_validator("amount", mode="before")
    @classmethod
    def _reject_bool(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("bid amount must be a number, not a bool")
        return v


class BidsExit(BaseModel):
    """Exit record through port ``settle``: everything the settlement layer needs."""

    bids: dict[AgentId, float]
    values: dict[AgentId, float]
    rule: str
    reveal_bids: bool


class BidReceipt(BaseModel):
    """Report to a bidder confirming their own bid (and nothing about anyone else's)."""

    status: Literal["bid accepted"]
    own_bid: float


def _observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
    state = BiddingState.model_validate(cfg.state)
    agent = cfg.roles[role]
    if agent not in state.values:
        raise ContractError(f"bidder {agent!r} (role {role!r}) has no entry in state.values")
    return BidObs(
        value=state.values[agent],
        rule=state.rule,
        bidders=sorted(cfg.roles.values()),
        tie_break=TIE_BREAK,
        bid_disclosure="all_bids_after_settlement" if state.reveal_bids else "winning_price_only",
    )


def _action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
    return Bid


def _outcome(
    cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
) -> BaseModel:
    state = BiddingState.model_validate(cfg.state)
    bids: dict[AgentId, float] = {}
    for role in sorted(actions):
        bid = actions[role]
        if not isinstance(bid, Bid):
            raise TypeError(f"expected Bid for role {role!r}, got {type(bid).__name__}")
        bids[cfg.roles[role]] = bid.amount
    return BidsExit(bids=bids, values=state.values, rule=state.rule, reveal_bids=state.reveal_bids)


def _export(cfg: Config, outcome: BaseModel) -> Exit:
    return Exit(port=Port("settle"), record=outcome.model_dump(mode="json"))


def _report(
    cfg: Config, outcome: BaseModel, recipient: AgentId, rng: Generator
) -> BaseModel | None:
    if not isinstance(outcome, BidsExit):
        raise TypeError(f"expected BidsExit, got {type(outcome).__name__}")
    if recipient not in outcome.bids:
        return None
    return BidReceipt(status="bid accepted", own_bid=outcome.bids[recipient])


def bidding_layer() -> SimpleLayer:
    """The sealed-bid bidding layer (timeout policy: Fail)."""
    return SimpleLayer(
        name="bidding",
        entry=Contract(state_schema=BiddingState),
        exits={Port("settle"): BidsExit},
        timeout=Fail(),
        observe=_observe,
        action_schema=_action_schema,
        outcome=_outcome,
        export=_export,
        report=_report,
    )
