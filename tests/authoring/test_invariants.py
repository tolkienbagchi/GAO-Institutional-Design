"""Invariants and secrets over fuzzed runs: the M8 mutants are caught without any card."""

import pytest

from gao_idl.authoring import Run, check_invariants, check_spec, edge_scripts, play
from gao_idl.authoring.predicates import (
    never_below_zero,
    report_field_at_most_param,
    transfers_sum_to_zero,
)
from tests.authoring.envs import GRANTS, ULTIMATUM, grants_spec, ultimatum_spec


def _fuzz(spec_params: dict[str, object], env: str, n: int = 200) -> list[Run]:
    return [Run(params=spec_params, seed=5, script=s) for s in edge_scripts(env, spec_params, 5, n)]


def _kinds(mutant: str) -> set[str]:
    spec = ultimatum_spec(mutant)
    runs = _fuzz(spec.params, ULTIMATUM)
    return {m.kind for m in check_spec(spec, runs) + check_invariants(spec, runs)}


def test_reference_is_clean_under_fuzzing() -> None:
    assert _kinds("none") == set()


@pytest.mark.parametrize(
    ("mutant", "expected"),
    [
        ("leak_report", {"undeclared_told", "secret_leak"}),
        ("leak_observation", {"undeclared_sees", "secret_leak"}),
        ("default_timeout", {"timeout_policy"}),
        ("no_response", {"step_missing"}),
    ],
)
def test_m8_mutants_are_caught_by_fuzzing_alone(mutant: str, expected: set[str]) -> None:
    assert expected <= _kinds(mutant)


def test_overspending_is_caught_by_the_budget_predicate() -> None:
    clean = grants_spec()
    found = check_invariants(clean, _fuzz(clean.params, GRANTS))
    assert [(m.kind, m.where) for m in found] == [("invariant_unchecked", "fair")]
    broken = grants_spec(overspend=True)
    found = check_invariants(broken, _fuzz(broken.params, GRANTS))
    violated = [m for m in found if m.kind == "invariant_violated"]
    assert len(violated) == 1 and violated[0].where.startswith("within_budget (run ")
    assert "above budget = 10" in violated[0].message
    again = Run.model_validate(violated[0].found)  # the offending run is reported and replays
    session = play(GRANTS, again.params, again.seed, again.script)
    with pytest.raises(AssertionError):
        report_field_at_most_param(session, field="spent", param="budget")


def test_secrets_can_be_skipped_and_no_runs_means_not_exercised() -> None:
    spec = ultimatum_spec("leak_report")
    runs = _fuzz(spec.params, ULTIMATUM, 20)
    assert "secret_leak" in {m.kind for m in check_invariants(spec, runs)}
    assert check_invariants(spec, runs, secrets=False) == []
    assert [m.kind for m in check_invariants(spec, [])] == ["not_exercised"]


def test_generic_predicates() -> None:
    deal = play(ULTIMATUM, {}, 0, {"ann": [{"offer": 4.0}], "ben": [{"accept": True}]})
    never_below_zero(deal, roles=["proposer", "responder"])
    with pytest.raises(AssertionError, match="sum to 9"):
        transfers_sum_to_zero(deal, roles=["proposer", "responder"])  # the cost of 1 is burned
    costly = play(ULTIMATUM, {"cost": 8.0}, 0, {"ann": [{"offer": 4.0}], "ben": [{"accept": True}]})
    with pytest.raises(AssertionError, match="ann ends with a negative payoff"):
        never_below_zero(costly, roles=["proposer"])
    no_deal = play(ULTIMATUM, {}, 0, {"ann": [{"offer": 4.0}], "ben": [{"accept": False}]})
    transfers_sum_to_zero(no_deal, roles=["proposer", "responder"])
