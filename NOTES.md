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
