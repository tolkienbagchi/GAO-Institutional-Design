"""The library: the environments a service offers, as an administrator declared them.

The library file is trusted configuration. It, and the specs it points to, are the only places a
``module:function`` string may come from: a client of the service names an entry by its slug and
a cast by its name, never code. ``load_library`` checks the whole file before anything is served;
``resolve_params`` turns a client's overrides into the factory parameters of one run, accepting
only the parameters the entry declares, with their types and bounds.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import importlib
import json
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, PrivateAttr, ValidationError

from gao_idl.authoring.cards import Card, load_cards
from gao_idl.authoring.spec import EnvSpec, load_spec
from gao_idl.core.schemas import DecisionRequest, FrozenModel, ParticipantReport

Policy = Callable[[DecisionRequest, list[ParticipantReport]], dict[str, Any]]

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")


class _Strict(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ParamSpec(_Strict):
    """One factory parameter a user may change: its kind, bounds, label and default.

    ``minimum`` and ``maximum`` apply to ``int`` and ``float``; ``choices`` to ``choice``.
    """

    name: str
    label: str
    help: str = ""
    kind: Literal["int", "float", "bool", "choice"]
    minimum: float | None = None
    maximum: float | None = None
    choices: list[Any] = []
    default: Any


class Cast(_Strict):
    """A named set of scripted policies: role kind -> ``module:function`` of a policy."""

    name: str
    label: str
    description: str
    policies: dict[str, str]


class Entry(_Strict):
    """One environment of the library; ``spec`` and ``cards`` are relative to the library file."""

    slug: str
    title: str
    spec: str
    cards: str
    params: list[ParamSpec]
    casts: list[Cast]


class Library(_Strict):
    """Every entry of a library file, checked by ``load_library``.

    A library made by ``load_library`` also holds each entry's spec, cards and resolved policies
    (``spec_of``, ``cards_of``, ``policy``); one built by hand has none of them.
    """

    entries: list[Entry]
    _specs: dict[str, EnvSpec] = PrivateAttr(default_factory=dict)
    _cards: dict[str, list[Card]] = PrivateAttr(default_factory=dict)
    _policies: dict[str, Policy] = PrivateAttr(default_factory=dict)

    def entry(self, slug: str) -> Entry:
        """The entry called ``slug``; ``KeyError`` if there is none."""
        for e in self.entries:
            if e.slug == slug:
                return e
        raise KeyError(slug)

    def spec_of(self, entry: Entry) -> EnvSpec:
        """The spec ``entry`` points to, as loaded by ``load_library``."""
        return self._specs[entry.slug]

    def cards_of(self, entry: Entry) -> list[Card]:
        """The cards ``entry`` points to, as loaded by ``load_library``."""
        return list(self._cards[entry.slug])

    def policy(self, ref: str) -> Policy:
        """The policy named ``ref`` in some cast of this library (resolved at load)."""
        return self._policies[ref]


def _resolve_policy(ref: str) -> Policy:
    """Import ``module:function`` from the trusted library file; ``ValueError`` if it fails."""
    module_path, sep, attr = ref.partition(":")
    if not sep or not module_path or not attr:
        raise ValueError(f"a policy must look like 'module.path:function', got {ref!r}")
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ValueError(f"policy {ref!r}: cannot import {module_path!r} ({e})") from e
    fn = getattr(module, attr, None)
    if fn is None or not callable(fn):
        raise ValueError(f"policy {ref!r}: {module_path!r} has no function {attr!r}")
    return fn  # type: ignore[no-any-return]


def check_value(p: ParamSpec, value: Any) -> Any:
    """``value`` checked against ``p`` (an int given for a float becomes a float).

    Raises ``ValueError`` naming the parameter when the type, the bounds or the choice is wrong.
    """
    where = f"parameter {p.name!r}"
    if p.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{where} must be true or false, got {json.dumps(value)[:40]}")
        return value
    if p.kind == "choice":
        for c in p.choices:
            if type(c) is type(value) and c == value:
                return value
        raise ValueError(f"{where} must be one of {json.dumps(p.choices)[:200]}")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{where} must be a number, got {json.dumps(value)[:40]}")
    if p.kind == "int" and not isinstance(value, int):
        raise ValueError(f"{where} must be a whole number, got {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{where} must be a finite number")
    if p.minimum is not None and value < p.minimum:
        raise ValueError(f"{where} must be at least {p.minimum:g}, got {value!r}")
    if p.maximum is not None and value > p.maximum:
        raise ValueError(f"{where} must be at most {p.maximum:g}, got {value!r}")
    return float(value) if p.kind == "float" else value


def _check_param(p: ParamSpec, spec: EnvSpec, where: str) -> None:
    if not NAME.fullmatch(p.name):
        raise ValueError(f"{where}: bad parameter name {p.name!r}")
    if p.name not in spec.params:
        raise ValueError(f"{where}: parameter {p.name!r} is not a parameter of the spec")
    if p.kind == "choice":
        if not p.choices:
            raise ValueError(f"{where}: parameter {p.name!r} has no choices")
        if p.minimum is not None or p.maximum is not None:
            raise ValueError(f"{where}: parameter {p.name!r}: bounds apply to numbers only")
    elif p.choices:
        raise ValueError(f"{where}: parameter {p.name!r}: choices apply to kind 'choice' only")
    if p.kind == "bool" and (p.minimum is not None or p.maximum is not None):
        raise ValueError(f"{where}: parameter {p.name!r}: bounds apply to numbers only")
    if p.minimum is not None and p.maximum is not None and p.minimum > p.maximum:
        raise ValueError(f"{where}: parameter {p.name!r}: minimum is above maximum")
    try:
        check_value(p, p.default)
    except ValueError as e:
        raise ValueError(f"{where}: the default of {e}") from e


def load_library(path: Path) -> Library:
    """Read and check a library file; ``ValueError`` naming the first problem.

    Checks: slugs unique and made of lower-case letters, digits and hyphens; each spec and cards
    file exists and loads (cards for the spec's environment); every parameter is a key of the
    spec's ``params`` and its default is within its own bounds; every cast covers exactly the
    spec's role kinds; every policy resolves to a function.
    """
    path = Path(path)
    try:
        library = Library.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"cannot read the library file {path}: {e}") from e
    except ValidationError as e:
        raise ValueError(f"the library file {path} is not valid: {e}") from e
    root = path.resolve().parent
    seen: set[str] = set()
    for entry in library.entries:
        where = f"entry {entry.slug!r}"
        if not SLUG.fullmatch(entry.slug):
            raise ValueError(f"{where}: a slug is lower-case letters, digits and hyphens")
        if entry.slug in seen:
            raise ValueError(f"{where}: the slug is used twice")
        seen.add(entry.slug)
        try:
            spec = load_spec(root / entry.spec)
        except (OSError, ValueError) as e:
            raise ValueError(f"{where}: cannot load the spec {entry.spec}: {e}") from e
        try:
            cards = load_cards(root / entry.cards)
        except (OSError, ValueError) as e:
            raise ValueError(f"{where}: cannot load the cards {entry.cards}: {e}") from e
        for card in cards:
            if card.env_ref != spec.env_ref:
                raise ValueError(f"{where}: card {card.id!r} is for another environment")
        names = [p.name for p in entry.params]
        if len(set(names)) != len(names):
            raise ValueError(f"{where}: a parameter is declared twice")
        for p in entry.params:
            _check_param(p, spec, where)
        kinds = {r.name for r in spec.roles}
        cast_names = [c.name for c in entry.casts]
        if not entry.casts:
            raise ValueError(f"{where}: no casts")
        if len(set(cast_names)) != len(cast_names):
            raise ValueError(f"{where}: a cast name is used twice")
        for cast in entry.casts:
            if not NAME.fullmatch(cast.name):
                raise ValueError(f"{where}: bad cast name {cast.name!r}")
            missing = sorted(kinds - set(cast.policies))
            if missing:
                raise ValueError(f"{where}: cast {cast.name!r} has no policy for {missing}")
            extra = sorted(set(cast.policies) - kinds)
            if extra:
                raise ValueError(f"{where}: cast {cast.name!r} names unknown role kinds {extra}")
            for ref in cast.policies.values():
                try:
                    library._policies[ref] = _resolve_policy(ref)
                except ValueError as e:
                    raise ValueError(f"{where}: cast {cast.name!r}: {e}") from e
        library._specs[entry.slug] = spec
        library._cards[entry.slug] = cards
    return library


def resolve_params(entry: Entry, spec: EnvSpec, overrides: dict[str, Any]) -> dict[str, Any]:
    """``spec.params`` with ``overrides`` merged in, after checking every override.

    Only names declared in ``entry.params`` are accepted, each with its kind and bounds; anything
    else raises ``ValueError`` naming the field. The result is a new dict.
    """
    if not isinstance(overrides, dict):
        raise ValueError("params must be an object of parameter names and values")
    declared = {p.name: p for p in entry.params}
    params = json.loads(json.dumps(spec.params))  # a deep copy of plain JSON
    for name in sorted(overrides):
        if name not in declared:
            raise ValueError(f"parameter {str(name)[:60]!r} cannot be changed here")
        params[name] = check_value(declared[name], overrides[name])
    return params  # type: ignore[no-any-return]
