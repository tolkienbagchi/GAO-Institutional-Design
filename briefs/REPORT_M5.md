PyPI was unreachable (DNS failure on `pip download hypothesis`, tried once, not retried) and **`httpx` is not installed in this sandbox**, so `pip install -e ".[dev,llm]"`, all hypothesis tests (3 inherited + 2 new in `tests/environments/test_pb_rules_property.py`), `pytest-timeout`, `python -m build`, Python 3.11 and CI were NOT run, and the real HTTP transports of the OpenAI-compatible/Anthropic backends were only type-checked (the two `httpx.MockTransport` tests stay skipped). M5 itself needs neither httpx nor hypothesis. All results below come from standalone ruff/mypy/pytest on Python 3.13 with `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages`.

# REPORT — M5: participatory budgeting (generated ballot schemas, swappable funding rules)

**Brief name mismatch:** the request called the brief `M5_llm_agents.md`; the attached one is `M5_participatory_budgeting.md` and I treated it as M5 (M4 was the LLM milestone). It has no numbered tasks, so I defined them in the brief's order (see below).

## Gate status

**The gate as literally written was NOT fully met**: the literal `pytest -q` was not run (it fails at collection without `hypothesis`), and `pip install -e .[dev,llm]` was not run. Pytest was run with `--ignore=tests/core/test_paths_property.py --ignore=tests/environments/test_pb_rules_property.py`.

| Gate command | Result |
|---|---|
| `ruff check .` | Pass: `All checks passed!` |
| `ruff format --check .` | Pass: `138 files already formatted` |
| `mypy src/gao_idl` (all packages, 65 files) | Pass: `no issues found` |
| `mypy src/gao_idl/core ... llm` (CI's current line) | Pass, 42 files. CI's line does not cover `environments`, `runtime`, `testing` (not changed, see NOTES) |
| `pytest -q -m "not slow"` | **629 passed, 4 skipped, 2 deselected in 27.6 s** (baseline before M5: 553 passed, 4 skipped, 1 deselected) |
| `pytest -q -m slow` | 2 passed in 38 s (PD run, exhaustive 5-voter EJR enumeration) |
| `python -c "import gao_idl.core, gao_idl.runtime, sys; assert 'httpx' not in sys.modules"` | Pass |

Test command: `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages pytest -q -m "not slow" --ignore=tests/core/test_paths_property.py --ignore=tests/environments/test_pb_rules_property.py`

## What was built

Commits (one per task; messages are the task titles):

```
7fbccd9 Schemas and the four pure funding rules    task 1
8137911 PB layers and build_pb                     tasks 2-3 (merged, see NOTES)
da32a95 Institution tests                          task 4
(this report)                                      task 5
efb1ec1 Inherited from M4                          task 0 (NOTES entry only)
```

```
src/gao_idl/environments/pb/schemas.py   models, generated ballot_schema (create_model over Literal[ids], no-duplicate validator)
src/gao_idl/environments/pb/rules.py     greedy, knapsack (exact DP over cents), mes (cardinality, Add1 + greedy completion), phragmen
src/gao_idl/environments/pb/layers.py    nomination / eligibility / voting layers, funding_layer(name, fn) factory
src/gao_idl/environments/pb/__init__.py  build_pb; rule chosen in one line via FUNDING[rule]
tests/environments/test_pb_rules.py      21 tests (20 not-slow, 1 slow)
tests/environments/test_pb_rules_property.py  2 hypothesis wrappers, NEVER EXECUTED (compile only)
tests/environments/test_pb.py            56 institution tests
NOTES.md                                 four entries appended (below)
```

No runtime or composition code was touched.

## Things I am unsure about

- **Literature fixtures are not verified against the PDFs.** I only had a summarising fetch tool. Every fixture was re-derived by hand and matched, but Peters & Skowron's Example 2 profile was reconstructed from prose, and Brill et al. example numbers are the journal version's.
- **Hypothesis tests never ran**; numpy-seeded equivalents (400 and 200 instances) run always.
- **Python 3.11 not tested** (3.13 only); one PEP 695 slip was caught and fixed.
- **MES variant choices** the brief leaves open: cardinality utilities, Add1 "unit" = one currency unit per voter, last within-budget run returned. Phragmén skips projects that don't fit rather than stopping.
- `DefaultAction` nomination timeout is refused; voting timeout is `Fail`. Both are my readings.
- The brief's `none`-port wording was read literally; the nomination layer itself emits the public "none" report.
- Whether CI should type-check `environments` was left unchanged ("do not touch outside scope").

## NOTES.md entries added in M5

## 2026-10-22 — M5 task 0: brief mismatch, no numbered tasks, PyPI still unreachable
- **The request names `M5_llm_agents.md`; the attached brief is `M5_participatory_budgeting.md`** (M4 was the LLM milestone, and the
  M5 attachment is the PB institution). I treated the attached PB brief as the M5 brief. If a different M5 brief was intended, none
  of this work is from it.
- **The PB brief has no numbered tasks and no "task 0".** The user's instruction asks for "task 0" first and one commit per task, so I
  defined the tasks myself, in the brief's own order: task 0 = this note (inherited/environment items, no code); task 1 = schemas and
  the four pure funding rules with their tests; task 2 = the four layers (nomination, eligibility, voting, funding factory);
  task 3 = the `build_pb` composite; task 4 = the institution tests; the report is the last commit. Each commit message is the title.
- `pip download hypothesis` fails ("No matching distribution"), tried once, not retried. So `pip install -e ".[dev,llm]"`, the
  hypothesis tests (the 3 inherited ones and the 2 new ones in `tests/environments/test_pb_rules_property.py`), `pytest-timeout`,
  `python -m build`, Python 3.11 and CI were NOT run. **`httpx` is not installed either**, so the OpenAI-compatible and Anthropic
  HTTP transports can only be type-checked, and the two `httpx.MockTransport` tests stay skipped. Gate substitute as in M4:
  `PYTHONPATH=src:.:/usr/local/lib/python3.13/dist-packages` with `--ignore=tests/core/test_paths_property.py` and
  `--ignore=tests/environments/test_pb_rules_property.py`. Baseline before M5: 553 passed, 4 skipped, 1 deselected (not slow).
- No new dependency is added in M5 (`fractions` and numpy only).

## 2026-10-22 — M5 task 1: schemas and the four pure funding rules (`environments/pb/schemas.py`, `rules.py`)
- **Common conventions (documented in the module docstring).** `rule(projects, ballots, budget) -> list[str]`, result in ascending
  *natural* id order (`p2` before `p10`, reusing `role_sort_key`; plain string order would put `p10` before `p2`). `ballots` must map
  every voter, abstainers with `[]`: `n = len(ballots)`. Unknown or repeated ids in a ballot are a `ValueError` (not ignored).
  **Projects nobody approves are never funded, by any rule** (my reading of "take if fits": otherwise greedy would spend leftover
  money on projects with zero support). Projects costing more than the budget are never candidates. Costs and budgets are compared as
  exact decimal fractions (`Fraction(repr(x))`), so `0.1 + 0.2 <= 0.3` holds.
- **Greedy:** approvals desc, cost asc, id asc; take if it fits.
- **Knapsack:** exact DP over integer cents (numpy rows). Costs must be whole cents (`ValueError`); the *budget* is floored to cents,
  which is exact for whole-cent costs (the brief says "assert exactness"; asserting it for the budget would refuse e.g. 10.005 although
  the answer is well defined). Refuses `> 60` projects and tables over 50 M cells. Tie-break among optimal sets: lexicographically
  smallest ascending id list (include a project whenever an optimum still exists with it).
- **MES (the one documented variant):** cardinality utilities (every approved project is worth 1 to each approver), so ρ-affordability
  is `sum(min(b_i, ρ)) >= cost`, smallest ρ first, supporters pay `min(b_i, ρ)`, exact rationals. The brief names no utility; cost
  utilities are excluded by "Do not", and approval counts are what greedy and knapsack maximise. **Add1 completion** follows Peters,
  Pierczyński, Skowron et al. as summarised in Boehmer et al., "Participatory Budgeting: Data, Tools, and Analysis" (arXiv:2305.11035,
  §2.2): start at `b/n`, if the outcome is not exhaustive raise every initial endowment by one *unit* and rerun from scratch, until
  exhaustive or until the next rerun would exceed `b`. That paper never says what a "unit" is or which run is returned; I took **one
  currency unit per voter** and **the last run whose cost is within `b`** (round 0 never overspends). *Exhaustive* = no unfunded
  candidate fits the money left. Then **greedy completion** adds whatever still fits in greedy order. Add1 is capped at 100 000 reruns
  (`ValueError`); with costs in the millions and few voters that would be reached. EJR holds for the completed rule on the unit-cost
  instances tested: an Add1 run at endowment `k'/n >= k/n` is MES for a larger committee size, which implies EJR for `k`, and
  greedy completion only adds projects.
- **Sequential Phragmén:** money formulation. `load_i` = money voter `i` has spent; a project is bought at time
  `(cost + sum of supporters' loads) / |supporters|`; smallest time next, supporters' loads become that time. **When the next project
  does not fit the money left it is discarded and the rule goes on** (not "stop at the first that does not fit"); the brief is silent
  and this matches greedy's "take if fits". Pinned by a hand example.
- **Worked examples, and how they were obtained.** The shell has no web access, so I read the papers through the WebFetch tool (a
  summarising model, not the PDFs). Every fixture below was *also re-derived by hand from the definitions* before being pinned, and
  matched, so I trust the numbers but not the transcription of the figures:
  Peters & Skowron, *Proportionality and the Limits of Welfarism* (arXiv:1911.11747), Example 2 (n = 15, k = 4) — **the profile figure
  was not in the extracted text; the tool reconstructed it from the prose** (c1, c2: v1–v12; c3: v1–v10; c4, c5: v6–v15). The stated
  q-values 15/48, 15/48, 15/40, 1 and the committee {c1, c2, c3, c4} reproduce, with the paper's own tie choice (c3 over c4/c5) equal
  to the smaller-id tie-break. Rey & Maly, *The (Computational) Social Choice Take on Indivisible Participatory Budgeting*
  (arXiv:2303.00621): the MES example with cardinality satisfaction (outcome {p1, p2, p3}, bought p2, p1, p3) and the Phragmén example
  (outcome {p1, p2}; the tool noted a garbled cell for p4, which I did not use). Brill, Freeman, Janson, Lackner, *Phragmén's Voting
  Methods and Justified Representation* (AAAI 2017; Math. Programming 2024), Examples 4.3/4.6 (k = 3) and 6.3 (k = 4, final loads
  3/4, 3/4, 0, 0, 3/4, 3/4, 1/2, 1/2). **Example numbers are those of the 2024 journal version as relayed by the tool**, the AAAI
  version may number them differently. **Not verified against the PDFs:** all of the above. For greedy and knapsack there is no
  literature example in the brief's list; their fixtures are mine (labelled HAND in the tests), plus the Rey–Maly instance computed by
  hand. Two MES fixtures (add1, completion) are hand-made too; the Add1 one was found by a random search and re-derived.
- `test_mes_ejr_on_small_instances` enumerates all multisets of ballots for up to 4 voters and up to 4 unit-cost projects with budgets
  1–3, plus 1 500 seeded 5-voter/5-project profiles, for plain MES and for MES with completion (about 6 s). The exhaustive 5-voter
  enumeration is `@pytest.mark.slow` (about 25 s). EJR was checked with the standard unit-cost definition (group size `>= ell*n/k`,
  `>= ell` commonly approved projects); no violation was found, so the implementation was not touched on that account.
- **Hypothesis:** `test_knapsack_matches_bruteforce` and `test_all_rules_within_budget` are in
  `tests/environments/test_pb_rules_property.py` (thin wrappers over the same check functions as the numpy-seeded
  `*_randomized` tests in `test_pb_rules.py`, which always run: 400 and 200 instances). The hypothesis module was NOT executed here.
- Schemas: `NominationState.rule` is a validated `str` and is **not forwarded** through the pipeline (the funding layer is chosen by the
  factory), so no pre-funding trace event depends on the rule. `Nomination.cost` is strict (numeric strings and bools refused, like
  `Bid.amount`). `ProjectsExit`/`EmptyExit` carry `voters` and `proposers` (the public audience), and `VotingState`/`BallotsExit`
  gain `proposers: list[AgentId] = []` — an addition to the brief's field lists, needed so the funding layer can address proposers.
  `ballot_schema` is a cached `create_model` over `Literal[tuple(ids)]` with a no-duplicates validator and `extra="forbid"`; with a
  single project pydantic emits `const` rather than `enum` for the item.
- Mutation checks (all reverted): MES tie to the larger id, Phragmén stopping instead of skipping, knapsack preferring exclusion, no Add1,
  float instead of decimal costs each fail exactly the test that pins them.

## 2026-10-22 — M5 tasks 2-3 merged: the four layers and `build_pb` (`environments/pb/layers.py`, `__init__.py`)
- **Plan change (task 0 listed layers and composite as two tasks):** a layer cannot be exercised without the composite, so the layers and
  `build_pb` are one commit with a smoke test (`test_every_rule_completes_the_pipeline`, `test_the_process_is_well_wired`); the
  institution tests are the next commit.
- **Layout.** `Seq("pb": nomination, proposed -> Seq("after_nomination": eligibility, eligible -> Seq("after_eligibility": voting,
  voted -> funding_<rule>)))`: the brief's chain needs nested `Seq`s because a route's continuation is one process. The two inner names
  are path elements, so ids read `pb/nomination#proposer:0` and `pb/after_nomination/after_eligibility/voting#voter:1`.
  Funding layers are named `funding_<rule>` ("one layer per rule"); `FundingOutcome.rule` carries the plain rule name.
  The rule is chosen in exactly one place: `FUNDING[rule]` in `build_pb` (`FUNDING` is `rules.RULE_FUNCTIONS`).
- **`none` routing (reading).** "The `none` ports re-export at the top level" is taken literally: neither `none` port is routed, so `Seq`
  re-exports both (same `EmptyExit` class, so no schema conflict). The brief also requires a public report when everyone dropped out; the
  eligibility layer never runs then, so **the nomination layer itself delivers the public "none" report** (an `EligibilityReport` with
  `status="none"`, empty lists) to every voter and proposer when nobody nominated, and delivers no report otherwise. The eligibility layer
  does the same when nothing survives the screen.
- **Nomination timeout.** Default `DropRole`; `Fail` is accepted; **`DefaultAction` is refused (`ContractError`)**: there is no neutral
  nomination, a default one would be an invented project (invariant 6). `timeout_nomination` also takes the JSON dict form so session params
  stay JSON. Voting uses `Fail` (the brief gives voting no timeout parameter; a silent abstention would be invented).
- **Roles/ids.** Proposers and voters are sorted by agent id and numbered `proposer:i` / `voter:i`; duplicates inside one list are a
  `ContractError`, the same agent in both lists is allowed. At least one voter is required (an empty electorate is a configuration error);
  an empty proposer list is allowed and simply exits `none`. Project ids `p{i}` follow sorted `(proposer, title)`.
- **Eligibility** rejects `cost > budget` or `cost > max_cost` with reason `over_budget`, `over_max_cost` or `over_budget_and_max_cost`
  (the brief says "reason" only). Surviving projects keep their nomination ids (no renumbering) so ballots refer to stable ids.
- **Ballots** are stored sorted in natural id order in `VotingOutcome` (a ballot is a set; the submitted order stays in `LayerCompleted.actions`).
  The voting layer credits no utility (the brief defines none); funding does not either.
- **Reports.** Eligibility/none: `EligibilityReport`, byte-identical for every recipient. Voting: `VotingReceipt(own_ballot)` to each voter.
  Funding: `FundingReport{public, private?}`; `public` = rule, budget, funded `(id, title, cost)`, spent, and `ballots` only with
  `reveal_ballots`; `private` (key omitted when absent) = `own_value_of_funded`, only for voters present in `private_values`, a missing
  project value counting 0. **No report contains approval counts or any other rule detail** (those live in `FundingOutcome.details`, i.e. the
  trace), so a voter's ballot reaches other voters' streams only through the funded set and `spent`.
- `reveal_ballots` is closed over by `funding_layer(name, fn, *, reveal_ballots=False)` and `private_values` by the connector into voting
  (neither is in the brief's `NominationState`); the whole process is still a function of the `env_ref` params (invariant 3).
- `funding_layer` verifies each rule's answer (known ids, no repeats, exact budget check) and raises `ContractError` on a violation, so a bad
  rule cannot silently overspend.
- `ballot_schema(...)` with a single eligible project gives `{"const": "p0"}` for the item (pydantic's rendering of a one-value `Literal`)
  instead of a one-element `enum`; `RandomAgent`, the local LLM validator and pydantic all handle it. With two or more projects it is an `enum`.
- Not done, by the brief's "Do not": no runtime/composition change, no cardinal ballots, no cost-utility rules.

## 2026-10-22 — M5 task 4: institution tests (`tests/environments/test_pb.py`, 56 tests)
- Every test named in the brief exists under its name. Readings and deviations:
  - **`test_random_agents_complete_pb_for_every_rule`**: `RandomAgent` cannot sample a nomination (`string` with length bounds and
    `exclusiveMinimum`, both unsupported), and the brief only promises PB-free *ballots*. The test wraps `RandomAgent` in a tiny adapter that
    answers `nomination` requests from a table and delegates everything else; PB-specific code is those three lines. Parametrised over
    the four rules, five seeds each, five voters; it asserts no `action_rejected`/`action_defaulted` (every sampled ballot was valid first
    time) and replay equivalence. Extending `RandomAgent` was not done ("Do not add new runtime features").
  - **`test_llm_mock_agent_completes_pb`**: proposers are scripted, the two voters are `LLMAgent`s over `MockBackend([{"approve": ["p0","p1"]}])`
    with the stock `DefaultCodec` (no PB codec). Each mock is called exactly once, receives the request's schema as `response_schema`
    (enum `p0`, `p1`) and a prompt containing the project title.
  - **`test_empty_nomination_routes_to_none`** drives `Session.timeout` directly on both proposers (policy `DropRole`), then checks exit port,
    `role_dropped` count, that no voting request exists, and that voters and proposers got the same public payload. Added: `Fail` policy fails
    the session, no proposers exits `none`, everything ineligible exits `none` with a public report listing the rejections.
  - **`test_public_vs_private_report_split`**'s noninterference fixture is hand-written: `gao_idl.testing.assert_noninterference` submits
    `actions[agent]` to every pending decision of an agent, which cannot drive a pipeline where one agent answers different schemas in
    different stages. The test compares full `ledger.information` streams of two runs that differ only in one voter's `private_values`: all other
    agents' streams are identical; the perturbed voter's differs in exactly two items (her voting observation and her funding report).
  - **`test_ballot_privacy`**: (1) profile where bob's ballot cannot change the funded set (budget fits everything three voters approve):
    alice's and carol's streams are identical for three different ballots of bob, bob's own differs; (2) control: with `reveal_ballots=True`
    the same perturbation does change alice's stream (so the test can see a leak); (3) profile where bob's ballot flips the funded set
    (budget 6): alice's stream differs in exactly one item, the funding report's `funded`, and has no `ballots` key.
  - **`test_rule_swap_changes_only_funding_stage`**: all four rules on the greedy-trap profile (greedy funds p0, knapsack p1+p2); the trace up to and
    including the voting stage's `exited` event is identical after `strip_paths`, **with `t` kept** (stricter than the brief), and the next
    event is `funding_<rule>`'s. Every agent's stream differs only in its last item (the funding report). A second test shows the process
    trees (`describe`) differ only by the funding layer's name.
  - **`test_pb_replay_and_partial_restore_between_stages`**: restore (through `json.dumps`/`loads` of the snapshot) before anything, with
    nomination requests pending, with one nomination already in, between stages, and with one ballot already in; each continuation ends with
    a trace equal to the uninterrupted run's. Uses `private_values`, `reveal_ballots` and rule `mes`.
- Added beyond the brief: `build_pb` argument validation (16 cases), the schema follows the nominations (incl. the lone-project `const`),
  bad nominations (zero, negative, numeric string, bool, NaN, empty/long title, extra/missing key) stay pending, `p{i}` ids ignore arrival
  order (exact trace equality for forward and reversed submission), the voting receipt and `reveal_ballots`, an overspending/ghost-id rule is
  caught by `funding_layer`, session params and snapshot are plain JSON.
- Mutation checks (all reverted, each killed): approval counts leaked into the public report; a voter getting someone else's private value;
  the ballot schema offering only the first project; the eligibility screen disabled; the rule leaking into pre-funding state; project ids not in
  proposer order; ballots always revealed; no `none` report from nomination.
- Two test expectations of mine were wrong at first and were corrected (not the code): with ballots `alice {p0,p2}`, `erin {p2}` greedy funds p2,
  not p0 (more approvals); a cost equal to the budget is eligible (so the over-budget case needs cost 6 with budget 5).
- Timings: `pytest -q -m "not slow"` 629 passed, 4 skipped, 2 deselected in about 30 s; the new tests add about 1 s (`test_pb.py`) and 6 s
  (`test_pb_rules.py`). Slow: two tests, about 36 s (the PD run and the exhaustive EJR enumeration).
- Observation, not changed: `.github/workflows/ci.yml` runs mypy on `core composition agents learning llm` only, so `environments`, `runtime` and
  `testing` (and thus all of PB) are not type-checked in CI although the brief's gate says "all packages"; `mypy src/gao_idl` passes locally.
