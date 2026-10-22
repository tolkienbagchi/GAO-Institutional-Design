"""The stylized city: zones, routes, BPR congestion, exposure surrogate and two equity metrics.

Everything here is a pure function of its arguments (no randomness, no clock), so layers built on
it are deterministic. The numbers are stylized on purpose: this is an institution to compare
reporting policies on, not a model of New York or of any real traffic or air-quality system.
The exposure kernel is a surrogate shaped after the UTECH pipeline of Lu et al., *Transportation
Research Part D* 161 (2026) 105628: route flows become emissions, emissions disperse onto zones
through a fixed weight matrix, and zone concentrations become exposure.

Stability: internal; it may change without notice. Use the names exported by ``gao_idl`` where
there is one.
"""

import math
from typing import Literal

import numpy as np
from pydantic import model_validator

from gao_idl.core.errors import ContractError
from gao_idl.core.schemas import FrozenModel

RouteKind = Literal["tolled", "bypass", "transit"]
KINDS: tuple[RouteKind, ...] = ("tolled", "bypass", "transit")
CAR_KINDS: tuple[RouteKind, ...] = ("tolled", "bypass")
CBD = "CBD"

# fleet electrification of the paper's scenarios E0-E4, light-duty vehicles only (no buses)
EV_SHARE_E0_E4: tuple[float, ...] = (0.0, 0.5, 0.5, 0.5, 0.8)


class Zone(FrozenModel):
    """A zone with a resident population; ``is_dac`` marks a disadvantaged community."""

    name: str
    population: float
    is_dac: bool = False


class Route(FrozenModel):
    """A route from ``origin`` to the CBD.

    ``t0`` is the free-flow time in hours and ``cap`` the capacity in vehicles per hour (unused
    by ``transit``, whose time is fixed). ``ef`` is the emission factor in grams per vehicle and
    ``touches`` the dispersion weights: zone -> concentration added per kg/h emitted on the route.
    """

    origin: str
    kind: RouteKind
    t0: float
    cap: float = 1.0
    ef: float = 0.0
    touches: dict[str, float] = {}

    @property
    def rid(self) -> str:
        """The route's identifier, ``"<origin>/<kind>"``."""
        return f"{self.origin}/{self.kind}"


class City(FrozenModel):
    """Zones, routes and the constants of the cost, congestion and exposure kernels.

    ``agent_weight`` is the number of vehicles one commuter-agent stands for; flows and
    capacities are in vehicles per hour. ``threshold`` is the concentration above which a zone is
    a hotspot; ``dac_weight`` weighs the disadvantaged group in the regulator's objective.
    """

    zones: list[Zone]
    routes: list[Route]
    fare: float = 3.0
    agent_weight: float = 1000.0
    bpr_alpha: float = 0.15
    bpr_beta: float = 4.0
    inhalation_rate: float = 0.85
    threshold: float = 5.0
    dac_weight: float = 2.0

    @model_validator(mode="after")
    def _well_formed(self) -> "City":
        names = [z.name for z in self.zones]
        if len(set(names)) != len(names) or not names or names[0] != CBD:
            raise ContractError(f"zones must have distinct names and start with {CBD!r}: {names}")
        if len(names) < 2:
            raise ContractError("a city needs at least one peripheral zone")
        for z in self.zones:
            if not math.isfinite(z.population) or z.population <= 0:
                raise ContractError(f"zone {z.name!r}: population must be finite and > 0")
        periphery = names[1:]
        want = {(o, k) for o in periphery for k in KINDS}
        have = [(r.origin, r.kind) for r in self.routes]
        if set(have) != want or len(have) != len(want):
            raise ContractError(
                f"routes must be exactly one per (peripheral zone, kind); got {sorted(have)}"
            )
        for r in self.routes:
            if not (math.isfinite(r.t0) and r.t0 > 0 and math.isfinite(r.cap) and r.cap > 0):
                raise ContractError(f"route {r.rid!r}: t0 and cap must be finite and > 0")
            if r.ef < 0 or not math.isfinite(r.ef):
                raise ContractError(f"route {r.rid!r}: emission factor must be finite and >= 0")
            if r.kind == "transit" and (r.ef != 0 or r.touches):
                raise ContractError(f"route {r.rid!r}: transit has no emissions")
            unknown = sorted(set(r.touches) - set(names))
            if unknown:
                raise ContractError(f"route {r.rid!r} touches unknown zones {unknown}")
            if any(not math.isfinite(w) or w < 0 for w in r.touches.values()):
                raise ContractError(f"route {r.rid!r}: dispersion weights must be finite and >= 0")
        for name, value in (
            ("fare", self.fare),
            ("agent_weight", self.agent_weight),
            ("inhalation_rate", self.inhalation_rate),
            ("threshold", self.threshold),
            ("dac_weight", self.dac_weight),
        ):
            if not math.isfinite(value) or value < 0 or (name == "agent_weight" and value == 0):
                raise ContractError(f"city.{name} must be finite and positive, got {value!r}")
        return self

    @property
    def periphery(self) -> list[str]:
        """The peripheral zone names, in order."""
        return [z.name for z in self.zones[1:]]

    def zone(self, name: str) -> Zone:
        """The zone called ``name``."""
        for z in self.zones:
            if z.name == name:
                return z
        raise KeyError(name)

    def route(self, origin: str, kind: str) -> Route:
        """The route of ``kind`` leaving ``origin``."""
        for r in self.routes:
            if r.origin == origin and r.kind == kind:
                return r
        raise KeyError((origin, kind))


def default_city(k: int = 3) -> City:
    """The default stylized city: the CBD, ``k`` peripheral zones, the last one a DAC.

    Tolled routes cross the cordon and load the CBD and their origin; bypass routes are longer,
    emit more and load the DAC zone (the "boundary crossing"). See the module docstring: the
    numbers are stylized.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ContractError(f"k must be an int >= 1, got {k!r}")
    populations = [3000.0, 1000.0, 4000.0] if k == 3 else [2000.0] * (k - 1) + [4000.0]
    names = [f"Z{i + 1}" for i in range(k)]
    zones = [Zone(name=CBD, population=3000.0)] + [
        Zone(name=n, population=populations[i], is_dac=(i == k - 1)) for i, n in enumerate(names)
    ]
    dac = names[-1]
    # free-flow times by origin (hours): tolled, bypass, transit; k != 3 repeats the last entry
    t_tolled, t_bypass, t_transit = (0.35, 0.35, 0.3), (0.65, 0.65, 0.55), (0.5, 0.75, 0.75)
    routes: list[Route] = []
    for i, o in enumerate(names):
        j = min(i, 2)
        tolled = {CBD: 0.5, o: 0.3}
        bypass = {o: 0.1}
        bypass[dac] = bypass.get(dac, 0.0) + 0.6
        routes.append(
            Route(origin=o, kind="tolled", t0=t_tolled[j], cap=4000.0, ef=1.0, touches=tolled)
        )
        routes.append(
            Route(origin=o, kind="bypass", t0=t_bypass[j], cap=4000.0, ef=1.2, touches=bypass)
        )
        routes.append(Route(origin=o, kind="transit", t0=t_transit[j]))
    return City(zones=zones, routes=routes, agent_weight=500.0, threshold=1.2, dac_weight=2.0)


# ------------------------------------------------------------------ congestion and costs


def bpr_time(route: Route, flow: float, alpha: float = 0.15, beta: float = 4.0) -> float:
    """BPR travel time ``t0 * (1 + alpha * (flow / cap) ** beta)``; ``transit`` ignores flow."""
    if route.kind == "transit":
        return route.t0
    return float(route.t0 * (1.0 + alpha * (flow / route.cap) ** beta))


def trip_cost(value_of_time: float, time: float, toll_paid: float, fare: float) -> float:
    """Generalized cost of a trip: ``value_of_time * time + toll_paid + fare``."""
    return value_of_time * time + toll_paid + fare


def route_times(city: City, flows: dict[str, float]) -> dict[str, float]:
    """BPR time of every route given vehicle flows by route id (missing = 0)."""
    return {
        r.rid: bpr_time(r, flows.get(r.rid, 0.0), city.bpr_alpha, city.bpr_beta)
        for r in city.routes
    }


# ------------------------------------------------------------------ the exposure surrogate


class GroupCounts(FrozenModel):
    """A count split by DAC status."""

    dac: int
    non_dac: int


class GroupMeans(FrozenModel):
    """A value split by DAC status."""

    dac: float
    non_dac: float


class HotspotMetric(FrozenModel):
    """Hotspot zones (concentration above the threshold), split by DAC status."""

    threshold: float
    hotspots: GroupCounts
    objective: float


class PopWeightedMetric(FrozenModel):
    """Population-weighted mean concentration of the DAC and the non-DAC zones."""

    pop_weighted: GroupMeans
    objective: float


class Exposure(FrozenModel):
    """Result of the surrogate kernel for one period (all dicts are keyed by route id or zone)."""

    emissions: dict[str, float]
    concentration: dict[str, float]
    exposure: dict[str, float]
    hotspot: HotspotMetric
    pop_weighted: PopWeightedMetric


def emissions(city: City, flows: dict[str, float], ev_share: float) -> dict[str, float]:
    """Emissions in kg/h per route: ``flow * ef * (1 - ev_share) / 1000`` (transit: 0)."""
    return {
        r.rid: flows.get(r.rid, 0.0) * r.ef * (1.0 - ev_share) / 1000.0
        for r in city.routes
        if r.kind != "transit"
    }


def concentrations(city: City, emitted: dict[str, float]) -> dict[str, float]:
    """Zone concentration: ``sum over routes of w[zone][route] * emissions[route]``."""
    out = {z.name: 0.0 for z in city.zones}
    for r in city.routes:
        for zone, w in sorted(r.touches.items()):
            out[zone] += w * emitted.get(r.rid, 0.0)
    return out


def hotspot_metric(city: City, conc: dict[str, float]) -> HotspotMetric:
    """Zones with concentration strictly above the threshold, by DAC status."""
    hot = [z for z in city.zones if conc[z.name] > city.threshold]
    counts = GroupCounts(
        dac=sum(1 for z in hot if z.is_dac), non_dac=sum(1 for z in hot if not z.is_dac)
    )
    return HotspotMetric(
        threshold=city.threshold,
        hotspots=counts,
        objective=city.dac_weight * counts.dac + counts.non_dac,
    )


def pop_weighted_metric(city: City, conc: dict[str, float]) -> PopWeightedMetric:
    """Population-weighted mean concentration of each group (0.0 for an empty group)."""

    def mean(dac: bool) -> float:
        group = [z for z in city.zones if z.is_dac == dac]
        total = math.fsum(z.population for z in group)
        return math.fsum(z.population * conc[z.name] for z in group) / total if total else 0.0

    means = GroupMeans(dac=mean(True), non_dac=mean(False))
    return PopWeightedMetric(
        pop_weighted=means, objective=city.dac_weight * means.dac + means.non_dac
    )


def exposure_kernel(city: City, flows: dict[str, float], ev_share: float) -> Exposure:
    """The surrogate: flows -> emissions -> concentrations -> exposure and both equity metrics.

    ``exposure[zone] = concentration * population * inhalation_rate``. ``ev_share`` scales road
    emission factors by ``1 - ev_share`` and leaves flows untouched.
    """
    if not 0.0 <= ev_share <= 1.0:
        raise ContractError(f"ev_share must be in [0, 1], got {ev_share!r}")
    emitted = emissions(city, flows, ev_share)
    conc = concentrations(city, emitted)
    expo = {z.name: conc[z.name] * z.population * city.inhalation_rate for z in city.zones}
    return Exposure(
        emissions=emitted,
        concentration=conc,
        exposure=expo,
        hotspot=hotspot_metric(city, conc),
        pop_weighted=pop_weighted_metric(city, conc),
    )


def citywide_mean(city: City, conc: dict[str, float]) -> float:
    """Population-weighted mean concentration over all zones."""
    total = math.fsum(z.population for z in city.zones)
    return math.fsum(z.population * conc[z.name] for z in city.zones) / total


# ------------------------------------------------------------------ the population


class Commuter(FrozenModel):
    """One commuter-agent: home zone, private value of time ($/h) and car availability."""

    agent: str
    home_zone: str
    value_of_time: float
    car_available: bool = True


def draw_population(city: City, n: int, seed: int, car_share: float = 0.8) -> list[Commuter]:
    """``n`` commuters drawn from ``seed`` (a pure function of the arguments).

    Home zones cycle through the periphery; the value of time is log-normal around 20 $/h,
    rounded to cents and clipped to [5, 80]; each commuter has a car with probability
    ``car_share``. Agents are called ``c0, c1, ...``.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ContractError(f"n_commuters must be an int >= 1, got {n!r}")
    rng = np.random.default_rng(seed)
    values = np.clip(rng.lognormal(mean=math.log(20.0), sigma=0.5, size=n), 5.0, 80.0)
    cars = rng.random(n) < car_share
    zones = city.periphery
    return [
        Commuter(
            agent=f"c{i}",
            home_zone=zones[i % len(zones)],
            value_of_time=round(float(values[i]), 2),
            car_available=bool(cars[i]),
        )
        for i in range(n)
    ]


def tutorial_commuters() -> list[Commuter]:
    """The six-commuter fixture the tutorial and the headline test use (default 3-zone city).

    With a static toll-sensitive best response the hotspot objective is lowest at toll 15, the
    population-weighted one at toll 10, and toll 0 is worse than both on both.
    """
    spec = [("Z1", 65.0), ("Z2", 75.0), ("Z3", 45.0), ("Z1", 35.0), ("Z2", 65.0), ("Z3", 75.0)]
    return [
        Commuter(agent=f"c{i}", home_zone=z, value_of_time=v, car_available=True)
        for i, (z, v) in enumerate(spec)
    ]
