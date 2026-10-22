"""The local JSON Schema checker: the shapes pydantic emits, and no coercion."""

from typing import Any

import pytest
from pydantic import BaseModel, Field

from gao_idl.environments.auction.bidding import Bid
from gao_idl.environments.pd.schemas import PDAction
from gao_idl.llm.validate import against_json_schema, first_problems

BID = Bid.model_json_schema()
PD = PDAction.model_json_schema()


def ok(value: Any, schema: dict[str, Any]) -> bool:
    return against_json_schema(value, schema) == []


def test_the_bid_schema_accepts_numbers_and_refuses_the_rest() -> None:
    assert ok({"amount": 5}, BID) and ok({"amount": 5.5}, BID) and ok({"amount": 0}, BID)
    assert not ok({"amount": -1}, BID)
    assert not ok({"amount": "5"}, BID)  # never coerced
    assert not ok({"amount": True}, BID)
    assert not ok({"amount": None}, BID)
    assert not ok({}, BID)
    assert not ok([], BID)
    assert not ok({"amount": float("nan")}, BID) and not ok({"amount": float("inf")}, BID)


def test_messages_name_the_field_and_the_problem() -> None:
    assert "$.amount" in against_json_schema({"amount": -1}, BID)[0]
    assert "less than the minimum" in against_json_schema({"amount": -1}, BID)[0]
    assert "missing required field 'amount'" in against_json_schema({}, BID)[0]


def test_literal_enum_const() -> None:
    assert ok({"move": "C"}, PD) and ok({"move": "D"}, PD)
    assert not ok({"move": "X"}, PD) and not ok({"move": "c"}, PD) and not ok({"move": 1}, PD)
    assert ok("a", {"const": "a"}) and not ok("b", {"const": "a"})
    # JSON equality, not Python equality: True is not 1.
    assert not ok(True, {"enum": [1, 2]}) and not ok(1, {"enum": [True]})
    assert ok(1.0, {"enum": [1, 2]})


def test_types_are_strict() -> None:
    assert ok(5, {"type": "integer"}) and not ok(5.0, {"type": "integer"})
    assert not ok(True, {"type": "integer"}) and not ok("5", {"type": "integer"})
    assert (
        ok(5, {"type": "number"})
        and ok(5.5, {"type": "number"})
        and not ok(True, {"type": "number"})
    )
    assert ok(True, {"type": "boolean"}) and not ok(1, {"type": "boolean"})
    assert (
        ok(None, {"type": "null"}) and ok("s", {"type": "string"}) and not ok(1, {"type": "string"})
    )
    assert ok(1, {"type": ["integer", "null"]}) and ok(None, {"type": ["integer", "null"]})


def test_bounds_inclusive_and_exclusive() -> None:
    s = {"type": "number", "minimum": 1, "maximum": 3}
    assert ok(1, s) and ok(3, s) and not ok(0.99, s) and not ok(3.01, s)
    e = {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}
    assert ok(0.5, e) and not ok(0, e) and not ok(1, e)


def test_additional_properties_and_nested_objects() -> None:
    s = {
        "type": "object",
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "object", "properties": {"c": {"type": "string"}}, "required": ["c"]},
        },
        "required": ["a"],
        "additionalProperties": False,
    }
    assert ok({"a": 1}, s) and ok({"a": 1, "b": {"c": "x"}}, s)
    assert not ok({"a": 1, "z": 2}, s)
    errs = against_json_schema({"a": 1, "b": {}}, s)
    assert errs == ["$.b: missing required field 'c'"]
    assert ok({"a": 1, "z": 2}, {**s, "additionalProperties": True})
    typed_extra = {**s, "additionalProperties": {"type": "integer"}}
    assert ok({"a": 1, "z": 2}, typed_extra) and not ok({"a": 1, "z": "2"}, typed_extra)


def test_arrays() -> None:
    s = {"type": "array", "items": {"enum": ["x", "y"]}}
    assert ok([], s) and ok(["x", "y"], s)
    assert against_json_schema(["x", "z"], s) == ["$[1]: 'z' is not one of ['x', 'y']"]
    assert not ok({}, s)


def test_any_of_covers_optional_and_literal_unions() -> None:
    class M(BaseModel):
        x: int | None = None
        y: float | str = 0

    schema = M.model_json_schema()
    assert ok({}, schema) and ok({"x": None}, schema) and ok({"x": 3, "y": "s"}, schema)
    assert not ok({"x": 3.5}, schema) and not ok({"y": []}, schema)
    assert "none of the allowed alternatives" in against_json_schema({"x": "q"}, schema)[0]


def test_nested_models_resolve_local_refs() -> None:
    class Inner(BaseModel):
        n: int = Field(ge=1)

    class Outer(BaseModel):
        inner: Inner

    schema = Outer.model_json_schema()
    assert "$ref" in str(schema)
    assert ok({"inner": {"n": 1}}, schema)
    assert against_json_schema({"inner": {"n": 0}}, schema) == [
        "$.inner.n: 0 is less than the minimum 1"
    ]


def test_unknown_keywords_and_unresolvable_refs_are_not_errors() -> None:
    assert ok(5, {"type": "integer", "multipleOf": 7, "title": "T", "description": "d"})
    assert ok(5, {"$ref": "#/$defs/Missing"}) and ok(5, {"$ref": "http://elsewhere/schema"})


def test_first_problems_summarises() -> None:
    assert first_problems(["a", "b"]) == "a; b"
    assert first_problems(["a", "b", "c", "d", "e"]) == "a; b; c; and 2 more"


@pytest.mark.parametrize("depth", [50])
def test_self_referential_refs_terminate(depth: int) -> None:
    schema = {"$defs": {"N": {"$ref": "#/$defs/N"}}, "$ref": "#/$defs/N"}
    assert against_json_schema(1, schema) == []
