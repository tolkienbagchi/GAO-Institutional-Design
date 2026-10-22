PyPI was unreachable (DNS failure, tried once, not retried), so `python -m build`, `twine check`, `pip install -e ".[dev,llm]"`, all hypothesis tests (including `tests/environments/test_pb_rules_property.py`), `mkdocstrings`, Python 3.11 and the CI workflow were NOT run. `hatchling`, `build` and `twine` are not installed here either. Everything below was run on Python 3.13 with `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`; `mkdocs` and `mkdocs-material` are installed here, `mkdocstrings` is not.

# REPORT — M6: release (packaging, public API, docs, tutorials, demo configs, acceptance)

## Gate status

**The gate as literally written was NOT met.** What was run, and what could not be:

| Gate command | Result |
|---|---|
| `python -m build && twine check dist/*` | **Not run**: `build`, `hatchling` and `twine` are not installed and PyPI is unreachable. The wheel and sdist were never built; `pyproject.toml`'s hatch settings (wheel `packages`, sdist `include`) are untested. |
| `bash scripts/clean_install.sh` | **Not run against a real wheel.** Run with a hand-made stand-in wheel of `src/gao_idl` (`CLEAN_INSTALL_SYSTEM_SITE=1 CLEAN_INSTALL_PIP_ARGS="--no-index --no-deps"`): `clean_install: OK` (imported from the venv's site-packages, no httpx/hypothesis/pytest in `sys.modules`, selftest prints `ok`). This proves the script and the package contents, not the build. |
| `mkdocs build --strict` | **Partly.** Passes with the real `mkdocs.yml` minus the `mkdocstrings` plugin (the `::: module` lines render as text), run by `tests/test_docs.py::test_mkdocs_build_is_strict`. The real config with `mkdocstrings` was never built. |
| `pytest -q -m "not slow"` (includes `test_examples`, `test_public_api`) | **Pass with two files ignored** (they need `hypothesis`): **747 passed, 4 skipped, 2 deselected, 1 xfailed in 50 s** (M5 baseline: 629 passed). The literal command fails at collection without `hypothesis`. |
| `GAO_EXAMPLE_FAST=1 python examples/04_pause_resume.py` | **Pass** in 0.9 s: `identical to an uninterrupted run`. |

Other checks: `ruff check .` pass; `ruff format --check .` pass (159 files); `mypy src/gao_idl` pass (69 files); `mypy --strict examples/0*.py` pass (with `MYPYPATH=src`); `pytest -q -m slow` 2 passed in 35 s.

Test command: `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages pytest -q -m "not slow" --ignore=tests/core/test_paths_property.py --ignore=tests/environments/test_pb_rules_property.py`

## What was built

Commits (one per task; messages are the task titles):

```
8b7cc08 Inherited from M5            task 0
acfad32 Packaging                    task 1
10ef433 Public API surface           task 2
fdba47a Documentation                task 3
1d8b4e0 Reproducible demo configs    task 4
ccb29c0 Acceptance                   task 5
```

**Task 0.** `.github/workflows/ci.yml` (mypy step is `mypy src/gao_idl`); `src/gao_idl/agents/random_agent.py` (strings with `minLength`/`maxLength`, `exclusiveMinimum`/`exclusiveMaximum` on numbers); `tests/agents/test_adapters.py`; `tests/environments/test_pb.py` (200 sampled `Nomination`s validate; PB runs end to end with plain random agents; `knapsack` is a strict xfail, see NOTES). The hypothesis tests could not be run.

**Task 1.** `pyproject.toml` (0.1.0, metadata, extras `llm`/`dev`/`docs`, hatch wheel and sdist settings), `src/gao_idl/_selftest.py` (`gao_idl.selftest()`), `scripts/clean_install.sh`, `README.md` install section, `tests/test_version.py`, `tests/test_packaging.py`.

**Task 2.** `src/gao_idl/__init__.py` (exactly the 23 names; `selftest` and `__version__` as the only other attributes), `src/gao_idl/_version.py`, `src/gao_idl/llm/__init__.py` (24 LLM names), a `Stability:` paragraph in every module docstring (63 files), `tests/test_public_api.py`.

**Task 3.** `mkdocs.yml`; `docs/index.md`, `docs/concepts.md`, `docs/tutorials/01..06_*.md` (each includes its script with `--8<--`), `docs/api/{index,core,composition,runtime,agents,learning,llm,environments,testing}.md`, `docs/design/decisions.md`; `examples/01_define_a_layer.py` .. `06_information_tests.py`; `tests/test_examples.py` (each example runs in under 20 s with `GAO_EXAMPLE_FAST=1`, imports no internal module, is mirrored in its page), `tests/test_docs.py`; `.gitignore` (`site/`), `pyproject.toml` (ruff excludes `docs/`), CI jobs `docs` and `package`.

**Task 4.** `src/gao_idl/run.py` (`python -m gao_idl.run CONFIG --out run.json`), `src/gao_idl/replay.py` (`python -m gao_idl.replay run.json`), `examples/configs/{auction,pd,pb}.json`, `docs/runs.md`, `tests/test_cli.py`, `tests/modules.py` (loads entry-point modules without importing them), changes to the two module walkers in `tests/test_public_api.py` and `tests/test_docs.py`.

**Task 5.** `docs/ACCEPTANCE.md`, a worked answer in `examples/acceptance/{my_env,train_table,run_mixed}.py`, `tests/test_acceptance.py`. The acceptance run itself is for a human and was not done.

## Unsure about / please check

1. **Nothing about the build was exercised**: no wheel, no sdist, no `twine check`. The sdist `include` list and the wheel layout are from the hatchling documentation, not from a run. Also unrun: Python 3.11, the hypothesis tests, `pytest-timeout` (pytest warns `Unknown config option: timeout` here), the three CI jobs.
2. **`mkdocstrings` never ran.** Options are the minimal documented ones with `docstring_style: null` (free-form docstrings); API pages link to pages, not anchors. A griffe or autorefs warning would fail `--strict`; first thing to run on a machine with PyPI.
3. **The v2 plan was not supplied**, so the architecture diagram (`docs/index.md`, plan §2) and `docs/design/decisions.md` (§0 and §14) are derived from the code, the shared context and `NOTES.md`, and say so. "The four information objects" and "substitute into the PD tournament's `Par`" are my readings (NOTES, task 3).
4. **Brief conflict: module `gao_idl.run` vs exported function `gao_idl.run`.** Importing the module would replace the function. Taken: the CLI is run only with `-m`, never imported, marked internal; test walkers read entry points from source (NOTES, task 4). A rename of the module would remove the problem.
5. **Tutorial 1 is 150 lines, not about 60.** Tutorials import only public or supporting modules (enforced by a test); two helper types needed a public annotation (`Primitive`, `Loop`) because `Process` is internal.
6. **`gao_idl.llm.__all__` grew from 2 to 24 names** and `gao_idl.selftest` is a 24th attribute outside `__all__`; both are additive readings of "exactly" and "LLM symbols from `gao_idl.llm`" (NOTES, task 2).
7. **The 23-name API is a promise made by a test, but the tiers (public, supporting, internal) are my judgement** of what is documented; review the `Stability:` paragraphs.
8. **`knapsack` rejects random float costs** (a `ValueError`); not fixed because it would change semantics; strict xfail and a note in `docs/runs.md`. Demo configs avoid it.
9. **Acceptance step 5 cannot go through the CLI**: the named scripted policies are PD/auction-only, so the exercise uses a Python script. No license, authors, URLs or `py.typed` in the package metadata (not briefed).
10. Version `0.1.0` is documented as the first release; I did not tag anything.

## NOTES.md entries added in M6 (verbatim)

## 2026-10-22 — M6 task 0: inherited from M5 (PyPI unreachable again; RandomAgent strings and exclusive bounds)
- **PyPI is unreachable** (`pip download hypothesis`: DNS "Name or service not known", tried once, not retried). So
  `pip install -e ".[dev,llm]" && pytest -q` was NOT run, and the hypothesis tests (`tests/core/test_paths_property.py`,
  `tests/environments/test_pb_rules_property.py`) have still never run in a sandbox. `httpx`, `build`, `hatchling`, `twine`
  and `mkdocstrings` are not installed either. Gate substitute as in M5:
  `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages` with the two hypothesis files ignored. Baseline before M6:
  629 passed, 4 skipped, 2 deselected (not slow).
- **CI:** the mypy step is now `mypy src/gao_idl` (all packages; it passes locally: 65 source files). CI itself was not run.
- **`RandomAgent` / `sample_schema` now sample `type: "string"`** (lowercase ASCII letters, length uniform in
  `[minLength, maxLength]`, default range 1–12). Readings for the cases the brief leaves open: `minLength` alone gives
  `[minLength, max(minLength, 12)]`; `maxLength` alone gives `[min(1, maxLength), maxLength]` (so `maxLength: 0` is the empty
  string); an empty range or a negative length is a `ValueError`; a non-integer bound is `NotImplementedError`. `pattern`
  and `format` on a string stay unsupported (`NotImplementedError`): ignoring them would be unsound.
- **`exclusiveMinimum` / `exclusiveMaximum` are sampled for `type: "number"` only**, strictly inside (a draw that lands on an
  open end is redrawn). Open upper bound: `exclusiveMinimum + 10` (as briefed); open lower bound: `[bound - 10, bound)`,
  mirroring the closed case. With a closed and an exclusive bound on one side the tighter one wins (the exclusive one on a
  tie). **`integer` with an exclusive bound still raises `NotImplementedError`** — the brief says numbers, and
  widening it was left out (it would be a small extension: `floor(b) + 1`). An empty or one-ulp-wide open range is a `ValueError`.
- Tests: 200 sampled `Nomination`s all validate (`test_random_agent_samples_pb_nominations`); strings, exclusive bounds,
  edge draws (a stub generator that returns exactly the open end), empty ranges; the parametrised
  "unsupported fragment" test now uses `null`, `integer`+`exclusiveMinimum`, `pattern`, `format` (it used `string` and
  `number`+`exclusiveMinimum`, which are supported now — this is the intended behaviour change of the brief, not a regression).
  Mutation check (reverted): dropping the strictness test fails the adjacent-ulp and the edge-draw tests.
- **Issue found, NOT fixed (semantics decision): `knapsack` crashes on non-cent costs.** With plain `RandomAgent`s
  (`test_plain_random_agents_run_pb_end_to_end`) greedy, MES and Phragmén complete for 8 seeds each, but `knapsack` raises
  `ValueError: cost of 'p1' is not a whole number of cents` out of `Session.advance()` (it escapes as an exception, the session
  is not marked failed), because a nomination cost is any positive float and the knapsack DP needs whole cents. This is the M5
  behaviour ("costs must be whole cents") but it means one agent's legal nomination can crash a `knapsack` run. Possible
  fixes, each a semantics change that M6 forbids: reject non-cent costs at nomination (needs the rule in the nomination
  layer), round, or turn the error into a `Failed` session. Left as is; the test case is `xfail(strict=True, raises=ValueError)`
  so a future fix flips it loudly. **Demo configs (task 4) therefore do not pair `knapsack` with random nominators.**

## 2026-10-22 — M6 task 1: packaging (`pyproject.toml`, `gao_idl.selftest`, `scripts/clean_install.sh`)
- **NOT run (no PyPI, no `build`/`hatchling`/`twine` installed): `python -m build` and `twine check dist/*`.** So the sdist, the
  wheel and the rendering of `README.md` as the PyPI long description are unverified; what was checked instead: the
  `pyproject.toml` parses (`tests/test_version.py` reads it with `tomllib` and pins version, `requires-python`, the three extras
  and the two core dependencies), and the wheel *layout* assumption (`packages = ["src/gao_idl"]`, the same line as since M0) is
  unchanged. The `[tool.hatch.build.targets.sdist] include = [...]` list is new and unexecuted (paths that do not exist yet,
  such as `/docs`, simply match nothing).
- **`clean_install.sh` was exercised only against a hand-made stand-in wheel**
  (a zip of `src/gao_idl` with `METADATA`/`WHEEL`/`RECORD`, built by a throwaway script outside the repository), with
  `CLEAN_INSTALL_SYSTEM_SITE=1 CLEAN_INSTALL_PIP_ARGS="--no-index --no-deps"` to reuse the system pydantic/numpy. It passed:
  new venv, wheel installed, `gao_idl` imported from the venv and not from the repository (`python -I`, cwd outside the repo),
  `httpx`/`hypothesis`/`pytest` absent from `sys.modules` after `import gao_idl, gao_idl.llm`, `selftest()` OK. The two env
  knobs are my addition (the brief's script has none; the defaults are the brief's check: no extras, real PyPI). Run
  `python -m build && bash scripts/clean_install.sh` where PyPI is reachable.
- **Extras:** `llm = ["httpx"]` and `dev` as before, plus **`build` and `twine` in `dev`** (the gate runs both; no other place
  to declare them), and `docs = ["mkdocs-material>=9.5", "mkdocstrings[python]>=0.25"]` (lower bounds only, my choice; no upper
  pins). No change to the core dependencies. No `[project.scripts]`: the two CLIs of task 4 are `python -m` entry points.
- **Not set, on purpose:** `license`, `authors`, `urls` (the repository has no licence file and I will not invent one; `twine
  check` does not require them). Also no `py.typed` marker, although `mypy --strict` passes on the package: shipping it is a
  promise about the typing of the public API that the maintainer should make.
- `gao_idl.selftest()` lives in `gao_idl/_selftest.py` (internal) and is re-exported with the explicit `as selftest` form. It runs the
  auction (two `RandomAgent`s, seeds 1 and 2) and the repeated PD (tit-for-tat vs always-defect, 3 rounds; totals 2.0 / 7.0 are
  asserted), each through `run`, then `check_determinism()` and a snapshot/restore comparison, and prints exit ports and ledger
  totals. It uses `asyncio.run`, so it cannot be called from inside a running event loop (documented in its docstring).
  Imports are inside the function, so `import gao_idl` stays light. **Interaction with task 2:** task 1 requires `gao_idl.selftest()`
  while task 2 says `__init__` re-exports *exactly* a list that does not contain it. Reading: `selftest` stays reachable as an
  attribute but is not in `__all__`; `tests/test_public_api.py` names it as the one allowed non-exported public attribute.
- Version is `0.1.0` in `pyproject.toml` and `gao_idl.__version__` (static in both, a test keeps them equal).
- README refreshed (install section; the one tested Python example is untouched: `tests/test_readme_example.py` still requires exactly
  one `python` block of at most 22 lines). `tests/test_packaging.py`: import hygiene in a fresh interpreter, `gao_idl.llm` imports
  without httpx, `selftest` output and determinism.

## 2026-10-22 — M6 task 2: public API surface (`gao_idl/__init__.py`, `gao_idl.llm`, `tests/test_public_api.py`)
- `gao_idl.__all__` is exactly the 23 names of the brief, in the brief's order; `tests/test_public_api.py` pins the set, the count, the
  `from gao_idl import *` namespace and the non-module attributes of the package. **`selftest` is the one other public attribute**
  (task 1 requires `gao_idl.selftest()`; task 2 says "exactly"); it is not in `__all__` and the test names it explicitly. Reading, not
  a decision: if you want it exported, add it to the list in the test.
- **`__version__` moved to `gao_idl/_version.py`** (re-exported as `gao_idl.__version__`). Reason: `learning/qlearning.py` did
  `from gao_idl import __version__`; now that `gao_idl/__init__` imports `TableAgent`, that would be an import cycle. No semantic
  change (the checkpoint's `created_by` string is `gao_idl 0.1.0`).
- **LLM symbols are exported from `gao_idl.llm` only — and `gao_idl.llm.__all__` grew** from the two Anthropic names to the 24 public
  LLM names (agent, repair policy, call record, backends, mock, replay, codecs, memories, `Message`/`Response`/`BackendError`). The
  brief says LLM symbols come from `gao_idl.llm`; before, only `AnthropicBackend`/`to_anthropic_schema` did, so a tutorial would have had
  to import `gao_idl.llm.agent` and `gao_idl.llm.mock`. Additive, no rename, `httpx` is still imported lazily (tests: `import gao_idl.llm`
  without httpx; `import gao_idl` does not import `gao_idl.llm` at all). The list is pinned in the test. Environment-specific codecs
  (`PDCodec`, `AuctionCodec`) stay in their environment modules.
- **"Documented as internal":** no module or symbol was renamed with a `_` (the brief forbids renames, and a module rename would break
  `env_ref` strings and every import in 700 tests); instead **every module under `src/gao_idl` now ends its docstring with a
  "Stability:" paragraph** (63 files stamped by a one-off script, not committed). Three levels: *public* (the 23 names, or the names in
  `gao_idl.llm.__all__`; the module's docstring names exactly them); *supporting* (documented in the API reference, not re-exported, may
  change in a minor release before 1.0: `core.{ids,errors,events,paths,store,ledger}`, the rest of `core.schemas`/`core.layer`,
  `agents.*` rest, `learning`, `environments` factories and their LLM codecs, `testing`, `composition.contracts`); *internal*
  (everything else). `test_every_module_declares_its_stability` fails for a new module without the paragraph, and
  `test_stability_notes_name_exactly_the_exported_symbols` keeps the paragraphs and the export lists in agreement (mutation-checked:
  a wrong name, a missing paragraph, an extra top-level import and an undocumented `Session` property each fail exactly their test).
  The classification is my judgement; the tutorials of task 3 import only public and supporting modules (a test will pin that).
- Every exported symbol, and every public method/property defined in an exported class, has its own docstring (checked by
  `test_every_public_symbol_is_documented`; nothing needed adding). `SimpleLayer`'s hooks are dataclass fields, documented in its
  class docstring, not individually.
- `import gao_idl` takes about 0.4 s here (it imports the runtime, composition, agents and learning packages, numpy and pydantic).

## 2026-10-22 — M6 task 3: documentation (`mkdocs.yml`, `docs/`, `examples/01..06`, `tests/test_examples.py`, `tests/test_docs.py`)
- **The v2 plan was not supplied** (`MATRIX_implementation_plan_v2.md` is quoted, not attached), so the two things the brief says to copy
  from it are *derived*, not copied: the architecture diagram of `docs/index.md` (§2) is drawn from the code (participants, runner,
  session, process tree), and `docs/design/decisions.md` (§0 and §14) is compiled from the invariants and amendments of
  `00_SHARED_CONTEXT.md` plus the deviations in this file. The page says so in an admonition. If the plan's diagram or decision log
  differs, replace those two pages; nothing else depends on them.
- **"The four information objects"** (concepts.md) are my reading: `DecisionRequest`, `ParticipantReport`, `Exit`, `UtilityIncrement`
  (the four things that cross a boundary). The brief does not list them.
- **Examples are digit-named files** (`01_define_a_layer.py`, as briefed), which cannot be imported with `import`. Later tutorials load
  earlier ones with `importlib.import_module("01_define_a_layer")` after putting their own directory on `sys.path`, and environment
  factories are addressed as `"01_define_a_layer:build_public_goods"` / `"02_compose:build_repeated_pg"` / `"02_compose:build_pg_tournament"`.
  This also makes tutorial 4's restore in a fresh process work (the script's directory is `sys.path[0]` there too).
- **Tutorial 1 is 150 lines, not "~60"**: five pydantic models, the hooks as named functions and the factory, formatted at line length 100.
  It defines `action_schema` per call with `create_model(..., amount=(float, Field(ge=0, le=endowment)))` so agents that read the JSON
  Schema know the cap; `feasible` is therefore not needed and the page mentions it as the place for rules a schema cannot state.
  Observation carries `round` so a tabular learner can use it (`FieldObsCodec(["round"])`).
- **Tutorial 2, "substitute it into the PD tournament's Par"** — read as: build a tournament of the same shape as `build_repeated_pd`
  (`Loop(Par(branches with a layer each))`) whose leaves are public-goods layers, and show with `describe()` that the skeleton is the same
  kind of object (`loop(seq(par(primitive, primitive), primitive))` vs `loop(par(primitive, primitive))`). The PD's `Seq` + round counter
  is replaced by a small custom `Joiner` that picks `again`/`done` (this also demonstrates the Joiner protocol). I did not literally
  re-use `build_repeated_pd`'s `Par` object: its `RoundJoin` schema and splitter are PD-specific, and modifying PD is out of scope.
  The feedback connector carries the endowments unchanged ("carrying endowments"); earnings are not reinvested.
- **`build_repeated_pg` gained an optional `layer=` argument** (loop body override) so tutorial 6 can show `assert_substitution_equivalent`
  on a rewritten layer (different summation, mutation-checked: a 1% change in the share makes the helper fail).
- **Tutorial 6** exercises `assert_replay_equivalent`, `assert_incremental_equals_fold`, `assert_arrival_invariant`,
  `assert_noninterference` (team one does not learn team two's endowment) with a *negative control* (the same check fails for `cy`, who is
  told their own endowment) and `assert_substitution_equivalent`. `assert_branch_rng_independence` is described in the page but not run:
  the public-goods layer draws no random numbers, so the check would be vacuous.
- **Tutorials import only public or supporting modules**; `tests/test_examples.py` enforces it (it caught my first draft importing
  `gao_idl.composition.process`, which is internal; the return annotations now use `Primitive`/`Loop`). `from gao_idl.core.ids import ...`
  and `core.schemas`, `learning.*`, `composition.contracts` and `testing` are supporting, i.e. documented but allowed to change before 1.0.
- **`tests/test_examples.py`**: each example runs in a subprocess with `GAO_EXAMPLE_FAST=1`, 20 s limit, and prints an expected line; each is
  mirrored in `docs/tutorials/<name>.md` by a `--8<--` include. Whole file takes about 6 s here (05 is the slowest, ~2 s fast, ~15 s full).
  Under fast mode 03 trains 8 episodes, 05 trains 20 / evaluates 10, 04 plays 2 rounds.
- **`tests/test_docs.py`**: every non-internal module has a `::: module` entry in `docs/api/`, every `__all__` name is listed in
  `docs/api/index.md` with its defining module's page, the nav and the files agree, `site/` is git-ignored, and `mkdocs build --strict`
  succeeds (skipped without mkdocs; uses the real config when `mkdocstrings` is importable, otherwise the same config without that plugin).
- **mkdocstrings could not be run here** (not installed, PyPI unreachable): `mkdocs build --strict` was verified only with the plugin
  removed (the `::: module` lines then render as text). The mkdocstrings options are the minimal documented ones; `docstring_style: null`
  because the docstrings are free-form Markdown, not Google/NumPy style, and parse warnings would fail `--strict`. API pages link to pages,
  not anchors, for the same reason. First thing to check on a machine with PyPI: `mkdocs build --strict` with the real config.
- API pages are generated once from the module list (`docs/api/*.md` are plain files; a one-off script, not committed); `index.md` lists the
  public names. `ruff` excludes `docs/` (it tried to format the `--8<--` fences); CI gets a `docs` job (`mkdocs build --strict`) and a
  `package` job (`python -m build`, `twine check`, `clean_install.sh`), neither of which I could run.
- Nav does not list `docs/ACCEPTANCE.md` yet (task 5 adds it with its nav entry).

## 2026-10-22 — M6 task 4: reproducible demo configs (`gao_idl/run.py`, `gao_idl/replay.py`, `examples/configs/`, `docs/runs.md`)
- **Brief conflict: a module `gao_idl/run.py` vs the exported function `gao_idl.run`** (task 2 pins `run` in `__all__`; task 4 asks for
  `python -m gao_idl.run`). Importing a submodule rebinds the package attribute, so `import gao_idl.run` (as a module) turns
  `gao_idl.run` from the function into the module (checked: `type(gao_idl.run)` goes `function` -> `module`). Conservative reading taken:
  keep both names as briefed, run the CLI **only** with `-m` (runpy executes it as `__main__` and does not rebind the attribute; the
  package never imports it; a test checks that `import gao_idl` leaves `gao_idl.run` a function and loads neither entry point), mark
  both modules *Stability: internal* with the warning in the docstring, and make the test walkers that import every module
  (`tests/test_public_api.py`, `tests/test_docs.py`) read entry points from source through `tests/modules.py::load_module` instead of
  importing them. If this is unwanted, the fix is to rename the module (for instance `gao_idl.cli_run`) and change the command in the
  brief; nothing else depends on the name. `replay` has no clash.
- **Config** (`gao_idl.run.RunConfig`, pydantic, `extra="forbid"`): `env_ref`, `params`, `seed`, `agents` (agent -> adapter). An adapter is
  a description string already understood by `learning.opponents.default_opponent_factory` (`"random"`, `"scripted:<name>"`,
  `"table:<path>"`), or `{"kind": "mock_llm", "reply": {...}}` (my addition, so a demo config can exercise the LLM adapter without a
  network: `LLMAgent` on a `MockBackend` that always answers with `reply`). Real LLM backends are deliberately not configurable (credentials).
  Random agents are seeded with `derive_seed(session seed, agent, "random")`, so a config alone fixes the run (test: two runs give
  byte-identical files).
- **Output** (`gao_idl.run/1`): `config`, `result` (`finished`, `failed`, `port`, `record`), `ledger` (exact float totals for every agent
  in the config or the ledger) and `snapshot` (`Session.snapshot()`). Exit status 0 if the session finished without failing, 1 if
  not, 2 for a bad config or a run that raised `ValueError`/`RuntimeError`/`GaoError` (for example an agent without an adapter: the runner
  reports a stall). **Replay** restores the snapshot (replay of the log), calls `check_determinism()`, then compares result and totals
  with the file (exact equality): status 1 and a message on any difference, a wrong `format`, or a log that no longer replays.
  Tested by editing the ledger, the result port, and one submitted action in the log.
- Demo configs: `pd.json` (tit-for-tat vs random, 10 rounds), `auction.json` (second price with reserve; truthful, random and mock-LLM
  bidders), `pb.json` (five random agents, `greedy`, with private values). The PB config cannot use `knapsack` (see the M6 task 0
  entry: random float costs are not whole cents). PB ledgers are all zero by design: funding utilities are not credited, the voter's value
  is only disclosed in `FundingReport.private`; the totals line of the CLI is therefore uninformative for PB.
- `docs/runs.md` documents the format (nav entry "Reproducible runs"); the CLI modules are not in the API reference.

## 2026-10-22 — M6 task 5: acceptance (`docs/ACCEPTANCE.md`, `examples/acceptance/`, `tests/test_acceptance.py`)
- **Not executed by a human**, as the brief says; this entry only records what was prepared. `docs/ACCEPTANCE.md` has six timed steps (install
  5, tutorials 1-4 15, own layer 15, wiring into PD or PB 10, scripted + table + mock-LLM 10, public symbols 5 = 60 minutes), a tick-box
  checklist per step with the expected output (captured from a real run of the tutorials, selftest and the reference answer), a sign-off
  table and facilitator notes. It is in the nav as "Acceptance".
- **A worked answer exists so the exercise is known to be possible:** `examples/acceptance/` (`my_env.py` a `rate` layer played after the
  repeated PD with `Seq` + `Connector`; `train_table.py`; `run_mixed.py` with a scripted, a table, a mock-LLM and a random agent,
  `check_determinism` and a snapshot/restore round trip), run by `tests/test_acceptance.py` (train, run, restore; and `check()` on the
  composition). The page tells the facilitator not to show it unless the participant is stuck for ten minutes.
- **Sharp edges found while writing the reference answer** (all documented in the page; none is a bug and none was changed):
  1. `ScriptedAgent` policies and `MockBackend` scripts must answer every layer the agent takes part in; the named `scripted:<name>`
     policies of `learning.opponents` are PD/auction-only, so the *CLI* (`gao_idl.run`) cannot seat a scripted agent in a composed
     environment with a third kind of layer. The acceptance run therefore uses a Python script, not the CLI.
  2. A `FieldObsCodec` field must exist in the observation of every layer the learner plays; with no shared field use
     `FieldObsCodec([])` (one state). A failure there inside `run` becomes a timeout (and, under `Fail`, a failed session); inside `train` it
     raises `LearnerError`.
  3. The PD root `Loop` exits through `done` (record `round_index`, `rounds`, `pairs`) and the reserved `truncated`; an exit port that is not
     routed in a `Seq` is re-exported by the `Seq`, so the connector only needs the `done` route.
- Wording in the acceptance page is mine; the brief gave only the list of steps.
