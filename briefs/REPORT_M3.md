PyPI was unreachable (DNS failure on `pip download hypothesis`, tried once and not retried), so `pip install -e ".[dev]"`, the 3 hypothesis tests, `pytest-timeout`, `python -m build` and Python 3.11 were NOT run. Everything below was verified with the standalone ruff/mypy/pytest on Python 3.13 and `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`.

# REPORT — M3: tabular Q-learning, codecs, trainer/evaluator, checkpoints

## Gate status (read this first)

**The gate as literally written was NOT fully met**, for the same reason as M0–M2: `pytest -q` fails at collection on `tests/core/test_paths_property.py` (`hypothesis` is not installable here), so every pytest command was run with `--ignore=tests/core/test_paths_property.py`. Nothing else in the gate was skipped.

| Gate command | Result |
|---|---|
| `ruff check .` | Pass: `All checks passed!` |
| `ruff format --check .` | Pass: `101 files already formatted` |
| `mypy src/gao_idl/core src/gao_idl/composition src/gao_idl/agents src/gao_idl/learning` | Pass (strict, pydantic plugin): `no issues found in 31 source files`. `mypy src/gao_idl` (all 48 files, incl. `environments`, `runtime`, `testing`) passes too. Python 3.13 only. |
| `pytest -q -m "not slow"` | **407 passed, 1 deselected in 27.7 s** (limit 60 s) |
| `pytest -q -m slow` | **1 passed in 17.5 s** |
| `pytest -q` (literal) | Fails at collection (no hypothesis). With the 3 hypothesis tests the expected total is 411 (408 here + 3). |

Baseline before M3 was 303 passed; M3 adds 105 tests (1 runner, 32 codecs, 33 TableAgent, 16 trainer, 10 toy environments, 5 transitions, 5 sanity tasks, 3 checkpoint/eval); none were removed or changed except `test_truncation_bootstraps_vs_terminal`, which was reshaped during the work.

Still unverified, inherited and not improvable here: the hatchling wheel layout (the new `gao_idl.learning` and `gao_idl.environments.toy` sit under `src/gao_idl`, so `packages = ["src/gao_idl"]` should pick them up), the hypothesis property tests, `pytest-timeout` (pytest still prints `Unknown config option: timeout`), Python 3.11, and the CI workflow (edited, never executed).

## What was built

Six commits, one per brief task, messages are the task titles:

```
ea080e4 Inherited from the M2 fix-up          task 0
7a337c2 learning/codecs.py                    task 1
cfcd7f9 learning/qlearning.py                 task 2
3f956f5 learning/trainer.py                   task 3
6a7a595 Sanity environments: environments/toy/   task 4
67bb6d2 Tests: tests/learning, slow marker, CI split   task 5
```

Source:

```
src/gao_idl/runtime/runner.py               task 0: cancellation sends episode_end("failed") (1 line + docstring)
src/gao_idl/learning/codecs.py              ObsCodec/ActCodec, FieldObsCodec, SchemaActCodec, freeze/to_jsonable/from_jsonable,
                                            codec_spec, obs/act_codec_from_spec
src/gao_idl/learning/qlearning.py           QConfig, RewardExtractor, Transition, TableAgent (+ checkpoint / from_checkpoint)
src/gao_idl/learning/trainer.py             TrainSpec, TrainResult, EvalResult, train, evaluate, episode_seed, LearnerError
src/gao_idl/learning/opponents.py           OpponentFactory, default_opponent_factory, SCRIPTED policies, derive_seed
src/gao_idl/environments/toy/               bandit.py chain.py (build_bandit, build_chain)
pyproject.toml                              `slow` marker
.github/workflows/ci.yml                    `-m "not slow"`, wider mypy, nightly slow job
```

Tests (all new unless noted):

```
tests/runtime/test_runner.py        + test_cancelled_runner_sends_failed_episode_end_to_every_adapter (task 0)
tests/learning/test_codecs.py       32 tests
tests/learning/test_qlearning.py    33 tests: transition numbers, truncation, failed/abandoned episodes, frozen agent, ε decay,
                                    checkpoints, static "imports nothing from runtime/ledger/store" guard
tests/learning/test_trainer.py      16 tests: seed formula, reproducibility, isolation, errors not swallowed, opponents
tests/learning/test_toy_envs.py     10 tests
tests/learning/test_transitions.py  5 tests: the PD / auction / disclosure tests of the brief
tests/learning/test_sanity_tasks.py 5 tests: bandit, chain (3 seeds), PD (slow)
tests/learning/test_checkpoint_eval.py  3 tests: round trip, isolation, baselines
tests/learning/helpers.py envs.py   builders; the PD whose reports omit own_payoff
```

Every test named in the brief exists under that name (`test_chain_learns_optimal_policy` is parametrised over seeds 0–2).

### Not-vacuous checks (mutations, all reverted)
- Runner: restoring `except Exception` fails the new cancellation test.
- TableAgent: no truncation bootstrap, no rollback of a failed episode, rewards not accumulated, failed episodes still decaying ε, ignoring the rewards reported after the last decision, and shifting rewards across the boundary each fail the corresponding tests (several fail more than one).
- Trainer: `evaluate` building its agent with `learning=True` fails `test_the_evaluation_agent_is_new_frozen_and_never_updated`.
- Hyperparameters of the bandit and chain tasks were checked on 6 and 12 seeds before pinning, so the passes are not one lucky seed.

## NOTES.md entries added

All dated 2026-10-22; full text in `NOTES.md` (lines from "M3 task 0").
1. **Task 0** — PyPI still unreachable; the runner now also sends `episode_end("failed")` on cancellation (reverses the M2 fix-up call); known gap for a cancel during the normal `episode_end` phase.
2. **Task 1** — meaning of the lazy/construction-time `ValueError`; `history` is stateless (sees only `since_last`); added keyword `report_fields`; key freezing and the JSON round trip; what `SchemaActCodec` supports and refuses.
3. **Task 2** — truncation bootstrap proxy; online updates with an undo log for failed episodes; ε decay and `episodes_seen` only when learning; `dropped_reward`; `elapsed`/`discount_by_elapsed` literal; tie-break; checkpoint keys beyond the brief; additions to the API.
4. **Task 3** — returns are ledger utility read by the trainer; opponent factory and forms; evaluate signature extras; isolation guards and `_Pinned`/`LearnerError`; one event loop per call; EvalResult statistics.
5. **Task 4** — bandit and chain design; the reading of the chain's reward rule.
6. **Task 5** — `slow` marker and CI change; reading of the baselines test; how the truncation, silent-PD and auction tests are built; hyperparameter checks; what is unverified.

## Things I was unsure about (details in NOTES.md)

1. **Truncation bootstraps from `max_a Q(s_K, ·)` — the value of the last decision's own state.** That is what the brief says, and the agent has no later state, but it is a biased estimate whenever the truncated game would have moved on. Implemented literally; a proper fix needs the environment to disclose a post-horizon state. This is the one place where I think the brief may be wrong.
2. **Chain reward rule.** Read as: reward 1 exactly on the transition `state 1 --right--> state 2`; three of the eight 3-move sequences reach it (`LRR`, `RRL`, `RRR`), optimum 1. The other reading ("pays whenever a step ends in 2") would make `RRR` worth 2 and contradict "eval return == 1.0".
3. **`history` in `FieldObsCodec`.** Codecs get only the reports since the previous decision, so `history=h` cannot see older ones. I added a keyword-only `report_fields` so that history is usable at all (PD: `own_history` is observed, `other_move` is reported). Not in the pinned signature.
4. **"Failed episodes are discarded from learning."** Updates are online, so I log each table write and roll the table back on a failed end (and on an `episode_start` that finds an unfinished episode, which also counts as failed). The alternative, buffering the episode and applying it at the end, would delay learning inside an episode.
5. **`test_random_and_scripted_baselines_run_through_same_evaluate`.** `evaluate` takes a checkpoint, so I read "baselines" as the opponent kinds plus an untrained table as the baseline learner (known exact returns). If you meant baselines as the *evaluated* learner, `evaluate` needs a different input.
6. **Trainer/runner interaction.** The runner turns an exception in `act` into a timeout; for the learner that would silently fail every episode on a codec typo. `_Pinned` records the error and the trainer raises `LearnerError`. It also pins `keep_parameters=True`, which the runner's default already gives.
7. **Returns come from the ledger**, read by the trainer (not the agent, not passed to anyone). The learner is never told its final utility.
8. **Edits outside the strict task list:** `pyproject.toml` (`slow` marker) and `ci.yml` (not-slow on every push, slow nightly, wider mypy). I took the brief's "CI runs slow tests nightly only" as the reason; the workflow has not run.
9. **Task 0 implementation** is `except (Exception, asyncio.CancelledError)` rather than a flag in `finally`; same effect. A cancellation arriving during the final, normal `episode_end` loop can still leave later adapters un-notified.
10. **Elapsed 0** (two decisions with no report between them, e.g. two roles of one agent in a `Par`) is undiscounted when `discount_by_elapsed=True`; kept literal.
11. **Speed.** About 1.5 ms per bandit episode and about 15 ms per 5-round PD episode (session setup dominates), which is why the PD test is the only slow one. I did not look into the session cost.

## Explicitly not done (per brief)
No PPO, function approximation, replay buffers or neural dependency; no new dependencies (numpy/pydantic only; `pytest-timeout` was already a dev dependency). `TableAgent` never touches a session, ledger, store, trace or outcome (static import guard + it only implements the four adapter methods). Chain and bandit are ordinary environments built from existing constructors; the only runtime change is the runner's cancellation handling.
