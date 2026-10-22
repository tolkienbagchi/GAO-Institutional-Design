PyPI was unreachable again (DNS failure on `pip download pydantic`, tried once and not retried), so `pip install -e ".[dev]"`, the 3 hypothesis tests, `python -m build` and Python 3.11 were NOT run. Everything below was verified with the standalone ruff/mypy/pytest on Python 3.13 and the system packages (`PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`).

# REPORT — M2: repeated PD, agent adapters, runner

## Gate status (read this first)

**The gate as literally written was NOT met**, for the same reason as M0/M1: the sandbox cannot install anything, and `pytest -q` fails at collection on `tests/core/test_paths_property.py` (`hypothesis` missing).

| Gate command | Result |
|---|---|
| `ruff check .` | Pass: `All checks passed!` |
| `ruff format --check .` | Pass: `81 files already formatted` |
| `mypy src/gao_idl/core src/gao_idl/composition src/gao_idl/agents` | Pass (strict, pydantic plugin): `no issues found in 26 source files`. `mypy src/gao_idl` (also `environments`, `runtime`, `testing`) passes too: 40 files. Python 3.13 only. |
| `pytest -q --ignore=tests/core/test_paths_property.py` | **289 passed in 3.3 s** (gate: ≥ 90 tests, < 30 s). 67 core, 97 composition, 58 runtime, 51 environments, 14 agents, 2 top-level. With the 3 hypothesis tests the expected total is 292. |
| `pytest -q` (literal) | Fails at collection (no hypothesis), as in M0/M1. |

Baseline before M2 was 220 passed; M2 adds 69 tests. Still unverified, inherited: the hatchling wheel layout (new packages `gao_idl.agents`, `gao_idl.environments.pd` are under `src/gao_idl`, so `packages = ["src/gao_idl"]` should pick them up), the hypothesis tests, Python 3.11 / CI.

## What was built

Three commits, one per Part of the brief (the brief has no numbered tasks; the glob-scope item is part of Part A's commit):

```
350acec Repeated PD: glob store scopes, pd_round layer, build_repeated_pd     Part A
48dd847 Agent adapters: RandomAgent, ScriptedAgent, HumanTransportAgent        Part B
a9e0b77 Runner: sync and asyncio, deadlines, retries, human transport          Part C
```

Source:

```
src/gao_idl/core/store.py                 + in_scope(): fnmatchcase glob scopes in StoreView
src/gao_idl/environments/pd/              __init__ (build_repeated_pd) schemas layers matching
src/gao_idl/agents/                       __init__ base random_agent scripted human
src/gao_idl/runtime/runner.py             run, run_sync, RunnerPolicy
```

Tests (all new unless noted):

```
tests/environments/test_pd.py      28 tests: the 8 briefed ones (parametrised over fixed / round_robin where it matters)
                                   + matching, validation, ids
tests/agents/test_adapters.py      14 tests: RandomAgent (incl. test_random_agent_samples_from_schema), ScriptedAgent,
                                   HumanTransportAgent (incl. test_report_dedup_in_human_transport)
tests/runtime/test_runner.py       24 tests: every briefed runner test under its name, plus retries, exceptions,
                                   disclosure, terminal states, single-coroutine, copies, cancellation, no-adapter
tests/core/test_ledger_store.py    + 3 glob-scope tests (appended)
tests/runtime/envs.py              + Stalled/stalled, bidding_capped (test fixtures)
```

Every test named in the brief exists under that name. `test_other_pairs_not_disclosed` and `test_arrival_invariance_across_pairs` use fixed pairs for the noninterference claim and both matchings for arrival invariance (see NOTES for why).

### Not-vacuous checks (mutations, all reverted)
- PD: one global history key instead of per-pair keys: 10 tests fail. Counter finishing one round early: 11 fail.
- Runner: no deep copies of requests/reports: the tamper test fails. `MAX_ATTEMPTS = 1`: 4 retry tests fail. No catch-up delivery of earlier reports: the snapshot/restore test fails. Reading the ledger unconditionally: the 2 disclosure tests fail.
- Adapters: the report-dedup and wrong-agent tests would fail without the `_seen` set / agent check (checked by reading, not by mutation).

## Design calls to confirm (details in NOTES.md)

1. **`Layer.observe` has no store access in the pinned API, so "observe reads `hist:{pair_id}`" cannot be done as worded.** Shared context wins. The pair's `outcome` appends to `hist:{pair_id}` (it has the `StoreView`); the feedback connector copies each playing pair's stored history into the next round's state; `observe` renders it. Cost: `PDState` has one extra field, `history`. The pinned `Layer` protocol is untouched. If you would rather give `observe` a read-only `StoreView`, that is a protocol change for M3+.
2. **`PDRoundExit` has an extra `round_index` field**, needed by the round counter (the joined record carries nothing else to recover it from).
3. **Ledger vs `final_utility`.** The brief forbids the runner reading the ledger for adapters but also requires `final_utility` under `disclose_final_utility=True`. I read the ledger in that one place only, behind the flag (tested).
4. **Runner catch-up (extension).** On attach the runner delivers earlier `ReportDelivered` events from `session.trace` and dispatches everything in `session.pending()`. Without it a restored session (already advanced inside `replay`) gives a fresh adapter no history and no work. Not in the brief's algorithm.
5. **`pd_round` timeout policy is `Fail`** (the brief names none), so a runner deadline fails a PD session. Choosing a default move is a game-design decision I did not want to make silently.
6. **`RandomAgent` rejects `$ref`**, so an action model containing a nested pydantic model is unsupported (pydantic emits `$ref`/`$defs`). Open bounds beyond the briefed "no max" case are my choice (`[max-10, max]`, `[0, 10]`).
7. **Glob scopes make every scope string a pattern**: a literal key like `a[1]` also grants `a1`. Exact equality is checked first, so literal keys keep working. Nothing in the repo uses such keys.
8. **The Par→counter connector raises `ContractError`** if a branch did not exit through `done` (connectors cannot emit `Failed`; a `pd_round` branch cannot exit any other way, so this is a wiring bug).
9. **Ignored `DecisionRequest.deadline`** (units undefined); only `RunnerPolicy.deadline_seconds` is used.
10. Exceptions from `observe` / `episode_start` / `episode_end` propagate (the brief only specifies `act`).

## NOTES.md entries added

All dated 2026-10-21; full text in `NOTES.md`.
1. **M2 task 0** — PyPI unreachable, local gate incantation, baseline 220, no new dependencies.
2. **Part A: glob scopes** — `fnmatchcase`, pattern-vs-literal consequence, the unset-key handling in `read_history`.
3. **Part A: PD design calls** — the `observe` conflict and the `history` field, `round_index` on the exit, layout and paths, closed-over static settings, `RoundJoin` and the connector's `ContractError`, pair ids and the circle method, `Fail` timeout, why noninterference is tested on fixed pairs only.
4. **Part B: adapters** — sampling rules and the `$ref` limit, `RandomAgent` seeding, the agent-id guard and dedup in `ScriptedAgent`/`HumanTransportAgent`, `wait_outstanding`, `provide` semantics.
5. **Part C: runner** — ledger ambiguity, catch-up, deep copies, deterministic submit order, retry/exception rules, deadlines and the `clock` caveat, no-adapter error, how `terminal` is derived, stall detection, test-only additions.

## Things I was unsure about

- Everything under "Design calls to confirm", mainly 1, 3 and 4.
- Whether a first-meeting pair in `round_robin` should see an explicit empty history or none: the observation is `own_history: []` either way.
- Submission order from the runner when several adapters finish together is dispatch order (role order within the Par's branch order). With a `Par` the log order shapes the global trace, so a different tie-break would give a different (equally valid) trace.
- `test_human_transport_snapshot_restore_resumes_in_a_new_runner` snapshots when alice's request is outstanding; depending on event-loop scheduling bob's already-computed answer may or may not be in the log yet. Both are covered by the assertions (same pending id, same final trace as the uninterrupted run), but the snapshot content is not pinned.
- `ScriptedAgent` is synchronous-policy only; an async policy would be a small extension.
- Unverified because of the sandbox: hatchling wheel contents, hypothesis tests, Python 3.11 (asyncio and typing behaviour on 3.11 was not exercised), CI.

## Explicitly not done (per brief)
Q-learning, codecs, LLM backends, PB; no adapter ever receives a session, ledger, store or another adapter; `Session` is neither async nor thread-safe.
