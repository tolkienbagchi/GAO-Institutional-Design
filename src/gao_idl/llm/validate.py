"""A small JSON Schema checker for pre-validating model output before it is submitted.

The adapter does not have the layer's pydantic model, only the JSON Schema in the request. This
checks the shapes pydantic emits: ``type``, ``enum``, ``const``, ``minimum`` / ``maximum`` (and the
exclusive forms), ``required``, ``properties``, ``additionalProperties`` (``false`` or a schema),
``items``, ``anyOf`` (the ``Literal`` / ``Optional`` shapes) and local ``$ref`` into ``$defs``.
Other keywords are ignored, so this is a pre-check, never the authority: the layer still validates.

It never coerces. ``"5"`` is not a number, ``5.0`` is not an integer, and a bool is neither.

Stability: internal; the public LLM names are exported by ``gao_idl.llm``.
"""

import math
from typing import Any

MAX_REPORTED = 3


def against_json_schema(value: Any, schema: dict[str, Any]) -> list[str]:
    """Why ``value`` does not satisfy ``schema``; an empty list means it does."""
    errors: list[str] = []
    _check(value, schema, schema, "$", errors, 0)
    return errors


def first_problems(errors: list[str]) -> str:
    """A short, model-readable summary of the first few problems."""
    shown = "; ".join(errors[:MAX_REPORTED])
    more = len(errors) - MAX_REPORTED
    return shown + (f"; and {more} more" if more > 0 else "")


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _type_ok(v: Any, name: str) -> bool:
    if name == "string":
        return isinstance(v, str)
    if name == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if name == "number":
        return _is_number(v)
    if name == "boolean":
        return isinstance(v, bool)
    if name == "null":
        return v is None
    if name == "object":
        return isinstance(v, dict)
    if name == "array":
        return isinstance(v, list)
    return True  # unknown type names are not checked


def _same(a: Any, b: Any) -> bool:
    """JSON equality: ``True`` is not ``1``; ``1`` equals ``1.0``; containers compare deeply."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict | list) or isinstance(b, dict | list):
        return False
    if _is_number(a) and _is_number(b):
        return bool(a == b)
    return type(a) is type(b) and bool(a == b)


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def _show(v: Any) -> str:
    text = repr(v)
    return text if len(text) <= 60 else text[:57] + "..."


def _check(
    v: Any, schema: dict[str, Any], root: dict[str, Any], at: str, errors: list[str], depth: int
) -> None:
    if depth > 20:
        return
    if "$ref" in schema:
        target = _resolve(str(schema["$ref"]), root)
        if target is not None:
            _check(v, target, root, at, errors, depth + 1)
        return
    if "anyOf" in schema:
        branches = [b for b in schema["anyOf"] if isinstance(b, dict)]
        for branch in branches:
            sub: list[str] = []
            _check(v, branch, root, at, sub, depth + 1)
            if not sub:
                break
        else:
            if branches:
                errors.append(f"{at}: {_show(v)} matches none of the allowed alternatives")
                return
    if "const" in schema and not _same(v, schema["const"]):
        errors.append(f"{at}: must equal {_show(schema['const'])}, got {_show(v)}")
        return
    if "enum" in schema and not any(_same(v, e) for e in schema["enum"]):
        errors.append(f"{at}: {_show(v)} is not one of {_show(schema['enum'])}")
        return
    types = schema.get("type")
    if types is not None:
        names = types if isinstance(types, list) else [types]
        if not any(_type_ok(v, str(n)) for n in names):
            errors.append(f"{at}: {_show(v)} is not of type {'/'.join(map(str, names))}")
            return
    if _is_number(v):
        _numeric(v, schema, at, errors)
    if isinstance(v, dict):
        _object(v, schema, root, at, errors, depth)
    elif isinstance(v, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(v):
            _check(item, schema["items"], root, f"{at}[{i}]", errors, depth + 1)


def _numeric(v: Any, schema: dict[str, Any], at: str, errors: list[str]) -> None:
    lo, hi = schema.get("minimum"), schema.get("maximum")
    xlo, xhi = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
    if _is_number(lo) and v < lo:
        errors.append(f"{at}: {_show(v)} is less than the minimum {lo}")
    if _is_number(hi) and v > hi:
        errors.append(f"{at}: {_show(v)} is greater than the maximum {hi}")
    if _is_number(xlo) and v <= xlo:
        errors.append(f"{at}: {_show(v)} must be greater than {xlo}")
    if _is_number(xhi) and v >= xhi:
        errors.append(f"{at}: {_show(v)} must be less than {xhi}")


def _object(
    v: dict[str, Any],
    schema: dict[str, Any],
    root: dict[str, Any],
    at: str,
    errors: list[str],
    depth: int,
) -> None:
    props = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in v:
            errors.append(f"{at}: missing required field {name!r}")
    for name, sub in props.items():
        if name in v and isinstance(sub, dict):
            _check(v[name], sub, root, f"{at}.{name}", errors, depth + 1)
    extra = schema.get("additionalProperties")
    if extra is False:
        for name in v:
            if name not in props:
                errors.append(f"{at}: unexpected field {name!r}")
    elif isinstance(extra, dict):
        for name in v:
            if name not in props:
                _check(v[name], extra, root, f"{at}.{name}", errors, depth + 1)
