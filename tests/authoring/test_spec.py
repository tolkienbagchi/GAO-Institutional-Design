"""The spec model: validation, round trip, and the three shipped specs."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from gao_idl.authoring import EnvSpec, load_spec, role_kind
from tests.authoring.envs import ultimatum_spec

SPECS = Path(__file__).resolve().parents[2] / "examples" / "specs"


def test_role_kind() -> None:
    assert role_kind("bidder:0") == "bidder"
    assert role_kind("row") == "row"
    assert role_kind("verifier:1:x") == "verifier"


@pytest.mark.parametrize("name", ["auction", "pd", "vcm"])
def test_shipped_specs_load_and_round_trip(name: str) -> None:
    spec = load_spec(SPECS / f"{name}.json")
    assert EnvSpec.model_validate_json(spec.model_dump_json()) == spec
    assert spec.assumptions and not any(a.confirmed for a in spec.assumptions)


def _with(**changes: object) -> dict[str, object]:
    return {**ultimatum_spec().model_dump(), **changes}


def test_validation_rejects_inconsistent_specs() -> None:
    base = ultimatum_spec().model_dump()
    steps = base["steps"]
    cases = [
        _with(steps=[steps[0], steps[0]]),  # duplicate step names
        _with(steps=[{**steps[0], "chooses": {}}]),  # a decision nobody makes
        _with(steps=[{**steps[0], "chooses": {"stranger": ["x"]}, "sees": {}}]),  # unknown role
        _with(steps=[{**steps[1], "no_action_means": ""}]),  # a drop that means nothing
        _with(steps=[{**steps[0], "kind": "automatic"}]),  # automatic with deciders
        _with(secrets=[{**base["secrets"][0], "param": "nope"}]),
        _with(assumptions=[{"question": 12, "text": "x"}]),
        _with(repeats={"what": "x", "rounds_param": "rounds"}),
        _with(surprise=1),
    ]
    for case in cases:
        with pytest.raises(ValidationError):
            EnvSpec.model_validate(case)
