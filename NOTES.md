# NOTES

Append-only log of deviations, ambiguities, deferrals and dependency changes.

## 2026-10-21 — M0 task 1: version smoke test
- The brief's gate says `pytest -q` "runs zero tests successfully", but pytest exits with code 5 when it
  collects nothing, which fails CI. Added `tests/test_version.py` (one trivial test) so the command exits 0.

## 2026-10-21 — M0 task 1: environment could not run `pip install -e .[dev]`
- The build sandbox's egress policy refuses pypi.org, so `hatchling` and `hypothesis` could not be installed
  and the literal gate command `pip install -e .[dev]` was NOT run. pydantic 2.13 / numpy 2.5 were already
  present system-wide; pytest, mypy and ruff were available as standalone tools.
- Verification was done by running those tools with `PYTHONPATH=src`. The `hatchling` build config and the
  hypothesis-based tests are therefore unverified here; re-run the real gate where PyPI is reachable.

## 2026-10-21 — M0 task 4: trace sink and clock live in core
- `TraceSink` and `LogicalClock` are defined in `core/trace.py` (not `composition/process.py`) because
  `core/store.py` must emit `StoreAccess` events and core must not import composition. `composition.process`
  re-exports both, so the pinned import path still works.
- `tick()` increments then returns, so the first event has `t=1`; `now` is the last issued `t` (0 initially).
- Event `kind` fields have defaults (`kind: Literal["started"] = "started"`) so callers don't repeat them.
- `LayerEntered` carries only `t` and `path`, as pinned (the layer name is the last path element).
- mypy config: removed a pinned `python_version = "3.11"`; numpy's stubs use 3.12 syntax and break mypy
  when it is forced to parse as 3.11. mypy now targets the running interpreter.

## 2026-10-21 — M0 task 5: Store needs a clock; get() also deep-copies
- `Store(clock, initial=None)` takes the session's `LogicalClock` so `StoreView` can stamp `StoreAccess`
  events (the pinned `view(reads, writes, trace, path)` signature has no other source for `t`).
- `StoreView.get` returns a deep copy (brief only requires copy on `set`) so callers cannot mutate stored state.
- `get` on a key that is in scope but never set raises `KeyError` and emits no event.
- `Ledger.utility` uses `math.fsum` so the total does not depend on summation order.

## 2026-10-21 — M0 task 6: Layer protocol uses read-only properties; SimpleLayer defaults
- `Layer` (and later `Process`) declare `name`, `entry`, `exits`, `timeout` as read-only `@property`
  members instead of plain attributes. The names and types are unchanged; this lets frozen dataclasses
  such as `SimpleLayer` satisfy the protocol under `mypy --strict`.
- `SimpleLayer` is a frozen dataclass. Defaults beyond those listed in the brief: `observe` and `outcome`
  return an empty model, `action_schema` raises `NotImplementedError`, and `export` returns an empty
  record through the layer's only exit. With more than one exit an explicit `export` is required
  (`ContractError` at construction).
- `Contract.roles` may be empty even for layers with roles: roles not listed in a contract are accepted
  (constraints are opaque in M0), so dynamic role sets such as `bidder:i` need no declaration.

## 2026-10-21 — M0 task 7: Primitive emits resolution events at settle, not on arrival (deviation)
- Brief: on `Submitted` emit `ActionAccepted`; on `TimedOut` emit `ActionDefaulted` / `RoleDropped`, each
  during `collecting`. Problem: the input log does not record where `advance()` was called, so any trace
  ordering that depends on step boundaries cannot be reproduced by `replay`, and two arrival orders of the
  same submissions would give different traces (breaking `assert_arrival_invariant`).
- Implemented instead: `collecting` only records resolutions in the frame. When the layer settles,
  `ActionAccepted` / `ActionDefaulted` / `RoleDropped` are emitted for every role in sorted role order,
  immediately before `LayerCompleted`. The trace is therefore a function of the set of resolutions, not of
  arrival order or step grouping, and also discloses nothing about an action until the barrier is met.
  The brief's expected kind sequence (`... action_accepted×2, layer_completed, ...`) is unchanged.
- `Fail` is still handled immediately: `Failed` is emitted and earlier un-settled submissions are dropped.
- `PrimitiveFrame` gained `defaulted: list[RoleId]`, `exit: Exit | None`, `failed: str | None`
  (all defaulted) so that resolutions can be emitted at settle and the `done` phase is idempotent for both
  exit and failure. Existing pinned fields are unchanged.
- `ctx.path` is the path of the process instance itself, root included: a root `Primitive` runs at
  `(layer.name,)` and its decision ids look like `bidding#bidder:0`.
- Within a step, inputs are processed in arrival order; a second input for an already-resolved decision
  raises `UnknownDecision`.
- Role iteration uses lexicographic order of `RoleId` strings (so `bidder:10` sorts before `bidder:2`);
  it is deterministic, just not numeric.
- `Process.validate`'s `action` is typed `dict[str, Any]`; `Ctx` is a frozen dataclass with `with_path()`.

## 2026-10-21 — M0 task 8: Session behaviour details and deviations
- `ActionRejected` events are appended to the trace at `t = clock.now` WITHOUT ticking the clock, and
  never enter the input log. `replay` cannot reproduce them, so `check_determinism` excludes
  `action_rejected` events from the comparison (it compares `events.dump(...)` output otherwise, no `__eq__`
  tricks). A rejection for a decision that is not pending carries the root path.
- `submit` checks the session's pending set before `process.validate`: a decision is removed from pending as
  soon as it is submitted/timed out (the frame only learns at the next `advance()`), so a second submission
  of the same decision raises `UnknownDecision`. `SessionFinished` is checked first. Every `GaoError` raised
  by `submit` emits `ActionRejected` first (reason = exception class name), including after finish.
- `submit` also rejects, as `SchemaError`, actions that are not strictly JSON-able (e.g. NaN, non-JSON
  objects) so the input log always round-trips through JSON.
- `timeout` raises `SessionFinished` / `UnknownDecision` but emits no `ActionRejected` (brief only asks for
  the exception).
- `replay` calls `advance()` before any event that is not a submission for a currently pending decision, and
  once at the end, exactly as briefed. A leading `Started` must match `(env_ref, params, seed)` or
  `DeterminismError` is raised. `Aborted` events are not supported in M0 (no abort API exists); replaying
  one raises `DeterminismError`.
- `check_determinism` on a session with queued inputs not yet advanced: the replay always advances, so its
  trace may be longer; in that case only the shared prefix must match. It also compares ledger snapshots.
- Added `Session.failed` (bool) so callers can tell a failed session (`result is None`) from an unfinished one.
- `advance()` makes a single `process.step` call per invocation (the brief's "loop" has nothing to iterate in
  M0, where only `Pending` / `Exited` / `FailedResult` exist).

## 2026-10-21 — M0 task 9: bidding layer assumptions
- The v1 plan's §14 was not available to me, so the free-text observation fields are my choice:
  `tie_break = "lowest_agent_id"` (sorted agent ids) and `bid_disclosure = "all_bids_after_settlement"` if
  `reveal_bids` else `"winning_price_only"`. They are descriptive strings only; M0 has no settlement layer
  and `BidReceipt` never reveals anyone else's bid regardless of `reveal_bids`. Revisit when settlement lands.
- `Bid.amount` is `strict=True` in addition to `ge=0, allow_inf_nan=False`, so numeric strings such as
  `"5"` are rejected (pydantic's default lax mode would coerce them); ints are still accepted. Bools are
  rejected by an explicit `before` validator, as the brief requires.
- `_observe` raises `ContractError` if a role's agent has no entry in `state.values`.
- `build_bidding_only` takes `rule: str` (validated by `BiddingState` on entry, not by the factory).

## 2026-10-21 — M0 task 10: testing helpers
- Helpers live in `src/gao_idl/testing/__init__.py`, raise `AssertionError` explicitly (no pytest import) and
  strip `t` (including `increment.t`) before comparing traces for arrival invariance.
- They assume one pending decision per acting agent and submit `actions[agent]` to every pending request of
  that agent; a missing pending decision is an `AssertionError`. With more than 5 agents the brief's
  permutation cap is implemented as: sorted order, reversed order, and every rotation (n+1 orders).
- `assert_noninterference` submits in sorted agent order in both runs; `assert_incremental_equals_fold` is an
  alias of the replay check, as briefed.

## 2026-10-21 — M0 task 11: tests
- Test-only environment factories live in `tests/runtime/envs.py` and are addressed by `env_ref`
  (`tests.runtime.envs:...`); this needs `tests/` to be a package and `pythonpath = ["."]` in the pytest config,
  both added.
- `test_rejections` also covers numeric strings (`"5"`) and an empty action dict, besides the brief's cases.
- The hypothesis property tests (`tests/core/test_paths_property.py`) use `pytest.importorskip("hypothesis")`
  so the suite still runs where hypothesis is missing; they run in CI (hypothesis is in the `dev` extra).
  Because hypothesis was unavailable in the build sandbox they were NOT executed there. Equivalent
  numpy-seeded randomized tests (`test_ids_injective_randomized`, `test_random_interleavings_match_canonical_run`)
  always run. The id-injectivity properties assume names do not contain the reserved characters
  `/ # @ [ ]` (and `:` for agent ids); the code does not enforce that.
- Sanity-checked that the suite is not vacuous: temporarily emitting `ActionAccepted` at submit time (the
  brief's literal wording) makes 7 tests fail, including the barrier and arrival-invariance tests.

## 2026-10-21 — M0 fix-up tasks 1-2: real gate and wheel build NOT executed (blocked)
- The fix-up brief assumes network access, but this session's egress policy still denies `pypi.org`
  (proxy answers 403 to CONNECT; a direct connection fails DNS). `pip install -e ".[dev]"`, the
  3.11/3.13 gate, `python -m build` and the clean-venv wheel install were therefore **not run**. Python 3.11
  and 3.13 interpreters exist, but only 3.13 has pydantic/numpy (system-wide); 3.11 has no packages.
- Changes made for these tasks without being able to execute them: CI is now a `["3.11", "3.13"]` matrix;
  `tests/core/test_paths_property.py` imports hypothesis directly (no `importorskip`), so a missing dev
  dependency is a hard failure and "0 skipped" is a real gate. Its strategies already excluded the reserved
  characters `/ # @ [ ]` from layer/branch names; role/agent strategies still allow `:` and digits (like
  `bidder:0`) because `decision_id` splits on the first `#` and `report_id` on the first `@` / last `:`, so
  they are injective regardless. If hypothesis finds a counterexample, report it rather than weaken it.
- Wheel layout, by inspection only: `[tool.hatch.build.targets.wheel] packages = ["src/gao_idl"]` should ship
  `gao_idl.testing` and exclude `tests/`; unverified.
- Local verification here uses `PYTHONPATH` with standalone ruff/mypy/pytest on 3.13, with the hypothesis file
  excluded (`--ignore=tests/core/test_paths_property.py`) because hypothesis is not installable.

## 2026-10-21 — M0 fix-up task 3: `check_determinism` requires an empty inbox
- Raises `ValueError("call advance() before check_determinism()")` if inputs are queued; the prefix-only
  tolerance is gone. Ledger comparison kept. `test_check_determinism_with_unadvanced_inbox` became
  `test_check_determinism_requires_advance_first` (error case plus advance-then-check case).

## 2026-10-21 — M0 fix-up task 4: `Continue` step result; `advance()` loops
- Added `Continue(FrozenModel)` to `composition/process.py` (`StepResult = Pending | Continue | Exited |
  FailedResult`, exported). `Session.advance()` repeats `process.step` while it returns `Continue`; the inbox
  is consumed by the first step and later steps get `[]`. `Primitive` never returns `Continue`.
- There is no iteration cap: a process that returns `Continue` forever would hang `advance()`. Composites are
  expected to make finite progress each time; add a guard in M1 if that proves fragile.
- Test: stub `ContinueTwice` process (`tests/runtime/envs.py`) → one `advance()` finishes after 3 steps, and
  every step after the first sees no inputs.

## 2026-10-21 — M0 fix-up task 5: natural role order (changes trace order for >= 11 roles)
- `core.ids.role_sort_key` splits digit runs and compares them numerically (raw string as final tiebreak, so
  the order is total). Used wherever roles are ordered: four sites in `primitive.py` (the brief said three:
  request issue order, settle resolution order, and two over `f.actions`) and `bidding._outcome`.
- Behaviour change vs M0: with >= 11 roles the order of requests, resolution events and `LayerCompleted.actions`
  is now `bidder:0 … bidder:11` instead of `0, 1, 10, 11, 2 …`. Traces recorded under M0 with >= 11 roles are
  not byte-identical. Up to 10 roles nothing changes.
- Agent ordering is untouched: `build_bidding_only` still numbers roles by `sorted(values)` (lexicographic
  agent id) and `BidObs.bidders` is still lexicographic; only role order is natural.

## 2026-10-21 — M0 fix-up task 6: README example
- README has one runnable 17-line example; `tests/test_readme_example.py` extracts and executes it and checks
  its printed output.

## 2026-10-21 — M0 fix-up task 7: hygiene
- `.gitignore` now ignores `.venv*/` (plus the existing `.hypothesis/`, `dist/`). No `.Rhistory` or `~$*` files
  exist in the repository, so nothing was removed.
- Tasks 3-5 share files (`session.py`, tests), so they are one commit instead of three.

## 2026-10-21 — M1 task 0: inherited M0 items; PyPI still unreachable
- The M0 fix-up report still lists the hatchling build and the hypothesis tests as unverified. I tried
  `pip download hypothesis` once: DNS fails (`Name or service not known`), so
  `pip install -e ".[dev]"`, `pytest tests/core/test_paths_property.py` and `python -m build` could **not** be
  run. `build` and `hatchling` are not installed either. Not retried, per the brief. Still unverified: the
  hatchling wheel layout, the hypothesis property tests, mypy and CI on Python 3.11.
- Local gate substitute (same as M0, plus one fix): ruff/mypy/pytest are uv-tool venvs that cannot see the
  system `dist-packages`, so they are run with
  `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages` and `--ignore=tests/core/test_paths_property.py`
  (hypothesis missing). Baseline before any M1 change: 125 passed.
- `Session.advance()` now raises `RuntimeError("process did not reach quiescence")` after
  `MAX_CONSECUTIVE_CONTINUES = 10_000` consecutive `Continue` results (the 10 000th raises; 9 999 is fine).
  The frame is left as the last step produced it and the session is not marked finished. Tests:
  `tests/runtime/test_session_guard.py` with a `ContinueN` stub in `tests/runtime/envs.py`.
