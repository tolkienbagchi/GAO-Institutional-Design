"""RandomAgent: samples a valid action straight from the JSON Schema in the request."""

import math
from typing import Any

import numpy as np

from gao_idl.agents.base import EpisodeContext, EpisodeEnd
from gao_idl.core.schemas import DecisionRequest, ParticipantReport

UNBOUNDED_SPAN = 10.0
UNSUPPORTED_KEYWORDS = frozenset(
    {
        "$ref",
        "anyOf",
        "oneOf",
        "allOf",
        "not",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
    }
)


def _bounds(schema: dict[str, Any]) -> tuple[float, float]:
    """Sampling interval: ``[minimum, maximum]``; one open side is ``UNBOUNDED_SPAN`` wide.

    No ``maximum``: ``[min, min + 10]``. No ``minimum``: ``[max - 10, max]``. Neither: ``[0, 10]``.
    """
    minimum, maximum = schema.get("minimum"), schema.get("maximum")
    if minimum is not None:
        lo = float(minimum)
        hi = float(maximum) if maximum is not None else lo + UNBOUNDED_SPAN
    elif maximum is not None:
        hi = float(maximum)
        lo = hi - UNBOUNDED_SPAN
    else:
        lo, hi = 0.0, UNBOUNDED_SPAN
    if lo > hi:
        raise ValueError(f"empty range in schema fragment {schema!r}")
    return lo, hi


def sample_schema(schema: dict[str, Any], rng: np.random.Generator) -> Any:
    """A value valid under ``schema``.

    Supported: ``enum`` / ``const`` (Literal), ``integer`` and ``number`` with ``minimum`` /
    ``maximum`` (see ``_bounds``), ``boolean``, and ``object`` with ``required`` properties,
    recursively (optional properties are omitted). Anything else raises ``NotImplementedError``
    naming the fragment, rather than guessing.
    """
    if bad := sorted(UNSUPPORTED_KEYWORDS & set(schema)):
        raise NotImplementedError(f"unsupported keyword(s) {bad} in schema fragment {schema!r}")
    if "enum" in schema:
        values = schema["enum"]
        return values[int(rng.integers(len(values)))]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "boolean":
        return bool(rng.integers(2))
    if kind == "integer":
        lo, hi = _bounds(schema)
        low, high = math.ceil(lo), math.floor(hi)
        if low > high:
            raise ValueError(f"no integer in range for schema fragment {schema!r}")
        return int(rng.integers(low, high + 1))
    if kind == "number":
        lo, hi = _bounds(schema)
        return float(rng.uniform(lo, hi))
    if kind == "object":
        props: dict[str, Any] = schema.get("properties", {})
        out: dict[str, Any] = {}
        for name in schema.get("required", []):
            if name not in props:
                raise NotImplementedError(
                    f"required property {name!r} has no schema in fragment {schema!r}"
                )
            out[name] = sample_schema(props[name], rng)
        return out
    raise NotImplementedError(f"unsupported JSON Schema fragment: {schema!r}")


class RandomAgent:
    """Acts uniformly at random within each request's JSON Schema, from its own seeded stream."""

    def __init__(self, seed: int) -> None:
        self._rng = np.random.default_rng(seed)

    async def episode_start(self, ctx: EpisodeContext) -> None:
        """Nothing to prepare."""

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        """A random valid action for ``request`` (it may still be infeasible for the layer)."""
        action = sample_schema(request.action_schema, self._rng)
        if not isinstance(action, dict):
            raise NotImplementedError(
                f"action schema must describe an object: {request.action_schema!r}"
            )
        return action

    async def observe(self, report: ParticipantReport) -> None:
        """Reports are ignored."""

    async def episode_end(self, end: EpisodeEnd) -> None:
        """Nothing to clean up."""
