PyPI was unreachable again (DNS failure on `pip download httpx`, tried once, not retried), and **`httpx` is not installed in this sandbox**, so `pip install -e ".[dev,llm]"`, the 3 hypothesis tests, `pytest-timeout`, `python -m build` (and the check that the wheel contains `gao_idl/llm/`), and Python 3.11 were NOT run. Both real HTTP transports (`httpx_transport` of the OpenAI-compatible backend, used as is by the Anthropic backend) were only type-checked, never executed: everything else goes through injected fake transports. The two `httpx.MockTransport` tests I wrote are skipped here (`importorskip("httpx")`) and were never run. The two live tests (`GAO_LLM_BASE_URL`, `GAO_ANTHROPIC_API_KEY`) remain for the maintainer. All other results come from standalone ruff/mypy/pytest on Python 3.13 with `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`.

# REPORT — M4 fix-up: observable payoffs and reserve, Anthropic backend

## Gate status

**The gate as literally written was NOT fully met** (no `pip install -e ".[dev,llm]"`, no `python -m build`, `pytest -q` fails at collection on `tests/core/test_paths_property.py` because `hypothesis` is missing, as in M0-M4). Pytest was run with `--ignore=tests/core/test_paths_property.py`.

| Gate command | Result |
|---|---|
| `ruff check .` | Pass |
| `ruff format --check .` | Pass (130 files) |
| `mypy src/gao_idl` | Pass (strict, pydantic plugin): `no issues found in 61 source files` |
| `pytest -q -m "not slow" --ignore=tests/core/test_paths_property.py` | **553 passed, 4 skipped, 1 deselected** (required >= 545; was 520 passed + 1 skipped before). The 4 skips: two live tests, two `httpx.MockTransport` tests |
| `pytest -q -m slow --ignore=...` | 1 passed |
| `python -c "import gao_idl.core, gao_idl.runtime, sys; assert 'httpx' not in sys.modules"` | Pass (also after `import gao_idl.llm`, which now re-exports the Anthropic pieces; the existing subprocess test also covers it) |
| real install / `pytest -q` / `python -m build` | **Not run** (PyPI unreachable) |

## Commits (one per task, messages are the task titles)

```
e52be64 PD: payoffs are observable                task 1
8f78469 Auction: reserve is observable            task 2
f70ad21 llm/schema_transform.py                   task 3b  (committed before 3a: 3a imports it)
f8ac829 llm/anthropic.py: AnthropicBackend        task 3a
e244c33 tests/llm/test_anthropic.py               task 3c  (+ tests/llm/test_live_anthropic.py)
9640f57 Docs/notes                                task 3d  (NOTES.md, gao_idl.llm exports)
```
Task 4 is this report. The `test_schema_transform.py` unit tests are in the 3b commit, not 3c.

## What changed

1. **PD** `build_repeated_pd(..., disclose_payoffs: bool = True)`. `PDState.disclose_payoffs` carries it to the layer; `PDObs.payoffs` is the state's table. When false the key is absent from the dumped observation (a wrap serializer drops the `None`), so `FieldObsCodec` users and prompts see no placeholder. `PDCodec` prints a four-row table only if the key is present. Non-bool flag raises `ContractError`. The M4 prompt test that asserted "no table" now runs with `disclose_payoffs=False`. Noninterference test is parametrized over both settings.
2. **Auction** `BidObs.reserve: float | None` (key always present, `None` = none), fed from the same `reserve` argument as `settlement_layer`, so the two cannot disagree. `AuctionCodec` says "Reserve price R: bids below R are ignored." or "There is no reserve price.". `bid_disclosure` strings untouched.
3. **Anthropic backend** (`llm/anthropic.py`, `llm/schema_transform.py`): as specified in M4b; details and readings in NOTES.md ("M4 fix-up task 3"). `gao_idl.llm` now exports `AnthropicBackend` and `to_anthropic_schema`.

New/changed tests: `tests/environments/test_pd.py` (+3, one parametrized), `tests/llm/test_env_codecs.py` (+3), `tests/llm/test_schema_transform.py` (6), `tests/llm/test_anthropic.py` (19 run here, 2 skipped), `tests/llm/test_live_anthropic.py` (skipped).

Mutation check: leaving the credential out of the scrub list fails `test_secret_never_in_raw_or_errors` (reverted).

## Things I was unsure about (details in NOTES.md)

1. **Unexecuted code:** the `httpx.MockTransport` tests and the real `httpx_transport`; the live tests. Please run them once in CI / with a key. Default live model is `claude-haiku-5-5` (my choice; override with `GAO_ANTHROPIC_MODEL`).
2. **API facts** (`output_config.format`, `anthropic-version: 2023-06-01`, 529 retry) come from the brief; I had no network to re-check them against the docs.
3. **Test deviation:** the brief says to use `httpx.MockTransport` for the main tests; without httpx they use the injected fake transport (same request-building/parsing/retry code). The httpx-based pair is the real-transport check, skipped here.
4. **Undisclosed-PD "no digit" test:** round index and history legitimately contain digits, so the check is on the round-0 prompt with the "This is round N" line removed.
5. **Default PD behaviour changed:** `disclose_payoffs=True` means default PD LLM prompts now include the payoff table (intended by the brief).
6. **Schema transform keeps an existing `additionalProperties`** (e.g. `dict[str, float]` fields emit a schema there) since the brief says only to set it where missing; the API may reject map-like schemas, which no current action schema uses.
7. A first `assistant` message in the request is passed through unchanged (not rejected, not prefixed); a request with only system messages raises `BackendError`.
8. `anthropic.py` imports the private `_scrub_json` from `openai_compat.py` (same package) rather than duplicating it.

## Explicitly not changed (per brief)
One structured-output mechanism per backend; no provider SDK; `agent_failed` still trace-only; `ExternalResponse` still the replay mechanism; `llm/validate.py` untouched (request schema only is transformed); `DecisionRequest.deadline` still advisory. No M5 work.
