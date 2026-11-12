PyPI was unreachable in the build sandbox, so the same gate commands as in M8 did not run locally (real install, hypothesis tests, `tests/test_packaging.py`, `python -m build`, mkdocstrings, pytest-timeout). CI on the pushed branch runs them; see the last section.

# REPORT — M9: fuzzing, invariants, divergence search, two skills

## Gate status (local)

| Gate command | Result |
|---|---|
| `ruff check .`, `ruff format --check .`, `mypy src/gao_idl` | **Pass** (212 files; mypy 92 source files) |
| `pytest -q -m "not slow"` | **Pass with three files ignored**: 879 passed, 4 skipped, 12 deselected, 1 warning in 77.86s (0:01:17) |
| `pytest -q -m slow` | **Pass with the same ignores**: 12 passed (5 new: 200 fuzzed scripts on each shipped environment), 146 s |
| New files compile under Python 3.11 | **Pass** (`py_compile`; 3.11 has no pydantic here, so tests ran on 3.13 only) |
| `gao-idl spec check ... --fuzz 200 --seed 1` | **Exit 0** for auction (213 runs), PD (211) and carbon market (213, 66 s) |

## What was built

- `authoring/fuzz.py`: `edge_scripts`, `sample_action` (nested schemas, `$ref`, optional objects).
- `authoring/invariants.py`, `authoring/predicates.py`: `check_invariants` and three generic predicates; `InvariantSpec.args`.
- `authoring/diverge.py`: `diverge`, `Divergence`, `ChoiceCard`, `to_choice_cards`, `render_choice`, `load_choices`, `save_choices`.
- CLI: `gao-idl spec check --fuzz N --seed S`, `gao-idl diverge SPEC REF... --out FILE`.
- Skills `second-implementation` and `write-spec-tests`; `examples/candidates/auction_second.py`; docs, llms.md, CHANGELOG.
- Tests: `test_fuzz.py`, `test_diverge.py`, `test_invariants.py`, 2 CLI tests, 1 skills test; six auction variants and a grants round in `tests/authoring/envs.py`.

## Exit tests

| Task | Criterion | Met | Test |
|---|---|---|---|
| 1 | 200 scripts on each of five shipped environments, no engine error other than a declared stop | Yes | `test_fuzz.py::test_two_hundred_scripts_run_on_every_shipped_environment` (slow); 30 each in the fast suite |
| 2 | Seeded tie and reserve ambiguities found; identical candidates give none | Yes, in an amended form (see Deviations) | `test_diverge.py` |
| 3 | M8 mutants caught by fuzzing alone; overspending caught by the third predicate | Yes | `test_invariants.py` |
| 4 | Two skills, five expected, blind example imports no environment | Yes | `tests/test_skills.py` |

## Findings

- **The three shipped specs survive 200 edge-biased runs each.** No undeclared field, no secret leak. The carbon-market secret was resting on one run after M8; it now rests on 200.
- **A second auction built from the spec agrees with the shipped one on every ending and payoff** over 200 scripts, and differs only in information: the text of `status`, `tie_break` and `bid_disclosure`, and whether `reserve` and `status` appear in the settlement report of a sold item. The spec says which fields exist, not what they contain or when optional ones appear. For participants that read reports (LLM agents in particular) those differences are not cosmetic.
- **Caveat on that result:** I had read the shipped auction, so the second one is not independent. The agreement on payoffs shows the mechanics work, not that the spec is precise. A real test needs a fresh session that follows the skill's one rule.
- **None of the five stated invariants of the shipped specs has a predicate.** They print as notes. The generic predicates do not fit them (they are about losers' payments, audited projects, sellers' stock).

## Not done

- No predicate was written for the shipped specs' invariants.
- No choice card is answered and no card or assumption is confirmed.
- The carbon market's float sums still differ between Python 3.11 and 3.12+ (found in M8; untouched).
- No independent second implementation by a separate session, and no user trial.

## Where the brief underdetermined the design, and what I chose

1. **How silence is distributed** over scripts (per script, one in five).
2. **What makes two divergences "the same"**: kind and grouping of candidates.
3. **What counts as shown-the-same**: content and order, ignoring paths, ids and layer names.
4. **What a choice card looks like**: shared facts once, then per-option differences, labels A/B/C, implementations never named.
5. **Whether an unchecked invariant fails the check**: no, it is a note.
6. **Exit status of `diverge`**: 1 if any disagreement.

## Deviations from pinned names or signatures

- `edge_scripts`: silence per script, not 0.1 per decision.
- `diverge`: deduplicated by `(kind, groups)`; `Divergence` carries `groups` and `problems`; the exit test accepts the three-way grouping in addition to the two seeded ones.
- `check_invariants` has a `secrets` flag; `InvariantSpec` has `args`.
- `interface` divergence is detected by replay failure, not by comparing schemas up front.

## Unsure

- **Information divergences can swamp a result.** Any encoding difference makes every script an `information` divergence, deduplicated to one card. It no longer hides payoff differences (those are a separate kind), but one card cannot show *all* the encoding differences, only those in its script.
- **Fuzzing explores actions, not parameters.** All 200 runs use the spec's parameters. A leak that appears only with `reveal_bids` on, or with five bidders, is not looked for.
- **Feasibility by rejection.** Where few random actions are feasible the sampler falls back to plain ones, and coverage of the interesting region may be thin. I did not measure acceptance rates per environment.
- **Shared misreadings are invisible** to divergence search by construction.
- **Choice cards for the carbon market** will have the same length problem as its scenario cards.

## CI

Run on `dd01f9a` (branch `m9-verified-authoring`): `test (3.11)`, `test (3.13)`, `docs` (with mkdocstrings) and `package` all pass. The `slow` job runs only on the nightly schedule, so the five 200-script tests have run locally on Python 3.13 only.
