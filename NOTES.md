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

## 2026-10-21 — M1 tasks 1-2: Connector and Seq
- `Connector` is `Connector(fn, output_schema, store_reads=frozenset(), store_writes=frozenset())` (positional
  order matches the brief's example). Added two things the brief did not name: `Connector.apply(record, ctx)`
  (deep-copies the record, builds the scoped `StoreView` at `ctx.path`, validates the returned state against
  `output_schema`, `ContractError` otherwise; used by Seq and later Loop) and `ReadOnlyLedger`, a wrapper that
  exposes only `utility` / `increments` / `information`, so a connector cannot call `add_increment` /
  `record_information` even by accident. Connectors get no trace sink, so they cannot emit participant info.
- Composites are `@dataclass(frozen=True, eq=False)` (identity equality; frozen dicts are not hashable) with a
  keyword `name` that defaults to `"seq"`. The brief's `Seq(first, routes)` has no name but `Process.name` is
  needed for the root path and for `Seq(Seq(a, b), Seq(c, d))`-style trees; give siblings distinct names.
- **Paths:** `Seq` runs children at `child(ctx.path, child.name)`. Composites emit no `LayerEntered` / `Exited`
  events of their own (only `Primitive` does), so `Seq(Seq(a, b), c)` and `Seq(a, Seq(b, c))` have the same
  trace modulo paths and `t`. Consequence worth knowing: RNG streams are keyed by path, so a layer that
  *draws randomness* is not substitution-equivalent across restructurings (its path changes). Only
  deterministic layers are.
- `SeqFrame` has extra fields beyond the brief's four: `exited_port`+`exited_record` (the child's exit is
  *parked* in the frame and handled on the next call, which is what "one thing per step" requires), `route`
  (the port of `first` that selected the continuation, because frames must not hold Process objects), and
  `failed`. `Frame` children are `SerializeAsAny[BaseModel]`, so frames **dump** to JSON including nested
  children, but cannot be *validated back* (the child type is not recorded); state is rebuilt by replaying
  the input log (invariant 4), never by loading frames.
- Ownership of inputs uses `routing.owns(path, decision_id)`: it compares the path part of the id (before the
  first `#`) and respects the boundary, so `seq/a` does not claim `seq/ab#r`. The check is defence in depth;
  `Primitive` would also raise `UnknownDecision` for an id it does not know.
- Construction-time checks beyond the brief (all `ContractError`): a route for a port `first` never exits
  through; a continuation with the same name as `first` (decision ids would collide across stages);
  names containing the identifier-reserved characters `/ # @ [ ]` (M0 noted the code did not enforce them).
  Continuations may share a name with each other (only one runs).
- `Seq.entry` is `first`'s entry with the store scope widened to the union over everything in the Seq
  (informational; scopes are still enforced per layer/connector at runtime).

## 2026-10-21 — M1 task 3: Par (one deviation from the brief, made to satisfy invariant 3)
- **Deviation: `Par.step` applies at most ONE input per call** (the rest wait in `ParFrame.backlog`, and the
  call returns `Continue` while the backlog is non-empty). The brief says "route each input … then step every
  not-yet-exited branch once". Taken literally, the trace depends on how the log was batched into `advance()`
  calls: if bob (branch `B`) answers in one `advance()` and alice (`A`) in the next, the original trace has
  B's events first; `Session.replay` (which batches consecutive submissions into one `advance()`) steps
  branches in sorted order and puts A's first, so `check_determinism` / `restore` would disagree with the
  original (invariant 3: "replaying produces an identical trace"; shared context wins over the brief).
  With one input per call the trace is a function of the input-log *order* only. Verified by mutation:
  applying all inputs in one step makes `test_trace_depends_on_log_order_not_on_advance_boundaries` fail with
  a `DeterminismError`. Still true per call: the input is routed to its branch and every live branch is
  stepped once, in sorted-name order.
- Consequence: for `Par`, traces are NOT independent of submission order (they cannot be: if A completes
  before B has answered, A's events are already in the trace). Exact trace equality across submission
  permutations therefore cannot be asked of Par environments. (Superseded by the M1 fix-up, task 2 below:
  `assert_arrival_invariant` now checks projections and does apply to Par environments.)
- `Par.step` returns `Continue` when a branch returned `Continue`, when the backlog is non-empty, or when all
  branches have exited (the join runs on the *next* call, "one thing per call"); otherwise `Pending` with the
  requests of every pending branch in sorted branch order. A failing branch stops the call (later branches
  are not stepped) and the Par returns `FailedResult("branch 'A': …")`; `Failed` is not re-emitted by the Par.
- `Par.validate` has no path to match, so it asks each live branch in sorted order and takes the first that
  does not raise `UnknownDecision` (other errors propagate). Input routing in `step` uses `routing.owns`.
- `Splitter` / `Joiner` are Protocols. The brief gives `DisjointSplitter(fn)` but the Par's entry contract
  needs a `state_schema`, so `DisjointSplitter(fn, input_schema=OpenState)` takes an optional schema
  (`OpenState` accepts any fields). `Par.start` validates the entry state against it and checks that the
  splitter returned exactly the branch names (`ContractError`). `Par.entry.roles` is empty (dynamic).
- Branches are sorted with plain `sorted()` (lexicographic, as the brief says), not the natural sort used
  for roles: `branch_10` runs before `branch_2`. Deterministic, only affects trace order.
- Branches share the session `Store` (see task 6 for the hazard and tests). Par emits no events of its own.
- Added `ParFrame.backlog` and `ParFrame.failed` to the brief's fields; children are `SerializeAsAny`.

## 2026-10-21 — M1 task 4: Loop
- Iteration `i` (0-based) runs at `child(ctx.path, LoopIter(i))`; the body's own name is *not* added, so a
  `Primitive` body issues ids like `loop/loop[2]#player` and a `Par` body gets `loop/loop[2]/branch[x]#…`.
- **Limit semantics (my reading of "if `i == max_iter` is reached and the body would feed back again"):**
  after iteration `k` exits through a feedback port, feeding back would start iteration `k+1`; if
  `k+1 >= max_iter` the limit applies. So `max_iter=2` runs iterations 0 and 1, and `iterations` in the
  truncated record equals `max_iter`. A body that exits through an `until` port on its last allowed
  iteration terminates normally (no `Truncated`). **The feedback connector is not run at the limit** (it would
  build a config for an iteration that never starts, and could write the store). `last_exit` is the body's
  exit *record* only, as briefed; the port it exited through is not included.
- `on_limit` is a required argument (the brief lists no default), `name` is an optional keyword (`"loop"`).
  `on_limit="fail"` emits one `Failed` event at the loop's path and returns `FailedResult`; the reserved
  `"truncated"` port is in `Loop.exits` in both modes.
- Wiring errors (`ContractError`): port in both maps; port in neither (every body exit port must be routed);
  a map key that is not a body exit; `"truncated"` as an `until` target; feedback connector `output_schema`
  not identical to the body's entry `state_schema`; `max_iter` not an int >= 1 (bools rejected); bad
  `on_limit`; two `until` ports mapping to one target with different record schemas.
- `LoopFrame` has extra fields `exited_port`, `exited_record`, `result`, `failed` (see Seq/Par notes);
  once `result` or `failed` is set, stepping again returns it without re-emitting anything.

## 2026-10-21 — M1 task 5: contracts.check / describe
- Layout: each composite exposes its *local* rules as `validate_wiring()` and (Seq, Loop) `compute_exits()`;
  `contracts.check(process)` calls them and recurses through `_children()` (Seq: first + continuations by
  port; Par: branches by name; Loop: the body). Each constructor calls `contracts.check(self)` from
  `__post_init__` through a lazy import (contracts imports the composites, so a top-level import would be
  circular). In tasks 2-4 the constructors called `validate_wiring()` directly; behaviour is unchanged.
- `check` also recomputes the exit map, so conflicting port schemas are re-detected when it is called on an
  existing tree. Leaves (`Primitive`, custom processes) have no `_children` and are not inspected.
- The brief's store-ordering check is deliberately **not** implemented (it says to skip it). A composite's
  `entry.store_reads` / `store_writes` is the union over everything it contains, including connectors;
  `describe` shows it. Actual enforcement is the per-layer / per-connector `StoreView` scope at runtime.
- `describe(process)` returns `{kind, name, entry{state_schema{name, fields}, roles, store_reads,
  store_writes}, exits{port: schema}}` plus `first`/`routes` (Seq), `branches`/`splitter`/`joiner` (Par),
  `body`/`feedback`/`until`/`max_iter`/`on_limit` (Loop), `timeout` (Primitive). Schemas are described by
  class name and sorted field names, not full JSON Schema (keeps the tree small); everything is sorted so
  the output is stable.

## 2026-10-21 — M1 task 6: store scopes end to end
- **A store-scope violation now fails the session instead of escaping `advance()` as an exception.**
  `Primitive._settle` catches `KeyError` / `PermissionError` around `layer.outcome(...)` (the only place a
  layer has a `StoreView`), emits `Failed` at the layer's path and returns `FailedResult` with a reason like
  `layer 'snoop' violated its store scope: KeyError: store key 'secret' is not in this view's read scope`.
  Caveat: the catch is by exception type, so an unrelated `KeyError` bug inside a layer's `outcome` is also
  reported this way (the message names it, and the session is marked failed rather than crashing).
  The same conversion applies to connectors in `Seq` and to feedback connectors in `Loop` (`Failed` is
  emitted at the composite's path). Not converted: `ContractError` from a connector's output schema (a
  wiring bug, still an exception), `ValueError` for a non-JSON store value.
  Also: reading a key that is *in scope but never written* raises `KeyError` too (M0 behaviour), so it fails the
  session the same way.
- Store keys are shared across the whole session: values written in one `Loop` iteration are visible in the
  next (`test_store_persists_across_loop_iterations`; the body creates the key on iteration 0 and reads it
  afterwards, because a declared-but-unset key cannot be read).
- **`Par` branches share the one session store** (as briefed; a per-branch store is a possible later change).
  Hazard to know about: a cross-branch read-after-write dependency is only well defined if the writer has
  already run. Automatic branches run in sorted-name order inside one step, so the order is deterministic
  (`test_par_branches_share_the_store` has writer `A`, reader `B`; swapping the names makes the reader fail,
  pinned by `test_par_cross_branch_read_before_write_fails_in_sorted_order`). For decision branches the
  order follows the input-log order (see the Par note), which is deterministic given the log but is the
  agents' choice, so environments should not rely on cross-branch store dependencies.
- Ordering of writes before reads is not checked statically (the brief says to skip it).

## 2026-10-21 — M1 task 7: settlement layer and the full auction
- `build_auction` returns `Seq(bidding, {"settle": (connector, settlement)}, name="auction")`; the brief's
  sketch had no `name`, I added `name="auction"` so the root path is `auction` and ids read
  `auction/bidding#bidder:0`. `build_bidding_only` now shares a small `_bidders` helper with it (behaviour
  unchanged; its state dict and roles are identical).
- **Reserve semantics (ambiguous; I took the literal reading — please confirm).** "Bids below reserve are
  ignored" is implemented as a pure filter (`bid >= reserve` is eligible, so a bid equal to the reserve
  clears), after which the normal rules apply. Consequence: under second price, a *single* eligible bidder
  pays 0 even though a reserve exists. The textbook Vickrey auction with reserve charges
  `max(reserve, highest losing bid)`; that is a one-line change in `settlement.py` (`price = max(price,
  reserve)`) and a flip of one assertion in `test_reserve_ignores_low_bids_and_clears_at_equality`.
  First price is unaffected.
- The reserve lives in `settlement_layer(reserve)` (the environment), not in `SettlementState`, whose fields
  are exactly the brief's four. The `unsold` port is declared only when a reserve is given. `UnsoldOutcome`
  is `{reserve, payments, utilities}` with zero entries for every bidder; its `utilities` are credited as 0.0
  increments like any outcome's. **No participant report is delivered when the item goes unsold**
  (`SettlementReport` requires a winner and the brief defines no other report), so bidders see only their
  `BidReceipt`. Open question for the next milestone: should there be an `UnsoldReport`?
- `SettlementReport` omits `all_bids` from its JSON entirely (custom wrap serializer) unless `reveal_bids`,
  rather than serialising `null`, so "lacks `all_bids`" is literally true of the delivered payload.
  `winning_payment` is the price the winner pays; `own_*` fields are the recipient's.
- Settlement errors are `ContractError` (they escape `advance()`): unknown `rule`, a bidder missing from
  `values`, or no bids at all without a reserve. (An unknown rule is normally caught earlier by the bidding
  layer's `Literal` schema.)
- Ties: highest bid wins, smallest agent id (plain string order) among equal bids; the second price is the
  highest *other* eligible bid, so with a tie it equals the winning bid.
- The M0 bidding tests were moved with `git mv` from `tests/runtime/test_session_bidding.py` to
  `tests/environments/test_auction.py` (history kept) and the new tests appended. They still import the
  bidding-only factories from `tests/runtime/envs.py`. That file is mostly generic Session behaviour exercised
  through the bidding layer, so a later milestone may want to split it again.
- `assert_noninterference` / `assert_arrival_invariant` drive one decision round (they submit once per agent
  and advance), which is exactly what the two-layer auction needs because settlement is automatic.

## 2026-10-21 — M1 task 8: testing fixtures
- `events.strip_paths(dicts, *, drop_t=False)` (new, in `core/events.py`) works on dumped JSON dicts (trace
  events from `events.dump`, or `model_dump(mode="json")` of ledger information). It removes `path` (top level
  and inside `request` / `report` / `increment`), reduces `decision_id` to its role (`a/b#r` -> `r`) and
  `report_id` to `agent:n`, and optionally `t`. Only those path-bearing fields are touched, never user
  payloads (a user action containing a `"path"` key survives). Ids embedded inside free-text `reason`
  strings are not rewritten.
- `Session` resolves environments by `env_ref`, but the helpers receive Process objects, so they register
  the process in a module-level dict for the duration of a `with` block and address it through
  `gao_idl.testing._adhoc_factory` (the key `adhoc-N` appears only in the throwaway session's `Started`
  event). Consequences: sessions created this way are for inspection after the block; `check_determinism`
  / replay must be done inside it (`tests/composition/test_substitution.py` does).
- `assert_substitution_equivalent(a, b, cfg, seed, actions)` runs both to completion (answering every pending
  decision with `actions[agent]`), then compares result/failed flag, per-agent utility, per-agent information
  streams and traces *with `t` kept*, all path-stripped. Keeping `t` is deliberate: composites must not tick
  the clock. A mutation that makes `Seq` tick once on exit is caught.
- **Limit worth knowing:** because RNG streams are keyed by path (pinned design), restructuring a process
  changes the stream seen by a layer that draws randomness, so substitution equivalence holds only for
  deterministic layers. `test_substitution_is_only_for_deterministic_layers` pins this (the helper correctly
  reports a difference for a drawing layer wrapped in a Seq). The associativity test therefore uses
  `fixed_decision_layer` (no draws) and checks all five bracketings of four layers (three automatic, one
  decision).
- `assert_branch_rng_independence(build_par_env, seed, *, actions=None, branch="A", other="B")`: the brief's
  signature has no way to supply actions, so `actions` is an optional keyword (needed when branches have
  roles). It builds the Par over `(branch, other)` and over `(branch,)`, and compares the events whose path
  contains `Branch(branch)` and the information streams of the agents acting in it, path-stripped **and
  with `t` dropped** (the sibling's events shift the logical clock, so `t` cannot match). It also fails if
  the branch produced no events. A negative control (a splitter that leaks the number of branches into the
  branch state) is detected.
- Helper limitation carried over from M0: they submit `actions[agent]` to every pending decision of that
  agent. `_drive` now loops until the session finishes (needed for multi-stage processes), with a cap of
  1000 rounds.

## 2026-10-21 — M1 task 3 follow-up: correction to the Par note (found while writing task 9 tests)
- **The "one input per call" rule in the task 3 entry was not sufficient.** A randomized-schedule test over
  `Loop(Par(Seq))` (random submission order, random `advance()` placement, then `check_determinism`) failed
  on the unmutated code. Cause: a live session runs every input to quiescence (`advance()` loops on
  `Continue`) before the next input can arrive, but my backlog applied the next input in the *middle* of the
  previous input's `Continue` chain, so on replay alice's input interleaved with bob's branch moving on to
  its second layer (index 8 of the trace: `layer_entered B/second` originally, `action_accepted A/first` on
  replay).
- Fix (`ParFrame.settling`, commit "Par: apply queued inputs only after settling"): an input is applied only
  when the previous step made no progress (no branch returned `Continue`, and the Par is not about to join).
  While settling, the call steps every live branch with no input. Net effect: replaying a log that was
  batched arbitrarily gives exactly the trace of a session that called `advance()` after every input.
- Evidence: 900 random schedules (3 environments incl. a 3-branch Par with an agent holding two roles, 300
  seeds each) replay to identical traces and ledgers; removing the `settling` guard makes
  `test_random_schedules_replay_to_the_same_trace` and the deterministic regression
  `test_staggered_answers_with_multi_step_branches_replay_exactly` fail.
- Assumption this relies on: stepping a quiescent subtree with no inputs is idempotent (emits nothing and
  returns the same `Pending`). True for `Primitive`, `Seq`, `Loop` and `Par` as built here; a custom
  `Process` must satisfy it too.

## 2026-10-22 — M1 fix-up task 1: PyPI still unreachable (skipped)

- `pip download hypothesis` fails again ("No matching distribution"), tried once, not retried. So
  `pip install -e ".[dev]"`, `pytest -q` (the 3 hypothesis tests) and `python -m build` were NOT run; they stay
  unverified, exactly as in M0/M1. The gate runs with the standalone ruff/mypy/pytest and
  `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`.

## 2026-10-22 — M1 fix-up task 2: arrival invariance is projection invariance

- `assert_arrival_invariant` now drives each submission order to completion (every round: all pending
  decisions of each agent, in that order, then `advance()`), so multi-round environments such as the nested
  `Loop(Par(Seq))` fixture are covered. The first round still insists that every agent in `actions` has a
  pending decision; later rounds skip agents with none, and a stuck session raises.
- Per order it compares against the first run: (1) `ledger.information(agent)` exactly; (2) `ledger.snapshot()`;
  (3) per branch root, the events under that root, modulo `t`; (4) the events with no `Branch` in their path,
  modulo `t`. An event belongs to the root = its path up to its *last* `Branch` element, so with nested Pars an
  outer branch's events outside any inner branch form their own group (the brief's wording only covers
  roots with no Branch below them; this is the same thing there and also checks the outer events).
- Deviation on (2): increments in the snapshot carry `t` from the global logical clock, which interleaves by
  submission order when there is a Par (verified: the *only* difference between the two orders of the nested
  fixture is `t` on utility increments). The snapshot is therefore compared exactly when the trace has no
  event under a `Branch`, and with `t` dropped from increments when it has. Information items carry no `t`.
- Negative controls (tests): `OrderLeak` (a wrapper that traces arrival order) fails outside any branch and
  inside one; `test_par_global_trace_follows_submission_order_but_projections_do_not` shows exact trace
  equality really does differ for a Par while the projections agree.

## 2026-10-22 — M1 fix-up task 3: textbook reserve semantics

- Second price with a reserve now charges `max(reserve, highest other eligible bid)`; the reserve acts as a
  seller's bid (layer docstring updated). First price is unchanged (the winner pays their bid) and a
  setup without a reserve is unchanged (a lone bidder pays 0). This resolves the "Reserve semantics" call
  in the M1 entry for task 7: the literal pure-filter reading is no longer what the code does.
- Flipped the assertion in `test_reserve_ignores_low_bids_and_clears_at_equality` (alice now pays 7.0);
  added `test_reserve_binds_second_price`. Mutation check: reverting the price line fails exactly those two.

## 2026-10-22 — M1 fix-up task 4: unsold items are reported

- `UnsoldReport(status: Literal["unsold"], reserve, own_bid)` is delivered to every bidder when the settlement
  layer exits through `unsold`; the `return None` special case is gone. It has no `winner` field, and nothing
  in any bidder's information stream names a winner in that case. This answers the open question in the M1
  entry for task 7 (no participant report on unsold).
- Consequence for an existing test: `test_reserve_unsold_port` now sees two reports for alice (the bid receipt
  and the unsold report) instead of one. New test `test_unsold_items_are_reported_to_every_bidder`. Mutation
  check: restoring `return None` fails both.

## 2026-10-22 — M1 fix-up task 5: typed store exceptions

- `core/store.py` now defines `StoreScopeError(GaoError)`, `StoreReadDenied(StoreScopeError, KeyError)` and
  `StoreWriteDenied(StoreScopeError, PermissionError)`; `StoreView.get`/`set` raise them for out-of-scope keys.
  Because the subclasses are still `KeyError` / `PermissionError`, nothing written against the old exceptions
  breaks (`test_connector`, `test_ledger_store` still pass; the latter now asserts the typed classes).
- `Primitive`, `Seq` and `Loop` catch `StoreScopeError` only. A scope violation still becomes `Failed` +
  `FailedResult`, and the reason now names the typed class (`StoreReadDenied: ...`). Any other `KeyError`
  — including an in-scope key that was never set — propagates out of `advance()` as a plain `KeyError`.
- Behaviour change to be aware of: the documented Par shared-store hazard (reader named before its writer,
  so it reads an unset in-scope key) used to be a failed session and is now an exception. The existing test
  `test_par_cross_branch_read_before_write_fails_in_sorted_order` keeps its name but asserts the plain
  `KeyError`; its docstring says so.
- New tests: `test_unrelated_keyerror_in_layer_propagates` (dict indexing and unset in-scope key),
  `..._in_a_connector_propagates`, `..._in_a_feedback_connector_propagates`, and a class-hierarchy test.
  Mutation check: switching each of the three catch sites back to bare `KeyError, PermissionError` fails
  exactly its propagation test (the Primitive one also fails the Par hazard test).

## 2026-10-22 — M1 fix-up task 6: tests split back

- 25 of the 48 tests in `tests/environments/test_auction.py` moved, unchanged and under their own names, to
  the new `tests/runtime/test_session.py` (the small `fresh` / `kinds` / `dumped` helpers are duplicated there;
  no test imports from another test module). The set of test names across the two files is identical to
  before (checked mechanically); count unchanged.
- Rule used: a test moves if it is about Session or testing-helper mechanics with the bidding environment as a
  mere vehicle (pending/barrier, rejection protocol, timeouts, replay/snapshot/restore, determinism checks,
  advance semantics, decision ordering, helper negative controls, randomized interleavings). It stays if it is
  about bids, values, receipts or settlement. Judgement calls, easy to flip: `test_rejections` moved whole even
  though it also exercises the bid schema; `test_arrival_invariance` / `_three_bidders` and
  `test_noninterference_of_other_value` / `_own_value_does_matter` stayed (they state auction properties),
  while `test_arrival_invariance_many_bidders_uses_rotations` and
  `test_noninterference_helper_detects_a_leak` moved (they exercise the helpers).

## 2026-10-22 — M1 fix-up task 7: `DictJoiner` keeps the port (breaking change to a briefed shape)

- `DictJoiner.join` now returns record `{branch: {"port": exit.port, "record": exit.record}}` instead of
  `{branch: exit.record}`, so a downstream connector can tell whether a branch exited normally. The brief's
  original shape is gone; anything that read `joined[branch]` as the branch record now needs
  `joined[branch]["record"]`. The `schema` handed to `DictJoiner` validates the new shape.
- Knock-on: a Par inside a Loop feeds this shape to the feedback connector and into `TruncatedExit.last_exit`
  (the nested fixture's expected `last_exit` changed accordingly). `Par.exits`, `describe()` and wiring checks
  are unaffected (they only see the schema and port). The `Port` is stored as its plain string.
- Updated expectations in `test_par`, `test_store` and `test_nested`; the fixtures' joiner schemas are all
  `OpenState` (or the deliberately mismatching `NeedsC`), so none needed changing. New tests:
  `test_dict_joiner_keeps_each_branchs_exit_port` (with a schema for the new shape) and
  `test_par_branches_that_exit_through_different_ports_stay_distinguishable`. Mutation check: reverting the
  record shape fails 6 tests.

## 2026-10-21 — M2 task 0: PyPI still unreachable
- `pip download pydantic` fails with DNS "Name or service not known" (tried once, not retried). The real gate
  (`pip install -e ".[dev]"`, the 3 hypothesis tests, `python -m build`, Python 3.11, CI) was NOT run; the gate is
  run with the standalone ruff/mypy/pytest and `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`,
  with `--ignore=tests/core/test_paths_property.py`. Baseline before M2: 220 passed.
- No new dependencies were added in M2 (asyncio, logging, fnmatch are stdlib).

## 2026-10-21 — M2 part A: glob-style store scopes
- `StoreView` scopes are now patterns: a key is in scope if it equals a pattern or `fnmatch.fnmatchcase`
  matches it (`core.store.in_scope`). `fnmatchcase`, not `fnmatch`, so matching does not depend on the OS.
  `hist:*` therefore covers `hist:alice~bob` (and `hist:`, since `*` matches empty); `histx` is not covered.
- Consequence to know: a pattern is always a glob, so a *literal* scope key that contains `[` / `?` / `*` is also
  a pattern (`a[1]` grants `a1` as well as `a[1]`). Exact equality is checked first, so it still matches itself.
  Nothing in the repo uses such keys. Scopes stay unvalidated strings; the unions computed by composites are
  unions of patterns (not expanded).
- Reading an in-scope key that was never set is still a plain `KeyError` (M1 fix-up). The PD handles first
  meetings with `read_history`, which maps only that case to `[]` and re-raises `StoreReadDenied`
  (it is a `KeyError` subclass, so the order of the `except` clauses matters).

## 2026-10-21 — M2 part A: repeated PD design calls and deviations from the brief
- **`Layer.observe(cfg, role, rng)` has no store access in the pinned API, so "observe reads `hist:{pair_id}`" is
  impossible as worded (shared context wins).** Instead: `pd_round`'s `outcome` reads-appends-writes
  `hist:{pair_id}` (it has the `StoreView`), and the feedback connector (which has a read view) copies each
  playing pair's stored history into the next round's entry state, from where the splitter hands it to that pair's
  branch only. `PDState` therefore has one extra field, `history: list[dict[str, str]]` (each entry maps both agents
  of the pair to their move, oldest first, so it does not depend on who was `row`); `observe` renders it as
  `own_history` of `(own, other)`. The store is the source of truth; the state copy is rebuilt from it every round.
  I did not touch the pinned `Layer` protocol.
- `PDRoundExit` has one extra field, `round_index`: the counter needs the round number and the joined record carries
  nothing else from which to get it (the connector between `Par` and the counter has no `ctx`).
- Layout: `Loop("pd")` > `Seq("round_seq")` > `Par("round")` with branches `pair_0 … pair_{n/2-1}` (one `Primitive`
  of `pd_round` each), then the automatic `round_counter` layer, as briefed. Decision ids read
  `pd/loop[2]/round/branch[pair_1]#row` (neither the Seq nor the Primitive adds a path element; M1 behaviour).
  The counter exits `finished` on the last round and `again` before it, `until={"finished": "done"}`,
  `max_iter=rounds+1`, `on_limit="fail"`; normal runs never touch the limit. The `done` record is the last round's
  `RoundSummary` `{round_index, rounds, pairs: {pair_id: {agent: move}}}`.
- Static settings (agents, matching, payoffs, rounds) are closed over by the splitter and connectors built in
  `build_repeated_pd`; the per-round entry state `PDRoundInput` carries only `round_index` and the stored histories
  of the pairs playing (absent = never met). So the `env_ref` params fully determine the process (invariant 3).
- `RoundJoin` is a `RootModel[dict[str, BranchExit]]` matching `{branch: {"port": ..., "record": ...}}`. The Par->counter
  connector raises `ContractError` if any branch's port is not `done` or the branches disagree on the round index.
  Rationale: a `pd_round` branch can only exit through `done` (a failing branch fails the Par before the join), so
  this is a wiring bug, not a game outcome; connectors cannot emit `Failed`.
- Pair identity: `pair_id = "~".join(sorted(pair))`; agent ids containing `~` are rejected (`ContractError`) because
  they would make ids ambiguous. `fixed`: pairs `(agents[2k], agents[2k+1])`, first listed is `row`.
  `round_robin`: circle method, `agents[0]` fixed, the rest rotate right by `r mod (n-1)`, position `i` plays
  `n-1-i` (first of the pair is `row`). The schedule repeats after `n-1` rounds, so a pair can meet again and then
  sees only its own earlier games (tested). Odd or empty populations, duplicates, `rounds < 1` and bad payoff tables
  raise `ContractError` at construction.
- `pd_round` timeout policy is `Fail` (the brief names none; `SimpleLayer` default). A runner deadline therefore ends
  a PD session in failure; a `DefaultAction` variant is a later decision (what is the neutral move?).
- Tests for noninterference use fixed pairs only: under `round_robin` pair 2's moves legitimately reach alice later
  through the round-robin disclosure (alice's report in the round she meets carol), so there is no such property.
- `PDOutcome.moves` is keyed by role and `PDRoundExit.moves` by agent, as briefed.

## 2026-10-21 — M2 part B: agent adapters
- Package `gao_idl.agents` (`base`, `random_agent`, `scripted`, `human`; re-exported from `__init__`).
  `EpisodeContext` / `EpisodeEnd` are frozen pydantic models with the briefed fields. `EpisodeEnd.final_utility`
  defaults to `None`; its docstring states that filling it is a disclosure decision (the runner policy flag).
- **RandomAgent sampling**: `enum`, `const` (what pydantic emits for a one-value `Literal`), `integer` / `number`
  with `minimum` / `maximum`, `boolean`, `object` with its `required` properties recursively (optional
  properties are omitted). Open bounds: no `maximum` -> `[min, min+10]` (briefed); my choices for the cases the
  brief does not cover: no `minimum` -> `[max-10, max]`, neither -> `[0, 10]`. Integers use the integers inside
  the interval. Anything else raises `NotImplementedError` naming the (innermost) offending fragment, including
  keywords that would make sampling unsound if ignored (`anyOf`/`oneOf`/`allOf`/`not`, `exclusiveMinimum`/`Maximum`,
  `multipleOf`) and **`$ref`**. Consequence: an action model with a *nested pydantic model* is not supported, because
  pydantic emits `$ref`/`$defs` for it (all current actions, `Bid` and `PDAction`, are flat). Resolving local
  `$defs` is a small extension if a later environment needs it.
- A sampled action is schema-valid but may be infeasible for the layer (`Layer.feasible`); the runner's
  one-retry-then-timeout rule is what deals with that.
- `RandomAgent(seed)` owns a `numpy.random.default_rng(seed)`; `episode_start` does not reseed it, so a second
  episode with the same agent object continues the stream.
- `ScriptedAgent` and `HumanTransportAgent` both (a) remember the agent id from `episode_start` and raise
  `ValueError` if `observe` gets a report addressed to someone else (defence in depth for invariant 1), (b) dedupe
  reports by `report_id`, (c) forget earlier reports at `episode_start` unless `keep_episode_memory`. (b) and (c) are
  small additions to the brief for `ScriptedAgent` (it only asked for dedup in the human agent): the runner re-delivers
  a session's earlier reports when it attaches to a restored session, see the Part C entry.
- `HumanTransportAgent` additions: `wait_outstanding()` (an `asyncio.Event`-based wait, so tests need no sleeping),
  and `provide` raises `UnknownDecision` for an id that is not outstanding (already answered, cancelled, or never
  asked). A deadline cancels `act`, which withdraws the request from `outstanding()`.

## 2026-10-21 — M2 part C: runner (`runtime/runner.py`)
- Shape as briefed: `run(session, adapters, policy, clock)` and `run_sync` (`asyncio.run` with the real clock). All
  `advance` / `submit` / `timeout` calls are in the one coroutine (tested by recording `current_task()` and the thread
  id in a `Session` subclass); adapter `act` calls are tasks that only return an action.
- **Ambiguity: the ledger.** The brief says the runner "never reads `session.ledger` to construct anything it passes to
  an adapter" but also that `EpisodeEnd.final_utility` is filled when `disclose_final_utility=True`, and that value can
  only come from the ledger. Conservative reading: the ledger is read in exactly one place, the `final_utility` of
  `EpisodeEnd`, and only when the flag is set. Test: a `Session` subclass whose `ledger` raises runs fine by default and
  raises with the flag.
- **Extension: catch-up on attach.** After `episode_start` the runner delivers every `ReportDelivered` already in
  `session.trace` (by `report_id`, tracked in a set), and it dispatches `session.pending()` rather than only decisions
  that appeared in the events of this `advance()`. Reason: a session restored from a snapshot has already advanced inside
  `replay`, so `advance()` returns no events, and a fresh adapter (the briefed restore test) would otherwise have an empty
  history and no work. Reading `session.trace` for this is not the ledger. Within a run each report is delivered once
  (set of ids); `HumanTransportAgent` and `ScriptedAgent` also dedupe, so re-attaching a used adapter is harmless.
- Adapters get **deep copies** of requests and reports (a `DecisionRequest` has mutable dicts inside a frozen model, and the
  same objects sit in the ledger and the pending set). Mutating them has no effect on the session (tested).
- Order of events: when several tasks finish in one wake-up they are submitted in dispatch order (a sequence number), not
  in the set order `asyncio.wait` returns, so scripted runs write the same input log every time (tested). With a `Par`
  the log order is part of the trace, so this matters.
- Retry: `SchemaError` / `Infeasible` from `submit` -> the same request is dispatched once more (new `act` call; the
  deadline keeps counting from the first dispatch); a second failure -> `session.timeout`. Every rejection leaves an
  `action_rejected` event in the trace (existing Session behaviour). Any exception (or cancellation) raised by `act`
  -> warning log and `session.timeout`, no retry. Other `GaoError`s from `submit` (e.g. `UnknownDecision`) are bugs and
  propagate. Exceptions from `observe` / `episode_start` / `episode_end` also propagate (the brief says nothing; failing
  loudly was the conservative choice); the `finally` block cancels and awaits all in-flight adapter tasks, so a failed or
  cancelled run leaves no tasks behind (tested).
- Deadlines: `RunnerPolicy.deadline_seconds` applies per decision from its first dispatch, measured with `clock`
  (default `time.monotonic`) and used to compute the `asyncio.wait` timeout. `DecisionRequest.deadline` (the pinned
  per-request field, always `None` so far) is ignored: its units ("runner-interpreted wall clock") are undefined.
  A `clock` that does not advance in real time would make the wait loop sleep for real without expiring anything.
  Expired tasks are cancelled and awaited, then `session.timeout` is called, so the layer's `TimeoutPolicy` decides.
- A pending request whose agent has no adapter raises `RuntimeError("no adapter for agent …")` (silently timing it out would
  invent behaviour, invariant 6). Reports for an agent without an adapter are skipped.
- `terminal` is derived from the end state (`session.failed`, `result.port == "truncated"`), not the last trace event:
  for a `Loop` truncation the last event is `Truncated`, and for a `Par` failure it may be an unrelated store access.
- Stall detection is `RuntimeError("stalled: …")` when, after `advance()`, the session is not finished and no decision is in
  flight. Test stub: `tests/runtime/envs.py::Stalled` (returns `Pending([])`).
- `HumanTransportAgent.provide` now removes the request from `outstanding()` immediately (before `act` resumes), so
  `wait_outstanding()` right after `provide` cannot return the answered request.
- Test-only additions: `bidding_capped` (bids above 10 are infeasible; timeout default 0) and `stalled` in
  `tests/runtime/envs.py`. No test uses `asyncio.sleep`; the only real wait is the 0.01 s deadline.

## 2026-10-21 — M2 fix-up task 1: the runner never leaves work dangling
- `run()`: the main loop is in `try / except Exception / finally`. On an exception it first cancels and awaits every
  in-flight `act` task, then calls `episode_end(terminal="failed", final_utility=None)` on every adapter (a failing
  `episode_end` becomes a `warnings.warn`, so the original exception surfaces), then re-raises. The `finally` block
  also cancels and awaits in-flight tasks on cancellation (no `episode_end` is sent on cancellation: the brief says
  "by exception"; a cancelled runner is silent). The normal `episode_end` phase is after the block, so an exception from
  it propagates as before without a second round of "failed" notices. `final_utility` is never read on failure.
- `HumanTransportAgent`: `closed` (set by `episode_end`, cleared by `episode_start`); `provide` raises
  `RuntimeError("runner is gone")` once closed, checked before the unknown-decision check, also after a normal finish.
  `act` withdraws its request on `CancelledError` (explicit clause; the `finally` pop stays as the answered path).
- Tests: `test_runner_exception_in_observe_cancels_inflight_and_reraises`, `test_provide_after_runner_death_raises`,
  plus `test_a_failing_episode_end_does_not_hide_the_original_exception` and
  `test_provide_after_a_normal_finish_also_raises`. The scenario needs a human still waiting when `observe` raises: a
  four-agent fixed-pair PD where pair_0 completes (bob's `observe` raises) while carol (human) is outstanding.

## 2026-10-21 — M2 fix-up task 2: test timeouts (new dev dependency)
- **New dev dependency: `pytest-timeout`** (added to the `dev` extra) and `timeout = 30` under
  `[tool.pytest.ini_options]`. It is NOT installed in this sandbox (no PyPI), so here pytest prints
  "Unknown config option: timeout" and the plugin's 30 s cap is unverified; the in-test limits below do not need it.
- `tests/aio.py`: `soon(awaitable)` = `asyncio.wait_for(..., 5)`, `run_test(coro)` = `asyncio.run` with a 10 s cap on the
  whole body. In `tests/runtime/test_runner.py` and `tests/agents/test_adapters.py` every `wait_outstanding`, every await
  of a runner task, and every `asyncio.run` goes through them.
- Verified with the reviewer's mutation (reports broadcast to every adapter, so `ScriptedAgent.observe` raises): the full
  suite under `timeout 120` now ends in 14 s with 19 failed / 274 passed instead of hanging (the runner cleanup of
  task 1 plus the bounded waits). Mutation reverted.

## 2026-10-21 — M2 fix-up task 3: RandomAgent arrays
- `type: "array"` is supported when `items` is a finite set: `enum`, `const`, or an `anyOf` of consts / one-value enums
  (nested `anyOf` is not flattened). The sample is a subset without replacement, in random order, with size uniform in
  `[minItems or 0, min(maxItems or n, n)]`; `minItems` above the number of choices is a `ValueError` (the schema is
  unsatisfiable), not `NotImplementedError`. Arrays of anything else (`$ref`, `integer`, mixed `anyOf`, missing `items`)
  raise `NotImplementedError` naming the whole array fragment. `uniqueItems` is ignored (the result is always unique).
  Tests: a ballot-shaped schema over 200 samples (valid, duplicate-free, sizes 0-3 all occur), bounds, const-shaped items.

## 2026-10-21 — M2 fix-up task 4: PD timeout parameter
- `build_repeated_pd(..., timeout=Fail(), default_move=None)` and `pd_round_layer(timeout, default_move)`. Session params
  must be JSON, so `timeout` also accepts the dict form (`{"kind": "default"}`), parsed with a pydantic `TypeAdapter`;
  a policy object works when calling the factory directly but cannot go through `Session.start` params.
- `DefaultAction` requires `default_move` in `{"C", "D"}` (`ContractError` otherwise). `default_move` is ignored under
  `Fail` (not an error: the brief only constrains `DefaultAction`). **`DropRole` is refused with `ContractError`**: the
  round's outcome needs both players' moves, so a dropped role would crash settlement (my addition to the brief).
  An unparseable policy dict is also a `ContractError`.
- Tests (in `test_runner.py`): `test_pd_deadline_with_default_action_plays_the_default_move` (two rounds, both defaulted
  to `D`, session completes, JSON snapshot/restore works, utilities 10 / 0), bad-argument cases, and a check that the
  default still fails the session on a deadline.

## 2026-10-21 — M2 fix-up task 5: PyPI still unreachable (skipped)
- `pip download pytest-timeout` fails ("No matching distribution found"), tried once, not retried. So
  `pip install -e ".[dev]"`, the full `pytest -q` (3 hypothesis tests), `python -m build` and the wheel-contents check
  (`gao_idl/agents/`, `gao_idl/environments/pd/`) were NOT run, and `pytest-timeout` itself is unverified.

## 2026-10-22 — M3 task 0: PyPI still unreachable; runner cancellation sends `episode_end("failed")`
- `REPORT_M2_FIXUP.md` lists `pytest-timeout`, the full `pytest -q` (3 hypothesis tests) and `python -m build` as unverified,
  so the brief asks to run them if PyPI is reachable. It is not: `pip download hypothesis` fails with DNS
  "Name or service not known" (tried once, not retried). Still unverified: `pip install -e ".[dev]"`, hypothesis tests,
  `pytest-timeout`, `python -m build`, wheel contents, Python 3.11, CI. Local gate as in M2:
  `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages` with `--ignore=tests/core/test_paths_property.py`.
  Baseline before M3: 303 passed.
- Runner: the `except Exception` clause is now `except (Exception, asyncio.CancelledError)`, so a cancelled runner cancels
  and awaits its in-flight tasks, sends `episode_end(terminal="failed", final_utility=None)` to every adapter (an adapter's
  exception becomes a `warnings.warn`, the others still get theirs) and re-raises the cancellation. The brief says "in the
  runner's `finally`"; this is the same effect without a flag, and `finally` still does the unconditional `cancel_all()`.
  This reverses the M2 fix-up call "cancellation sends no `episode_end`". Not covered: a cancellation that arrives while the
  *normal* `episode_end` phase (after the loop) is running can leave later adapters without a notice.
- Test: `test_cancelled_runner_sends_failed_episode_end_to_every_adapter` (two humans, one adapter whose `episode_end`
  raises). Mutation check: restoring `except Exception` fails it.

## 2026-10-22 — M3 task 1: codecs (`learning/codecs.py`)
- **Ambiguity: "raises `ValueError` at construction time ... do this check lazily on first `encode`".** Taken as: argument
  errors (duplicate / empty / malformed paths, `history` not an int >= 0) raise at construction; a field missing from the
  observation raises on the first `encode` for each `(layer, role)` and the success is cached. `DecisionRequest` has no
  observation schema (only `action_schema`), so "not in the request's schema" can only mean "not in `request.observation`".
- **Ambiguity: which reports does `history` see?** `ObsCodec.encode` receives only `since_last` (reports since the previous
  decision) and codecs are stateless (a stateful codec would leak training state into evaluation). So `history=h` uses the
  last `h` reports *of `since_last`*, oldest first, left-padded with `None`. Reports older than the previous decision are
  never seen; with one report per round and `history=1` that is exactly "the last report".
- **Addition: keyword-only `report_fields`** on `FieldObsCodec` (default `None` = same as `fields`, i.e. the pinned
  behaviour). With a single `fields` list every selected field would have to exist in *both* the observation and the report
  payload, which makes `history` almost useless (PD: `own_history` is observed, `other_move` is reported). A report that lacks
  a selected field contributes `None` for it; only observation fields are validated.
- Keys: `freeze` turns JSON values into nested tuples (dict -> tuple of sorted `(key, value)` pairs); `to_jsonable` /
  `from_jsonable` map tuples <-> lists, so `from_jsonable(to_jsonable(k)) == k` for every frozen key. Non-finite floats are
  refused (`nan != nan` would break table lookups). Caveat: `True == 1` and `1 == 1.0` hash alike, so such states share a key.
- `SchemaActCodec` enumerates **required properties only** (as `RandomAgent` does); optional ones are omitted from the action.
  Supported: `enum`, `const`, `boolean`, integers bounded on both sides (`minimum`/`maximum` or exclusive forms), numbers via
  `grid` (values are checked against the schema bounds; duplicates dropped). Everything else, unbounded integers, plain
  strings, arrays, `$ref`, `anyOf`, `multipleOf`, empty domains, non-object schemas, is a `ValueError`; so is a product larger
  than `MAX_ACTIONS = 10_000` (my choice of "small"). `grid` keys that match no number field are ignored (one codec may serve
  several layers). Order is property order, first property slowest; this order decides greedy tie-breaks.
- Added `codec_spec(codec)` = `{"type": class name, "args": codec.config()}` for checkpoints; both codecs have `config()`.

## 2026-10-22 — M3 task 2: `TableAgent` (`learning/qlearning.py`)
- **Issue in the brief (kept as written): truncation bootstraps from `max_a Q(s_K, a)`, the value of the *last decision's own
  state*.** The agent never sees a state after its last decision, so `s_K` is the only available proxy for `s_{K+1}`; it
  treats the continuation as "stay where you were", which is biased whenever the truncated game would have moved elsewhere.
  Implemented literally (`max` over the action keys of request `K`, using `Q` before this update); a better estimate would
  need the environment to disclose a post-horizon state. `"normal"` is always terminal; the two settings are tested numerically.
- **Updates are online; "failed episodes are discarded from learning" is done with an undo log.** Each table write is
  logged as `(key, old value or missing)` and a `"failed"` end rolls the table back exactly (then drops the episode's recorded
  transitions, does not decay ε, does not count the episode in `episodes_seen`, and counts it in `failed_episodes`).
  The RNG is not rolled back. An `episode_start` that finds an unfinished episode (a runner that died without `episode_end`,
  e.g. cancelled before this M3's task 0) treats it the same way and counts it as failed.
- `episodes_seen` and the ε decay (`max(epsilon_min, eps * decay)`, once per episode) only happen when `learning=True`; a frozen
  agent changes nothing, and its `act` uses ε = 0 whatever `cfg.epsilon` says.
- `episode_start` clears `since_last`, the open transition and the dedupe set unconditionally (the brief), so
  `EpisodeContext.keep_episode_memory` has no effect on a `TableAgent`. `keep_parameters=False` clears `q` only (not ε or the
  counters).
- **Reward before the first decision** has no transition to attach to; it is added to the public counter `dropped_reward`
  (reported, not silently lost). Consequence: attaching a fresh `TableAgent` to a *restored mid-episode* session (the runner's
  catch-up delivers old reports first) cannot learn the earlier transitions; learning across a snapshot/restore is unsupported.
- `elapsed` = number of reports (rewarded or not) received between the two decisions. With `discount_by_elapsed=True` the factor
  is `gamma ** elapsed`, so two decisions with no report between them (elapsed 0, e.g. two roles of one agent in a `Par`) are
  not discounted at all. Kept literal ("keep it simple"). Several pending requests of one agent are handled in the order the
  runner dispatches `act`, as consecutive decision points.
- Tie-break: the **first** action in the codec's order (`values.index(max(values))`), so greedy play never depends on the RNG
  (needed for the checkpoint equality test). Every `act` draws one uniform from the agent's generator and, if exploring, one
  `integers(n)`; this stream is part of the checkpoint.
- Reports are checked against the agent id (`ValueError`, like the other adapters) and deduplicated by `report_id`. A reward field
  that is present but not a finite number (bool, string, NaN) is a `ValueError`; absent fields count 0.
- Checkpoint keys beyond the brief's list, needed because `from_checkpoint(d, obs_codec, act_codec)` has no other source for
  them: `format` (`"gao_idl.table_agent/1"`), `agent_id`, `reward`, `epsilon`, `failed_episodes`. `q` is a list of
  `[state, action, value]` sorted by the JSON text of `[state, action]`. `from_checkpoint` also takes keyword-only `learning`
  (default True, like the constructor) and `record_transitions`, refuses mismatching codec identifiers (`ValueError`), and builds
  fresh objects (never aliases `d`). `rng_state` holds 128-bit integers: fine for Python `json`, lossy in parsers that use doubles.
- Additions to the briefed API: `Transition` (frozen dataclass), `TableAgent.record_transitions` / `.transitions` (needed by the
  brief's tests), `.greedy_action(request, since_last=None)` (pure, for tests and inspection), counters `failed_episodes`,
  `dropped_reward`. `QConfig` is validated (alpha in (0,1], gamma/epsilon in [0,1], `epsilon_min <= epsilon`).
- Static guard test: `learning/codecs.py` and `learning/qlearning.py` import nothing from `runtime`, `core.ledger`, `core.store`
  or `composition` (invariant 1). Mutation checks (all reverted): no truncation bootstrap, no rollback, reward not accumulated,
  failed episodes decaying ε each make the corresponding tests fail.

## 2026-10-22 — M3 task 3: trainer and evaluator (`learning/trainer.py`, `learning/opponents.py`)
- **Returns are true ledger utility, read by the trainer, never by the agent.** `train`/`evaluate` report
  `session.ledger.utility(learner)` per episode (the ground truth); the runner is used with the default policy, so the learner
  gets no `final_utility` and learns only from report fields. This is the one place outside `Session`/runner/tests that touches
  the ledger; it is not reachable from adapter code.
- `opponent_factory` is `factory(agent_id, description, episode_seed) -> adapter`, called once per opponent per episode (so a
  `RandomAgent` is reproducible from the episode seed alone: `derive_seed` = blake2b of `episode_seed|agent|purpose`).
  `opponent_factory=None` means `default_opponent_factory()`, which understands the brief's three forms. `scripted:<name>` names
  come from `opponents.SCRIPTED` (`always_cooperate`, `always_defect`, `tit_for_tat`, `truthful_bidder`; extendable through the
  factory's argument). `table:<path>` loads a JSON checkpoint, rebuilds the built-in codecs from its codec identifiers
  (`obs_codec_from_spec` / `act_codec_from_spec`) and seats a frozen `TableAgent` at the opponent's agent id; this needed a
  keyword-only `agent_id` on `TableAgent.from_checkpoint`.
- `evaluate(spec, learner_ckpt, opponent_factory, episodes, seed)` follows the brief; extra keyword-only `obs_codec` / `act_codec`
  are for custom codec classes (the default rebuilds the built-ins from the checkpoint). The table is seated at `spec.learner`
  whatever agent id the checkpoint carries. `spec.episodes` / `spec.seed` are the *training* values and are ignored by it.
- **Isolation:** `evaluate` always builds a new `TableAgent` with `learning=False`; `train` refuses a frozen agent and an agent that
  `evaluate` built (module-level weak sets; explicit `AssertionError`, not a stripped-under-`-O` `assert`). Both wrap the learner in
  `_Pinned`, which forces `keep_parameters=True` in the `EpisodeContext` (the runner builds the context with that default
  anyway, so this only guards against a future default change) and records exceptions from `act`.
- **Why `_Pinned` records errors:** the runner turns an exception in `act` into a timeout (M2). With a misconfigured codec that
  would make every episode fail with only a log warning. The trainer re-raises the first such error as `LearnerError` (cause
  attached) after the episode. Exceptions from opponents keep the runner's behaviour (timeout -> failed episode).
- One `asyncio.run` for the whole training run (not one per episode), so `train`/`evaluate` cannot be called from inside a running
  event loop. The runner's catch-up delivery is irrelevant here (fresh sessions).
- `EvalResult.mean` / `std` are over non-failed episodes (population std, `ddof=0`); `None` if all failed. A failed episode's
  entry in `returns` is the utility accrued before the failure; `terminals` / `failed_episodes` tell them apart.
- `TrainSpec.episodes >= 1`; the learner may not also be listed as an opponent. `TrainResult` / `EvalResult` round-trip through
  `model_dump(mode="json")` / `model_validate` (tested for `TrainResult`).
- Mutation check (reverted): `evaluate` building the agent with `learning=True` fails `test_the_evaluation_agent_is_new_frozen_and_never_updated`.

## 2026-10-22 — M3 task 4: sanity environments (`environments/toy/`)
- Built only from existing pieces (`Primitive`, `Seq`, `Loop`, `Connector`, `SimpleLayer`); no runtime change.
- **Bandit** (`build_bandit(means, agent="agent")`): `Seq("bandit")` of the `pull` layer (role `player`, observation
  `{"n_arms": k}`, action `{"arm": int in [0, k-1]}`, strict int so `"0"` is refused) and the automatic, role-less `settle` layer,
  which draws `rng.random() < means[arm]` from its own path-keyed stream and reports `{"arm", "own_reward"}` to the player (also
  credited to the ledger). The reward is therefore a function of the session seed only; the trainer's per-episode seeds make
  episodes independent. `means` must be a non-empty list of finite probabilities (`ContractError` otherwise). The agent id is a
  parameter (the brief fixes none). Ids: `bandit/pull#player`.
- **Chain** (`build_chain(horizon=3, agent="agent")`): `Loop("chain")` over one `chain_step` layer, ids `chain/loop[i]#player`,
  `max_iter=horizon`, the body leaves through `done` on its last step, so the session ends `normal` and the loop limit (`on_limit="fail"`)
  is never touched. Observation `{"state": position}`; action `{"move": "left" | "right"}`; after each step the player is told
  `{"own_reward", "position"}`.
- **Ambiguity: "reward 1 only on reaching state 2 from the right action twice".** Conservative reading: reward 1 exactly on the
  transition `state 1 --right--> state 2` (`right` clamps at 2, `left` clamps at 0) and 0 on every other step, so a return is 0 or 1
  and the optimum within 3 steps is 1. Three of the eight 3-move sequences reach it (`LRR`, `RRL`, `RRR`: `left` at state 0
  stays in 0). Reading it as "pays on every step that ends in state 2" would make `RRR` worth 2 and contradict the brief's
  "eval return == 1.0".
- The horizon is not in the observation (the brief: observation = state index), so the last step's decision is state-aliased with
  earlier steps; harmless for this task.

## 2026-10-22 — M3 task 5: tests, `slow` marker, CI
- **`slow` marker registered** in `[tool.pytest.ini_options]` (`markers`), so `-m slow` / `-m "not slow"` are warning-free.
  **CI changed minimally** to match the gate in the brief: the matrix job now runs `mypy` on core, composition, agents and learning,
  and `pytest -q -m "not slow"`; a `slow` job (`pytest -q -m slow`, Python 3.13) runs only on a nightly `schedule` trigger. The
  workflow was not executed (no CI here). One test is marked slow: the PD run (1 000 episodes x 5 rounds, about 16 s).
  Timings here: not-slow 407 passed in about 28 s (limit 60 s), slow 1 passed in about 17 s.
- **Reading of `test_random_and_scripted_baselines_run_through_same_evaluate`.** `evaluate` takes a *checkpoint*, so a scripted
  agent cannot be the evaluated learner. Taken as: the random and scripted baselines are the opponent kinds (`"random"`,
  `"scripted:<name>"`) and all go through the same `evaluate` (same code path, same seeds), together with an *untrained* checkpoint as
  the baseline learner (empty table = always the first action, "C"), whose returns are known exactly against the scripted opponents
  (3/round vs a cooperator or tit-for-tat, 0 vs a defector) and statistically against a random one (1.5/round).
- `test_truncation_bootstraps_vs_terminal` drives the agent directly with synthetic requests and reports and ends the episode with
  `EpisodeEnd(terminal="truncated")` (no repo environment produces a truncated session through the runner); the numbers are exact
  (4.3 with bootstrap, 2.5 without). The runner-side derivation of `"truncated"` is covered by M2's `test_terminal_state_reaches_the_adapters`.
- `test_reward_only_from_disclosed_fields` uses `tests/learning/envs.py:pd_without_payoff_report`, which builds the real
  `build_repeated_pd` while `gao_idl.environments.pd.pd_round_layer` is patched (during the factory call only) to a copy of the layer
  whose report omits `own_payoff`. It imports two private helpers of `pd.layers` (`_outcome_model`, `_other`); the ledger is read by the
  trainer for the returns and shows the learner was paid (all returns > 0) while every recorded reward and every Q-value is 0.
- `test_settlement_reward_lands_in_final_transition`: the auction gives the learner exactly one decision, so the only transition is the
  terminal one; the test pins that the reward (own_utility = 8 - 5 = 3.0, delivered after the decision, behind a reward-less bid receipt,
  elapsed 2) is in it, equals the ledger, and yields the numerically expected Q update. A multi-decision settlement variant would need a
  repeated auction, which the repo does not have.
- Hyperparameters were checked on several seeds before pinning (bandit: 6 seeds, eval mean 0.886-0.914; chain: 12 seeds, eval return 1.0
  each; the chain test runs seeds 0-2). The PD learner needs `epsilon_min=0.1`: with 0.02 one round's greedy move stays "C" (untried "D" there),
  giving 23/25 = 4.6 per round, which still meets the brief's >= 4.5 but not "always defects".
- Not verified (no PyPI): the three hypothesis tests, `pytest-timeout` (the `timeout = 30` option still prints "Unknown config option"),
  `pip install -e ".[dev]"`, `python -m build`, Python 3.11, and the CI workflow. The literal `pytest -q` fails at collection on
  `tests/core/test_paths_property.py` (`hypothesis` missing), as in M0-M2.

## 2026-10-22 — M4 task 0: PyPI still unreachable (no httpx here); truncation default is terminal
- `REPORT_M3.md` lists `pip install -e ".[dev,llm]"`, the hypothesis tests, `pytest-timeout` and `python -m build` as
  unverified, so I tried `pip download hypothesis` once: DNS fails ("Name or service not known"), not retried. Still
  unverified: `pip install -e ".[dev,llm]"`, the 3 hypothesis tests, `pytest-timeout`, `python -m build`, wheel contents,
  Python 3.11, CI. **`httpx` is not installed in this sandbox** either, so the real HTTP transport of the OpenAI-compatible
  backend can only be type-checked here, never executed. Gate substitute as in M3:
  `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages` with `--ignore=tests/core/test_paths_property.py`.
  Baseline before M4: 407 passed (not slow), 1 deselected.
- `QConfig.bootstrap_on_truncation` now defaults to `False` (a truncated episode is terminal). The `True` branch is unchanged;
  its docstrings (field and module) now say it bootstraps from `max_a Q(s_K, a)`, the agent's *last* state used as a proxy for
  the unobserved next state, and is biased when the truncated game would have moved on. `test_truncation_bootstraps_vs_terminal`
  already set the flag explicitly in both cases (via `_truncated_episode(bootstrap)`); it gained the assertion that the default
  is `False`. `test_qconfig_validation` (which pins the full default dump) was updated to `False`. No other test relied on the old
  default.

## 2026-10-22 — M4 task 1: backends (`llm/backend.py`, `llm/openai_compat.py`, `llm/mock.py`)
- **`httpx` is not installed here, so the real HTTP call is never executed.** To keep everything else testable the client is split:
  `OpenAICompatBackend` holds all behaviour (request shape, `response_format` fallback, retries, backoff, scrubbing, response
  parsing) and talks through an injectable `transport(url, headers, body, timeout_s) -> HttpReply`. The default transport,
  `httpx_transport` (~8 lines, `httpx.AsyncClient.post`, `httpx.TransportError` -> `TransportFailure`), is the only code that touches
  httpx; it imports it **inside the function**, so `import gao_idl.llm` never needs httpx (tested in a subprocess). That function is
  type-checked only (`[[tool.mypy.overrides]] module = "httpx"` with `ignore_missing_imports`, so mypy is happy both with and without
  httpx installed) and was never run. Everything else is tested through a fake transport. Extra constructor keywords beyond the brief
  (all keyword-only): `max_retries=3`, `backoff_s=0.5`, `sleep`, `transport`.
- **Reading of "Transport retries: 3":** three *retries*, i.e. up to 4 requests, with delays 0.5, 1.0, 2.0 s (`backoff_s * 2**k`). Retried:
  429, 5xx, timeouts and any transport exception. Other 4xx fail at once. The `response_format` fallback is not a transport retry.
- The 400 fallback fires when the body contains `response_format` and the request had one; it is not remembered (every call tries
  the structured form first). `Response.parsed` is set only when the server accepted `response_format` and the content parses to a
  JSON object; otherwise `None`.
- **Credentials:** the callable is invoked for each request (retries included); the value lives in a local and the request header. It,
  and the values of extra headers whose name contains `auth`/`key`/`token`/`secret`, are redacted from every error message; the
  `Authorization`/`x-api-key` header pattern and `Bearer <token>` are redacted by `scrub()` in any case, and `BackendError.__init__`
  applies `scrub()` itself, so no code path can build a `BackendError` that prints a bearer token. `Response.raw` is the server's JSON
  with all strings scrubbed (shape unchanged). `repr(backend)` shows only the URL and model.
- `content: null` becomes empty text (the codec then fails to parse it and the agent repairs); non-string content (parts lists) is a `BackendError`.
- `llm.backend.current_decision` (a `ContextVar`) is set by `LLMAgent` around each backend call. `LLMBackend.complete` has no
  decision argument and `ReplayBackend` must serve "per `decision_id`"; this is how it learns which decision is asked about. The mock
  records it in `MockCall.decision_id`.
- `MockBackend` raises `MockExhausted` (not a `BackendError`) when the script runs out, so a test bug is not silently retried as a transport error.

## 2026-10-22 — M4 task 2: codec (`llm/codec.py`)
- `DecisionCodec.render(request, memory, rejections)` has exactly those parameters (tested via `inspect.signature`), so nothing else can
  be passed. **Render contract I had to add** (the brief does not say how the agent knows what to remember): the result is
  `[system messages..., *memory, turn messages...]`; the agent stores `render(request, [], [])` minus the leading system messages as
  the turn it remembers. Codecs must therefore render the same turn for the same request. Pinned `Protocol` unchanged.
- `DefaultCodec` is split into `system_prompt(request)` and `task_prompt(request)` hooks so environment codecs override text, not
  mechanics. Rejection reasons are appended to the *task* message ("Your previous reply was rejected: - ... Reply again with corrected
  JSON only"); the rejected reply itself is not echoed back (the codec is only given the reasons).
- `parse`: `response.parsed` if present, else the **first balanced `{...}`** in the text (string/escape aware) loaded with `json.loads`.
  "First" is literal: if that first block is not valid JSON (`"{maybe} then {...}"`), it is a `CodecParseError`, not a search for a
  later one. The dict is returned unvalidated and uncoerced (`{"amount": "5"}` stays a string).
- `ReportRenderer` and `DefaultReportRenderer` ("Report (<layer>): <payload JSON>") live here; ids and paths are not in the prompt.

## 2026-10-22 — M4 task 3: memory (`llm/memory.py`)
- `FullTranscript`, `Window(k)` and `NoMemory` (the brief's "`None` (stateless)": `LLMAgent(memory=None)` maps to it) share a small
  `Memory` protocol: `append(messages)`, `messages()`, `clear()`, `fresh()`. `fresh()` exists because the brief's default
  `memory=Window(20)` is a shared default argument; `LLMAgent` takes `memory.fresh()`, so the default instance is only a template and
  two agents never share a conversation.
- `Window(k)` stores only the last `k` messages and accepts `k = 0` (remembers nothing); `k` must be an int >= 0 (`ValueError`).
- What gets appended (turn prompt, own action, reports addressed to the agent) is decided in `LLMAgent`, see task 4.

## 2026-10-22 — M4 task 4: `LLMAgent`, local validator, `AgentFailed` / `agent_failed`
- **`AgentFailed` lives in `core/errors.py`, not in `llm/`.** The brief has the runner recognise it, but nothing in `core`,
  `composition` or `runtime` may import `gao_idl.llm`. It is a `GaoError` with `.reasons: list[str]`.
- **`AgentFailedEvent` (trace kind `agent_failed`: `decision_id`, `agent_id`, `reasons`)** is added to `core/events.py` and to the trace
  union. It is a trace-only event: `Session.record_agent_failure(decision_id, agent_id, reasons)` (new, called by the runner just before
  `session.timeout`) emits it at `t = clock.now` **without ticking the clock** and never logs it, exactly like `ActionRejected`
  (amendment 2). So replay/restore cannot reproduce it, and `check_determinism` now excludes `agent_failed` as well as
  `action_rejected`. Consequence for invariant 4 worth knowing: the *effect* of a failure (`TimedOut` -> the layer's policy) is in the
  log and replays; the *reasons* are only in the live trace (the failed attempts' raw replies are in the log, task 5, which is
  what a replay-with-agents regenerates them from). The order in the trace is `agent_failed` first, then `action_defaulted` /
  `role_dropped` at settle (amendment 1).
- Runner change is only in `_settle`: for `AgentFailed` it calls `record_agent_failure` before `timeout`. A `SchemaError`/`Infeasible`
  from `submit` still goes through the existing one-retry rule.
- **Redispatch detection.** The brief says to keep `last_decision_id`; one agent can hold several pending decisions at once (invariant 8)
  and the runner runs them as concurrent tasks, so a single "last" id cannot tell a re-dispatch from an interleaving. The agent keeps a
  set of the decisions it answered this episode (`_acted`, cleared at `episode_start`) and also exposes `last_decision_id`. A decision
  seen again gets the rejection reason `"previous action was rejected as infeasible"` (verbatim from the brief). The runner cannot say
  whether the session refused for schema or feasibility, so a (should-not-happen) `SchemaError` re-dispatch gets the same wording.
- **Memory rules.** After a successful `act` the turn (the prompt without system messages, from `render(request, [], [])`) and the agent's
  action as compact JSON are held *provisionally*; they are committed to memory when the next report arrives, the next different
  decision is asked, or the episode ends. A re-dispatch of the same decision drops its provisional turn first, so a rejected action is
  not remembered and the prompt is not duplicated. Rejected replies never enter memory; only the final accepted action does. Corner
  case: if another decision starts between an answer and its re-dispatch, the first answer was already committed and the turn is
  remembered twice (the runner re-dispatches within the same step, so this needs unusual scheduling).
- `episode_start` clears the dedupe set, the acted set and (unless `EpisodeContext.keep_conversation`) the memory; `CallRecord`s are
  cumulative across episodes. A fresh agent attached to a restored session only learns the earlier *reports* (runner catch-up), not its
  own earlier actions (those are not reports), like `TableAgent`.
- `CallRecord` fields: `decision_id, attempt, latency_s, usage, model, repair_reason, failure, prompt, response`. The brief names
  `repair_reason`; I read it as "why this call was a repair" (previous attempt's problem, or the infeasible note) and added `failure`
  ("why this call's own result was rejected") so both readings are available. `prompt`/`response` only with `record_text=True`.
- A `BackendError` counts as a failed attempt (the model is **not** told about it: transport problems are not rejection reasons) and
  the retry uses the same prompt. Any other exception from the backend (including `MockExhausted`) propagates, so the runner turns it
  into a timeout and a warning, with no `agent_failed` event.
- **Local validator (`llm/validate.py`)** — `against_json_schema(value, schema) -> list[str]` (empty = valid) covers the briefed
  keywords plus local `$ref` into `$defs`; everything else is ignored (a pre-check, the layer is the authority). Strict, no coercion:
  `"5"` is not a number, `5.0` is **not** an integer (stricter than JSON Schema, matching pydantic strict mode), a bool is neither,
  `True` does not equal `1` in `enum`/`const`, and `NaN`/`inf` are not numbers.

## 2026-10-22 — M4 task 5: recording raw replies in the input log; `ReplayBackend`
- **`ExternalRecorder` lives in `agents/base.py`** (runtime-checkable protocol, `drain_external() -> list[dict]`, re-exported from
  `gao_idl.agents`), not in `llm/`, for the same import-direction reason as `AgentFailed`: the runner may not import `gao_idl.llm`.
  `LLMAgent` implements it.
- **Envelope key.** One agent can have several decisions in flight and the runner runs them concurrently, so a single shared buffer
  would mix them. Each drained item therefore carries `"decision_id"`; the runner pops that key and passes the rest to
  `session.record_external(decision_id, payload)`. The payload is the briefed `{"attempt", "text", "parsed", "usage", "model",
  "latency_s"}`, `"raw"` only with `LLMAgent(record_raw=True)`.
- **Addition: failed calls are logged too.** A transport error is recorded as `{"attempt": i, "error": <scrubbed message>}`. Without
  it a replay would hand the next recorded reply to the first attempt and diverge. `ReplayBackend` raises `BackendError(error)` for
  such an item, so the agent repeats the same failure. Not in the brief; the payload dict is free-form.
- Runner: after a flight finishes (answer, exception or `AgentFailed`) and for deadline expiries (after the cancel), the agent's buffer
  is drained and logged **before** `submit` / `timeout` for that decision. A drain may also write items of other in-flight decisions
  of the same agent; they are still logged before those decisions' own submissions. Buffers are not drained when the whole run dies.
  A refused submission (`Infeasible`) is not in the log, but its raw reply is (the log shows both replies, then one `Submitted`).
- **`ReplayBackend(session_log)`** takes the list of input events (not a `Session`). `complete()` has no decision argument, so it
  reads `llm.backend.current_decision` (set by `LLMAgent` around each call) and serves the n-th recorded payload for that decision.
  Outside an agent call, or past the end, it raises `ReplayExhausted` — a `DeterminismError`, deliberately **not** a `BackendError`,
  so the agent does not retry it and a divergence is loud. `remaining()` / `served` are for assertions. Round trip tested through
  `json.dumps(snapshot)`: a fresh `Session` driven by `LLMAgent`s over `ReplayBackend`s writes the identical input log (payloads
  included) and an identical trace (including `agent_failed` and its reasons), with zero mock calls. Also with a `Par` (4-player PD).
- `Session.replay` is unchanged: it calls `advance()` before each `ExternalResponse` (it is not a submission), which differs from the
  live batching but cannot change the trace (Par applies inputs one at a time; amendment M1-1) — confirmed by the `Par` test.
- Mutation check (reverted): not draining/recording in the runner fails 7 tests.

## 2026-10-22 — M4 task 6: environment codecs (`environments/auction/llm.py`, `environments/pd/llm.py`)
- `AuctionCodec`, `AuctionReportRenderer`, `PDCodec`, `PDReportRenderer`. They subclass `DefaultCodec` / `DefaultReportRenderer` and only
  override the prompt text; they import nothing but `gao_idl.core.schemas`, `gao_idl.llm.*` and `json` (tested), and are not imported
  by the environments' `__init__`, so the environments stay usable without the `llm` package.
- **The prompts state only what the observation says.** The auction observation has the value, the rule name, the bidder list, a
  tie-break tag and a disclosure tag; the prompt turns those tags into sentences (a lookup table in the codec, a reader's gloss on the
  tag, not new information) and does **not** state a payoff or utility rule or a reserve, because the observation omits them. The PD
  prompt does not give a payoff table (the observation has none); the player learns its own payoff from the report after each round.
  A model therefore plays the PD without knowing the payoffs. That follows the brief ("do not reveal the payoff matrix if the
  observation omits it") but makes the PD prompt weak; whether the PD observation should carry the payoffs is a game-design question for you.
- The source-grep test checks the lowercase source for `ledger`, `store`, `session`, `outcome` (docstrings and comments included), which
  is why the module docstrings avoid those words. Sentinel test: bob's prompts never contain alice's value `7777.5`; a control against
  `tests.runtime.envs:bidding_leaky` shows the check does find a leak.
- Round-robin PD: the observation is already per pair, so a prompt shows history only against the current opponent (empty for a new
  one). Opponent names appear in no PD prompt (the observation does not carry them); the auction prompt names the other bidders because
  the observation's `bidders` list does.

## 2026-10-22 — M4 task 7: tests (`tests/llm/`), CI
- Every test named in the brief exists under that name, in `tests/llm/`: `test_default_codec_renders_only_authorized_fields` (parametrised
  over `DefaultCodec` and `AuctionCodec`, sentinel 7777.5, plus a whole-run check and a control against `bidding_leaky`),
  `test_structured_output_path_and_fallback_text_path`, `test_invalid_json_then_repair_succeeds`, `test_schema_violation_repair`
  (as `test_schema_violation_repair_is_caught_locally_before_submit`), `test_infeasible_from_session_triggers_runner_redispatch`
  (uses `tests.runtime.envs:bidding_capped`, which rejects bids above 10), `test_repairs_exhausted_then_timeout_policy`,
  `test_transport_error_retries_then_fails`, `test_external_responses_in_log_and_replay_without_calls`,
  `test_window_memory_bounds_messages`, `test_no_secret_in_logs_or_snapshot`, `test_mixed_population_llm_table_scripted_pd`, and
  `tests/llm/test_live.py` (skipped unless `GAO_LLM_BASE_URL` is set; also needs httpx; **never run here**).
- **Reading of the replay test:** "replay with `ReplayBackend`; traces equal; `MockBackend.calls` on a fresh mock is empty" is done as:
  run live with two mocks; push the snapshot through `json.dumps/loads`; drive a *fresh* `Session` with `LLMAgent`s over `ReplayBackend`s
  and compare trace, input log (payloads included) and ledger; and separately `Session.restore(snapshot)` (no agent at all) gives
  the same trace. The "fresh mock" is a mock handed to nobody; its empty `.calls` is trivially true, the real evidence is that the
  replayed agents have only a `ReplayBackend` and `served` equals the recorded replies. The same is repeated for a 4-player PD in
  parallel branches (a `Par`) and for a session where a decision failed (so `agent_failed` and its reasons are regenerated).
- **Reading of `test_mixed_population_llm_table_scripted_pd`:** the PD needs an even population, so four agents: an `LLMAgent`
  (mock), a learning `TableAgent`, a `ScriptedAgent` and a `RandomAgent`, round-robin, 3 rounds. "Ledger consistent" is checked as: each
  agent's ledger utility equals the sum of the `own_payoff` in the reports it received, and the total equals the sum of all of them.
- Extra tests beyond the brief: a concurrency test (one agent holding two roles in one layer, so two decisions in flight; a backend
  that yields the event loop mid-call and answers by `current_decision`; checks raw replies are logged under the right decision before
  that decision's submission and that the run replays), a leak test (a server reply echoing the credential is scrubbed from the
  logged `raw`), static import-direction tests (nothing below `llm/` imports it except the two environment codec files; `llm/` imports
  nothing from `runtime`, `composition`, `core.ledger`, `core.store`), the gate's httpx check as a subprocess test, and
  validator/memory/codec/backend unit tests.
- `tests/llm/helpers.py` holds the small builders. All awaits go through `tests.aio.run_test` (`wait_for`, 10 s) or `asyncio.wait_for`.
  No test sleeps in real time (`asyncio.sleep(0)` and an injected no-op `sleep` for backoff only).
- **CI** (`.github/workflows/ci.yml`, not executed): installs `.[dev,llm]`, mypy additionally covers `src/gao_idl/llm`, and the httpx
  import check from the gate runs as a step; the nightly slow job also installs `llm`. This is the gate of the brief and no more.
- Mutation checks (all reverted): runner not logging externals -> 7 tests fail; runner not emitting `agent_failed` -> 5 fail;
  agent not noticing a re-dispatch -> the infeasible test fails; validator accepting numeric strings -> 2 fail.

## M4 fix-up tasks 1-2: payoffs and reserve are observable (2026-10-22)

- **PD** (`build_repeated_pd(disclose_payoffs=True)`): `PDState.disclose_payoffs` carries the flag to the layer; `PDObs.payoffs` is the
  state's table. When the flag is false the key is *absent* from the dumped observation (a wrap serializer drops a `None`, the same
  device as `all_bids` in the auction report), not `None`. `PDCodec` renders "Payoffs (your move, their move: your payoff, their
  payoff)" with four rows only when the key is there. The flag must be a bool (`ContractError` otherwise). It is a plain JSON param, so
  session `env_ref` params stay replayable. Default is `True`, so default PD runs now show payoffs to their players; the M4 prompt test
  that asserted no table now runs with `disclose_payoffs=False`.
- **Auction**: `BiddingState.reserve` and `BidObs.reserve: float | None` (here the key is always present, `None` meaning no reserve, as the
  brief says). `build_auction` feeds the same `reserve` to the bidding state and to `settlement_layer`, so the two cannot disagree;
  `build_bidding_only` has no settlement and no reserve. `bid_disclosure` strings are unchanged. `AuctionCodec` says "Reserve price R:
  bids below R are ignored." or "There is no reserve price.".
- Reading: the brief says "no digit from the table" for the undisclosed PD prompt. The round index and history legitimately
  contain digits, so the test checks the round-0 prompt with its "This is round N" line removed.

## M4 fix-up task 3 (M4b): Anthropic Messages API backend (2026-10-22)

- **Commit order.** 3b (`schema_transform.py`, with its unit tests) was committed before 3a (`anthropic.py`), because 3a imports it;
  the other two are in brief order. The brief's `httpx.MockTransport` tests need `httpx`, which this sandbox lacks, so
  `tests/llm/test_anthropic.py` drives the backend through the same injected fake transport as the OpenAI-compatible tests (the real
  request building, retry and parsing code all run) and adds two `httpx.MockTransport` tests behind `pytest.importorskip("httpx")`
  (they patch `httpx.AsyncClient` so the real `httpx_transport` runs). Those two were **never executed** here.
- **API facts used** (from the brief, checked against the live docs there on 2026-10-22; I could not re-check them: no network):
  `POST {base_url}/v1/messages`; headers `x-api-key`, `anthropic-version: 2023-06-01` (the default of `api_version`, overridable),
  `content-type: application/json`; system text in the top-level `system`; structured output via `output_config.format`
  `{"type": "json_schema", "schema": ...}`; no beta header, no `output_format`, no tools. `temperature` and `max_tokens` are always
  sent. If the live docs disagree with this, the docs win; nothing here depends on a field the brief did not name.
- **Messages.** System messages join with a blank line; consecutive same-role messages merge with a blank line. A request with no
  user/assistant message raises `BackendError`. A first `assistant` message is passed through as is (not adjusted).
- **Parsing.** First `text` block (other block types, e.g. thinking, are skipped). `parsed` is set only for a JSON *object*; a
  `stop_reason` of `refusal` or `max_tokens` forces `parsed=None` (the text is still returned, `raw["stop_reason"]` has the reason).
  `usage` keeps only `input_tokens` and `output_tokens`. `Response.raw` is the whole reply with the credential scrubbed.
- **Retries** reuse the OpenAI-compatible policy: 429, 529 and every 5xx plus transport failures, `max_retries=3` retries (4 requests,
  delays 0.5/1/2 s); other 4xx never retry. The credential is fetched per request and scrubbed (value, `x-api-key` header text).
- **Stripped schema features** (`to_anthropic_schema`): `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`,
  `minLength`, `maxLength`, `pattern` (always dropped, even simple ones, as the brief prefers) and `maxItems` are removed because the
  API's schema subset does not accept them; `minItems` above 1 is clamped to 1. Each is appended to the node's `description` as
  `"(minimum: 0)"` so the model still reads it. Objects lacking `additionalProperties` get `false`; an existing value (for example
  the schema a `dict[str, float]` field emits) is kept, because the brief says only "lacks it". `$defs`/`$ref`, `enum`, `const`,
  `anyOf`, `required`, `default`, `title` and `description` are untouched. The walk visits subschema positions only, never
  `enum`/`const`/`default` data. Pure and idempotent.
- **The local validator is the authority.** The transform applies to the request only. `LLMAgent` validates against the original
  schema (`llm/validate.py`, unchanged), so a `{"amount": -1}` the API accepts is still rejected and repaired (a test covers this).
- **Not changed:** `OpenAICompatBackend`, the validator, the agent, the runner. `OpenAICompatBackend`'s `HttpReply`,
  `TransportFailure`, `httpx_transport`, `RETRYABLE_STATUS` and `_scrub_json` are imported by the new module (one private helper
  crosses modules inside the package).
- `gao_idl.llm.__init__` now exports `AnthropicBackend` and `to_anthropic_schema`. Importing `gao_idl.llm` still never imports
  `httpx` (checked by the existing subprocess test).
- Live smoke test `tests/llm/test_live_anthropic.py` (`GAO_ANTHROPIC_API_KEY`, optional `GAO_ANTHROPIC_MODEL`, default
  `claude-haiku-5-5`, and `GAO_ANTHROPIC_BASE_URL`): skipped here; left for the maintainer.

## 2026-10-22 — M5 task 0: brief mismatch, no numbered tasks, PyPI still unreachable
- **The request names `M5_llm_agents.md`; the attached brief is `M5_participatory_budgeting.md`** (M4 was the LLM milestone, and the
  M5 attachment is the PB institution). I treated the attached PB brief as the M5 brief. If a different M5 brief was intended, none
  of this work is from it.
- **The PB brief has no numbered tasks and no "task 0".** The user's instruction asks for "task 0" first and one commit per task, so I
  defined the tasks myself, in the brief's own order: task 0 = this note (inherited/environment items, no code); task 1 = schemas and
  the four pure funding rules with their tests; task 2 = the four layers (nomination, eligibility, voting, funding factory);
  task 3 = the `build_pb` composite; task 4 = the institution tests; the report is the last commit. Each commit message is the title.
- `pip download hypothesis` fails ("No matching distribution"), tried once, not retried. So `pip install -e ".[dev,llm]"`, the
  hypothesis tests (the 3 inherited ones and the 2 new ones in `tests/environments/test_pb_rules_property.py`), `pytest-timeout`,
  `python -m build`, Python 3.11 and CI were NOT run. **`httpx` is not installed either**, so the OpenAI-compatible and Anthropic
  HTTP transports can only be type-checked, and the two `httpx.MockTransport` tests stay skipped. Gate substitute as in M4:
  `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages` with `--ignore=tests/core/test_paths_property.py` and
  `--ignore=tests/environments/test_pb_rules_property.py`. Baseline before M5: 553 passed, 4 skipped, 1 deselected (not slow).
- No new dependency is added in M5 (`fractions` and numpy only).

## 2026-10-22 — M5 task 1: schemas and the four pure funding rules (`environments/pb/schemas.py`, `rules.py`)
- **Common conventions (documented in the module docstring).** `rule(projects, ballots, budget) -> list[str]`, result in ascending
  *natural* id order (`p2` before `p10`, reusing `role_sort_key`; plain string order would put `p10` before `p2`). `ballots` must map
  every voter, abstainers with `[]`: `n = len(ballots)`. Unknown or repeated ids in a ballot are a `ValueError` (not ignored).
  **Projects nobody approves are never funded, by any rule** (my reading of "take if fits": otherwise greedy would spend leftover
  money on projects with zero support). Projects costing more than the budget are never candidates. Costs and budgets are compared as
  exact decimal fractions (`Fraction(repr(x))`), so `0.1 + 0.2 <= 0.3` holds.
- **Greedy:** approvals desc, cost asc, id asc; take if it fits.
- **Knapsack:** exact DP over integer cents (numpy rows). Costs must be whole cents (`ValueError`); the *budget* is floored to cents,
  which is exact for whole-cent costs (the brief says "assert exactness"; asserting it for the budget would refuse e.g. 10.005 although
  the answer is well defined). Refuses `> 60` projects and tables over 50 M cells. Tie-break among optimal sets: lexicographically
  smallest ascending id list (include a project whenever an optimum still exists with it).
- **MES (the one documented variant):** cardinality utilities (every approved project is worth 1 to each approver), so ρ-affordability
  is `sum(min(b_i, ρ)) >= cost`, smallest ρ first, supporters pay `min(b_i, ρ)`, exact rationals. The brief names no utility; cost
  utilities are excluded by "Do not", and approval counts are what greedy and knapsack maximise. **Add1 completion** follows Peters,
  Pierczyński, Skowron et al. as summarised in Boehmer et al., "Participatory Budgeting: Data, Tools, and Analysis" (arXiv:2305.11035,
  §2.2): start at `b/n`, if the outcome is not exhaustive raise every initial endowment by one *unit* and rerun from scratch, until
  exhaustive or until the next rerun would exceed `b`. That paper never says what a "unit" is or which run is returned; I took **one
  currency unit per voter** and **the last run whose cost is within `b`** (round 0 never overspends). *Exhaustive* = no unfunded
  candidate fits the money left. Then **greedy completion** adds whatever still fits in greedy order. Add1 is capped at 100 000 reruns
  (`ValueError`); with costs in the millions and few voters that would be reached. EJR holds for the completed rule on the unit-cost
  instances tested: an Add1 run at endowment `k'/n >= k/n` is MES for a larger committee size, which implies EJR for `k`, and
  greedy completion only adds projects.
- **Sequential Phragmén:** money formulation. `load_i` = money voter `i` has spent; a project is bought at time
  `(cost + sum of supporters' loads) / |supporters|`; smallest time next, supporters' loads become that time. **When the next project
  does not fit the money left it is discarded and the rule goes on** (not "stop at the first that does not fit"); the brief is silent
  and this matches greedy's "take if fits". Pinned by a hand example.
- **Worked examples, and how they were obtained.** The shell has no web access, so I read the papers through the WebFetch tool (a
  summarising model, not the PDFs). Every fixture below was *also re-derived by hand from the definitions* before being pinned, and
  matched, so I trust the numbers but not the transcription of the figures:
  Peters & Skowron, *Proportionality and the Limits of Welfarism* (arXiv:1911.11747), Example 2 (n = 15, k = 4) — **the profile figure
  was not in the extracted text; the tool reconstructed it from the prose** (c1, c2: v1–v12; c3: v1–v10; c4, c5: v6–v15). The stated
  q-values 15/48, 15/48, 15/40, 1 and the committee {c1, c2, c3, c4} reproduce, with the paper's own tie choice (c3 over c4/c5) equal
  to the smaller-id tie-break. Rey & Maly, *The (Computational) Social Choice Take on Indivisible Participatory Budgeting*
  (arXiv:2303.00621): the MES example with cardinality satisfaction (outcome {p1, p2, p3}, bought p2, p1, p3) and the Phragmén example
  (outcome {p1, p2}; the tool noted a garbled cell for p4, which I did not use). Brill, Freeman, Janson, Lackner, *Phragmén's Voting
  Methods and Justified Representation* (AAAI 2017; Math. Programming 2024), Examples 4.3/4.6 (k = 3) and 6.3 (k = 4, final loads
  3/4, 3/4, 0, 0, 3/4, 3/4, 1/2, 1/2). **Example numbers are those of the 2024 journal version as relayed by the tool**, the AAAI
  version may number them differently. **Not verified against the PDFs:** all of the above. For greedy and knapsack there is no
  literature example in the brief's list; their fixtures are mine (labelled HAND in the tests), plus the Rey–Maly instance computed by
  hand. Two MES fixtures (add1, completion) are hand-made too; the Add1 one was found by a random search and re-derived.
- `test_mes_ejr_on_small_instances` enumerates all multisets of ballots for up to 4 voters and up to 4 unit-cost projects with budgets
  1–3, plus 1 500 seeded 5-voter/5-project profiles, for plain MES and for MES with completion (about 6 s). The exhaustive 5-voter
  enumeration is `@pytest.mark.slow` (about 25 s). EJR was checked with the standard unit-cost definition (group size `>= ell*n/k`,
  `>= ell` commonly approved projects); no violation was found, so the implementation was not touched on that account.
- **Hypothesis:** `test_knapsack_matches_bruteforce` and `test_all_rules_within_budget` are in
  `tests/environments/test_pb_rules_property.py` (thin wrappers over the same check functions as the numpy-seeded
  `*_randomized` tests in `test_pb_rules.py`, which always run: 400 and 200 instances). The hypothesis module was NOT executed here.
- Schemas: `NominationState.rule` is a validated `str` and is **not forwarded** through the pipeline (the funding layer is chosen by the
  factory), so no pre-funding trace event depends on the rule. `Nomination.cost` is strict (numeric strings and bools refused, like
  `Bid.amount`). `ProjectsExit`/`EmptyExit` carry `voters` and `proposers` (the public audience), and `VotingState`/`BallotsExit`
  gain `proposers: list[AgentId] = []` — an addition to the brief's field lists, needed so the funding layer can address proposers.
  `ballot_schema` is a cached `create_model` over `Literal[tuple(ids)]` with a no-duplicates validator and `extra="forbid"`; with a
  single project pydantic emits `const` rather than `enum` for the item.
- Mutation checks (all reverted): MES tie to the larger id, Phragmén stopping instead of skipping, knapsack preferring exclusion, no Add1,
  float instead of decimal costs each fail exactly the test that pins them.

## 2026-10-22 — M5 tasks 2-3 merged: the four layers and `build_pb` (`environments/pb/layers.py`, `__init__.py`)
- **Plan change (task 0 listed layers and composite as two tasks):** a layer cannot be exercised without the composite, so the layers and
  `build_pb` are one commit with a smoke test (`test_every_rule_completes_the_pipeline`, `test_the_process_is_well_wired`); the
  institution tests are the next commit.
- **Layout.** `Seq("pb": nomination, proposed -> Seq("after_nomination": eligibility, eligible -> Seq("after_eligibility": voting,
  voted -> funding_<rule>)))`: the brief's chain needs nested `Seq`s because a route's continuation is one process. The two inner names
  are path elements, so ids read `pb/nomination#proposer:0` and `pb/after_nomination/after_eligibility/voting#voter:1`.
  Funding layers are named `funding_<rule>` ("one layer per rule"); `FundingOutcome.rule` carries the plain rule name.
  The rule is chosen in exactly one place: `FUNDING[rule]` in `build_pb` (`FUNDING` is `rules.RULE_FUNCTIONS`).
- **`none` routing (reading).** "The `none` ports re-export at the top level" is taken literally: neither `none` port is routed, so `Seq`
  re-exports both (same `EmptyExit` class, so no schema conflict). The brief also requires a public report when everyone dropped out; the
  eligibility layer never runs then, so **the nomination layer itself delivers the public "none" report** (an `EligibilityReport` with
  `status="none"`, empty lists) to every voter and proposer when nobody nominated, and delivers no report otherwise. The eligibility layer
  does the same when nothing survives the screen.
- **Nomination timeout.** Default `DropRole`; `Fail` is accepted; **`DefaultAction` is refused (`ContractError`)**: there is no neutral
  nomination, a default one would be an invented project (invariant 6). `timeout_nomination` also takes the JSON dict form so session params
  stay JSON. Voting uses `Fail` (the brief gives voting no timeout parameter; a silent abstention would be invented).
- **Roles/ids.** Proposers and voters are sorted by agent id and numbered `proposer:i` / `voter:i`; duplicates inside one list are a
  `ContractError`, the same agent in both lists is allowed. At least one voter is required (an empty electorate is a configuration error);
  an empty proposer list is allowed and simply exits `none`. Project ids `p{i}` follow sorted `(proposer, title)`.
- **Eligibility** rejects `cost > budget` or `cost > max_cost` with reason `over_budget`, `over_max_cost` or `over_budget_and_max_cost`
  (the brief says "reason" only). Surviving projects keep their nomination ids (no renumbering) so ballots refer to stable ids.
- **Ballots** are stored sorted in natural id order in `VotingOutcome` (a ballot is a set; the submitted order stays in `LayerCompleted.actions`).
  The voting layer credits no utility (the brief defines none); funding does not either.
- **Reports.** Eligibility/none: `EligibilityReport`, byte-identical for every recipient. Voting: `VotingReceipt(own_ballot)` to each voter.
  Funding: `FundingReport{public, private?}`; `public` = rule, budget, funded `(id, title, cost)`, spent, and `ballots` only with
  `reveal_ballots`; `private` (key omitted when absent) = `own_value_of_funded`, only for voters present in `private_values`, a missing
  project value counting 0. **No report contains approval counts or any other rule detail** (those live in `FundingOutcome.details`, i.e. the
  trace), so a voter's ballot reaches other voters' streams only through the funded set and `spent`.
- `reveal_ballots` is closed over by `funding_layer(name, fn, *, reveal_ballots=False)` and `private_values` by the connector into voting
  (neither is in the brief's `NominationState`); the whole process is still a function of the `env_ref` params (invariant 3).
- `funding_layer` verifies each rule's answer (known ids, no repeats, exact budget check) and raises `ContractError` on a violation, so a bad
  rule cannot silently overspend.
- `ballot_schema(...)` with a single eligible project gives `{"const": "p0"}` for the item (pydantic's rendering of a one-value `Literal`)
  instead of a one-element `enum`; `RandomAgent`, the local LLM validator and pydantic all handle it. With two or more projects it is an `enum`.
- Not done, by the brief's "Do not": no runtime/composition change, no cardinal ballots, no cost-utility rules.
