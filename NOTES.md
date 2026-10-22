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
