# REPORT — M0: skeleton, core types, resumable single-layer session

## Gate status (read this first)

**The gate as literally written was NOT met**, because the first command could not be run:

| Gate command | Result |
|---|---|
| `pip install -e .[dev]` | **Not run successfully.** The sandbox's egress policy refuses `pypi.org`, so `hatchling` (build backend) and `hypothesis` could not be installed. |
| `ruff check . && ruff format --check .` | Pass (ruff 0.16.9): `All checks passed!`, `40 files already formatted` |
| `mypy src/gao_idl/core src/gao_idl/composition` | Pass (mypy 2.3.1, `strict = true`, pydantic plugin loaded): `Success: no issues found in 15 source files` |
| `pytest -q` | **113 passed, 1 skipped**, exit 0 (gate asks for ≥ 30) |

How the last three were run: ruff, mypy and pytest were available as standalone tools; pydantic 2.13 and numpy 2.5
were installed system-wide. I ran them with `PYTHONPATH=src:.` plus the system site-packages instead of an
editable install. So:

- The `hatchling` build configuration in `pyproject.toml` is **unverified** (no wheel/editable build was done).
- The 1 skipped test is the hypothesis module `tests/core/test_paths_property.py` (3 property tests), which
  uses `pytest.importorskip("hypothesis")`. It has **never been executed**. Equivalent numpy-seeded randomized
  tests run in its place.
- Everything ran on Python 3.13; the package targets ≥ 3.11 and CI uses 3.11, which I did not exercise.

Please re-run the real gate once PyPI is reachable (`pip install -e .[dev]`, then the other three commands).

## What was built

One commit per brief task (11 commits; task 10's commit was amended once to fix a docstring line length).

```
pyproject.toml  README.md  NOTES.md  .gitignore  .github/workflows/ci.yml
src/gao_idl/__init__.py                     __version__ = "0.0.1"
src/gao_idl/core/    ids.py paths.py errors.py rng.py schemas.py events.py timeout.py trace.py
                     ledger.py store.py layer.py
src/gao_idl/composition/  process.py primitive.py
src/gao_idl/runtime/      registry.py session.py
src/gao_idl/environments/auction/  __init__.py (build_bidding_only)  bidding.py
src/gao_idl/testing/__init__.py             assert_replay_equivalent / arrival_invariant /
                                            noninterference / incremental_equals_fold
tests/test_version.py
tests/core/       test_paths.py test_paths_property.py test_rng.py test_events.py
                  test_ledger_store.py test_layer.py
tests/composition/  test_primitive.py
tests/runtime/    envs.py test_session_bidding.py test_registry.py
briefs/REPORT_M0.md
```

All eleven test names required by task 11 exist in `tests/runtime/test_session_bidding.py`.

Test output summary (`pytest -q`):

```
113 passed, 1 skipped in 0.75s
SKIPPED [1] tests/core/test_paths_property.py:5: could not import 'hypothesis'
```

Not-vacuous check: temporarily emitting `ActionAccepted` at submit time (the brief's literal wording) makes
7 tests fail, including `test_barrier_and_no_disclosure` and the arrival-invariance tests. The original was restored.

Hard rules checked: no `random`/`time`/`uuid` imports and no `id()` in `src/`; frames hold data only (no layers
or callables); traces are compared via `events.dump(...)`, no `__eq__` tricks; no composites, settlement,
agents, runner, LLM or RL code.

## NOTES.md entries added

All entries are dated 2026-10-21; full text is in `NOTES.md`. Summary by task:

1. **Task 1 — version smoke test.** Added `tests/test_version.py` because pytest exits 5 on zero collected tests.
2. **Task 1 — environment.** `pip install -e .[dev]` impossible here (PyPI blocked); verification via `PYTHONPATH`.
3. **Task 4 — trace sink and clock in core.** `TraceSink`/`LogicalClock` live in `core/trace.py` and are
   re-exported from `composition.process`; `tick()` returns the new time (first event `t=1`); event `kind`
   fields have defaults; `LayerEntered` has only `t` and `path`; removed the pinned mypy `python_version`.
4. **Task 5 — store.** `Store(clock, initial=None)` needs a clock; `get` deep-copies; `Ledger.utility` uses `fsum`.
5. **Task 6 — layer.** Protocol members are read-only properties; extra `SimpleLayer` defaults; `Contract.roles`
   may be empty for dynamic roles.
6. **Task 7 — Primitive emits resolution events at settle (deviation).** See "Design call to confirm" below.
   Also: extra `PrimitiveFrame` fields, `ctx.path` includes the root, lexicographic role order.
7. **Task 8 — session details.** Rejections don't tick the clock and are excluded from `check_determinism`;
   `submit` checks the pending set first; non-JSON-able actions rejected as `SchemaError`; `replay` rules,
   `Aborted` unsupported; `Session.failed` added; `advance()` is a single step.
8. **Task 9 — bidding.** Observation strings are my choice (v1 plan §14 unavailable); `Bid.amount` is strict;
   `ContractError` for a bidder missing from `values`.
9. **Task 10 — testing helpers.** One-pending-decision-per-agent assumption; >5 agents use sorted/reversed/rotations.
10. **Task 11 — tests.** Test-only env factories and `pythonpath = ["."]`; hypothesis tests skip if absent;
    reserved-character assumption for id injectivity; mutation sanity check.

## Design call to confirm: where `ActionAccepted` is emitted

The brief has `collecting` emit `ActionAccepted` / `ActionDefaulted` / `RoleDropped` as inputs arrive. I emit
them when the layer settles, in sorted role order, just before `LayerCompleted`. Reason: the input log does not
record where `advance()` was called, so an arrival-time trace depends on step boundaries that `replay` cannot
reproduce, and two submission orders would yield different traces (which `assert_arrival_invariant` forbids).
Settle-time emission makes the trace a function of the set of resolutions, and also means the trace discloses
nothing about an action before the barrier. The kind sequence in `test_completion_reports_and_exit` is unchanged.
The cost is that, before settlement, an observer of the trace cannot see that a role has submitted. If later
milestones need arrival-time acceptance events, the alternative is to record `advance()` boundaries in the log.

## Things I was unsure about

- **Settle-time emission** (above) is the main departure from the brief.
- **Rejections vs. determinism.** The brief says rejections emit `ActionRejected` but never enter the input log,
  and also that `check_determinism` compares full traces. These conflict, so rejections are excluded from the
  comparison and do not advance the logical clock.
- **Bidding observation fields** (`tie_break`, `bid_disclosure`) are placeholders I invented; I did not have §14.
- **`pyproject.toml` under hatchling** and **CI on Python 3.11** are untested (see Gate status).
- **Role ordering** is lexicographic, so with ≥ 11 bidders `bidder:10` precedes `bidder:2`. Deterministic, but
  not numeric; matters only for display order and the order of events.
- **`Layer`/`Process` as read-only properties** rather than the pinned plain attributes; names and types match.
