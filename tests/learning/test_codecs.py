"""Codecs: observation keys, finite action sets, and the JSON round trip of keys."""

import json
from typing import Any

import pytest

from gao_idl.core.ids import AgentId, DecisionId, ReportId, RoleId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.environments.auction.bidding import Bid
from gao_idl.environments.pd.schemas import PDAction
from gao_idl.learning.codecs import (
    FieldObsCodec,
    SchemaActCodec,
    codec_spec,
    freeze,
    from_jsonable,
    to_jsonable,
)


def request(
    observation: dict[str, Any], schema: dict[str, Any] | None = None, layer: str = "pd_round"
) -> DecisionRequest:
    return DecisionRequest(
        decision_id=DecisionId("x/" + layer + "#row"),
        path=["x", layer],
        layer=layer,
        role=RoleId("row"),
        agent_id=AgentId("alice"),
        observation=observation,
        action_schema=PDAction.model_json_schema() if schema is None else schema,
    )


def report(n: int, **payload: Any) -> ParticipantReport:
    return ParticipantReport(
        report_id=ReportId(f"x@alice:{n}"),
        path=["x"],
        layer="pd_round",
        agent_id=AgentId("alice"),
        payload=payload,
    )


# ------------------------------------------------------------------ keys


def test_freeze_and_json_round_trip() -> None:
    value = {"b": [1, [2.5, None]], "a": {"z": True, "y": "s"}}
    key = freeze(value)
    assert hash(key) == hash(freeze(json.loads(json.dumps(value))))
    assert key == (("a", (("y", "s"), ("z", True))), ("b", (1, (2.5, None))))
    assert from_jsonable(json.loads(json.dumps(to_jsonable(key)))) == key


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), object(), {1: 2}])
def test_freeze_rejects_non_json(bad: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        freeze(bad)


# ------------------------------------------------------------------ FieldObsCodec


def test_field_obs_codec_selects_dotted_fields() -> None:
    codec = FieldObsCodec(["round_index", "own_history.0"])
    obs = {"round_index": 2, "own_history": [["C", "D"], ["D", "D"]], "ignored": 7}
    assert codec.encode(request(obs), []) == (2, (("C", "D")))
    assert codec.encode(request({**obs, "ignored": 8}), []) == (
        2,
        ("C", "D"),
    )  # unselected: no effect


def test_field_obs_codec_missing_field_is_a_value_error_cached_per_layer() -> None:
    codec = FieldObsCodec(["round_index", "nope"])
    with pytest.raises(ValueError, match="'nope'"):
        codec.encode(request({"round_index": 1}), [])
    ok = FieldObsCodec(["round_index"])
    ok.encode(request({"round_index": 1}), [])
    # The check is cached for (layer, role): a later request of the same layer is not re-checked ...
    ok.encode(request({"round_index": 1}), [])
    assert ok._checked == {("pd_round", "row")}
    # ... but a different layer is checked on its own first encode.
    with pytest.raises(ValueError):
        ok.encode(request({"other": 1}, layer="auction"), [])


@pytest.mark.parametrize("fields", [["a", "a"], [""], ["a..b"], [".a"]])
def test_field_obs_codec_construction_errors(fields: list[str]) -> None:
    with pytest.raises(ValueError):
        FieldObsCodec(fields)


def test_field_obs_codec_bad_history() -> None:
    for bad in (-1, True):
        with pytest.raises(ValueError):
            FieldObsCodec(["a"], history=bad)


def test_field_obs_codec_history_uses_last_reports_with_left_padding() -> None:
    codec = FieldObsCodec(["round_index"], history=2, report_fields=["other_move", "nope"])
    req = request({"round_index": 3})
    reports = [report(0, other_move="C"), report(1, other_move="D"), report(2, other_move="D")]
    assert codec.encode(req, reports) == ((3,), ("D", None), ("D", None))
    assert codec.encode(req, reports[:1]) == ((3,), None, ("C", None))
    assert codec.encode(req, []) == ((3,), None, None)


def test_field_obs_codec_history_defaults_to_the_same_fields() -> None:
    codec = FieldObsCodec(["round_index"], history=1)
    assert codec.encode(request({"round_index": 3}), [report(0, round_index=2)]) == ((3,), (2,))


def test_obs_key_survives_the_json_round_trip() -> None:
    codec = FieldObsCodec(["round_index", "own_history"], history=1)
    req = request({"round_index": 1, "own_history": [["C", "D"]]})
    key = codec.encode(req, [report(0, round_index=0, own_payoff=0.0)])
    assert from_jsonable(json.loads(json.dumps(to_jsonable(key)))) == key


def test_obs_codec_gets_a_copy_of_nothing_it_can_mutate() -> None:
    codec = FieldObsCodec(["own_history"])
    req = request({"own_history": [["C", "D"]]})
    key = codec.encode(req, [])
    req.observation["own_history"].append(["D", "D"])  # the key is immutable data
    assert key == ((("C", "D"),),)


# ------------------------------------------------------------------ SchemaActCodec


def test_enum_actions_from_the_pd_schema() -> None:
    codec = SchemaActCodec()
    actions = codec.actions(request({}))
    assert actions == [{"move": "C"}, {"move": "D"}]
    assert [codec.encode(a) for a in actions] == [(("move", "C"),), (("move", "D"),)]


def test_actions_are_fresh_copies() -> None:
    codec = SchemaActCodec()
    first = codec.actions(request({}))
    first[0]["move"] = "tampered"
    assert codec.actions(request({}))[0] == {"move": "C"}


def test_number_field_needs_a_grid() -> None:
    schema = Bid.model_json_schema()
    with pytest.raises(ValueError, match="grid"):
        SchemaActCodec().actions(request({}, schema))
    codec = SchemaActCodec(grid={"amount": list(range(11))})
    actions = codec.actions(request({}, schema))
    assert [a["amount"] for a in actions] == list(range(11))


def test_grid_values_must_satisfy_the_schema() -> None:
    schema = Bid.model_json_schema()  # minimum 0
    with pytest.raises(ValueError, match="violates"):
        SchemaActCodec(grid={"amount": [-1, 0]}).actions(request({}, schema))
    with pytest.raises(ValueError, match="finite number"):
        SchemaActCodec(grid={"amount": [True]}).actions(request({}, schema))


def test_bounded_integers_booleans_and_products() -> None:
    schema = {
        "type": "object",
        "properties": {
            "k": {"type": "integer", "minimum": 1, "maximum": 3},
            "flag": {"type": "boolean"},
            "side": {"enum": ["L", "R"]},
            "optional": {"enum": ["x", "y"]},
        },
        "required": ["k", "flag", "side"],
    }
    actions = SchemaActCodec().actions(request({}, schema))
    assert len(actions) == 3 * 2 * 2
    assert actions[0] == {"k": 1, "flag": False, "side": "L"}  # optional property left out
    assert actions[-1] == {"k": 3, "flag": True, "side": "R"}
    assert len({SchemaActCodec().encode(a) for a in actions}) == 12


def test_exclusive_integer_bounds() -> None:
    schema = {
        "type": "object",
        "properties": {"k": {"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 3}},
        "required": ["k"],
    }
    assert [a["k"] for a in SchemaActCodec().actions(request({}, schema))] == [1, 2]


@pytest.mark.parametrize(
    "prop",
    [
        {"type": "integer"},
        {"type": "integer", "minimum": 0},
        {"type": "string"},
        {"type": "array", "items": {"enum": [1]}},
        {"$ref": "#/$defs/X"},
        {"anyOf": [{"enum": [1]}, {"enum": [2]}]},
        {"enum": []},
    ],
)
def test_unsupported_properties_are_value_errors(prop: dict[str, Any]) -> None:
    schema = {"type": "object", "properties": {"p": prop}, "required": ["p"]}
    with pytest.raises(ValueError):
        SchemaActCodec().actions(request({}, schema))


def test_too_large_a_product_is_refused() -> None:
    schema = {
        "type": "object",
        "properties": {c: {"type": "integer", "minimum": 0, "maximum": 99} for c in "abc"},
        "required": list("abc"),
    }
    with pytest.raises(ValueError, match="MAX_ACTIONS"):
        SchemaActCodec().actions(request({}, schema))


def test_non_object_schema_is_refused() -> None:
    with pytest.raises(ValueError):
        SchemaActCodec().actions(request({}, {"type": "string"}))


def test_codec_spec_identifies_type_and_arguments() -> None:
    obs, act = FieldObsCodec(["a"], history=1), SchemaActCodec(grid={"amount": [0, 1]})
    assert codec_spec(obs) == {
        "type": "FieldObsCodec",
        "args": {"fields": ["a"], "history": 1, "report_fields": ["a"]},
    }
    assert codec_spec(act) == {"type": "SchemaActCodec", "args": {"grid": {"amount": [0, 1]}}}
