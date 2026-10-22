PyPI was unreachable again (DNS failure on `pip download hypothesis`, tried once and not retried), so `pip install -e ".[dev]"`, the hypothesis tests and `python -m build` were NOT run. Everything below was verified with the standalone tools on Python 3.13.

# REPORT — M1: composition (`Seq`, `Par`, `Loop`), contracts, store scopes, settlement

## Gate status (read this first)

**The gate as literally written was NOT met**, for the same reason as M0: the sandbox cannot install anything.

| Gate command | Result |
|---|---|
| `ruff check .` | Pass (ruff 0.16.9): `All checks passed!` |
| `ruff format --check .` | Pass: `63 files already formatted` |
| `mypy src/gao_idl/core src/gao_idl/composition` | Pass (mypy 2.3.1, strict, pydantic plugin): `Success: no issues found in 21 source files`. Python 3.13 only. |
| `pytest -q` (literal) | **Fails at collection**: `tests/core/test_paths_property.py` does `from hypothesis import given` and hypothesis is not installed. |
| `pytest -q --ignore=tests/core/test_paths_property.py` | **207 passed** (gate asks for ≥ 60). 64 core, 87 composition, 9 runtime, 45 environments, 2 top-level. |
| `describe(build_auction({'a':1,'b':2}))` one-liner | Runs and prints the tree (with `PYTHONPATH` set to `src` and the system `dist-packages`, since there is no editable install). |

How the tools were run: ruff/mypy/pytest are uv-tool venvs that can't see the system `dist-packages` where pydantic/numpy live, so they run with `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`. (M0's report only said "plus the system site-packages"; this is the exact incantation.) Baseline before any M1 change was 125 passed.

Still unverified, inherited from M0 and not improvable here: the hatchling wheel layout, the hypothesis property tests, mypy and the CI matrix on Python 3.11.

## What was built

Commits (one per brief task, plus one fix and the report; messages are the task titles):

```
Inherited from the M0 fix-up           Session.advance() guard (10 000 Continues)
composition/connector.py               task 1
composition/seq.py                     task 2 (+ composition/routing.py)
composition/par.py                     task 3
composition/loop.py                    task 4
Contract checker: composition/contracts.py   task 5
Store scopes end-to-end                task 6
Settlement layer and full auction      task 7
Testing fixtures additions             task 8
Par: apply queued inputs only after settling (fix to task 3)   see "Design calls" 1
Tests                                  task 9
```

Source:

```
src/gao_idl/composition/  connector.py seq.py par.py loop.py contracts.py routing.py
src/gao_idl/environments/auction/  settlement.py  __init__.py (build_auction)
src/gao_idl/core/events.py            + strip_paths
src/gao_idl/composition/primitive.py  store-scope violation -> FailedResult (9 lines)
src/gao_idl/runtime/session.py        + MAX_CONSECUTIVE_CONTINUES guard (6 lines)
src/gao_idl/testing/__init__.py       + assert_substitution_equivalent, assert_branch_rng_independence
```

Tests (all new unless noted):

```
tests/composition/   test_connector test_seq test_par test_loop test_contracts test_store
                     test_substitution test_nested  + layers.py envs.py (fixtures)
tests/environments/  test_auction.py   (git mv of tests/runtime/test_session_bidding.py, + 14 new tests)
tests/runtime/       test_session_guard.py (+ ContinueN stub in envs.py)
tests/core/          test_events.py (+ strip_paths test)
```

Every test named in the brief exists under the brief's name. `test_par_branch_rng_independence` lives in `test_substitution.py` (next to the helper it exercises).

### Not-vacuous checks (mutations, all reverted)
- Seq never routes: 3 tests fail. Loop `>=` -> `>` on the limit: 2 fail. Settlement second price = first price: 5 fail. Settlement always reveals bids: 2 fail. `Primitive` no longer converts store violations: 2 fail. `Seq` ticks the clock once on exit: associativity test fails. Par applies all inputs in one step, or without the `settling` guard: the replay tests fail.
- Randomized check, not committed: 900 random schedules (3 environments, 300 seeds each) replay to identical traces/ledgers; a 25-seed version is a committed test.
- A negative control exists for each new helper (it must raise on a leaky/different environment).

## Design calls to confirm (details in NOTES.md)

1. **`Par.step` does not apply all inputs at once (deviation from the brief, forced by invariant 3).** Taken literally ("route each input, step every branch once"), the trace depends on how the log was batched into `advance()` calls, so `check_determinism` / `restore` disagree with the original session when, say, bob's branch answers in one `advance()` and alice's in the next. `Par` now queues inputs (`ParFrame.backlog`) and applies one only after the previous one has run to quiescence (`ParFrame.settling`). **My first version of this was wrong** (it applied the next input in the middle of the previous input's `Continue` chain); the randomized-schedule test caught it and the second commit fixes it. Cost: for `Par`, traces legitimately depend on log order, so `assert_arrival_invariant` (exact trace equality over submission permutations) does not apply to Par environments. It is only used on Seq/Primitive ones.
2. **Reserve semantics.** Implemented literally: below-reserve bids are ignored, then normal rules apply, so under second price a lone eligible bidder pays 0. The textbook rule is `max(reserve, highest losing bid)`. One-line change in `settlement.py`, one assertion to flip. Please say which you want.
3. **Unsold items produce no participant report** (`SettlementReport` needs a winner; the brief defines no other). Bidders see only their `BidReceipt`.
4. **Loop limit.** `max_iter=k` runs iterations 0..k-1; truncation fires when feeding back would start iteration k, and the feedback connector is not run then. `last_exit` is the body's exit record only (its port is not included).
5. **Store violations fail the session** instead of escaping `advance()`. The catch is by type (`KeyError`/`PermissionError` around `layer.outcome`, and around connectors), so an unrelated `KeyError` bug in a layer is reported the same way, with its message.
6. **Names.** Composites take a keyword `name` (default `"seq"`/`"par"`/`"loop"`); `build_auction` names its Seq `"auction"` (ids read `auction/bidding#bidder:0`). New `ContractError`s beyond the brief: a route for a port `first` never exits through, a continuation named like `first`, names containing `/ # @ [ ]`.

## NOTES.md entries added

All dated 2026-10-21; full text in `NOTES.md`.
1. **M1 task 0** — PyPI still unreachable, what stays unverified, the exact local gate incantation, the `Continue` guard (10 000th raises; 9 999 is fine).
2. **Tasks 1-2** — `Connector.apply` and `ReadOnlyLedger`; composites are `eq=False` frozen dataclasses with `name`; composites emit no events (so Seq is associative modulo paths) and path-keyed RNG limits substitution equivalence to deterministic layers; extra `SeqFrame` fields and why frames dump but cannot be validated back; `routing.owns` boundary check; extra construction-time checks.
3. **Task 3** — the Par deviation (backlog), consequences for arrival invariance, `Continue` rules, `validate` asks each live branch, `DisjointSplitter(fn, input_schema)`, sorted (lexicographic) branch order, shared store.
4. **Task 3 follow-up** — correction: one-input-per-call was insufficient; the `settling` fix, evidence, and the idempotence assumption it relies on.
5. **Task 4** — Loop path layout, limit semantics, wiring errors, extra frame fields.
6. **Task 5** — `validate_wiring` / `compute_exits` + recursive `check`, lazy import, `describe` shape; static store ordering not implemented (as briefed).
7. **Task 6** — store violations -> `FailedResult` (layers, Seq connectors, Loop feedback connectors), persistence across iterations, Par shared-store hazard and its pinned test.
8. **Task 7** — `build_auction` layout, reserve semantics, unsold port/report, omitted `all_bids`, error cases, tie rule, the test-file move.
9. **Task 8** — `strip_paths`, ad-hoc process registry, what the substitution/rng helpers compare (and why `t` is dropped for one and kept for the other), `actions` keyword added to the rng helper, `_drive` loop.

## Things I was unsure about

- Everything under "Design calls to confirm" above, mainly 1 (Par) and 2 (reserve).
- Whether `Seq`/`Loop` should also convert `ContractError` raised by a connector's output validation into `Failed`. I left it as an exception (a wiring bug, not a play outcome).
- `Par` correctness relies on quiescent stepping being idempotent (stepping a waiting subtree with no inputs emits nothing). True for everything built here; any custom `Process` must satisfy it.
- `DictJoiner`'s record `{branch: exit.record}` drops the port each branch exited through, as briefed; if branches can exit on different ports that information is lost.
- Frames are JSON-dumpable but not JSON-loadable (nested children are polymorphic). State is always rebuilt by replay, so nothing needs loading, but the pinned text says "round-trippable".
- Moving `test_session_bidding.py` wholesale into `test_auction.py` puts generic Session tests in the auction file; they would be better split again later.
- The ad-hoc registry in `gao_idl.testing` is module-level mutable state (test helpers only; keys appear only in throwaway sessions' `Started` events).
