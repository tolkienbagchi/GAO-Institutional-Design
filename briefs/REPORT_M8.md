PyPI was unreachable, so these gate commands did not run as written: `pip install -e ".[dev,llm,docs]"` (tests ran with `PYTHONPATH=src`), the two hypothesis test files, `tests/test_packaging.py` and `python -m build` (`build` missing), `mkdocs build --strict` with mkdocstrings (the reduced build inside `tests/test_docs.py` passes; the `::: gao_idl.authoring.*` entries have never rendered), and `pytest-timeout` (the 30 s limit was not enforced; the whole fast suite takes 58 s).

# REPORT — M8: verified authoring (spec, rendering, check, cards)

## Gate status

| Gate command | Result |
|---|---|
| `ruff check .`, `ruff format --check .`, `mypy src/gao_idl` | **Pass** (201 files; mypy 88 source files) |
| `pytest -q -m "not slow"` | **Pass with three files ignored** (see first line): 849 passed, 4 skipped, 58 s. Baseline on `main`: 806. |
| `pytest -q -m slow` | **Pass with the same ignores**: 7 passed, 109 s. No new slow tests. |
| `mkdocs build --strict` | **Reduced config only**, via `tests/test_docs.py`. |
| `python -m build` | **Not run.** |
| `gao-idl spec check examples/specs/auction.json --cards examples/cards/auction.json` | **Exit 0**: 0 mismatches over 13 runs; 7 assumptions not yet confirmed. Same for `pd` (11 runs) and `vcm` (13 runs). |

## What was built

- `src/gao_idl/authoring/`: `spec.py`, `play.py`, `render.py`, `check.py`, `cards.py` (about 1,150 lines); `assert_cards` in `gao_idl.testing`; six subcommands in `gao_idl/cli.py`.
- `examples/specs/{auction,pd,vcm}.json`; `examples/cards/{auction,pd,vcm}.json` (13, 11 and 13 cards, **all `proposed`**); `scripts/make_example_cards.py`.
- `tests/authoring/` (38 tests, two test environments with four mutants, six golden files); 5 tests added to `tests/test_cli.py`.
- `docs/verifying.md`, `docs/api/authoring.md`, nav, a section in `docs/for-agents/llms.md`, both skills, README, CHANGELOG.
- `gao_idl.__all__` is unchanged (23 names). No new dependency. Nothing under `authoring` imports `gao_idl.llm`.

## Exit tests

| Task | Criterion | Met | Test |
|---|---|---|---|
| 1 | Three specs load and round-trip | Yes | `test_spec.py::test_shipped_specs_load_and_round_trip` |
| 2 | Scripts reproduce auction and PD outcomes; `None` triggers each timeout policy | Yes | `test_play.py` (5 tests) |
| 3 | Golden diagram and design for auction and PD; VCM buyers never see `theta` | Yes | `test_render.py` |
| 4 | Reference clean; four mutants each caught with the named kind; shipped specs clean | Yes | `test_check.py::test_a_*_is_caught`, `test_shipped_specs_agree_with_their_environments` |
| 5 | A confirmed card fails when the tie-break changes; a rejected card fails until it does | Yes | `test_cards.py` |
| 6 | Subcommands and exit statuses | Yes | `tests/test_cli.py` (5 new) |

Beyond the brief's mutants I weakened the shipped specs by hand and the secret check fired each time: dropping the owner exception in the auction, claiming the carbon-market secret under audits, and hiding true quality from verifiers.

## Spec findings

- **No mismatch between the three shipped environments and their specs remains**, but the specs were written from the code by the same author, so this shows the tooling is consistent, not that the environments are what Tolkien meant. That is what the cards and the 17 unconfirmed assumptions are for.
- **One of my own assumptions was wrong and a card exposed it.** I wrote that carbon-market buyers are credited on reported quality by default. The `understated` card showed `own_value = 65`, which is the true-quality figure: an audited project is valued at its true quality even under `settle_on = "reported"`. The assumption text is corrected. This is the workflow doing its job, on its author.
- **Things a designer should look at first** (all are unconfirmed assumptions or proposed cards):
  - Auction: a missing bid stops the whole run (`no-bid`); ties go to the alphabetically first name (`tie-top`, `tie-lower-value-wins`); with no reserve and all-zero bids the item is still sold (`no-reserve-all-zero`).
  - PD: one silent player stops the run for every pair, including pairs that already moved (`no-move-stops`).
  - Carbon market: a verifier that *understates* quality is penalized exactly like one that overstates (`understated`); a project that does not trade cannot be audited (`no-trade-no-audit`); sellers are told revenue, never profit.
- **Assumptions written into the specs:** 7 (auction), 4 (PD), 6 (carbon market); every one has `confirmed: false`.

## Not done

- The full gate, as listed in the first line.
- No card is confirmed and no assumption is marked confirmed: that is Tolkien's step (V5).
- `docs/designing.md` was not changed; only the skills and the new page describe the spec step.

## Where the brief underdetermined the design, and what I chose

1. **What a script is keyed by.** Agent, not role id (see Deviations).
2. **Which run a secret is tested on.** Every run that satisfies `only_when`; one `not_exercised` if none does.
3. **Whose kind a report belongs to** when the recipient holds no role in the step (settlement, audit): the kinds it holds elsewhere, else `observer`.
4. **When `unfulfilled_*` fires.** Whenever no run showed the field, even if the field is conditional (`all_bids` appears only with `reveal_bids`). A card must exercise it.
5. **Step order** is order of first request or completion across runs, compared only over steps that were reached.
6. **What `render_design` shows for timeouts**: the built policy from the tree, with the spec's `no_action_means` beside it.
7. **A scenario that cannot be played** renders as a card saying so; it does not raise.

## Deviations from pinned names or signatures

- `ActionScript` is keyed by **agent id**. Role ids repeat across `Par` branches (every PD pair has a `row`).
- `SecretSpec` has two extra fields, `except_agents` and `only_when`; neither shipped secret can be stated without them.
- `StepSpec.on_no_action` is required for decision steps; all spec models forbid unknown keys.
- Added `script_of`, `Run`, `KnowledgeTable.row`, `EnvSpec.step`, `cards show --spec`, `scripts/make_example_cards.py`.
- `to_mermaid` cannot mark automatic steps: `describe()` does not know (see NOTES). The design page says which steps are automatic from the spec, and the check verifies it from runs.
- Commits are split by task but not each independently green (`authoring/__init__.py` imports all five modules).

## Unsure

- **`knowledge_table` compares field names, not contents.** A declared field that carries too much passes unless a secret or a card catches it. I think this is the weakest part of the check.
- **The carbon-market secret rests on a single unaudited card.** One run is thin evidence for noninterference; M9's fuzzing is what would make it credible.
- **Card length.** A two-round carbon-market card renders to about 60 lines, most of it nested order books. I doubt a non-programmer reads 13 of those. The auction and PD cards are fine. This needs the user trial, and probably a per-environment renderer for nested fields.
- **Field names that are data** (`s0`..`s3` as action fields) make the carbon-market spec valid for one population only.
- **Mermaid output is untested in a renderer.** The golden files pin the text; I did not see it drawn.
