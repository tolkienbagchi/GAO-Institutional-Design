"""Data objects of the congestion-pricing institution.

Stability: internal; it may change without notice. Use the names exported by ``gao_idl`` where
there is one.
"""

from typing import Any, Literal

from pydantic import model_serializer

from gao_idl.core.schemas import FrozenModel
from gao_idl.environments.congestion.model import HotspotMetric, PopWeightedMetric

RouteName = Literal["tolled", "bypass", "transit"]
TOLL_MENU: tuple[int, ...] = (0, 10, 15)
Toll = Literal[0, 10, 15]


def _drop_none(handler: Any, value: Any) -> dict[str, Any]:
    data: dict[str, Any] = handler(value)
    return {k: v for k, v in data.items() if v is not None}


# ------------------------------------------------------------------ toll setting


class PeriodIn(FrozenModel):
    """Entry of every period: the period index and what the last period left in the store.

    ``prev_times`` are BPR times by route id (free-flow times in period 0); ``prev_report`` is the
    regulator-visible report of the last period (``None`` in period 0). Both are copied out of
    the store by the feedback connector, because ``Layer.observe`` has no store access.
    """

    period: int
    prev_times: dict[str, float]
    prev_report: dict[str, Any] | None = None


class TollObs(FrozenModel):
    """What the regulator sees: the menu and the previous period's regime-filtered report."""

    period: int
    menu: list[int]
    prev_report: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_report(self, handler: Any) -> dict[str, Any]:
        return _drop_none(handler, self)


class TollAction(FrozenModel):
    """The regulator's choice from the menu."""

    toll: Toll


class TollOutcome(FrozenModel):
    """The toll in force this period."""

    period: int
    toll: int
    prev_times: dict[str, float]


class TollExit(FrozenModel):
    """Exit of ``toll_setting`` (both forms): one record, so downstream cannot tell them apart."""

    period: int
    toll: int
    prev_times: dict[str, float]


class TollAnnouncement(FrozenModel):
    """The public report of the toll: sent to every commuter."""

    period: int
    toll: int


# ------------------------------------------------------------------ travel


class TravelState(FrozenModel):
    """Entry of ``travel``: the toll and the previous period's route times."""

    period: int
    toll: int
    prev_times: dict[str, float]


class CommuterObs(FrozenModel):
    """What a commuter sees: own zone and value of time, the toll, last period's own-zone times."""

    period: int
    zone: str
    value_of_time: float
    car_available: bool
    toll: int
    fare: float
    route_times: dict[str, float]


class Trip(FrozenModel):
    """A commuter's route choice."""

    route: RouteName


class TravelOutcome(FrozenModel):
    """Route choices, flows, BPR times and generalized costs of one period."""

    period: int
    toll: int
    routes: dict[str, RouteName]
    flows: dict[str, float]
    times: dict[str, float]
    costs: dict[str, float]


class TravelExit(FrozenModel):
    """Exit of ``travel`` and entry of ``exposure``; same fields as the outcome."""

    period: int
    toll: int
    routes: dict[str, RouteName]
    flows: dict[str, float]
    times: dict[str, float]
    costs: dict[str, float]


# ------------------------------------------------------------------ exposure and reports


class RegulatorReport(FrozenModel):
    """The regulator's report: only the metric(s) the reporting regime allows, plus the objective.

    ``objective`` is the value of the metric the regulator is judged on (``equity_metric``), which
    the regime guarantees is among the metrics shown; ``payoff`` is the utility credited for the
    period, ``-objective`` (the field a tabular learner takes its reward from). Absent metrics
    are absent keys.
    """

    period: int
    toll: int
    objective: float
    payoff: float
    hotspot: HotspotMetric | None = None
    pop_weighted: PopWeightedMetric | None = None
    citywide_mean: float | None = None

    @model_serializer(mode="wrap")
    def _omit_unreported(self, handler: Any) -> dict[str, Any]:
        return _drop_none(handler, self)


class CommuterReport(FrozenModel):
    """A commuter's report: own cost, own route and its time, own zone's concentration."""

    period: int
    own_route: str
    own_cost: float
    own_route_time: float
    zone_concentration: float
    citywide_mean: float | None = None

    @model_serializer(mode="wrap")
    def _omit_unreported(self, handler: Any) -> dict[str, Any]:
        return _drop_none(handler, self)


class ExposureOutcome(FrozenModel):
    """Everything the surrogate computed, plus who gets what."""

    period: int
    periods: int
    toll: int
    routes: dict[str, RouteName]
    flows: dict[str, float]
    times: dict[str, float]
    costs: dict[str, float]
    concentration: dict[str, float]
    exposure: dict[str, float]
    emissions: dict[str, float]
    hotspot: HotspotMetric
    pop_weighted: PopWeightedMetric
    citywide_mean: float
    objective: float


class ExposureExit(FrozenModel):
    """Exit of ``exposure`` (ports ``again`` / ``finished``): the whole period, both metrics."""

    period: int
    toll: int
    flows: dict[str, float]
    times: dict[str, float]
    concentration: dict[str, float]
    exposure: dict[str, float]
    hotspot: HotspotMetric
    pop_weighted: PopWeightedMetric
    objective: float
