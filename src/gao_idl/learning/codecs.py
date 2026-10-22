"""Codecs: how a tabular learner turns requests and reports into table keys and finite actions.

A codec only ever sees ``DecisionRequest`` / ``ParticipantReport`` objects addressed to the learner
(invariant 1). Keys are nested tuples of JSON scalars, so a Q-table can be written to JSON and read
back (``to_jsonable`` / ``from_jsonable``).
"""

import itertools
import json
import math
from collections.abc import Hashable
from typing import Any, Protocol

from gao_idl.core.schemas import DecisionRequest, ParticipantReport

MAX_ACTIONS = 10_000
"""Upper bound on the size of one request's action set (a product of per-field domains)."""

JsonScalar = None | bool | int | float | str


class ObsCodec(Protocol):
    """Encodes a decision request plus the reports received since the previous decision."""

    def encode(self, request: DecisionRequest, since_last: list[ParticipantReport]) -> Hashable:
        """Return the table key (state) for ``request``."""
        ...


class ActCodec(Protocol):
    """Enumerates the finite action set of a request and gives each action a table key."""

    def actions(self, request: DecisionRequest) -> list[dict[str, Any]]:
        """The finite action set for this request, in a deterministic order."""
        ...

    def encode(self, action: dict[str, Any]) -> Hashable:
        """The table key of one action."""
        ...


# ---------------------------------------------------------------- JSON <-> hashable keys


def freeze(value: Any) -> Hashable:
    """A JSON value as a hashable key (lists -> tuples, dicts -> sorted ``(key, value)`` tuples).

    Raises ``TypeError`` for non-JSON objects and ``ValueError`` for non-finite floats.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float {value!r} cannot be part of a table key")
        return value
    if isinstance(value, list | tuple):
        return tuple(freeze(v) for v in value)
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise TypeError(f"only string keys can be frozen, got {sorted(map(repr, value))}")
        return tuple((k, freeze(value[k])) for k in sorted(value))
    raise TypeError(f"cannot freeze {type(value).__name__}")


def to_jsonable(key: Hashable) -> Any:
    """A frozen key as JSON: every tuple becomes a list (scalars are unchanged)."""
    if isinstance(key, tuple):
        return [to_jsonable(k) for k in key]
    return key


def from_jsonable(value: Any) -> Hashable:
    """Inverse of ``to_jsonable``: every list becomes a tuple again."""
    if isinstance(value, list):
        return tuple(from_jsonable(v) for v in value)
    return value  # type: ignore[no-any-return]


def lookup(obj: Any, path: str) -> Any:
    """The value at a dotted path through dicts/lists (integer segments); ``KeyError`` if absent."""
    current = obj
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            raise KeyError(path)
    return current


def _check_paths(fields: list[str], what: str) -> None:
    if len(set(fields)) != len(fields):
        raise ValueError(f"{what} has duplicate entries: {fields}")
    for f in fields:
        if not isinstance(f, str) or not f or any(seg == "" for seg in f.split(".")):
            raise ValueError(f"{what}: {f!r} is not a dotted path")


# ---------------------------------------------------------------- observation codec


class FieldObsCodec:
    """State = the selected observation fields plus those of the last ``history`` reports.

    ``fields`` are dotted paths into ``request.observation``. Every field must exist in the
    observation of every layer/role this codec is used on: that is checked on the first ``encode``
    for each (layer, role) and cached; a missing field is a ``ValueError``.

    History (``history > 0``) uses the last ``history`` reports of ``since_last`` (the reports
    received since the learner's previous decision), oldest first, padded on the left with ``None``
    when fewer arrived. It is stateless: reports older than the previous decision are not seen.
    ``report_fields`` (default: the same as ``fields``) names the fields taken from each report
    payload; a report that lacks a field contributes ``None`` for it.
    """

    def __init__(
        self, fields: list[str], history: int = 0, *, report_fields: list[str] | None = None
    ) -> None:
        _check_paths(fields, "fields")
        if report_fields is not None:
            _check_paths(report_fields, "report_fields")
        if isinstance(history, bool) or not isinstance(history, int) or history < 0:
            raise ValueError(f"history must be an int >= 0, got {history!r}")
        self.fields = list(fields)
        self.history = history
        self.report_fields = list(fields if report_fields is None else report_fields)
        self._checked: set[tuple[str, str]] = set()

    def config(self) -> dict[str, Any]:
        """The constructor arguments (written into checkpoints to identify the codec)."""
        return {"fields": self.fields, "history": self.history, "report_fields": self.report_fields}

    def encode(self, request: DecisionRequest, since_last: list[ParticipantReport]) -> Hashable:
        """``(observation fields, report_1 fields, ..., report_h fields)`` as nested tuples."""
        self._check(request)
        obs = tuple(freeze(lookup(request.observation, f)) for f in self.fields)
        if self.history == 0:
            return obs
        recent = since_last[-self.history :]
        reports = [
            tuple(freeze(_get(report.payload, f)) for f in self.report_fields) for report in recent
        ]
        padding: list[Hashable] = [None] * (self.history - len(reports))
        return (obs, *padding, *reports)

    def _check(self, request: DecisionRequest) -> None:
        key = (request.layer, str(request.role))
        if key in self._checked:
            return
        for f in self.fields:
            try:
                lookup(request.observation, f)
            except KeyError:
                raise ValueError(
                    f"field {f!r} is not in the observation of layer {request.layer!r} "
                    f"(role {request.role!r}); observation keys: {sorted(request.observation)}"
                ) from None
        self._checked.add(key)


def _get(payload: dict[str, Any], path: str) -> Any:
    try:
        return lookup(payload, path)
    except KeyError:
        return None


# ---------------------------------------------------------------- action codec


class SchemaActCodec:
    """Enumerates the actions of a request from its JSON Schema (required properties only).

    Supported property types: ``enum`` / ``const``, ``boolean``, ``integer`` with both bounds
    (``minimum`` / ``maximum``, or their exclusive forms), and ``number`` when ``grid[name]`` lists
    the values to consider. Anything else (unbounded integers, strings without ``enum``, arrays,
    ``$ref``, ``anyOf`` ...) is a ``ValueError``, as is a set larger than ``MAX_ACTIONS``. Optional
    properties are left out of the actions. Order: schema property order, then each domain's order,
    first property varying slowest. ``grid`` keys that match no number field are ignored.
    """

    def __init__(self, grid: dict[str, list[Any]] | None = None) -> None:
        self.grid = None if grid is None else {k: list(v) for k, v in grid.items()}
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def config(self) -> dict[str, Any]:
        """The constructor arguments (written into checkpoints to identify the codec)."""
        return {"grid": self.grid}

    def actions(self, request: DecisionRequest) -> list[dict[str, Any]]:
        """All actions of ``request.action_schema`` (fresh dicts on every call)."""
        cache_key = json.dumps(request.action_schema, sort_keys=True)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._enumerate(request.action_schema)
        return [dict(a) for a in self._cache[cache_key]]

    def encode(self, action: dict[str, Any]) -> Hashable:
        """The action as a sorted tuple of ``(field, value)`` pairs."""
        return freeze(action)

    def _enumerate(self, schema: dict[str, Any]) -> list[dict[str, Any]]:
        if schema.get("type") != "object" or "properties" not in schema:
            raise ValueError(f"SchemaActCodec needs an object schema with properties: {schema!r}")
        required = set(schema.get("required", []))
        names = [n for n in schema["properties"] if n in required]
        domains = [self._domain(n, schema["properties"][n]) for n in names]
        size = math.prod(len(d) for d in domains)
        if size > MAX_ACTIONS:
            raise ValueError(f"action set has {size} elements, more than MAX_ACTIONS={MAX_ACTIONS}")
        return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*domains)]

    def _domain(self, name: str, prop: dict[str, Any]) -> list[Any]:
        if "enum" in prop:
            values = list(prop["enum"])
        elif "const" in prop:
            values = [prop["const"]]
        elif prop.get("type") == "boolean":
            values = [False, True]
        elif prop.get("type") == "integer":
            values = self._integers(name, prop)
        elif prop.get("type") == "number":
            values = self._numbers(name, prop)
        else:
            raise ValueError(f"property {name!r}: unsupported or non-finite schema {prop!r}")
        if not values:
            raise ValueError(f"property {name!r} has an empty domain")
        return values

    @staticmethod
    def _bounds(prop: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
        return (
            prop.get("minimum"),
            prop.get("maximum"),
            prop.get("exclusiveMinimum"),
            prop.get("exclusiveMaximum"),
        )

    def _integers(self, name: str, prop: dict[str, Any]) -> list[Any]:
        lo, hi, xlo, xhi = self._bounds(prop)
        if "multipleOf" in prop:
            raise ValueError(f"property {name!r}: multipleOf is not supported")
        low = lo if lo is not None else (None if xlo is None else math.floor(xlo) + 1)
        high = hi if hi is not None else (None if xhi is None else math.ceil(xhi) - 1)
        if low is None or high is None:
            raise ValueError(f"integer property {name!r} is not bounded: {prop!r}")
        return list(range(math.ceil(low), math.floor(high) + 1))

    def _numbers(self, name: str, prop: dict[str, Any]) -> list[Any]:
        if self.grid is None or name not in self.grid:
            raise ValueError(f"number property {name!r} needs a grid entry (grid={self.grid!r})")
        if "multipleOf" in prop:
            raise ValueError(f"property {name!r}: multipleOf is not supported")
        lo, hi, xlo, xhi = self._bounds(prop)
        values: list[Any] = []
        for v in self.grid[name]:
            if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
                raise ValueError(f"grid[{name!r}] contains {v!r}, which is not a finite number")
            if (
                (lo is not None and v < lo)
                or (hi is not None and v > hi)
                or (xlo is not None and v <= xlo)
                or (xhi is not None and v >= xhi)
            ):
                raise ValueError(f"grid[{name!r}] value {v!r} violates the schema {prop!r}")
            values.append(v)
        return list(dict.fromkeys(values))


def codec_spec(codec: object) -> dict[str, Any]:
    """``{"type": class name, "args": constructor args}`` of a codec (args from ``config()``)."""
    config = getattr(codec, "config", None)
    args = config() if callable(config) else {}
    spec: dict[str, Any] = {"type": type(codec).__name__, "args": args}
    json.dumps(spec)  # must be JSON-able: it goes into checkpoints
    return spec
