"""``to_anthropic_schema``: what is removed, what stays, purity and idempotence."""

import copy
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from gao_idl.environments.auction.bidding import Bid
from gao_idl.environments.pd.schemas import PDAction
from gao_idl.llm.schema_transform import to_anthropic_schema

FORBIDDEN = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "maxItems",
}


class Ballot(BaseModel):
    """A PB-style ballot: a list of project names drawn from a fixed set."""

    approved: list[Literal["park", "library", "pool"]] = Field(min_length=2, max_length=3)


class Nested(BaseModel):
    label: str = Field(min_length=1, max_length=8, pattern="^[a-z]+$")
    share: float = Field(gt=0, le=1, multiple_of=0.25, description="a share")
    inner: Bid
    maybe: Bid | None = None
    many: list[Bid] = Field(min_length=3)


def walk(node: Any) -> Any:
    """Every dict reachable in a schema (data under enum/const/default is skipped)."""
    if isinstance(node, dict):
        yield node
        for key, value in node.items():
            if key not in ("enum", "const", "default"):
                yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def check_clean(schema: dict[str, Any]) -> None:
    for node in walk(schema):
        assert not FORBIDDEN & node.keys(), node
        if "minItems" in node:
            assert node["minItems"] in (0, 1)
        if node.get("type") == "object" or "properties" in node:
            assert node.get("additionalProperties") is False, node


def test_bid_loses_its_bound_and_keeps_it_in_the_description() -> None:
    original = Bid.model_json_schema()
    out = to_anthropic_schema(original)
    check_clean(out)
    amount = out["properties"]["amount"]
    assert amount["type"] == "number"
    assert "(minimum: 0)" in amount["description"]
    assert out["required"] == ["amount"]
    assert out["additionalProperties"] is False


def test_pd_action_keeps_its_enum_and_gets_additional_properties_false() -> None:
    original = PDAction.model_json_schema()
    out = to_anthropic_schema(original)
    check_clean(out)
    assert out["properties"]["move"]["enum"] == ["C", "D"]
    assert out["required"] == ["move"]


def test_ballot_schema_list_of_literals() -> None:
    original = Ballot.model_json_schema()
    assert {"minItems", "maxItems"} <= original["properties"]["approved"].keys()
    out = to_anthropic_schema(original)
    check_clean(out)
    approved = out["properties"]["approved"]
    assert approved["minItems"] == 1 and "maxItems" not in approved
    assert "(minItems: 2)" in approved["description"] and "(maxItems: 3)" in approved["description"]
    assert approved["items"]["enum"] == ["park", "library", "pool"]


def test_defs_refs_anyof_and_every_nested_object() -> None:
    original = Nested.model_json_schema()
    assert "$defs" in original
    out = to_anthropic_schema(original)
    check_clean(out)
    assert out["$defs"]["Bid"]["additionalProperties"] is False
    assert out["properties"]["inner"] == {"$ref": "#/$defs/Bid"}
    assert [s.get("$ref", s.get("type")) for s in out["properties"]["maybe"]["anyOf"]] == [
        "#/$defs/Bid",
        "null",
    ]
    label = out["properties"]["label"]
    assert label["type"] == "string" and "pattern" not in label
    for text in ("(minLength: 1)", "(maxLength: 8)", "(pattern: ^[a-z]+$)"):
        assert text in label["description"]
    share = out["properties"]["share"]
    assert share["description"].startswith("a share (")
    assert "(exclusiveMinimum: 0)" in share["description"]
    assert "(maximum: 1)" in share["description"] and "(multipleOf: 0.25)" in share["description"]


def test_pure_and_idempotent() -> None:
    for model in (Bid, PDAction, Ballot, Nested):
        original = model.model_json_schema()
        frozen = copy.deepcopy(original)
        once = to_anthropic_schema(original)
        assert original == frozen  # input not mutated
        assert to_anthropic_schema(once) == once  # idempotent
        assert to_anthropic_schema(original) == once  # pure
        assert json.loads(json.dumps(once)) == once  # still plain JSON


def test_existing_additional_properties_and_data_values_are_kept() -> None:
    schema = {
        "type": "object",
        "properties": {
            "scores": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0}},
            "flag": {"type": "string", "default": "x", "const": "x", "title": "Flag"},
            "weird": {"enum": [{"minimum": 1, "pattern": "p"}]},
        },
    }
    out = to_anthropic_schema(schema)
    scores = out["properties"]["scores"]
    assert scores["additionalProperties"] == {"type": "number", "description": "(minimum: 0)"}
    assert out["properties"]["flag"] == schema["properties"]["flag"]
    assert out["properties"]["weird"] == schema["properties"]["weird"]  # data, not a schema
    assert out["additionalProperties"] is False
