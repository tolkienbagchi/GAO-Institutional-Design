"""Settlement layer: resolves sealed bids into a winner, a price and utilities (no roles)."""

import math
from typing import Any

from numpy.random import Generator
from pydantic import BaseModel, model_serializer

from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config, Exit
from gao_idl.core.store import StoreView
from gao_idl.core.timeout import Fail

RULES = ("first_price", "second_price")
DONE = Port("done")
UNSOLD = Port("unsold")


class SettlementState(BaseModel):
    """Entry state: the bids, the private values, the rule, and whether bids become public."""

    bids: dict[AgentId, float]
    values: dict[AgentId, float]
    rule: str
    reveal_bids: bool


class SettlementOutcome(BaseModel):
    """Exit record through ``done``: every bidder appears in ``payments`` and ``utilities``."""

    winner: AgentId
    price: float
    payments: dict[AgentId, float]
    utilities: dict[AgentId, float]


class UnsoldOutcome(BaseModel):
    """Exit record through ``unsold``: nobody cleared the reserve, so nobody pays."""

    reserve: float
    payments: dict[AgentId, float]
    utilities: dict[AgentId, float]


class SettlementReport(BaseModel):
    """What one bidder learns: the winner and price, their own result, and bids only if public.

    Never contains anyone else's value. ``all_bids`` is omitted (not null) unless bids are revealed.
    """

    winner: AgentId
    winning_payment: float
    own_bid: float
    own_payment: float
    own_utility: float
    all_bids: dict[AgentId, float] | None = None

    @model_serializer(mode="wrap")
    def _omit_hidden_bids(self, handler: Any) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("all_bids") is None:
            data.pop("all_bids", None)
        return data


def _state(cfg: Config) -> SettlementState:
    state = SettlementState.model_validate(cfg.state)
    if state.rule not in RULES:
        raise ContractError(f"settlement: unknown rule {state.rule!r} (expected one of {RULES})")
    missing = sorted(set(state.bids) - set(state.values))
    if missing:
        raise ContractError(f"settlement: bidders {missing} have no entry in values")
    return state


def settlement_layer(reserve: float | None = None) -> SimpleLayer:
    """The settlement layer. ``reserve`` is part of the environment, not of the entry state.

    Bids below ``reserve`` are ignored; if none remains the layer exits through ``unsold``. Ties go
    to the lexicographically smallest agent id. First price charges the winner's own bid. Second
    price charges the highest other eligible bid, and the reserve acts as a bid by the seller: the
    price is ``max(reserve, highest other eligible bid)``, so a lone eligible bidder pays the
    reserve. Without a reserve a lone bidder pays 0.
    """
    if reserve is not None and (math.isnan(reserve) or reserve < 0):
        raise ContractError(f"settlement: reserve must be a non-negative number, got {reserve!r}")

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = _state(cfg)
        bidders = sorted(state.bids)
        eligible = {a: b for a, b in state.bids.items() if reserve is None or b >= reserve}
        if not eligible:
            if reserve is None:
                raise ContractError("settlement: no bids")
            zeros = dict.fromkeys(bidders, 0.0)
            return UnsoldOutcome(reserve=reserve, payments=zeros, utilities=dict(zeros))
        winner = min(eligible, key=lambda a: (-eligible[a], a))
        if state.rule == "first_price":
            price = eligible[winner]
        else:
            rival = max((b for a, b in eligible.items() if a != winner), default=0.0)
            price = rival if reserve is None else max(reserve, rival)  # the seller's bid
        payments = {a: (price if a == winner else 0.0) for a in bidders}
        utilities = {a: (state.values[a] - price if a == winner else 0.0) for a in bidders}
        return SettlementOutcome(winner=winner, price=price, payments=payments, utilities=utilities)

    def utilities(cfg: Config, out: BaseModel) -> dict[AgentId, float]:
        if not isinstance(out, SettlementOutcome | UnsoldOutcome):
            raise TypeError(f"unexpected settlement outcome {type(out).__name__}")
        return dict(out.utilities)

    def export(cfg: Config, out: BaseModel) -> Exit:
        port = UNSOLD if isinstance(out, UnsoldOutcome) else DONE
        return Exit(port=port, record=out.model_dump(mode="json"))

    def audience(cfg: Config, out: BaseModel) -> set[AgentId]:
        return set(_state(cfg).bids)

    def report(cfg: Config, out: BaseModel, recipient: AgentId, rng: Generator) -> BaseModel | None:
        if not isinstance(out, SettlementOutcome):
            return None  # unsold: no winner to report (see NOTES)
        state = _state(cfg)
        if recipient not in state.bids:
            return None
        return SettlementReport(
            winner=out.winner,
            winning_payment=out.payments[out.winner],
            own_bid=state.bids[recipient],
            own_payment=out.payments[recipient],
            own_utility=out.utilities[recipient],
            all_bids=dict(state.bids) if state.reveal_bids else None,
        )

    exits: dict[Port, type[BaseModel]] = {DONE: SettlementOutcome}
    if reserve is not None:
        exits[UNSOLD] = UnsoldOutcome
    return SimpleLayer(
        name="settlement",
        entry=Contract(state_schema=SettlementState),
        exits=exits,
        timeout=Fail(),
        outcome=outcome,
        utilities=utilities,
        export=export,
        audience=audience,
        report=report,
    )
