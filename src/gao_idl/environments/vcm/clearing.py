"""The uniform-price call auction: a pure function from sealed orders to a price and fills.

Rule (a ``k = 1/2`` double auction on units). Expand every bid into ``qty`` unit bids and every ask
into ``qty`` unit asks; sort unit bids by price, highest first, and unit asks lowest first.
The volume ``V`` is the largest ``m`` with ``bid_m >= ask_m`` (``0`` if even ``m = 1`` fails).
The clearing price is the midpoint ``(bid_V + ask_V) / 2`` of the marginal pair: every filled bid
is at least, and every filled ask at most, that price, so everybody trades at a price they accept.
All orders strictly better than the marginal price on their side fill completely; orders priced
exactly at the marginal bid (or ask) share what is left of the volume pro rata to their quantity,
so ties never depend on arrival or on names. Arithmetic is exact (``Fraction``); fills may be
fractional at the margin.

Stability: internal; it may change without notice. Use the names exported by ``gao_idl`` where
there is one.
"""

from collections.abc import Sequence
from fractions import Fraction

from gao_idl.core.errors import ContractError
from gao_idl.core.schemas import FrozenModel

MAX_UNITS = 1_000_000

Order = tuple[str, float, int]
"""``(agent, price, qty)``: a bid's limit price or an ask's reservation price, and the quantity."""


class Clearing(FrozenModel):
    """Result of one call auction. ``price`` is ``None`` (and the volume 0) when nothing trades."""

    price: float | None
    volume: int
    bid_fills: dict[str, float]
    ask_fills: dict[str, float]


def _units(orders: Sequence[Order], descending: bool) -> list[Fraction]:
    prices = [Fraction(p) for _, p, q in orders for _ in range(q)]
    return sorted(prices, reverse=descending)


def _fills(
    orders: Sequence[Order], marginal: Fraction, volume: int, better_is_higher: bool
) -> dict[str, float]:
    def strictly_better(p: Fraction) -> bool:
        return p > marginal if better_is_higher else p < marginal

    sure = sum(q for _, p, q in orders if strictly_better(Fraction(p)))
    at_margin = sum(q for _, p, q in orders if Fraction(p) == marginal)
    share = Fraction(volume - sure, at_margin) if at_margin else Fraction(0)
    out: dict[str, float] = {}
    for agent, p, q in orders:
        fp = Fraction(p)
        if strictly_better(fp):
            out[agent] = float(q)
        elif fp == marginal:
            out[agent] = float(q * share)
        else:
            out[agent] = 0.0
    return out


def clear_call_auction(bids: Sequence[Order], asks: Sequence[Order]) -> Clearing:
    """Clear sealed ``bids`` and ``asks`` (see the module docstring for the exact rule)."""
    for side, orders in (("bid", bids), ("ask", asks)):
        for agent, price, qty in orders:
            if qty < 0 or price < 0 or price != price or price == float("inf"):
                raise ContractError(f"invalid {side} from {agent!r}: price {price!r}, qty {qty!r}")
    live_bids = [o for o in bids if o[2] > 0]
    live_asks = [o for o in asks if o[2] > 0]
    if sum(q for _, _, q in live_bids) + sum(q for _, _, q in live_asks) > MAX_UNITS:
        raise ContractError(f"more than {MAX_UNITS} units in one auction")
    bid_units, ask_units = _units(live_bids, True), _units(live_asks, False)
    volume = 0
    for b, a in zip(bid_units, ask_units, strict=False):
        if b < a:
            break
        volume += 1
    none = {o[0]: 0.0 for o in bids}, {o[0]: 0.0 for o in asks}
    if volume == 0:
        return Clearing(price=None, volume=0, bid_fills=none[0], ask_fills=none[1])
    marginal_bid, marginal_ask = bid_units[volume - 1], ask_units[volume - 1]
    midpoint = (marginal_bid + marginal_ask) / 2
    return Clearing(
        price=float(midpoint),
        volume=volume,
        bid_fills=_fills(live_bids, marginal_bid, volume, True)
        | {a: 0.0 for a, _, _ in bids if a not in {o[0] for o in live_bids}},
        ask_fills=_fills(live_asks, marginal_ask, volume, False)
        | {a: 0.0 for a, _, _ in asks if a not in {o[0] for o in live_asks}},
    )
