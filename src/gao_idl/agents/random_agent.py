"""RandomAgent: samples a valid action straight from the JSON Schema in the request.

Stability: ``RandomAgent`` is public (re-exported by ``gao_idl``); the rest of this module is
supporting.
"""

import math
from typing import Any

import numpy as np

from gao_idl.agents.base import EpisodeContext, EpisodeEnd
from gao_idl.core.schemas import DecisionRequest, ParticipantReport

UNBOUNDED_SPAN = 10.0
DEFAULT_STRING_LENGTHS = (1, 12)
LETTERS = "abcdefghijklmnopqrstuvwxyz"
EXCLUSIVE_KEYWORDS = frozenset({"exclusiveMinimum", "exclusiveMaximum"})
UNSUPPORTED_KEYWORDS = frozenset(
    {"$ref", "anyOf", "oneOf", "allOf", "not", "multipleOf"} | EXCLUSIVE_KEYWORDS
)
UNSUPPORTED_STRING_KEYWORDS = frozenset({"pattern", "format"})


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


def _number(schema: dict[str, Any], key: str) -> float | None:
    """A numeric bound of ``schema`` as a float (None if absent); anything else is unsupported."""
    value = schema.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise NotImplementedError(f"non-numeric {key!r} in schema fragment {schema!r}")
    return float(value)


def _sample_number(schema: dict[str, Any], rng: np.random.Generator) -> float:
    """A float strictly inside any ``exclusiveMinimum`` / ``exclusiveMaximum`` of ``schema``.

    Closed bounds work as in ``_bounds``. With both a closed and an exclusive bound on one side the
    tighter one wins (the exclusive one on a tie). An open side is ``UNBOUNDED_SPAN`` wide:
    no upper bound gives ``(exclusiveMinimum, exclusiveMinimum + 10]``, no lower bound
    ``[maximum - 10, maximum)``, neither ``[0, 10]``.
    """
    lo, lo_open = _number(schema, "minimum"), False
    hi, hi_open = _number(schema, "maximum"), False
    ex_lo, ex_hi = _number(schema, "exclusiveMinimum"), _number(schema, "exclusiveMaximum")
    if ex_lo is not None and (lo is None or ex_lo >= lo):
        lo, lo_open = ex_lo, True
    if ex_hi is not None and (hi is None or ex_hi <= hi):
        hi, hi_open = ex_hi, True
    had_lower = lo is not None
    if lo is None:
        lo = 0.0 if hi is None else hi - UNBOUNDED_SPAN
    if hi is None:
        hi = lo + UNBOUNDED_SPAN if had_lower else UNBOUNDED_SPAN
    if lo > hi or ((lo_open or hi_open) and lo >= hi):
        raise ValueError(f"empty range in schema fragment {schema!r}")
    for _ in range(100):
        x = float(rng.uniform(lo, hi))  # half-open: [lo, hi) up to rounding
        if (x > lo or not lo_open) and (x < hi or not hi_open):
            return x
    raise ValueError(f"no value strictly inside the range of schema fragment {schema!r}")


def _sample_string(schema: dict[str, Any], rng: np.random.Generator) -> str:
    """Lowercase letters, length uniform in ``[minLength, maxLength]`` (default range 1 to 12).

    Only one bound given: ``minLength`` alone gives ``[minLength, max(minLength, 12)]``,
    ``maxLength`` alone ``[min(1, maxLength), maxLength]``. ``pattern`` and ``format`` are
    unsupported (ignoring them would be unsound).
    """
    if bad := sorted(UNSUPPORTED_STRING_KEYWORDS & set(schema)):
        raise NotImplementedError(f"unsupported keyword(s) {bad} in schema fragment {schema!r}")
    minimum, maximum = schema.get("minLength"), schema.get("maxLength")
    for bound in (minimum, maximum):
        if bound is not None and (isinstance(bound, bool) or not isinstance(bound, int)):
            raise NotImplementedError(f"non-integer length bound in schema fragment {schema!r}")
    if maximum is None:
        low = DEFAULT_STRING_LENGTHS[0] if minimum is None else int(minimum)
        high = max(low, DEFAULT_STRING_LENGTHS[1])
    else:
        high = int(maximum)
        low = min(DEFAULT_STRING_LENGTHS[0], high) if minimum is None else int(minimum)
    if low < 0 or low > high:
        raise ValueError(f"no valid string length in schema fragment {schema!r}")
    size = int(rng.integers(low, high + 1))
    return "".join(LETTERS[int(i)] for i in rng.integers(0, len(LETTERS), size=size))


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

    Supported: ``enum`` / ``const`` (Literal), ``integer`` with ``minimum`` / ``maximum`` (see
    ``_bounds``), ``number`` with those and ``exclusiveMinimum`` / ``exclusiveMaximum`` (see
    ``_sample_number``), ``string`` with ``minLength`` / ``maxLength`` (see ``_sample_string``),
    ``boolean``, and ``object`` with ``required`` properties, recursively (optional properties
    are omitted), and ``array`` whose ``items`` are a finite set (``enum`` / ``const`` /
    ``anyOf`` of consts): a subset without replacement, size uniform in
    ``[minItems or 0, maxItems or len]``. Anything else raises ``NotImplementedError``
    naming the fragment, rather than guessing.
    """
    unsupported = UNSUPPORTED_KEYWORDS
    if schema.get("type") == "number" and "enum" not in schema and "const" not in schema:
        unsupported = unsupported - EXCLUSIVE_KEYWORDS  # numbers sample strictly inside them
    if bad := sorted(unsupported & set(schema)):
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
        return _sample_number(schema, rng)
    if kind == "string":
        return _sample_string(schema, rng)
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
