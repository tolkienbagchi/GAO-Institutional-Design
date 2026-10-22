# Design decisions

This page is the decision log of the implementation plan (v2, October 21, 2026: its section 0, the
D1–D12 table, and section 14, the log), lightly edited, followed by the decisions taken while the
package was built, grouped by milestone. The plan used the working package name `matrix_envs`; here
it reads `gao_idl`. Where an implementation decision differs from the plan, the later one wins and
`NOTES.md` has the date and the detail.

## Decisions taken in the plan (D1–D12)

| # | Decision | Replaces (v1) |
| --- | --- | --- |
| D1 | A process is a *small-step state machine* with explicit, serializable frames. No generators or coroutines for process state. | Implicit in the v1 process section; the example hardcoded a two-layer sequence. |
| D2 | Resumption is *event-sourced*: a session is fully determined by (environment reference, parameters, seed, log of external inputs). Snapshot = that log. | v1 "save enough session state". |
| D3 | The runtime owns a per-agent **ledger** (cumulative utility, delivered-information stream) and a per-episode **store** with declared read/write scopes. Connectors stop threading everything by hand. | v1 "state transfer by connectors only". |
| D4 | Action, observation, report and exit schemas are **pydantic v2 models**. One declaration drives validation, JSON Schema for LLM structured output, serialization, and future human forms. | v1 "types and runtime validators" (unspecified). |
| D5 | `Choice` is folded into port-routed `Sequence`. Three constructors: `Seq`, `Par`, `Loop`. | v1 had four constructors. |
| D6 | Timeout/absence is part of the **layer's contract** (`fail`, `default_action`, or `drop_role`), always logged as a distinct event. | v1 "do not silently invent an action": kept, but given a mechanism. |
| D7 | The `Session` object is synchronous and single-threaded. All concurrency lives in the `Runner`. | v1 async runner "sits above": made strict. |
| D8 | Build order for reference environments: **PD → auction → PB**. PB is the hardest and goes last. | v1 order. |
| D9 | `numpy` is a core dependency (for `SeedSequence` and RL tables). pydantic is core. Everything else is an extra. | v1 "keep core small": slightly relaxed. |
| D10 | PPO is **out of this plan**. It gets its own plan once trajectory collection exists. | v1 PPO sections. |
| D11 | Overlapping identities across `Par` branches are a *splitter policy*, not an architectural invariant. Core data structures (ledger, pending map, paths) never assume one pending decision per agent. v1 ships `DisjointSplitter`; relaxing it later touches no runtime code. | v1 "disjoint identities". |
| D12 | PB voting rules: approval ballots with greedy and exact knapsack, plus Method of Equal Shares and sequential Phragmén. Aggregation rules are pluggable automatic layers sharing one ballot schema. | v1 PB scope. |

## Decisions log (resolved October 21, 2026)

1. **Name.** Working import name `gao_idl`, distribution `GAO-Institutional-Design-Lab`. Placeholder;
   rename before release. Keep the import name out of user-facing docs until it is final so the
   rename is a `sed`. *(As built, the distribution is called `gao-idl` and the import name is
   `gao_idl`.)*
2. **Store vs. pure threading.** Store with declared per-layer read/write scopes (D3). Pure
   threading means every connector in a loop must carry every persistent fact forward, and
   PD-with-rematching or PB eligibility make that noisy fast; the scoped store keeps information
   flow auditable (every access traced, scopes checked at construction) while removing the
   boilerplate. Revisit only if the scope declarations turn out to be ignored in practice.
3. **Identity in parallel branches.** Flexible by construction (D11): v1 ships only the disjoint
   splitter, but the runtime never assumes disjointness.
4. **PB voting rules.** Approval ballots; greedy + exact knapsack, MES, sequential Phragmén (D12).
5. **Pydantic v2** is a core dependency.
6. **Python ≥ 3.11.**
7. **Trace policy.** Record everything; redaction is an export filter, never a recording switch.

**Still open** (not blocking M0–M2): whether to add cumulative/cardinal ballots alongside approval
in PB; whether `numpy` stays core or moves to a learning extra once the hash-based RNG derivation
is written (it may only need `random`).

## Decisions taken during implementation

Each group was confirmed at the review of that milestone; later groups win where they differ.

### M0: skeleton and core

1. **Resolution events are emitted at settle time.** `ActionAccepted`, `ActionDefaulted` and
   `RoleDropped` are emitted by `Primitive` when the layer settles, in role order, immediately
   before `LayerCompleted`, not when the input arrives. The trace is therefore a function of the
   *set* of resolutions, not of arrival order or `advance()` boundaries. `Fail` is the exception:
   `Failed` is emitted as soon as the timeout is processed.
2. **`ActionRejected` is not replayable.** It is emitted at `t = clock.now` without ticking the
   clock, never enters the input log, and is excluded from `check_determinism`.
3. **`TraceSink` and `LogicalClock` live in `gao_idl.core.trace`** (so `core.store` can emit
   `StoreAccess`), re-exported from `composition.process`. `tick()` increments then returns; the
   first event has `t=1`.
4. **`Layer` and `Process` declare `name`, `entry`, `exits`, `timeout` as read-only properties**, so
   frozen dataclasses satisfy them under `mypy --strict`.
5. **`Store(clock, initial=None)`**; `StoreView.get` deep-copies; `get` on an in-scope but unset key
   raises `KeyError` without emitting.
6. **`ctx.path` includes the root**: a root `Primitive` named `bidding` runs at `("bidding",)`;
   decision ids look like `bidding#bidder:0`.
7. **`PrimitiveFrame` has extra fields** `defaulted`, `exit`, `failed`; the `done` phase is
   idempotent for both exit and failure.
8. **`Session.failed: bool`** distinguishes a failed session (`result is None`) from an unfinished
   one.
9. **`StepResult` gains `Continue`** (added in the M0 fix-up): "progress made, step me again without
   new inputs". `Session.advance()` loops while it receives `Continue`. Composites return
   `Continue` after starting a child or routing an exit; they never loop internally. `Primitive`
   never returns `Continue`.
10. **`check_determinism` requires an empty inbox** (it raises otherwise).
11. **Role order is natural sort** (`bidder:2` before `bidder:10`) via `core.ids.role_sort_key`.
12. **`Contract.roles` may be empty for dynamic role sets**; constraints are still opaque.
13. **`submit` rejects non-strictly-JSON actions** (`NaN`, non-JSON objects) as `SchemaError` before
    validation.
14. **Event `kind` fields have defaults**; `LayerEntered` carries only `t` and `path`.
15. The `testing` helpers assume at most one pending decision per agent per call and submit
    `actions[agent]` to each; this is a helper limitation, not a runtime one (the runtime supports
    several pending decisions per agent, and a test covers it).

### M1: composition

1. **`Par` applies inputs one at a time, in input-log order, each to quiescence**
   (`ParFrame.backlog`, `ParFrame.settling`). The global interleaving of trace events from
   different branches therefore depends on submission order, which is allowed because log order is
   part of the log. **Arrival invariance is defined per projection:** information streams, the
   ledger, the trace restricted to each branch root, and the trace restricted to non-branch paths
   must each be order independent; the global trace need not be.
2. **Composites emit no trace events of their own**; only `Loop` emits `Truncated`/`Failed`, and a
   failing connector emits `Failed`. Hence `Seq` is associative modulo paths.
3. **Composites take `name: str`** (defaults `"seq"`, `"par"`, `"loop"`); it is the path element.
   Names may not contain `/ # @ [ ]`.
4. **Loop limit:** `max_iter=k` runs iterations `0..k-1`; truncation triggers when feedback would
   start iteration `k`, and the feedback connector is not run then. The exit is
   `{"iterations": k, "last_exit": record}` on the reserved port `"truncated"`.
5. **Store-scope violations fail the session** (`FailedResult` plus a `Failed` event) rather than
   escaping `advance()`. After the M1 fix-up the caught type is `StoreScopeError` only.
6. **`DictJoiner` record shape** (after the M1 fix-up): `{branch: {"port": ..., "record": ...}}`.
7. **Frames are JSON-dumpable, not loadable.** Session state is only ever rebuilt by replay.
8. **Quiescence idempotence is a `Process` requirement:** stepping a waiting subtree with no inputs
   must emit nothing and return the same `Pending`. `Par` correctness depends on it. Any new
   `Process` implementation must satisfy it;
   `tests/composition/test_nested.py::test_random_schedules_replay_to_the_same_trace` is the
   regression test.
9. **`Connector` has `.apply(record, ctx) -> Config`** and receives a `ReadOnlyLedger`; connectors
   never emit participant information.
10. **`Session.advance()` raises `RuntimeError` after 10 000 consecutive `Continue`s.**
11. **Auction reserve** (after the M1 fix-up) acts as a seller's bid: second price pays
    `max(reserve, highest other bid)`; unsold items are reported to all bidders via `UnsoldReport`.

### M2: PD, auction and the runner

1. **`Layer.observe` is a pure function of `(cfg, role, rng)` and has no store access.** Persistent
   information an agent should see at decision time is copied into the layer's entry state by a
   connector (which does have a `StoreView`). What an agent can observe is therefore fully
   determined by its entry `Config`, which is what the noninterference tests inspect.
2. **Store scope keys are glob patterns** (`fnmatchcase`), exact match checked first. `hist:*` is the
   canonical example.
3. **Runner semantics:** on attach it delivers earlier reports from the trace and dispatches all
   pending decisions (catch-up), so a restored session works with fresh adapters. Submissions
   happen in one coroutine in deterministic dispatch order. Requests and reports are deep-copied
   before reaching an adapter. The runner reads `session.ledger` in exactly one place,
   `final_utility`, and only when `RunnerPolicy.disclose_final_utility=True`. After the M2 fix-up:
   on any exit the runner cancels in-flight `act` tasks (see M3 item 6 for the later change to
   failure handling).
4. **Adapter-side guards:** `ScriptedAgent` and `HumanTransportAgent` raise `ValueError` on a report
   addressed to another agent and deduplicate by `report_id`.
5. **`DecisionRequest.deadline` is advisory**; the runner enforces only
   `RunnerPolicy.deadline_seconds`.
6. **`RandomAgent` samples from the JSON Schema**; `$ref` and nested models are unsupported. After
   the M2 fix-up, arrays of enum items are supported (sampled without replacement).
7. **PD layout:** `Loop(Seq(Par(pair_k: Primitive(pd_round)), round_counter))`; pair ids and the
   circle method are in `environments/pd/matching.py`; `PDRoundExit` carries `round_index`;
   `build_repeated_pd` takes `timeout`/`default_move` after the fix-up.
8. **Tests must not hang:** `pytest-timeout` (30 s) is a dev dependency and every runner-dependent
   `await` in tests is wrapped in `asyncio.wait_for`.

### M3: tabular learning

1. **Learner rewards come only from report fields** named in `RewardExtractor.fields`; `TableAgent`
   has a static import guard against `runtime`, `ledger`, `store`. The *trainer* reads
   `session.ledger` for true returns after each episode (researcher-side), never the agent.
2. **Truncation default is terminal** (after the M4 task 0 change):
   `QConfig.bootstrap_on_truncation=False`. The opt-in bootstrap uses `max_a Q(s_K, a)`, the last
   state as a proxy for the unobserved next state, and is documented as biased.
3. **Failed episodes are rolled back**: `TableAgent` logs table writes during an episode and undoes
   them on `episode_end(terminal="failed")` or on an `episode_start` that finds an unfinished
   episode. ε decay and `episodes_seen` advance only on completed episodes with `learning=True`.
4. **`FieldObsCodec(fields, history=0, *, report_fields=())`**: codecs see only reports since the
   previous decision; `report_fields` selects fields from those reports.
5. **The trainer wraps the learner in `_Pinned`**: an exception in the learner's `act` is recorded
   and re-raised by the trainer as `LearnerError` (the runner would otherwise turn it into a
   timeout and silently fail every episode). Episode seed is `spec.seed * 1_000_003 + e`.
6. **Runner cancellation** now also sends `episode_end(terminal="failed")` to every adapter
   (reverses the M2 fix-up's reading).
7. **Test markers:** `slow` tests run nightly in CI, `pytest -q -m "not slow"` on every push.
8. **Toy environments** `environments/toy/{bandit,chain}.py` exist for learning sanity checks; the
   chain pays 1 exactly on the transition `1 --right--> 2`.

### M4: LLM integration

1. **`gao_idl.llm` is optional** (`[llm]` extra, `httpx`). Nothing in `core`, `composition`,
   `runtime`, `agents`, `learning` or `environments` imports it; a subprocess test enforces this.
   Provider backends use raw HTTP via `httpx`; there are no provider SDKs.
2. **`AgentFailed` lives in `core/errors.py`; `ExternalRecorder` in `agents/base.py`** (the runner
   may not import `llm`). The runner turns `AgentFailed` into a trace-only `agent_failed` event and
   then `session.timeout(decision_id)`, so the layer's `TimeoutPolicy` resolves it.
3. **External responses are input events.** The runner drains `ExternalRecorder.drain_external()`
   and calls `session.record_external(decision_id, payload)` *before* the submission or timeout
   for that decision. Failed calls are logged too (`{"attempt", "error"}`), so replay reproduces
   failures in place. `ReplayBackend` serves these per decision (keyed via a ContextVar set by
   `LLMAgent`) and raises `ReplayExhausted(DeterminismError)` when asked for more.
4. **`agent_failed` and `action_rejected` are the two trace-only kinds**: not in the input log,
   excluded from `check_determinism`.
5. **Codec render contract:** `render(request, memory, rejections)` returns
   `[system..., *memory, turn...]`; the agent remembers `render(request, [], [])` minus system
   messages as the turn, committed lazily (at the next report, decision or episode end) so a
   re-dispatch does not duplicate it.
6. **Local validation is authoritative** (`llm/validate.py`: strict types, `enum`/`const`, numeric
   bounds, `required`, `additionalProperties: false`, `items`, `anyOf`, local `$ref`). A provider's
   narrower schema support is handled by transforming the *request* schema (`to_anthropic_schema`
   after the M4 fix-up), never by relaxing the validator.
7. **Disclosure gaps closed in the M4 fix-up:** PD observations carry the payoff table when
   `disclose_payoffs=True` (default); auction observations carry `reserve`.
8. **Backends:** `OpenAICompatBackend` (chat completions, `response_format: json_schema` with text
   fallback) and, after the M4 fix-up, `AnthropicBackend` (Messages API, `output_config.format`
   json_schema, JSON in the text block). Both retry three times with backoff on 429/5xx/timeouts,
   take credentials through a `SecretStr`-returning callable, and scrub them from every error and
   record.

### M5: participatory budgeting

1. **PB layout:** `Seq("pb": nomination, proposed → Seq("after_nomination": eligibility, eligible →
   Seq("after_eligibility": voting, voted → funding_<rule>)))`. Both `none` ports re-export at the
   top level; the layer that discovers emptiness (nomination or eligibility) delivers the public
   "none" report itself.
2. **Funding rules are pure functions** `rule(projects, ballots, budget) -> list[str]` in
   `environments/pb/rules.py` (`greedy`, `knapsack`, `mes`, `phragmen`; `RULE_FUNCTIONS` maps
   names), wrapped by `funding_layer(name, fn, *, reveal_ballots)`. Results come in natural id
   order; ties by id ascending; exact `Fraction` arithmetic; zero-approval projects are never
   funded; the layer re-checks a rule result (`ContractError` on overspend or unknown id).
3. **MES variant:** cardinality utilities; Add1 completion (raise every endowment by one currency
   unit per voter and rerun; keep the last within-budget run) then greedy completion. **Phragmén:**
   continuous loads, purchase time `(cost + Σ loads)/|supporters|`; a project that no longer fits
   is skipped, not stopping. Both were verified against Peters & Skowron Ex. 2 and Brill et al.
   Ex. 4.6/6.3 by the reviewer from the papers.
4. **Generated ballot schema:** `ballot_schema(projects)` is a cached `create_model` over
   `Literal[ids]` with a no-duplicate validator and `extra="forbid"`; a single project yields
   `const`, not `enum`.
5. **Nomination timeout:** `DropRole` default, `Fail` allowed, `DefaultAction` refused (a default
   nomination would be an invented project). The voting timeout is `Fail`.
6. **Reports:** eligibility and funding public payloads are byte-identical across recipients;
   `FundingReport.private` (only for voters in `private_values`) carries `own_value_of_funded`;
   approval counts and rule internals live only in `FundingOutcome.details` (trace).
7. **`gao_idl.testing.assert_noninterference` cannot drive multi-stage pipelines** where one agent
   answers different schemas per stage; PB tests hand-roll the comparison. A generalized driver is
   a possible later improvement.

### M6: release

1. **Public API** is exactly the 23 names in `gao_idl.__all__` (pinned by
   `tests/test_public_api.py`), plus `gao_idl.selftest` and `gao_idl.__version__` as attributes;
   LLM names come from `gao_idl.llm.__all__` (24 names). Every module docstring ends with a
   `Stability:` paragraph (public, supporting or internal); tests keep paragraphs and export lists
   in agreement.
2. **`gao_idl.__version__` lives in `gao_idl/_version.py`** (import-cycle avoidance). Version 0.1.0.
3. **CLI** (after the M6 fix-up): `python -m gao_idl.cli run CONFIG --out FILE` and
   `python -m gao_idl.cli replay FILE`, also installed as `gao-idl`. Config: `env_ref`, `params`,
   `seed`, `agents` (adapter descriptors: `random`, `scripted:<name>`, `table:<path>`,
   `{"kind":"mock_llm","reply":…}`); real LLM backends are deliberately not configurable from JSON.
4. **Examples are digit-named scripts** (`examples/01_…py`); later ones load earlier ones via
   `importlib`, and factories are addressed as `"01_define_a_layer:build_public_goods"`. Tutorials
   import only public or supporting modules (test-enforced).
5. **Packaging:** hatchling, src layout, extras `llm` / `dev` (including `build` and `twine`) /
   `docs` (`mkdocs>=1.6,<2`, material, mkdocstrings); `scripts/clean_install.sh` installs the
   version-matching wheel in a fresh venv and runs `gao_idl.selftest()`; Apache-2.0; `py.typed`
   shipped (after the fix-up).
6. **`knapsack` operates at cent resolution** (it rounds costs; after the fix-up); the other rules
   use exact costs.
7. **Acceptance:** `docs/ACCEPTANCE.md` is a 60-minute script for a person who has not seen the
   code; `examples/acceptance/` holds a reference answer, shown only if the participant is stuck.
