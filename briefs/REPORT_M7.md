PyPI was unreachable (tried once, "No matching distribution found", not retried), so `pip install -e ".[dev,llm,docs]"`, the hypothesis tests (`tests/core/test_paths_property.py`, `tests/environments/test_pb_rules_property.py`), `pytest-timeout`, `python -m build`, `httpx`, `mkdocstrings` and Python 3.11 were NOT run. Everything below ran on Python 3.13 with `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`.

# REPORT — M7: tutorials, two environments, agent skills

## Gate status

| Gate command | Result |
|---|---|
| `ruff check . && ruff format --check . && mypy src/gao_idl` | **Pass** (181 files formatted; mypy 81 source files) |
| `pytest -q -m "not slow"` | **Pass with the two hypothesis files `--ignore`d**: 801 passed, 4 skipped, 6 deselected in 75 s (< 120 s). The literal command fails at collection without `hypothesis`. Baseline before M7: 753 passed. |
| `pytest -q -m slow` | **Pass with the same two files ignored**: 6 passed in 83 s |
| `mkdocs build --strict` | **Not met as written.** `mkdocstrings` is not installed. A reduced config (no mkdocstrings, API and design pages excluded) builds strict, including both new tutorials, `designing.md` and `for-agents/llms.md`. The `::: module` entries for `vcm`, `vcm.llm`, `vcm.model`, `vcm.scripted` and the congestion modules were never rendered. |
| `GAO_EXAMPLE_FAST=1 python examples/10_congestion.py && ... 11_vcm.py` | **Pass**: `step 7 model calls: 3`, `done: carbon market` |

Test command: `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages pytest -q -m "not slow" --ignore=tests/core/test_paths_property.py --ignore=tests/environments/test_pb_rules_property.py`

## What was built (one commit per task)

```
8ac8509 M7 task 0: inherited items and environment
576db0e M7 task 1: environments/congestion
b6f2fa5 M7 task 2: congestion tutorial and examples/10_congestion.py
f139428 M7 task 3: environments/vcm
3170298 M7 task 4: carbon-market tutorial and examples/11_vcm.py
2033833 M7 task 5: agent skills, docs/for-agents/llms.md and tests/test_skills.py
fafb991 M7 task 6: designing chapter, nav, README, sdist skills include
```

- `src/gao_idl/environments/congestion/` (model, schemas, layers, scripted, llm, factory) and `.../vcm/` (clearing, model, schemas, layers, scripted, llm, factory); tests `tests/environments/test_congestion.py` (19) and `test_vcm.py` (17, one `slow` parametrized x2).
- `docs/tutorials/{congestion,vcm}.md`, `examples/{10_congestion,11_vcm}.py`, `docs/designing.md`, `docs/for-agents/llms.md`, `skills/{design-environment,write-llm-codec,explain-environment}/SKILL.md`, `tests/test_skills.py`.
- Edits outside the brief's file list, each recorded in NOTES.md: `tests/llm/test_integration.py` (import allow-list for the two environments' `llm.py`), `tests/test_examples.py` (page-name mapping for unnumbered pages, `EXPECTED_OUTPUT`), `docs/api/environments.md`, `mkdocs.yml`, `README.md`, `pyproject.toml` (`/skills` in sdist). `core`, `composition`, `runtime`, `agents`, `learning`, `llm` and `environments/pb` are untouched; the public API is still 23 names.

## Not done

- **Stretch `revenue_to_pb`: not attempted.** I did not establish whether it can reuse the PB layers without a change to `environments/pb` (forbidden to fork). Skipped, so nothing claims it.
- **VCM learner result is negative.** The brief asks for a tabular buyer that learns to discount a biased verifier when the record is visible. On the fixture (and the variants I tried) every trading bid loses money, so the learner learns to stay out of the market with or without the record, and there is no visible-vs-hidden separation. The `slow` test asserts only what is true (naive buyer loses, learner does not), and the tutorial says so. The lemons effect is shown with scripted buyers instead.

## Where the brief underdetermined the design, and what I chose

(These feed the design skill's question list; Q-numbers refer to it.)

1. **No "task 0"** in the brief; task 0 is a NOTES entry (as in M5).
2. **Toll counter (Q5).** "Counter as in PD": no separate counter layer; `exposure` exits `again`/`finished`.
3. **Metric the regulator is paid on (Q9).** New factory parameter `equity_metric`; refused if the regime hides it.
4. **Payoff in a report (Q9).** The regulator's report carries `payoff = -objective` because `RewardExtractor` cannot negate.
5. **"Citywide mean only" (Q8).** `public_report` adds `citywide_mean` to every report.
6. **Value of time "drawn from the seed" (Q2).** The factory cannot see the session seed: `population_seed` parameter; explicit `commuters` override.
7. **Committed vs chosen toll (Q3).** Both forms announce the toll to every commuter so they cannot tell which is running.
8. **Timeouts (Q7).** Congestion: `Fail` or `DefaultAction`, `DropRole` refused. VCM: verification `Fail` only; a dropped or defaulted market role is an order for 0.
9. **VCM verifier assignment.** One verifier per project (a partition), not "a subset" with overlap; enforced.
10. **VCM measurement vs report.** A verifier's measurement (truth + bias + noise) is distinct from its report.
11. **VCM prices and baskets.** Price per credit; credits pooled across sellers, so a buyer's basket is a pro rata mix.
12. **VCM clearing tie/indifference rule.** Midpoint (k=1/2) of the marginal pair; bid equal to ask trades; pro rata by quantity at the margin; exact `Fraction` arithmetic.
13. **VCM audit randomness (Q4).** One draw per project per round keyed by path, in project order.
14. **VCM `settle_on`.** Affects only the ledger's settlement; reports state value at known quality, so it cannot leak truth.
15. **Test drivers.** `assert_noninterference` / `assert_arrival_invariant` need every listed agent pending in round one; multi-stage environments use hand-rolled comparisons.
16. **Tutorial structure.** Tutorial 10 rebuilds a miniature from the public API (making `congestion/model.py` supporting); tutorial 11 switches `build_vcm` features on instead, because its layers are internal.
17. **"The four information objects"** were not defined in the briefs I had; I chose observation, action schema, report, exit record.
18. **"Generic fixture names"** = the six public `assert_*` names in `gao_idl.testing`.
19. **Diagram** in `designing.md` is a text block, not Mermaid (no Mermaid support configured; no dependency added).

## Unverified or unsure

- Strict mkdocs with mkdocstrings; hypothesis tests; Python 3.11; wheel/sdist build (including the new `/skills` include).
- Prose numbers in both tutorials are quoted from runs of the examples; the congestion ones are pinned by tests, the VCM ones (prices 7.2 → 4.6, share 0.67 → 1.0) are asserted only in direction (`shown[-1] > hidden[-1]`).
- `llms.md` is checked mechanically for the presence of every public name only; signatures and prose were written by hand from the code and may drift.
- The codec skill's leak test detects hidden *numbers* in prompts, not leaks in words.
- Calibration of the VCM fixture (lemons effect) came from a grid search in a scratchpad, not from any real market; both environments are stylized and make no empirical claim.

## NOTES.md entries added in M7

## 2026-10-22 — M7 task 0: no numbered task 0 in the brief; PyPI unreachable; reading order
- **The brief numbers its tasks 1–6 and has no task 0** (the request says "starting with task 0"). As in M5, task 0 is this entry:
  inherited/environment items, no code. The request also names the brief `M7_tutortials_agent_skills.md`; the attached file is
  `M7_tutorials_and_agent_skills.md` (typo only). I read the shared context with all amendments, the brief, this file in full and
  every `briefs/REPORT_*.md` (M0–M6 and the four fix-ups) before touching code.
- **PyPI is unreachable** (`pip download hypothesis`: "No matching distribution found", tried once, not retried). So
  `pip install -e ".[dev,llm,docs]"`, the hypothesis tests (`tests/core/test_paths_property.py`,
  `tests/environments/test_pb_rules_property.py`), `pytest-timeout`, `python -m build`, `httpx`, `mkdocstrings` and Python 3.11 are
  unavailable/unverified, exactly as in M0–M6. `mkdocs` and `mkdocs-material` are present. Gate substitute:
  `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages` with the two hypothesis files `--ignore`d. **Baseline before M7:
  753 passed, 4 skipped, 2 deselected (not slow) in 58 s; ruff, ruff format and `mypy src/gao_idl` (68 files) clean.**
- Dependencies: M7 adds none (stdlib, numpy and pydantic only).
- The brief's "Stretch (only if tasks 1–6 are otherwise done)" is item 6 of task 1's layer list, not a reference to the brief's
  tasks. It is decided at the end of task 1 (see that entry).

## 2026-10-22 — M7 task 1: `environments/congestion/`
- **Layout.** `model.py` (internal; the pure stylized city: zones, routes, BPR, the exposure surrogate, both equity metrics,
  the commuter population), `schemas.py` and `layers.py` (internal), `__init__.py` (`build_congestion`, supporting), `scripted.py`
  (`myopic_commuter`, `greedy_regulator`; supporting) and `llm.py` (`RegulatorCodec`, `CommuterCodec`, `CongestionReportRenderer`;
  supporting). No change to core, composition, runtime, agents, learning or llm. Public API unchanged (23 names).
- **Composition.** `Loop(Seq(toll_setting, Seq(travel, exposure)))`, loop name `congestion`, `max_iter=periods+1`,
  `on_limit="fail"`. The brief says "normal termination through a counter, as in PD"; I did not add a separate counter layer:
  `exposure` is the last automatic layer of the period and exits `again` or, after the last period, `finished`. Same semantics, one
  layer fewer. Say if you want the PD's separate `round_counter`.
- **Store.** `exposure.outcome` writes `prev:times` and `prev:report` (the regulator-visible report); the feedback connector reads
  `prev:*` and copies both into the next `PeriodIn` (amendment: `observe` has no store access). Period 0 starts from free-flow
  times (`t0`) and no report — the declared initial condition, nothing invented.
- **Brief gaps, conservative readings.**
  - `equity_metric` (the metric the regulator's utility `-objective` is defined by) is a factory parameter. It defaults to the
    regime's metric; under `report_regime="both"` it must be given (`ContractError` otherwise); a metric the regime hides is
    refused, so the regulator's objective can never leak a hidden metric.
  - The regulator's report carries `objective` and `payoff = -objective` (what the exposure layer credits it). `payoff` exists so a
    tabular learner can take its reward from a report field, as the learning package requires; `RewardExtractor` cannot negate.
  - `public_report` adds `citywide_mean` (population-weighted mean concentration over all zones) to every report, regulator's
    included. The brief says only "citywide mean concentration only".
  - The factory does not see the session seed, so "value of time drawn from the seed" is a factory parameter, `population_seed`
    (default 0). Explicit `commuters` (dicts) override the draw. `tutorial_commuters()` is the explicit six-commuter fixture of the
    tutorial and the headline test.
  - Both toll forms announce the toll to every commuter (public report of `toll_setting`), so commuters cannot tell which form is
    running. The comparison test drops the regulator's own events (its report, its utility increments) and the global clock `t`.
  - Commuter reports are sent by `exposure` (it knows the zone concentration) to agents that hold no role in that layer: `audience`
    names them. The runtime accepts this; nothing in the brief forbids it.
  - Timeouts: `Fail` (default) or `DefaultAction` (commuters take `transit`, which is open to all; the regulator plays
    `default_toll`); `DropRole` is refused for both layers, like the PD.
  - `ev_share` is validated at construction as well as in the kernel.
- **Stylized numbers.** All of `default_city` is stylized (module docstring says so): 3 peripheral zones (`Z3` a DAC), fare 3,
  500 vehicles per commuter-agent, BPR caps 4000, threshold 1.2 and DAC weight 2 in the objectives. The numbers were chosen by a
  seeded random search, run in a scratchpad and not part of the repo, for one property: with `tutorial_commuters()` the hotspot
  objective is lowest at toll 15 and the population-weighted one at toll 10, toll 0 worse than the best on both, with every
  commuter's best route at least 1 cost unit better than the second best at every toll, so the outcome is stable under BPR and the
  myopic commuter does not oscillate. A first fixture without that margin did oscillate at toll 10 (period-by-period flip between
  two routes); that is why the margin is part of the fixture's explanation in `test_equity_metrics_can_disagree`.
- **Tests** (`tests/environments/test_congestion.py`, 19 tests, 2 `slow`): all the brief's named tests plus factory validation,
  restore at three points, population determinism, commuter reports. `assert_noninterference` needs every listed agent to have a
  pending decision in the first round, so the `v_i` test uses the committed form (no regulator in round 1) and checks that the
  helper does catch c0's own information changing. `assert_substitution_equivalent` compares the whole trace including the
  regulator's decision events, so it cannot be used for the two forms; the test compares the downstream events directly.
- **Out-of-scope edit.** `tests/llm/test_integration.py::test_nothing_below_the_llm_package_imports_it` has an allow-list of
  environment modules that may import `gao_idl.llm`; `congestion/llm.py` (required by the brief) is added to it.
- **Stretch (`revenue_to_pb`) — not done in this task.** It reuses the PB layers inside a stage that does not exist yet; decide at
  the end of the milestone, only if everything else is green. Recorded as a decision to revisit, not as done.

## 2026-10-22 — M7 task 2: tutorial "Congestion pricing as an institution"
- **Files.** `docs/tutorials/congestion.md` (the path the brief names), `examples/10_congestion.py`, nav entry "10. Congestion
  pricing". The existing tests expect `docs/tutorials/<example stem>.md`; for stems numbered 10 and up, `tests/test_examples.py`
  now also accepts the page named without the number (`congestion.md`). `EXPECTED_OUTPUT` gains `10_congestion`, and
  `test_the_six_tutorials_exist` is renamed `test_the_tutorials_exist` (same check). Both edits are what task 6 asks for anyway.
- **Walk-through vs the real environment.** Examples may import only non-internal modules, and `layers.py` / `schemas.py` are
  internal, so steps 1–5 rebuild a *miniature* of the environment from the public API (`SimpleLayer`, `Seq`, `Loop`,
  `Connector`) around one permissive `Stage` record, with the real kernel from `congestion.model`; steps 6–7 use
  `build_congestion`. To allow that, `congestion/model.py` is now **supporting** (documented in the API reference), not internal:
  it is the swappable kernel, which the docs chapter points at. Nothing else changed in `src/`.
- **Mirroring.** The page shows each step through snippet sections (`--8<-- "examples/10_congestion.py:<section>"`) and the whole
  script once at the end, which is what the mirror test checks for.
- **Prose numbers** (tolls `[0, 10, 15, 15, 15, 15]` vs `[0, 10, 15, 10, 10, 10]`; DAC exposure 1,938 vs 1,020, non-DAC 255 vs 638) are
  quoted from a run of the example; `test_equity_metrics_can_disagree` pins the objectives they come from.
- **Not verifiable here.** `mkdocs build --strict` needs `mkdocstrings`, which is not installed (PyPI unreachable). I built the
  tutorial pages with a reduced config (no API pages, no mkdocstrings) and checked that every snippet section resolves and the
  section markers are stripped; the full strict build stays unverified, as in M0–M6. The real-backend snippet in step 7 is shown,
  not executed (it needs credentials and `httpx`).

## 2026-10-22 — M7 task 3: `environments/vcm` (voluntary carbon market)
- **Prices and baskets.** Prices are per credit. Credits are pooled across sellers: a buyer's credits come pro rata from the
  sold projects, so a buyer's true value of a fill is the quantity-weighted true quality of the basket, not of one project.
- **Verifiers.** The brief did not say how verifiers map to projects; I chose a one-verifier-per-project partition
  (`Population.check()` enforces it). A verifier's *measurement* (true quality + bias + noise, `sigma`) is distinct from its
  *report* (what it publishes); honest scripted verifiers publish the measurement. The verification layer accepts only `Fail`
  timeouts (a missing verification has no sensible default).
- **Clearing.** Uniform-price call auction, exact `Fraction` arithmetic. Price = midpoint (k=1/2) of the marginal bid/ask pair;
  ties on the long side are rationed pro rata (not by arrival). Hand-worked cases are tests. `DefaultAction`/`DropRole` on a
  market role means an order for quantity 0.
- **Audits.** One uniform draw per project per round, in project order, keyed by path (not by a stateful counter), so the draws
  do not depend on who acted first. Penalty applies when |reported − true| > `tol`. `mean_error` is 0.0 when nothing was audited.
  Reputation lives in the store (`rep:<verifier>`, `last:clearing`); the observation connector copies it into state because
  `observe` has no store access (Amendments).
- **`settle_on`.** "reported"/"true" changes only the ledger's settlement value; it is not shown to reports (buyer reports state
  value at known quality), so it cannot leak the truth.
- **Test drivers.** `assert_noninterference` / `assert_arrival_invariant` need every listed agent pending in round 1, which a
  multi-stage market lacks; the tests use custom drivers instead.
- **`population_seed`** seeds only the drawn population, never play.
- **Slow learning test (decision).** I first tried to show a tabular buyer exploiting the visible track record. On this fixture
  every trading bid loses money (about −13 vs honest, −20..−24 vs inflating verifier), so the learner stays out whether or not
  the record is visible. The slow test therefore asserts only that the naive buyer loses and the learner does not; it does not
  claim a visible-vs-hidden separation for learners. The reputation effect is shown with scripted naive vs skeptical buyers
  (tutorial step 5). Underdetermined by the brief; conservative reading taken.
- **Out-of-scope edit.** `vcm/llm.py` added to the import allow-list in `tests/llm/test_integration.py`.

## 2026-10-22 — M7 task 4: tutorial "A carbon market with verifiers"
- **Files.** `docs/tutorials/vcm.md`, `examples/11_vcm.py`, nav entry, `EXPECTED_OUTPUT["11_vcm"]`. The page-name mapping from
  task 2 already covers `vcm.md`.
- **No hand-built miniature.** Unlike tutorial 10, `vcm/layers.py` and `schemas.py` are internal and a faithful miniature would
  duplicate them, so steps 2–4 switch features of `build_vcm` on (`p_audit`, `rounds`, `reputation_visible`) and inspect the ledger.
  Step 1 is `clear_call_auction` alone. Underdetermined by the brief ("add verification…"); this is the conservative reading.
- **Step 2** is the noninterference comparison written directly (complement all `theta`, compare buyers'/sellers' streams) since
  the helper needs round-one decisions for every listed agent.
- **Step 5 (experiment)** uses scripted buyers (naive vs skeptical, record hidden vs shown). Biased-verifier volume share is taken
  from the sellers' own fills (s2, s3 are v1's). Result and its limits (pooled basket drives it) are in the prose.
- **Step 6 (learning buyers)** shows the tabular buyer staying out of the market (0 vs −90 for the naive buyer) in both
  conditions: no visible-vs-hidden separation on this fixture; stated in the tutorial, not tuned away.
- **"Each a one-line parameter or a swappable layer"**: fee, penalty/tol, `p_audit`, `reputation_visible` are parameters; audit
  targeting and fee-on-correctness are layer swaps. The page says so.
- Prose numbers are quoted from a run; `mkdocs build --strict` remains unverified (no mkdocstrings).

## 2026-10-22 — M7 task 5: skills and `docs/for-agents/llms.md`
- **Files.** `skills/{design-environment,write-llm-codec,explain-environment}/SKILL.md` (167, 163, 85 lines; limit 400),
  `docs/for-agents/llms.md` (≈330 lines; limit 1500), `tests/test_skills.py`.
- **Runnable blocks** are fenced `python` blocks whose first line is `# runnable`; the test `exec`s each with the repository root as
  working directory and `examples/` on `sys.path` (so skills may use the digit-named example modules). I mutation-checked that a
  failing assertion in a block fails the test. The design skill's block uses the PD environment, not an invented one, so it needs no
  importable module.
- **"Generic fixture names"** are read as every public name in `gao_idl.testing` starting with `assert_` (six); the test requires
  the design skill to mention all of them.
- **"Nine invariants"** in `llms.md` are the nine from the shared context verbatim in substance. **"The four information objects"**
  were not defined in the briefs I was given; I chose observation, action schema, report and exit record (the ledger and trace are
  listed as researcher-side only). Underdetermined; the design skill's questions refer to these.
- **Skill links** in `llms.md` are repo paths in code spans, not URLs, because the repository's public URL is not known to me and
  `mkdocs --strict` rejects links out of `docs/`.
- **Codec skill's leak test** compares the numbers in the prompt with the numbers in everything the agent was given (observations,
  schemas, delivered reports). That detects a leak of any hidden number; it does not detect a leak in words.
- **`llms.md` accuracy** is checked mechanically only for the presence of every public name (23 + 24); signatures and prose were
  written from the code and the shared-context amendments and are not machine-checked.

## 2026-10-22 — M7 task 6: designing chapter and integration
- `docs/designing.md` (nine questions in prose, a text diagram of the four information objects, both tutorials as worked cases),
  nav entries "Designing an environment" and "For AI agents", README "Tutorials" section, `/skills` added to the sdist include list.
  `tests/test_examples.py` already runs both new examples under `GAO_EXAMPLE_FAST=1` (tasks 2 and 4).
- The diagram is a fenced text block, not Mermaid: the docs theme config has no Mermaid support and I did not add a dependency.
- **Stretch `revenue_to_pb` — not done.** It would need the PB layers reused inside a new stage; the brief forbids forking
  `environments/pb`, and reuse without a change to `pb` (out of scope) was not possible for me to establish in the time. Skipped, not
  attempted.
