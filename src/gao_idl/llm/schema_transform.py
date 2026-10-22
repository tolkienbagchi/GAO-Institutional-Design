"""Request-side JSON Schema transform for the Anthropic structured-output feature.

The API accepts a subset of JSON Schema: numeric and length bounds, ``pattern`` and ``maxItems``
are not supported, ``minItems`` only as 0 or 1, and every object must say
``additionalProperties: false``. :func:`to_anthropic_schema` rewrites the schema pydantic emits to
that subset and moves each dropped constraint into the node's ``description`` so the model still
reads it.

This is a transform of the *request* only. The agent's local validator keeps checking replies
against the original schema (``llm/validate.py``), so whatever the API lets through that the
original schema forbids is still rejected and repaired.

Stability: ``to_anthropic_schema`` is public (exported by ``gao_idl.llm``); the rest of this
module is internal.
"""

import copy
import json
from typing import Any

# Dropped keywords, in the order they are appended to the description.
_DROPPED = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "maxItems",
)
# Keywords whose value is a list of subschemas / one subschema / a name -> subschema map.
_SCHEMA_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")
_SCHEMA_SINGLE = ("items", "not", "additionalProperties", "contains")
_SCHEMA_MAPS = ("properties", "$defs", "definitions", "patternProperties")


def to_anthropic_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``schema`` restricted to what the Messages API accepts (pure, idempotent).

    * ``minimum``, ``maximum``, ``exclusiveMinimum``, ``exclusiveMaximum``, ``multipleOf``,
      ``minLength``, ``maxLength``, ``pattern`` and ``maxItems`` are removed (``pattern`` always:
      the local validator enforces it); ``minItems`` above 1 is clamped to 1.
    * Each removed or clamped constraint is appended to the node's ``description`` as
      ``"(minimum: 0)"``.
    * Every object schema (``type: object`` or with ``properties``) that lacks
      ``additionalProperties`` gets ``additionalProperties: false``; an existing value is kept.
    * ``$defs`` / ``$ref``, ``enum``, ``const``, ``anyOf``, ``type``, ``required``, ``properties``,
      ``items``, ``default``, ``title`` and ``description`` are kept as they are.
    """
    return _node(copy.deepcopy(schema))  # type: ignore[no-any-return]


def _node(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    notes: list[str] = []
    for key in _DROPPED:
        if key in node:
            notes.append(f"({key}: {_show(node.pop(key))})")
    min_items = node.get("minItems")
    if isinstance(min_items, (int, float)) and not isinstance(min_items, bool) and min_items > 1:
        notes.append(f"(minItems: {_show(min_items)})")
        node["minItems"] = 1
    if notes:
        description = node.get("description")
        parts = [description] if isinstance(description, str) and description else []
        node["description"] = " ".join([*parts, *notes])
    for key in _SCHEMA_LISTS:
        if isinstance(node.get(key), list):
            node[key] = [_node(sub) for sub in node[key]]
    for key in _SCHEMA_SINGLE:
        if isinstance(node.get(key), dict):
            node[key] = _node(node[key])
    for key in _SCHEMA_MAPS:
        if isinstance(node.get(key), dict):
            node[key] = {name: _node(sub) for name, sub in node[key].items()}
    if (
        node.get("type") == "object" or "properties" in node
    ) and "additionalProperties" not in node:
        node["additionalProperties"] = False
    return node


def _show(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)
