"""check_spec: the reference passes clean, each mutant is caught, shipped specs agree."""

from pathlib import Path

import pytest

from gao_idl.authoring import Run, check_spec, load_cards, load_spec
from tests.authoring.envs import ultimatum_spec

ROOT = Path(__file__).resolve().parents[2]


def _runs(mutant: str) -> list[Run]:
    params = {"pie": 10.0, "cost": 1.0, "mutant": mutant}
    return [
        Run(params=params, seed=0, script={"ann": [{"offer": 4.0}], "ben": [{"accept": True}]}),
        Run(params=params, seed=0, script={"ann": [{"offer": 1.0}], "ben": [{"accept": False}]}),
    ]


def _kinds(mutant: str) -> set[str]:
    return {m.kind for m in check_spec(ultimatum_spec(mutant), _runs(mutant))}


def test_reference_is_clean() -> None:
    assert check_spec(ultimatum_spec(), _runs("none")) == []


def test_a_hidden_value_in_a_report_is_caught() -> None:
    assert {"undeclared_told", "secret_leak"} <= _kinds("leak_report")


def test_a_secret_in_another_roles_observation_is_caught() -> None:
    assert {"undeclared_sees", "secret_leak"} <= _kinds("leak_observation")


def test_a_changed_timeout_policy_is_caught() -> None:
    assert _kinds("default_timeout") == {"timeout_policy"}


def test_a_removed_step_is_caught() -> None:
    runs = [Run(params={"mutant": "no_response"}, seed=0, script={"ann": [{"offer": 4.0}]})]
    kinds = {m.kind for m in check_spec(ultimatum_spec("no_response"), runs)}
    assert "step_missing" in kinds


def test_spec_errors_are_caught_in_both_directions() -> None:
    spec = ultimatum_spec()
    data = spec.model_dump()
    data["steps"][1]["told"]["responder"] = ["accepted", "own_payoff", "reason"]
    data["steps"][1]["sees"]["responder"] = ["pie"]
    data["repeats"] = {"what": "It repeats."}
    found = {(m.kind, m.where) for m in check_spec(type(spec).model_validate(data), _runs("none"))}
    assert ("unfulfilled_told", "response/responder") in found
    assert ("undeclared_sees", "response/responder") in found
    assert ("repeat", "structure") in found


def test_without_runs_nothing_is_claimed() -> None:
    found = check_spec(ultimatum_spec(), [])
    assert found and {m.kind for m in found} == {"not_exercised"}
    assert {m.where for m in found} >= {"offer", "response", "secret[0] cost"}


def test_a_run_that_stops_early_leaves_the_rest_not_exercised() -> None:
    runs = [Run(params={}, seed=0, script={"ann": [None], "ben": [None]})]
    found = {(m.kind, m.where) for m in check_spec(ultimatum_spec(), runs)}
    assert ("not_exercised", "response") in found


@pytest.mark.parametrize("name", ["auction", "pd", "vcm"])
def test_shipped_specs_agree_with_their_environments(name: str) -> None:
    spec = load_spec(ROOT / "examples" / "specs" / f"{name}.json")
    cards = load_cards(ROOT / "examples" / "cards" / f"{name}.json")
    runs = [Run(params=c.params, seed=c.seed, script=c.script) for c in cards]
    assert [m.message for m in check_spec(spec, runs)] == []
