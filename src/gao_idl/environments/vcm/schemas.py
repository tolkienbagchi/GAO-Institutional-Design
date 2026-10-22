"""Data objects of the carbon-market round: state, observations, reports.

Stability: internal; it may change without notice. Use the names exported by ``gao_idl`` where
there is one.
"""

from typing import Any

from pydantic import model_serializer

from gao_idl.core.schemas import FrozenModel


def drop_none(handler: Any, value: Any) -> dict[str, Any]:
    """Serialize ``value`` without the keys whose value is ``None`` (absent, not a placeholder)."""
    data: dict[str, Any] = handler(value)
    return {k: v for k, v in data.items() if v is not None}


class Order(FrozenModel):
    """A sealed order: price per credit and quantity in credits."""

    price: float
    qty: int


class VerifiedProject(FrozenModel):
    """What a verifier reported for a project."""

    verifier: str
    report: float


class RepStat(FrozenModel):
    """A verifier's audit history: revealed-vs-reported, accumulated over rounds."""

    audited: int = 0
    wrong: int = 0
    sum_error: float = 0.0


class ClearingRecord(FrozenModel):
    """The auction's result as the next layers need it."""

    price: float | None
    volume: int
    fills: dict[str, float]


class RoundState(FrozenModel):
    """The record every stage of a round hands to the next, filled in as the round proceeds.

    ``reputation`` is ``None`` unless the factory shows it to buyers. ``last_price`` /
    ``last_volume`` are the previous round's public clearing (copied out of the store by the
    feedback connector because ``Layer.observe`` has no store access).
    """

    round: int = 0
    rounds: int = 1
    reputation: dict[str, RepStat] | None = None
    last_price: float | None = None
    last_volume: int = 0
    reports: dict[str, VerifiedProject] = {}
    orders: dict[str, Order] = {}
    clearing: ClearingRecord | None = None


# ------------------------------------------------------------------ observations


class VerifierObs(FrozenModel):
    """Own measurements and the rules of the fee and the audit."""

    round: int
    measurements: dict[str, float]
    fee: float
    penalty: float
    tolerance: float
    audit_probability: float


class SellerObs(FrozenModel):
    """Own project, its verified quality and the last public price."""

    round: int
    project: str
    quantity: int
    cost: float
    verified_quality: float
    last_price: float | None = None
    last_volume: int = 0

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: Any) -> dict[str, Any]:
        return drop_none(handler, self)


class ProjectView(FrozenModel):
    """A listed project as a buyer sees it: who verified it and what they reported."""

    project: str
    verifier: str
    quantity: int
    verified_quality: float


class BuyerObs(FrozenModel):
    """Own demand and value, the verified projects, maybe the verifiers' track records."""

    round: int
    demand: int
    value: float
    projects: list[ProjectView]
    reputation: dict[str, dict[str, float]] | None = None
    last_price: float | None = None
    last_volume: int = 0

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: Any) -> dict[str, Any]:
        return drop_none(handler, self)


# ------------------------------------------------------------------ reports


class ClearingReport(FrozenModel):
    """Public: price and volume. Private: the recipient's own fill, payment or receipts."""

    round: int
    price: float | None = None
    volume: int
    own_fill: float | None = None
    own_payment: float | None = None
    own_revenue: float | None = None

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: Any) -> dict[str, Any]:
        return drop_none(handler, self)


class Revelation(FrozenModel):
    """An audit's public result for one project."""

    verifier: str
    report: float
    true_quality: float


class AuditReport(FrozenModel):
    """Public: what the audit revealed. Private: a buyer's value and net, a verifier's money."""

    round: int
    revealed: dict[str, Revelation]
    own_value: float | None = None
    own_net: float | None = None
    fees: float | None = None
    penalties: float | None = None

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: Any) -> dict[str, Any]:
        return drop_none(handler, self)
