# REPORT — M0 fix-up

## Gate status (read this first)

**Tasks 1 and 2 were not executed, and the fix-up gate was not met.** The brief says network access is
available; in this session it is not. `pypi.org` is denied by the egress policy (the proxy answers `403` to
`CONNECT`; a direct connection fails DNS), so I did not retry or work around it. Consequently:

| Gate item | Status |
|---|---|
| `pip install -e ".[dev]"` on 3.11 and 3.13 | **Not run** (no PyPI). 3.11 has no packages installed at all. |
| `ruff check . && ruff format --check .` | Pass (3.13 tooling): `All checks passed!`, `43 files already formatted` |
| `mypy src/gao_idl/core src/gao_idl/composition` | Pass on 3.13 only: `Success: no issues found in 15 source files`. **Not checked on 3.11.** |
| `pytest -q`, 0 skipped | **125 passed** with `tests/core/test_paths_property.py` excluded. With it included, collection fails here (`hypothesis` not installable), so "0 skipped, all green" is **unverified**. |
| `python -m build`, fresh-venv wheel install, `import gao_idl.testing` | **Not run.** |

What I could not confirm, and what to look at when you re-run it with PyPI:
- The hatchling build and wheel contents (`gao_idl.testing` should ship, `tests/` should not; by inspection of
  `packages = ["src/gao_idl"]` only).
- The hypothesis property tests have still never executed.
- mypy on 3.11 with numpy's stubs. If it fails, the brief's fallback (pin mypy and note it) applies.
- The new CI matrix (`["3.11", "3.13"]`) has not run.

Everything else below was implemented and tested on 3.13. Please run the real gate before merging.

## What changed

Commits (tasks 3-5 share files, so they are one commit; task 2 produced no change):

1. `Run the real gate on Python 3.11 and 3.13 (CI matrix; not executed here, see NOTES)` — `ci.yml` matrix;
   `tests/core/test_paths_property.py` now imports hypothesis directly (no `importorskip`) so a missing dev
   dependency fails loudly and "0 skipped" is meaningful.
2. `Tighten check_determinism; advance() loops on Continue; natural role order` — tasks 3, 4, 5.
3. `Document the API as built` — README example + `tests/test_readme_example.py`.
4. `Hygiene` — `.gitignore` (`.venv*/`; `.hypothesis/` and `dist/` were already there). There were no
   `.Rhistory` or `~$*` files in the repo.
5. This report and the NOTES entries.

Files touched: `src/gao_idl/core/ids.py` (`role_sort_key`), `composition/process.py` (`Continue`),
`composition/primitive.py`, `runtime/session.py`, `environments/auction/bidding.py`; tests
`tests/core/test_ids.py` (new), `tests/runtime/envs.py` (stub `ContinueTwice` process),
`tests/runtime/test_session_bidding.py`, `tests/test_readme_example.py` (new), `README.md`, `.github/workflows/ci.yml`.

- **Task 3.** `check_determinism` raises `ValueError("call advance() before check_determinism()")` if the
  inbox is non-empty; prefix tolerance removed; ledger comparison kept. Test updated, with an
  advance-then-check case.
- **Task 4.** `Continue` added to `StepResult`; `Session.advance()` loops while it is returned; the inbox is
  consumed by the first step. Test: a stub process returning `Continue` twice then `Exited` finishes in one
  `advance()`, and the later steps receive no inputs.
- **Task 5.** `role_sort_key` (natural order, total). Used at four sites in `primitive.py` (the brief said
  three) and in `bidding._outcome`. Test with 12 bidders asserts request order `bidder:0 … bidder:11` and the
  same order for `action_accepted` events, plus unit tests for the key.
- **Task 6.** README has a 17-line runnable example (start, advance, pending_for, submit, snapshot/restore,
  reports_for); the test executes it and checks its output.

## NOTES.md entries added

All dated 2026-10-21; full text in `NOTES.md`:
- Fix-up tasks 1-2: real gate and wheel build not executed (PyPI denied); what was changed unverified; how local
  verification was done.
- Task 3: `check_determinism` requires an empty inbox.
- Task 4: `Continue`; **no iteration cap**, so a process returning `Continue` forever would hang `advance()`.
- Task 5: natural order; **behaviour change for ≥ 11 roles** versus M0 traces; agent ordering left lexicographic.
- Task 6: README example. Task 7: hygiene, and that tasks 3-5 are one commit.

## Things I was unsure about

- Whether to keep the hypothesis test failing loudly where hypothesis is missing. I chose yes (the brief wants
  0 skipped), at the cost that I could not run that file here.
- The role/agent strategies in the property tests still allow `:` and digits, unlike the brief's "exclude
  `:`" suggestion for names. I argued `decision_id` and `report_id` are injective regardless, and layer/branch
  names already exclude all reserved characters. If hypothesis disagrees, that is a real finding.
- `Continue` has no loop guard (see above).
- The pinned API in the shared context lists `StepResult` without `Continue`; the amendments section you
  supplied already covers it, so I did not edit that file.
