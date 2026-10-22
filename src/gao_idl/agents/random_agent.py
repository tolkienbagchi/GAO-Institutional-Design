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


def _choices(items: dict[str, Any]) -> list[Any] | None:
    """The allowed values of an array's ``items`` if they form a finite set, else None.

    Recognised: ``{"enum": [...]}``, ``{"const": x}``, and an ``anyOf`` of consts / one-value enums
    (the shapes pydantic and hand-written schemas use for ``Literal``).
    """
    if "enum" in items:
        return list(items["enum"])
    if "const" in items:
        return [items["const"]]
    options = items.get("anyOf")
    if isinstance(options, list) and options:
        out: list[Any] = []
        for option in options:
            sub = _choices(option) if isinstance(option, dict) else None
            if sub is None or "anyOf" in option:
                return None
            out += sub
        return out
    return None


def _sample_array(schema: dict[str, Any], rng: np.random.Generator) -> list[Any]:
    """A subset of the item choices without replacement, in random order; size uniform in range."""
    items = schema.get("items")
    values = _choices(items) if isinstance(items, dict) else None
    if values is None:
        raise NotImplementedError(f"unsupported array items in schema fragment: {schema!r}")
    low = int(schema.get("minItems", 0))
    high = min(int(schema.get("maxItems", len(values))), len(values))
    if low > high:
        raise ValueError(f"no valid array size in schema fragment {schema!r}")
    size = int(rng.integers(low, high + 1))
    return [values[int(i)] for i in rng.choice(len(values), size=size, replace=False)]


def sample_schema(schema: dict[str, Any], rng: np.random.Generator) -> Any:
    """A value valid under ``schema``.

    Supported: ``enum`` / ``const`` (Literal), ``integer`` and ``number`` with ``minimum`` /
    ``maximum`` (see ``_bounds``), ``boolean``, and ``object`` with ``required`` properties,
    recursively (optional properties are omitted), and ``array`` whose ``items`` are a finite
    set (``enum`` / ``const`` / ``anyOf`` of consts): a subset without replacement, size uniform in
    ``[minItems or 0, maxItems or len]``. Anything else raises ``NotImplementedError``
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
    if kind == "array":
        return _sample_array(schema, rng)
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
