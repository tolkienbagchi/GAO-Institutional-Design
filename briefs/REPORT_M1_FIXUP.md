PyPI was unreachable again (`pip download hypothesis`: "No matching distribution", tried once, not retried), so task 1 was skipped: `pip install -e ".[dev]"`, the 3 hypothesis tests and `python -m build` were NOT run. Everything below was verified with the standalone tools on Python 3.13.

# REPORT — M1 fix-up

## Gate status

| Gate command | Result |
|---|---|
| `ruff check .` | Pass: `All checks passed!` |
| `ruff format --check .` | Pass: `65 files already formatted` |
| `mypy src/gao_idl/core src/gao_idl/composition` | Pass (strict, pydantic plugin): `no issues found in 21 source files` |
| `pytest -q --ignore=tests/core/test_paths_property.py` | **220 passed** (gate asks for ≥ 212): 64 core, 97 composition, 34 runtime, 23 environments, 2 top-level |
| `pytest -q` (literal) | Fails at collection (no hypothesis), as before. With the 3 hypothesis tests the expected total is 223 (brief said 210 = 207 + 3 before the new tests) |
| `python -m build` | Not run (no PyPI) |

Run with `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`. Still unverified, inherited: hatchling wheel layout, hypothesis tests, Python 3.11 / CI.

Test count: 207 → 220 (+13): 5 for task 2, 1 for task 3, 1 for task 4, 4 for task 5, 2 for task 7; task 6 moved tests without adding or removing any (name set compared mechanically).

## Commits (one per task, messages are the task titles)

```
Redefine arrival invariance as projection invariance     task 2
Reserve price: textbook semantics                        task 3
Unsold items are reported                                task 4
Typed store exceptions                                   task 5
Split the test file                                      task 6
DictJoiner keeps the port                                task 7
```

Task 1 produced no commit (PyPI); its NOTES entry records that.

## What changed

**2. Projection invariance.** `assert_arrival_invariant` now runs each submission order to completion (per round: every agent answers all its pending decisions in that order, then `advance()`), so multi-round environments like `Loop(Par(Seq))` are covered. Against the first run it checks: information streams exactly; ledger snapshot; per branch root the events under it modulo `t`; Branch-free events modulo `t`. Applied to the nested PD-shaped fixture (`test_nested_arrival_projection_invariant`) and to a two-branch Par of auctions (`test_parallel_auctions_arrival_projection_invariant`, both pricing rules). Negative controls: an `OrderLeak` wrapper that traces arrival order fails both outside any branch and inside one; `test_par_global_trace_follows_submission_order_but_projections_do_not` shows exact trace equality does differ for a Par while the projections agree. The old arrival tests pass unchanged. The stale NOTES entry (helper "does not apply to Par") is rewritten.

**3. Reserve.** Second price with a reserve pays `max(reserve, highest other eligible bid)`; first price and the no-reserve case are unchanged. Docstring says the reserve is a seller's bid. One assertion flipped, `test_reserve_binds_second_price` added.

**4. Unsold.** `UnsoldReport(status="unsold", reserve, own_bid)` goes to every bidder on the `unsold` path; the `return None` special case is gone. Test checks exactly one settlement report per bidder and no `winner` anywhere in the bidders' information.

**5. Typed store exceptions.** `StoreScopeError(GaoError)`, `StoreReadDenied(StoreScopeError, KeyError)`, `StoreWriteDenied(StoreScopeError, PermissionError)` in `core/store.py`; `Primitive`, `Seq` and `Loop` catch `StoreScopeError` only. Scope violations still fail the session (reason now names the typed class); any other `KeyError`, including an in-scope key that was never set, propagates out of `advance()`.

**6. Split.** 25 tests moved to `tests/runtime/test_session.py`, 23 stay in `test_auction.py`.

**7. `DictJoiner`.** Record is `{branch: {"port": ..., "record": ...}}`. Noted in NOTES as a breaking change.

### Not-vacuous checks (mutations, all reverted)
- Reserve price line reverted: 2 tests fail. `return None` restored for unsold: 2 fail.
- Each of the three `except StoreScopeError` sites switched back to bare `KeyError, PermissionError`: exactly its own propagation test fails (Primitive's also fails the Par hazard test).
- Joiner record shape reverted: 6 tests fail.
- Task 2 has the negative controls above instead of a mutation of the helper.

## Judgement calls to confirm (details in NOTES.md)

1. **Snapshot check is not exact when a Par is present.** The brief asks for an identical `ledger.snapshot()`. Utility increments in the snapshot carry `t` from the global clock, which interleaves by submission order with a Par; I verified `t` is the only difference between the two orders of the nested fixture. So the snapshot is compared exactly when no trace event sits under a `Branch`, and with increment `t` dropped otherwise. Information items carry no `t`, so that check is exact in all cases.
2. **Branch root = path up to an event's last `Branch`.** For nested Pars an outer branch's events outside any inner branch form their own group. The brief's wording ("no further Branch inside it") only covers innermost roots; this is identical there and also checks the outer events.
3. **Failure-mode change for the Par shared-store hazard.** A reader named before its writer reads an in-scope, unset key; that used to be a failed session and is now a plain `KeyError` out of `advance()` (as the brief's rule on unset keys implies). `test_par_cross_branch_read_before_write_fails_in_sorted_order` keeps its name but asserts the exception; the name is now slightly misleading.
4. **Which tests count as Session-generic.** Rule and the borderline cases are in NOTES: `test_rejections` moved whole although it also exercises the bid schema; the two auction arrival tests and two noninterference tests stayed; the helper-focused ones moved. Easy to flip.
5. **`_run` in `gao_idl.testing` now drives to completion** (it also serves `assert_noninterference`). Stricter than before: a session that still waits on an agent not in `actions` raises instead of being compared half-way. No existing test was affected.
6. Failure reasons for store violations now read `StoreReadDenied: ...` / `StoreWriteDenied: ...` instead of `KeyError: ...` / `PermissionError: ...`. Tests that matched the old names were updated.

## Explicitly not changed (per brief)
Par backlog/settling semantics, composites emitting no events, Loop limit semantics, `Connector.apply` / `ReadOnlyLedger` naming, composite `name`, the extra wiring `ContractError`s, frames dump-but-not-load, shared store across Par branches. No M2 work was started.
