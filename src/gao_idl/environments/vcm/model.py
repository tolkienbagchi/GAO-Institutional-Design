"""The people of the market: sellers, buyers and verifiers, and how a default population is drawn.

Stylized on purpose: quantities, qualities, costs and values are made-up numbers for comparing
institutions (fee, penalty, audit rate, whether reputation is shown), not a model of any real
carbon market. A seller's cost rises with the project's true quality, so a high-quality project
is more expensive to offer per credit.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import math
import re

import numpy as np
from pydantic import field_validator

from gao_idl.core.errors import ContractError
from gao_idl.core.schemas import FrozenModel

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")


class Seller(FrozenModel):
    """One seller with one project (its project id is the seller's agent name).

    ``quantity`` credits are for sale at a cost of ``cost`` each; ``theta`` in ``[0, 1]`` is the
    project's true quality, known to the seller and revealed to others only by an audit.
    """

    agent: str
    quantity: int
    theta: float
    cost: float

    @field_validator("quantity")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("quantity must be >= 1")
        return v

    @field_validator("theta")
    @classmethod
    def _unit(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("theta must be in [0, 1]")
        return v

    @field_validator("cost")
    @classmethod
    def _nonneg(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0:
            raise ValueError("cost must be finite and >= 0")
        return v


class Buyer(FrozenModel):
    """One buyer: wants up to ``demand`` credits, each worth ``value * quality`` to it."""

    agent: str
    demand: int
    value: float

    @field_validator("demand")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("demand must be >= 1")
        return v

    @field_validator("value")
    @classmethod
    def _nonneg(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0:
            raise ValueError("value must be finite and >= 0")
        return v


class Verifier(FrozenModel):
    """A verifier and the projects it is assigned (seller agent names).

    What it measures for project ``j`` is ``clip(theta_j + bias + N(0, sigma))``; what it reports
    is its own choice.
    """

    agent: str
    projects: list[str]
    bias: float = 0.0
    sigma: float = 0.0

    @field_validator("sigma")
    @classmethod
    def _nonneg(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0:
            raise ValueError("sigma must be finite and >= 0")
        return v


class Population(FrozenModel):
    """Sellers, buyers and verifiers, validated together."""

    sellers: list[Seller]
    buyers: list[Buyer]
    verifiers: list[Verifier]

    def check(self) -> "Population":
        """Names are identifiers and distinct; every project has exactly one verifier."""
        names = [s.agent for s in self.sellers] + [b.agent for b in self.buyers]
        names += [v.agent for v in self.verifiers]
        if not self.sellers or not self.buyers or not self.verifiers:
            raise ContractError("a market needs at least one seller, one buyer and one verifier")
        bad = [n for n in names if not NAME.match(n)]
        if bad:
            raise ContractError(f"agent names must be identifiers (letters, digits, _): {bad}")
        if len(set(names)) != len(names):
            raise ContractError("agent names must be distinct across sellers, buyers, verifiers")
        projects = [p for v in self.verifiers for p in v.projects]
        if sorted(projects) != sorted(s.agent for s in self.sellers):
            raise ContractError(
                "every seller's project must be assigned to exactly one verifier; "
                f"assigned {sorted(projects)}"
            )
        if any(not v.projects for v in self.verifiers):
            raise ContractError("every verifier needs at least one project")
        return self

    def seller(self, agent: str) -> Seller:
        """The seller called ``agent``."""
        return next(s for s in self.sellers if s.agent == agent)

    def verifier_of(self, project: str) -> str:
        """The verifier a project is assigned to."""
        return next(v.agent for v in self.verifiers if project in v.projects)


def draw_population(
    n_sellers: int,
    n_buyers: int,
    n_verifiers: int,
    seed: int,
    bias: list[float] | None = None,
    sigma: float = 0.03,
) -> Population:
    """A population drawn from ``seed`` (a pure function of the arguments).

    Sellers ``s0..`` have 8-14 credits, true quality uniform in ``[0.3, 0.9]`` and cost
    ``1.5 + 7 * theta``; buyers ``b0..`` demand 6-12 credits at a value uniform in ``[9, 13]``;
    verifiers ``v0..`` get the projects round-robin (``s_j`` goes to ``v_(j mod n_verifiers)``),
    with ``bias[k]`` (default 0) and noise ``sigma``. Values are rounded to cents.
    """
    for name, value in (
        ("n_sellers", n_sellers),
        ("n_buyers", n_buyers),
        ("n_verifiers", n_verifiers),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ContractError(f"{name} must be an int >= 1, got {value!r}")
    if bias is not None and len(bias) != n_verifiers:
        raise ContractError(f"bias needs one entry per verifier ({n_verifiers}), got {len(bias)}")
    rng = np.random.default_rng(seed)
    stock = rng.integers(8, 15, size=n_sellers)
    theta = rng.uniform(0.3, 0.9, size=n_sellers)
    wants = rng.integers(6, 13, size=n_buyers)
    worth = rng.uniform(9.0, 13.0, size=n_buyers)
    sellers = [
        Seller(
            agent=f"s{j}",
            quantity=int(stock[j]),
            theta=round(float(theta[j]), 2),
            cost=round(1.5 + 7.0 * round(float(theta[j]), 2), 2),
        )
        for j in range(n_sellers)
    ]
    buyers = [
        Buyer(agent=f"b{i}", demand=int(wants[i]), value=round(float(worth[i]), 2))
        for i in range(n_buyers)
    ]
    verifiers = [
        Verifier(
            agent=f"v{k}",
            projects=[f"s{j}" for j in range(n_sellers) if j % n_verifiers == k],
            bias=0.0 if bias is None else bias[k],
            sigma=sigma,
        )
        for k in range(n_verifiers)
    ]
    return Population(sellers=sellers, buyers=buyers, verifiers=verifiers).check()


def lemons_population() -> Population:
    """The tutorial's four-project market (see the VCM tutorial).

    ``s0`` and ``s1`` are high quality and expensive, verified by the honest ``v0``; ``s2`` and
    ``s3`` are low quality and cheap, verified by ``v1``, who measures 0.3 too high. Three buyers
    want ten credits each.
    """
    sellers = [
        Seller(agent="s0", quantity=10, theta=0.9, cost=8.0),
        Seller(agent="s1", quantity=10, theta=0.8, cost=7.0),
        Seller(agent="s2", quantity=10, theta=0.4, cost=3.0),
        Seller(agent="s3", quantity=10, theta=0.3, cost=2.5),
    ]
    buyers = [
        Buyer(agent="b0", demand=10, value=10.0),
        Buyer(agent="b1", demand=10, value=10.5),
        Buyer(agent="b2", demand=10, value=11.0),
    ]
    verifiers = [
        Verifier(agent="v0", projects=["s0", "s1"], bias=0.0, sigma=0.03),
        Verifier(agent="v1", projects=["s2", "s3"], bias=0.3, sigma=0.03),
    ]
    return Population(sellers=sellers, buyers=buyers, verifiers=verifiers).check()
