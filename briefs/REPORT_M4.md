PyPI was unreachable again (DNS failure on `pip download hypothesis`, tried once, not retried), and **`httpx` is not installed in this sandbox**, so `pip install -e ".[dev,llm]"`, the 3 hypothesis tests, `pytest-timeout`, `python -m build` and Python 3.11 were NOT run, and the real HTTP transport of the OpenAI-compatible backend (`httpx_transport`, about 8 lines) was only type-checked, never executed. Everything else in the backend is tested through an injected fake transport. All other results below come from the standalone ruff/mypy/pytest on Python 3.13 with `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`.

# REPORT — M4: LLM agents (backend, codec, memory, repair loop, replay without inference)

## Gate status (read this first)

**The gate as literally written was NOT fully met**, for the reasons above: `pip install -e .[dev,llm]` was not run, and `pytest -q` fails at collection on `tests/core/test_paths_property.py` (no `hypothesis`), as in M0–M3. Every pytest command was run with `--ignore=tests/core/test_paths_property.py`.

| Gate command | Result |
|---|---|
| `ruff check .` | Pass: `All checks passed!` |
| `ruff format --check .` | Pass: `124 files already formatted` |
| `mypy src/gao_idl/core src/gao_idl/composition src/gao_idl/agents src/gao_idl/learning src/gao_idl/llm` | Pass (strict, pydantic plugin): `no issues found in 40 source files`. `mypy src/gao_idl` (all 59 files) passes too. Python 3.13 only. With httpx absent mypy relies on `[[tool.mypy.overrides]] module = "httpx"` (`ignore_missing_imports`); with httpx installed it is typed normally. |
| `pytest -q -m "not slow"` | **520 passed, 1 skipped, 1 deselected in 27.5 s** (skipped = the live smoke test, no `GAO_LLM_BASE_URL`). Baseline before M4: 407 passed. |
| `pytest -q -m slow` | 1 passed in 15.5 s |
| `python -c "import gao_idl.core, gao_idl.runtime, sys; assert 'httpx' not in sys.modules"` | Pass (also a test, in a subprocess, which also covers `gao_idl.llm.*` and the two environment codecs) |
| `pytest -q` (literal) | Fails at collection (no hypothesis). With the 3 hypothesis tests the expected not-slow total is 523 + the slow one. |

Test count: 407 -> 520 (+113 passed, +1 skipped live test). 114 tests live in `tests/llm/` (backend 19, codec 20, memory 8, validate 12, agent 25, recording 9, env codecs 12, integration 8, live 1), plus the changed M3 test (below).

## What was built

Eight commits, one per brief task (messages are the task titles; the NOTES entries have the same headings):

```
1035be9 Inherited from M3                         task 0   (amended once to add the attribution trailer; later commits carry it)
4b3c1c5 llm/backend.py                            task 1   backend.py, mock.py, openai_compat.py, pyproject mypy override
b056c35 llm/codec.py                              task 2
1d6c7b8 llm/memory.py                             task 3
cffad14 llm/agent.py                              task 4   agent.py, validate.py, AgentFailed, agent_failed event, runner hook
29c7697 Recording responses in the input log      task 5   ExternalRecorder, runner recording, replay.py
11eef39 Environment codecs                        task 6
9289b01 Tests: tests/llm, CI                      task 7
```

Source (new unless noted):

```
src/gao_idl/learning/qlearning.py        task 0: bootstrap_on_truncation default False, docstrings (modified)
src/gao_idl/llm/backend.py               Message, Response, LLMBackend, BackendError (scrubs), scrub(), current_decision ContextVar
src/gao_idl/llm/openai_compat.py         OpenAICompatBackend (+ injectable transport, httpx_transport, HttpReply, TransportFailure)
src/gao_idl/llm/mock.py                  MockBackend, MockCall, MockExhausted
src/gao_idl/llm/codec.py                 DecisionCodec, DefaultCodec, CodecParseError, ReportRenderer, DefaultReportRenderer
src/gao_idl/llm/validate.py              against_json_schema, first_problems
src/gao_idl/llm/memory.py                Memory protocol, FullTranscript, Window, NoMemory
src/gao_idl/llm/agent.py                 LLMAgent, RepairPolicy, CallRecord
src/gao_idl/llm/replay.py                ReplayBackend, ReplayExhausted
src/gao_idl/environments/auction/llm.py  AuctionCodec, AuctionReportRenderer
src/gao_idl/environments/pd/llm.py       PDCodec, PDReportRenderer
src/gao_idl/core/errors.py               + AgentFailed                       (modified)
src/gao_idl/core/events.py               + AgentFailedEvent (trace kind "agent_failed")   (modified)
src/gao_idl/agents/base.py, __init__.py  + ExternalRecorder                  (modified)
src/gao_idl/runtime/runner.py            AgentFailed -> agent_failed event; drain + record_external before submit/timeout   (modified)
src/gao_idl/runtime/session.py           + record_agent_failure; check_determinism also skips agent_failed   (modified)
.github/workflows/ci.yml, pyproject.toml  CI installs [dev,llm], mypy covers llm, httpx import check; mypy override for httpx
```

Tests: `tests/llm/{test_backend,test_codec,test_memory,test_validate,test_agent,test_recording,test_env_codecs,test_integration,test_live}.py` and `helpers.py`; every test named in the brief exists under that name (two with a longer suffix: `test_schema_violation_repair_is_caught_locally_before_submit`; the bootstrapping test kept its name in `tests/learning/test_qlearning.py`).

### Not-vacuous checks (mutations, all reverted)
- Runner not logging externals: 7 tests fail. Runner not emitting `agent_failed`: 5 fail. Agent not noticing a re-dispatch: the infeasible test fails. Validator accepting numeric strings: 2 fail.
- The sentinel test has a control: against `bidding_leaky` the same check does find `7777.5`.
- The concurrency test asserts the two model calls really interleaved (`start, start, end, end`) before checking that replies are logged under the right decision.

## NOTES.md entries added

All dated 2026-10-22; full text in `NOTES.md` (headings "M4 task 0" … "M4 task 7"): PyPI/httpx status and the truncation default; backend design (transport split, retry reading, credential handling, `current_decision`); codec render contract and parsing rules; memory kinds and `fresh()`; agent design (core placement of `AgentFailed`, the trace-only event, redispatch set, provisional memory, `CallRecord` fields, validator strictness); recording (`ExternalRecorder` placement, envelope key, logging failed calls, `ReplayBackend`); environment codecs (what the prompts leave out); tests and CI (readings of the two ambiguous tests).

## Things I was unsure about (details in NOTES.md)

1. **`httpx_transport` was never executed.** It is the only code that touches httpx (`AsyncClient.post`, `httpx.TransportError` -> retryable). Please run `tests/llm/test_live.py` against a real endpoint once.
2. **Import direction forced two placements the brief did not name:** `AgentFailed` in `core/errors.py` and `ExternalRecorder` in `agents/base.py`, because the runner may not import `gao_idl.llm`.
3. **`agent_failed` is trace-only** (like `action_rejected`): not in the input log, not reproduced by `Session.restore`, excluded from `check_determinism`. Its effect (`TimedOut` -> timeout policy) is in the log. A replay *with agents* over a `ReplayBackend` regenerates it with identical reasons (tested).
4. **Render contract added to the codec protocol** (without changing its signature): `render` returns `[system..., *memory, turn...]` and the agent stores `render(request, [], [])` minus the system messages as the remembered turn.
5. **Failed backend calls are logged too** (`{"attempt", "error"}` payload) so a replay repeats the same failure instead of shifting replies by one. The runner learns the decision from a `"decision_id"` key in each drained item and removes it from the payload.
6. **`ReplayBackend` learns the decision from a `ContextVar`** set by `LLMAgent`, since `complete()` has no decision argument. `ReplayExhausted` is a `DeterminismError`, not a `BackendError`, so it is not retried.
7. **"Transport retries: 3"** read as 3 retries (up to 4 requests; delays 0.5/1/2 s).
8. **Validator strictness:** `5.0` is not an `integer` (JSON Schema would accept it; pydantic strict mode does not). Unknown keywords are ignored; local `$ref` is resolved (not in the brief).
9. **Redispatch note** is verbatim `"previous action was rejected as infeasible"`; the runner cannot tell a `SchemaError` re-dispatch apart, which "should not happen" after local validation. Detection uses a per-episode set of answered decisions (plus `last_decision_id`) because one agent can have several decisions in flight.
10. **Memory commit is deferred** until the next report/decision/episode end so a re-dispatch does not duplicate the turn. Corner case: if another decision starts between an answer and its re-dispatch, the first answer is remembered twice.
11. **The PD prompt states no payoff table** because the observation has none, so an LLM plays the PD without knowing the payoffs. That follows the brief, but you may want the observation to carry them. The auction prompt states no utility rule or reserve for the same reason.
12. **Readings of two tests:** the replay test drives a fresh session with `ReplayBackend` agents (the "fresh mock" is trivially empty; `served` counts are the evidence); the mixed-population test uses four agents (LLM, table, scripted, random) because the PD needs an even population, and "ledger consistent" means ledger utility equals the sum of reported `own_payoff`.
13. **Order of work:** I drafted `validate.py`, `memory.py` and `codec.py` in the same sitting as the backend, before the task 1 tests were green; each commit nonetheless contains only its own task's files and was committed after its own tests (and the whole suite) passed. `validate.py` is in the task 4 commit because the agent uses it.
14. `Session.replay` is unchanged: it calls `advance()` before an `ExternalResponse` (not a submission), which differs from the live batching but cannot change the trace (Par applies inputs one at a time); a 4-player `Par` PD replays identically.
15. Unverified, inherited and not improvable here: the hatchling wheel layout (`gao_idl.llm` sits under `src/gao_idl`, so `packages = ["src/gao_idl"]` should ship it), hypothesis tests, `pytest-timeout` (pytest still prints `Unknown config option: timeout`), Python 3.11, and the edited CI workflow.

## Explicitly not done (per brief)
No provider SDKs (one HTTP shape), no coercion of model output (`"5"` is a repair), no context passed to prompts beyond request/memory/reports/rejections, nothing in `core`/`composition`/`runtime` imports `gao_idl.llm` (static test). The runner change is limited to the `agent_failed` trace event and the recording hook; `Session` gained one trace-only method.
