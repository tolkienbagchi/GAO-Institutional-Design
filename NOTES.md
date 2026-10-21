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
