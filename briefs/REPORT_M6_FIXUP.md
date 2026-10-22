PyPI was unreachable in this sandbox (not retried), so `pip install ".[dev,llm,docs]"`, `python -m build`, `twine check`, `bash scripts/clean_install.sh` against a real wheel, `mkdocs build --strict` with `mkdocstrings`, all hypothesis tests (including `tests/environments/test_pb_rules_property.py`) and Python 3.11 were NOT run. `hatchling`, `build`, `twine`, `httpx` and `hypothesis` are not installed here. Everything below was run on Python 3.13 with `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`.

# REPORT — M6 fix-up

## Gate status

| Gate command | Result |
|---|---|
| `ruff check . && ruff format --check . && mypy src/gao_idl` | **Pass** (159 files formatted; mypy: 68 source files) |
| `pytest -q -m "not slow" --ignore=tests/core/test_paths_property.py --ignore=tests/environments/test_pb_rules_property.py` | **Pass: 752 passed, 4 skipped, 2 deselected in 46 s.** Skips: 2 Anthropic-backend tests need `httpx` (not installed here), 2 live smoke tests need credentials. The maintainer's run (754 passed, 2 skipped, 1 xfailed) had `httpx` installed; the xfail is gone now (task 2). |
| `GAO_EXAMPLE_FAST=1 python examples/04_pause_resume.py` | **Pass**: `identical to an uninterrupted run` |
| `python -m gao_idl.cli run examples/configs/pd.json --out /tmp/run.json && python -m gao_idl.cli replay /tmp/run.json` | **Pass**: `replay identical: 21 inputs, exit=done` |
| Maintainer-only: `pip install ".[dev,llm,docs]" && pytest -q && python -m build && twine check dist/* && bash scripts/clean_install.sh && mkdocs build --strict` | **Not run** (no PyPI). See "Needs your run" below. |

Test command used: the pytest line above.

## What was built

Commits, one per task, messages are the task titles (HEAD is the last one):

1. `aa2bd51` One CLI module
2. `c3aa227` `knapsack` rounds costs to cents
3. `e863def` `clean_install.sh` picks the right wheel
4. `a67db50` Decision log from the actual plan
5. `8104507` Package metadata, license, typing marker, docs pin
6. `f970d46` Tag preparation

Files (relative to the M6 delivery):

- Task 1: `src/gao_idl/cli.py` (new; replaces `run.py` and `replay.py`), `[project.scripts] gao-idl` in `pyproject.toml`, `tests/test_cli.py`, `tests/test_docs.py`, `tests/test_public_api.py` (plain `importlib` module walks), `tests/modules.py` removed, `README.md`, `docs/runs.md`, `docs/ACCEPTANCE.md`. `import gao_idl.cli` leaves `gao_idl.run` the function (tested).
- Task 2: `environments/pb/rules.py` (`knapsack` rounds; docstrings), `tests/environments/test_pb_rules.py` (`test_knapsack_rounds_sub_cent_costs`), `tests/environments/test_pb.py` (xfail removed, knapsack case now passes), `examples/configs/pb.json` (rule is `knapsack` again), `docs/runs.md`, `docs/ACCEPTANCE.md`.
- Task 3: `scripts/clean_install.sh`.
- Task 4: `docs/design/decisions.md` (rebuilt), `docs/index.md` (diagram).
- Task 5: `LICENSE`, `CITATION.cff`, `src/gao_idl/py.typed`, `pyproject.toml`, `README.md`, `tests/test_version.py`.
- Task 6: `docs/releasing.md`, `mkdocs.yml` (nav), `CHANGELOG.md`.
- All tasks: `NOTES.md` (appended).

Not changed, per the brief: the 23-name API, stability tiers, tutorial prose and example structure, `selftest` outside `__all__`, the version (0.1.0). No tag was created. M7 not started.

## NOTES.md entries added

- "M6 fix-up task 1: one CLI module"
- "M6 fix-up task 2: `knapsack` rounds costs to cents"
- "M6 fix-up task 3: `clean_install.sh` picks the right wheel"
- "M6 fix-up task 4: decision log from the actual plan"
- "M6 fix-up task 5: metadata, license, typing marker, docs pin"
- "M6 fix-up task 6: tag preparation"

## Needs your run (not possible here)

- `python -m build`, then `unzip -l dist/*.whl | grep -E "py.typed|LICENSE"` and `unzip -l dist/*.tar.gz`-style check of the sdist (`tar tzf`) for `LICENSE` and `CITATION.cff`; then `twine check dist/*`. The wheel/sdist config has never been built by anyone but you, and this fix-up changed it (license fields, `hatchling>=1.27`, sdist include list).
- Task 3's manual check with a dummy 0.0.1 wheel next to the real one (commands in NOTES.md). I checked the selection logic with empty stand-in wheels: only a 0.0.1 present exits 2 with a clear message; with both present the 0.1.0 file is chosen and both files are left in `dist/`. The install and selftest part needs PyPI-built wheels.
- `pytest -q` with hypothesis, in particular `tests/environments/test_pb_rules_property.py` (its knapsack check uses integer costs, so I don't expect an effect from task 2, but it has not run) and `mkdocs build --strict` with `mkdocstrings`.

## Things I was unsure about

- **License classifier.** The brief asks for a license classifier and an SPDX `license` string. PEP 639 deprecates `License ::` classifiers alongside a license expression (some backends reject both), so I added only the SPDX field. Easy to add if you want it; check that `python -m build` accepts it.
- **`hatchling>=1.27`.** I raised the build requirement because SPDX `license` and `license-files` need a PEP 639-aware hatchling. I believe 1.27 is the first such release but could not check offline. Likewise `twine check` needs a recent twine (6.1+, I believe) to read `License-Expression` metadata.
- **`CITATION.cff` DOI.** `doi: ""` fails CFF validation, so the DOI is a comment line to fill in later.
- **Knapsack candidate filter.** A project whose exact cost exceeds the budget (1.004 vs budget 1.0) is still never a candidate, even though it would round to 1.00. The brief did not say; I took the conservative reading and pinned it in a test.
- **Knapsack overspend.** A set that fits the rounded costs can exceed the budget by under half a cent per project, in which case the layer's exact re-check would raise `ContractError`. I ran seeds 0–150 of the knapsack PB demo config through `gao_idl.cli run`: none raised. That is evidence, not proof. If it ever happens, the cheapest fix is to round costs up (ceil) instead of to nearest, which you didn't ask for.
- **Decision log.** The page now follows the plan's text, with `matrix_envs` read as `gao_idl`. The old invariant/composition/runtime tables were my reconstruction and are gone; their content is in the amendments. `docs/index.md` now uses the plan's §2 diagram in ASCII (the box-drawing characters converted), because it shows the trust boundary; the old diagram is one `git revert` of that hunk away.
- **Changelog date.** `0.1.0` is headed "Unreleased" until you tag.
