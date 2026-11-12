"""A second implementation of the sealed-bid auction, written from its spec and design page.

This is the worked example for ``skills/second-implementation``: the rules were taken from
``examples/specs/auction.json`` and its rendering (steps, who is told which fields, the seven
assumptions), not from ``gao_idl.environments.auction``. Where those two documents say nothing
(what the text fields contain, which report fields appear when the item is unsold) this file
makes its own choice. ``gao-idl diverge`` then shows exactly those places.

Run from the repository root with ``examples`` on the path::

    PYTHONPATH=examples gao-idl diverge examples/specs/auction.json \\
        candidates.auction_second:build_auction --out choices.json
"""

from typing import Any, Literal

from numpy.random import Generator
from pydantic import BaseModel, Field

from gao_idl import Config, Connector, Contract, Primitive, Seq, SimpleLayer
from gao_idl.composition.process import Process
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Exit
from gao_idl.core.store import StoreView

SETTLE, DONE, UNSOLD = Port("settle"), Port("done"), Port("unsold")


class Setup(BaseModel):
    values: dict[str, float]
    rule: Literal["first_price", "second_price"]
    reveal_bids: bool
    reserve: float | None


class Seen(BaseModel):
    value: float
    rule: str
    bidders: list[str]
    tie_break: str
    bid_disclosure: str
    reserve: float | None


class Bid(BaseModel):
    amount: float = Field(ge=0)


class Bids(Setup):
    bids: dict[str, float]


class Receipt(BaseModel):
    status: str
    own_bid: float


class Settled(BaseModel):
    winner: str | None
    price: float
    setup: Bids


def _bidding() -> SimpleLayer:
    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        s = Setup.model_validate(cfg.state)
        return Seen(
            value=s.values[str(cfg.roles[role])],
            rule=s.rule,
            bidders=sorted(s.values),
            tie_break="first name alphabetically",
            bid_disclosure="all bids" if s.reveal_bids else "winner and price only",
            reserve=s.reserve,
        )

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        bids = {str(cfg.roles[r]): a.amount for r, a in actions.items()}  # type: ignore[attr-defined]
        return Bids(**Setup.model_validate(cfg.state).model_dump(), bids=bids)

    return SimpleLayer(
        name="bidding",
        entry=Contract(state_schema=Setup),
        exits={SETTLE: Bids},
        observe=observe,
        action_schema=lambda cfg, role: Bid,
        outcome=outcome,
        export=lambda cfg, out: Exit(port=SETTLE, record=out.model_dump(mode="json")),
        report=lambda cfg, out, who, rng: Receipt(status="received", own_bid=out.bids[str(who)]),  # type: ignore[attr-defined]
    )


def _settlement(agents: list[str]) -> SimpleLayer:
    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        s = Bids.model_validate(cfg.state)
        floor = 0.0 if s.reserve is None else s.reserve
        live = {a: b for a, b in s.bids.items() if b >= floor}
        if not live:
            return Settled(winner=None, price=0.0, setup=s)
        winner = min(live, key=lambda a: (-live[a], a))  # highest bid, then first name
        others = [b for a, b in live.items() if a != winner]
        price = live[winner] if s.rule == "first_price" else max([floor, *others])
        return Settled(winner=winner, price=price, setup=s)

    def utilities(cfg: Config, out: Any) -> dict[AgentId, float]:
        if out.winner is None:
            return {}
        return {AgentId(out.winner): out.setup.values[out.winner] - out.price}

    def report(cfg: Config, out: Any, who: AgentId, rng: Generator) -> BaseModel | None:
        s = out.setup
        won = out.winner == str(who)
        told: dict[str, Any] = {
            "status": "unsold" if out.winner is None else "sold",
            "winner": out.winner,
            "winning_payment": out.price,
            "own_bid": s.bids[str(who)],
            "own_payment": out.price if won else 0.0,
            "own_utility": s.values[str(who)] - out.price if won else 0.0,
            "reserve": s.reserve,
        }
        if s.reveal_bids:
            told["all_bids"] = s.bids
        return _Told(payload=told)

    return SimpleLayer(
        name="settlement",
        entry=Contract(state_schema=Bids),
        exits={DONE: Settled, UNSOLD: Settled},
        outcome=outcome,
        utilities=utilities,
        export=lambda cfg, out: Exit(  # type: ignore[misc]
            port=UNSOLD if out.winner is None else DONE,  # type: ignore[attr-defined]
            record=out.model_dump(mode="json"),
        ),
        audience=lambda cfg, out: {AgentId(a) for a in agents},
        report=report,
    )


class _Told(BaseModel):
    payload: dict[str, Any]

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:  # the report is the payload itself
        return dict(self.payload)


def build_auction(
    values: dict[str, float],
    rule: Literal["first_price", "second_price"] = "second_price",
    reveal_bids: bool = False,
    reserve: float | None = None,
) -> tuple[Process, Config]:
    """Same parameters as the shipped factory; rules as stated in the spec."""
    agents = sorted(values)
    roles = {RoleId(f"bidder:{i}"): AgentId(a) for i, a in enumerate(agents)}
    state = {"values": dict(values), "rule": rule, "reveal_bids": reveal_bids, "reserve": reserve}
    to_settlement = Connector(lambda rec, ledger, store: Config(state=rec, roles={}), Bids)
    process = Seq(
        Primitive(_bidding()),
        {SETTLE: (to_settlement, Primitive(_settlement(agents)))},
        name="auction",
    )
    return process, Config(state=state, roles=roles)
