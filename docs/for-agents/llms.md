# gao_idl for AI agents

One file to paste into an agent's context. It states the invariants, the API as it is now (all
amendments folded in, no history), the four information objects, the test fixtures, the environment
factories and the three skills. The longer prose is in the other docs; the protocols are in the
skills. Python 3.11 or later; package `gao_idl`, version 0.1.0.

Skills (read the one that matches the job):

- `skills/design-environment/SKILL.md`: a person describes an institution; you build an environment.
- `skills/write-llm-codec/SKILL.md`: a language model must play a role of an existing layer.
- `skills/explain-environment/SKILL.md`: a person wants an existing environment explained.

## What it is

A library for **composable multi-agent environments**. An environment is a tree of **processes**.
The leaves are `Primitive` layers (one simultaneous decision round each); `Seq`, `Par` and `Loop`
compose them. Execution is a resumable, event-sourced `Session`: it pauses at pending decisions,
accepts actions later, and continues. Participants are scripted policies, humans, tabular
Q-learners or language models, all behind one adapter interface.

## The nine invariants

Violating one is a bug even if tests pass.

1. **Adapters never see the session.** Anything supplying actions receives only `DecisionRequest`
   and `ParticipantReport` objects addressed to its agent. Never pass a `Session`, ledger, store,
   config or outcome into adapter or codec code.
2. **Simultaneity barrier.** Within one primitive layer all requests are generated before any
   action is accepted, and nothing about a submitted action is disclosed until every active role
   has submitted or timed out.
3. **Determinism.** `(factory, params, seed, input log)` replays to an identical trace. Layer code
   draws randomness only from the `rng` it is given. No `random`, `time`, `id()`, dict-order
   dependence.
4. **Event sourcing.** The input log is the only source of truth; cached state equals
   `fold(input_log)`.
5. **Reports after every completed layer**, including automatic layers. An empty report set is
   valid; skipping evaluation is not.
6. **Nothing is silently invented.** A timeout becomes a `TimedOut` input event and is resolved by
   the layer's `TimeoutPolicy`; a defaulted action is distinguishable from a submitted one.
7. **Identifiers are path-based.** Decision ids, report ids and trace events carry the hierarchical
   `Path`; never a bare layer name or a global counter.
8. **Pending decisions are keyed by `(path, role)`, never by agent.** One agent may have several.
9. **No secrets in logs, traces or snapshots.** Credentials are `SecretStr`, never serialized.

## The four information objects

Everything a participant ever knows is one of these. Design and tests are about which one carries
which fact.

| Object | Who gets it | Built by | Contains |
|--------|-------------|----------|----------|
| **observation** (`DecisionRequest.observation`) | the role deciding | `Layer.observe(cfg, role, rng)` | a pure function of the layer's entry state; no store access |
| **action schema** (`DecisionRequest.action_schema`) | the role deciding | `Layer.action_schema(cfg, role)` | a JSON Schema; may depend on state (bounds, menus) |
| **report** (`ParticipantReport.payload`) | each agent in the layer's `audience` | `Layer.report(cfg, outcome, recipient, rng)` | what the recipient is told after the layer completed; the only channel for payoffs and results |
| **exit record** (`Exit.record`) | connectors only | `Layer.export(cfg, outcome)` | the state handed to the next stage; never reaches an adapter |

Researcher-side only: the **ledger** (utility increments, `session.ledger.utility(agent)`) and the
**trace**. Adapters cannot read them; a payoff reaches a learner or model only if a report carries it.

## Pinned API

### Identifiers and paths (`gao_idl.core`)

```python
AgentId, RoleId, Port, DecisionId, ReportId        # NewType of str
Path = tuple[str | LoopIter(i) | Branch(name), ...]
path_str(p)            # "tournament/loop[3]/branch[pair_1]/pd_round"
decision_id(p, role)   # f"{path_str(p)}#{role}"      e.g. "bidding#bidder:0"
report_id(p, agent, n) # f"{path_str(p)}@{agent}:{n}"
stream(root_seed, path, purpose)   # numpy Generator keyed by (seed, path, purpose)
role_sort_key          # natural order: bidder:2 before bidder:10
```

The root path element is the root process name: a root `Primitive` named `bidding` runs at
`("bidding",)`.

### Messages

```python
class Config(BaseModel):  state: dict[str, Any]; roles: dict[RoleId, AgentId]   # frozen
class DecisionRequest(BaseModel):
    decision_id; path; layer; role; agent_id
    observation: dict; action_schema: dict; deadline: float | None   # deadline is advisory
class ParticipantReport(BaseModel): report_id; path; layer; agent_id; payload: dict
class Exit(BaseModel): port: Port; record: dict
class UtilityIncrement(BaseModel): agent_id; amount; path; t
```

### Layers

```python
class Contract(BaseModel):
    state_schema: type[BaseModel]          # entry state, validated on layer entry
    roles: dict[RoleId, dict] = {}         # may be empty for dynamic role sets; constraints opaque
    store_reads: frozenset[str] = frozenset()   # glob patterns (fnmatchcase), e.g. "hist:*"
    store_writes: frozenset[str] = frozenset()

SimpleLayer(name, entry: Contract, exits: dict[Port, type[BaseModel]],
            timeout=Fail(), observe=..., action_schema=..., feasible=..., default_action=...,
            outcome=..., utilities=..., export=..., audience=..., report=...)
# observe(cfg, role, rng) -> BaseModel          pure; no store
# action_schema(cfg, role) -> type[BaseModel]
# feasible(cfg, role, action) -> bool
# default_action(cfg, role) -> BaseModel        only with DefaultAction()
# outcome(cfg, actions: dict[RoleId, BaseModel], rng, store: StoreView) -> BaseModel
# utilities(cfg, outcome) -> dict[AgentId, float]
# export(cfg, outcome) -> Exit                   must be given: there is no default
# audience(cfg, outcome) -> set[AgentId]         default: everyone with a role
# report(cfg, outcome, recipient, rng) -> BaseModel | None
```

A layer with no roles is automatic: it still runs `outcome`, exports, reports and credits utility.

`Primitive(layer)` is the leaf `Process`. Timeouts:

```python
Fail()           # the session fails as soon as the timeout is processed
DefaultAction()  # the layer's default_action is used; traced as defaulted
DropRole()       # the role is absent from `actions` in outcome
```

### Composition

```python
Connector(fn, output_schema, store_reads=frozenset(), store_writes=frozenset())
# fn(exit_record: dict, ledger: LedgerReadOnly, store: StoreView) -> Config
# output_schema must be the next entry's state_schema, else ContractError at construction

Seq(first, routes: dict[Port, tuple[Connector, Process]], name="seq")
Loop(body, feedback: dict[Port, Connector], until: dict[Port, Port],
     max_iter: int, on_limit: "truncate" | "fail", name="loop")
Par(branches: dict[str, Process], split: Splitter, join: Joiner, name="par")
DisjointSplitter(fn: Config -> dict[branch, Config], input_schema=OpenState)
DictJoiner(port, schema)    # record: {branch: {"port": ..., "record": ...}}
```

- `Loop(max_iter=k)` runs iterations `0..k-1`. Truncation triggers when feedback would start
  iteration `k` (the feedback connector is not run); it exits on reserved port `"truncated"` with
  `{"iterations": k, "last_exit": record}`. With `on_limit="fail"` the session fails instead.
- Composites emit no trace events themselves (only `Loop` emits `Truncated`/`Failed`). `Seq` is
  associative modulo paths. Names may not contain `/ # @ [ ]`.
- `Par` applies inputs one at a time, in input-log order. Arrival invariance is per projection:
  information streams, ledger, trace restricted to each branch root, and trace outside branches.
- Store scope violations fail the session (`Failed` event), they do not raise out of `advance()`.
- Persistent information an agent should see at decision time must be copied into entry state by a
  connector, because `observe` has no store access.
- Frames are JSON-dumpable, not loadable: session state is rebuilt only by replay.
- A new `Process` must be quiescence-idempotent: stepping a waiting subtree with no inputs emits
  nothing and returns the same `Pending`.

`describe(process)` (in `gao_idl.composition.contracts`) returns a JSON-able tree of kinds,
names, contracts, exits and routes.

### Session

```python
Session.start(env_ref, params, seed)          # env_ref = "module.path:factory"
Session.replay(env_ref, params, seed, log)
Session.restore(snapshot)                     # snapshot = session.snapshot(): JSON-able
session.advance() -> list[TraceEvent]         # loops over Continue; RuntimeError after 10 000
session.pending() / pending_for(agent)        # list[DecisionRequest]
session.submit(agent_id, decision_id, action) # SchemaError / Infeasible / WrongAgent / UnknownDecision
session.timeout(decision_id)
session.record_external(decision_id, payload) # model replies and failures are input events
session.reports_for(agent_id)
session.finished; session.failed; session.result; session.input_log; session.trace; session.ledger
session.check_determinism()                   # replay and compare; needs an empty inbox
```

`submit` rejects non-strict-JSON actions (`NaN`, non-JSON objects) as `SchemaError`.
`ActionRejected` and `agent_failed` are trace-only (not in the input log; excluded from
`check_determinism`). `ActionAccepted`, `ActionDefaulted` and `RoleDropped` are emitted when the
layer settles, in role order, just before `LayerCompleted`; the trace is a function of the set of
resolutions, not their arrival order. The factory returns `(Process, Config)`.

Errors (`gao_idl.core.errors`): `GaoError`, `ContractError` (construction-time wiring),
`UnknownDecision`, `WrongAgent`, `SchemaError`, `Infeasible`, `SessionFinished`,
`DeterminismError`, `AgentFailed`.

### Running with participants

```python
AgentAdapter                                  # protocol: episode_start, observe, act, episode_end
ScriptedAgent(policy)                         # policy(request, reports) -> action dict
RandomAgent(seed)                             # samples the JSON Schema (no $ref/nested models)
HumanTransportAgent()                         # provide(decision_id, action); wait_outstanding()
TableAgent(agent_id, obs_codec, act_codec, reward, cfg, seed, learning=True, record_transitions=False)
EpisodeContext(agent_id, env_ref, seed, keep_episode_memory=False, keep_conversation=False, keep_parameters=True)
RunnerPolicy(deadline_seconds=None, disclose_final_utility=False)
await run(session, adapters, policy=RunnerPolicy())      # async
run_sync(session, adapters, policy=RunnerPolicy())       # same, blocking
```

The runner delivers earlier reports on attach (a restored session works with fresh adapters),
deep-copies requests and reports before they reach an adapter, turns `AgentFailed` into
`session.timeout`, and reads `session.ledger` in one place only (`final_utility`, when
`disclose_final_utility=True`). `ScriptedAgent` and `HumanTransportAgent` raise `ValueError` on a
report addressed to another agent and deduplicate by `report_id`.

### Learning (`gao_idl.learning`)

```python
TableAgent(agent_id, FieldObsCodec(fields, history=0, *, report_fields=()),
           SchemaActCodec(grid={"field": [values]}), RewardExtractor(fields=[...]), QConfig(...), seed)
TrainSpec(env_ref, params, learner, episodes, seed, opponents={agent: "random" | "scripted:<name>" | "table:<path>"})
train(spec, learner, opponent_factory) -> result with .checkpoint
evaluate(spec, checkpoint, opponent_factory, episodes, seed) -> object with .mean
default_opponent_factory(scripted={name: policy})
```

- Rewards come **only** from report fields named in `RewardExtractor.fields` (summed, no sign
  flip: report a `payoff` field). The trainer reads `session.ledger` for true returns, never the agent.
- Truncation is terminal by default (`QConfig.bootstrap_on_truncation=False`).
- Failed episodes are rolled back. Episode seed is `spec.seed * 1_000_003 + e`.

## `gao_idl.llm` (optional, extra `llm`)

Nothing in `core`, `composition`, `runtime`, `agents`, `learning` or `environments` imports it,
except an environment's own `llm.py` modules (allow-listed). Provider backends use raw HTTP
(`httpx`), no provider SDKs. Credentials are a callable returning `SecretStr`, never a literal.

```python
LLMAgent(agent_id, backend, codec=DefaultCodec(), memory=Window(20), repair=RepairPolicy(),
         temperature=0.0, max_tokens=256, timeout_s=60.0, report_renderer=DefaultReportRenderer(),
         *, record_text=False, record_raw=False)
```

| Name | What it is |
|------|------------|
| `LLMAgent` | an `AgentAdapter` that calls a backend, repairs invalid replies, records external responses |
| `LLMBackend` | protocol: `await complete(messages, response_schema, *, temperature, max_tokens, timeout_s) -> Response` |
| `AnthropicBackend` | Messages API backend (`output_config.format` json_schema) |
| `OpenAICompatBackend` | chat-completions backend (`response_format: json_schema`, text fallback) |
| `MockBackend` | serves a list (or calls a callable) in order; `.calls` is a list of `MockCall`; `Exception` items are raised |
| `MockCall` | one recorded call: messages, response_schema, temperature, max_tokens, timeout_s, decision_id |
| `ReplayBackend` | replays recorded external responses per decision; raises `ReplayExhausted` when asked for more |
| `ReplayExhausted` | a `DeterminismError`: replay asked for more model replies than were logged |
| `MockExhausted` | the mock script ran out |
| `BackendError` | a backend failed (after 3 retries on 429/5xx/timeouts); credentials are scrubbed |
| `Message` / `Response` | a chat message (`role`, `content`) / a reply (`text`, `parsed`) |
| `CallRecord` | per-call record kept by the agent (attempt, latency, repair reason, failure) |
| `DecisionCodec` | protocol: `render(request, memory, rejections) -> list[Message]`, `parse(response, request) -> dict` |
| `DefaultCodec` | system prompt, observation, action schema, "reply with JSON only"; subclass and override `system_prompt` / `task_prompt` |
| `CodecParseError` | the reply has no usable JSON object |
| `ReportRenderer` / `DefaultReportRenderer` | protocol `render_report(report) -> Message` / `Report (<layer>): <json>` |
| `RepairPolicy` | `max_attempts=3`, `include_rejection_reason=True` |
| `Memory`, `NoMemory`, `Window`, `FullTranscript` | conversation memory strategies (`Window(20)` is the default) |
| `to_anthropic_schema` | pure transform of an action schema to what the Messages API accepts; bounds go into descriptions; local validation stays authoritative |

Codec render contract: `render(request, memory, rejections)` returns
`[system..., *memory, turn...]`; the agent remembers `render(request, [], [])` minus the system
messages as the turn. A codec may read only the request, its memory and rejection reasons. Local
validation (`strict types, enum/const, bounds, required, additionalProperties: false, items,
anyOf, local $ref`) is authoritative; a provider's narrower support is handled by transforming the
request schema, never by relaxing the validator. Failed calls are input events, so replay
reproduces failures in place.

## Test fixtures (`gao_idl.testing`)

They raise `AssertionError`, have no pytest dependency, and assume one pending decision per agent
per call (a helper limit, not a runtime one). In round one every listed agent must have a pending
decision, so multi-stage environments need hand-rolled drivers.

| Fixture | Asserts |
|---------|---------|
| `assert_replay_equivalent(session)` | replaying the input log reproduces the trace |
| `assert_incremental_equals_fold(session)` | cached state equals the fold of the log |
| `assert_arrival_invariant(env_ref, params, seed, actions)` | information, ledger and per-branch traces do not depend on submission order |
| `assert_noninterference(env_ref, params_a, params_b, seed, actions, agents)` | the listed agents' information is identical under both parameter sets |
| `assert_substitution_equivalent(process_a, process_b, cfg, seed, actions)` | two structures are indistinguishable apart from paths (deterministic layers only) |
| `assert_branch_rng_independence(build_par_env, seed, ...)` | a branch's trace does not depend on whether a sibling exists |

## Environments and their factories

All return `(Process, Config)` and are addressed as `"module:factory"`. Numbers are stylized
institutions, not models of any real market or city.

| `env_ref` | Parameters |
|-----------|------------|
| `gao_idl.environments.auction:build_auction` | `values: dict[str, float]`, `rule="second_price"`, `reveal_bids=False`, `reserve=None` |
| `gao_idl.environments.pd:build_repeated_pd` | `agents`, `rounds`, `matching="fixed"\|"round_robin"`, `payoffs`, `timeout=Fail()`, `default_move=None`, `disclose_payoffs=True` |
| `gao_idl.environments.pb:build_pb` | `proposers`, `voters`, `budget`, `max_cost`, `rule="greedy"\|"knapsack"\|"mes"\|"phragmen"`, `private_values=None`, `reveal_ballots=False`, `timeout_nomination=DropRole()` |
| `gao_idl.environments.congestion:build_congestion` | `n_commuters=6`, `periods=10`, `toll_policy=None`, `report_regime="hotspot"\|"pop_weighted"\|"both"`, `equity_metric=None`, `ev_share=0.0`, `public_report=False`, `k=3`, `population_seed=0`, `city=None`, `commuters=None`, `timeout=Fail()`, `default_toll=None` |
| `gao_idl.environments.vcm:build_vcm` | `market_form="per_project"\|"pooled"`, `n_sellers=None`, `n_buyers=None`, `n_verifiers=None` (all `None`: the stylized lemons fixture; any given: a drawn population with defaults 4/3/2), `rounds=5`, `fee=1.0`, `penalty=5.0`, `tol=0.1`, `p_audit=0.5`, `settle_on="reported"\|"true"`, `reputation_visible=False`, `price_cap=50.0`, `population_seed=None` (0 when drawing), `bias=None`, `sigma=0.03`, `sellers/buyers/verifiers=None`, `timeout=Fail()` |
| `gao_idl.environments.toy.bandit:build_bandit`, `...toy.chain:build_chain` | small sanity environments for learners |

Layouts:

- **PD:** `Loop(Seq(Par(pair_k: Primitive(pd_round)), round_counter))`.
- **PB:** `Seq(nomination, Seq(eligibility, Seq(voting, funding_<rule>)))`; funding rules are
  pure functions `rule(projects, ballots, budget) -> list[str]` with exact `Fraction` arithmetic.
- **Congestion:** `Loop(Seq(toll_setting, Seq(travel, exposure)))`. A toll is a committed rule
  (`toll_policy`) or chosen by a `regulator` role; the two are interchangeable downstream. What the
  regulator is told is `report_regime`.
- **VCM:** `Loop(Seq(verification, market, clearing, audit))`. Roles `verifier:k`, `seller:j`,
  `buyer:i`. Uniform-price call auction (midpoint of the marginal pair, pro rata at the margin,
  exact arithmetic), prices per credit, credits pooled across sellers. One verifier per project.
  Audit randomness is one draw per project per round keyed by path. Reputation lives in the store
  and reaches buyers only with `reputation_visible=True`.
- **Auction:** one simultaneous layer; `reserve` acts as a seller's bid; unsold items are reported
  to all bidders.

Scripted players and codecs for the larger environments live in `environments/<name>/scripted.py`
and `llm.py` (for example `honest_verifier`, `naive_buyer`, `greedy_regulator`, `myopic_commuter`,
`RegulatorCodec`, `BuyerCodec`).

## Conventions for new code

- Public data objects are frozen pydantic models; everything in the input log or trace
  round-trips through `model_dump(mode="json")` / `model_validate`.
- Every module docstring ends with a `Stability:` paragraph (public / supporting / internal); each
  non-internal module needs a `::: module` entry in `docs/api/`. Examples import only
  non-internal modules.
- Core dependencies: `pydantic>=2.7`, `numpy>=1.26`. Extras: `llm` (httpx), `dev`, `docs`.
- `ruff check`, `ruff format --check`, `mypy` and `pytest -q -m "not slow"` must pass. No network
  and no sleeping in tests. Append dated entries to `NOTES.md` for any deviation or ambiguity.
- The public API of `gao_idl` is exactly 23 names, pinned by a test; names for language models
  come from `gao_idl.llm.__all__`.
- Command line: `python -m gao_idl.cli run CONFIG --out FILE` and `replay FILE` (also `gao-idl`).
  Agent descriptors in the config: `random`, `scripted:<name>`, `table:<path>`, and a mock
  language model. Real backends are not configurable from JSON.

## Public names of `gao_idl`

`AgentAdapter`, `Config`, `Connector`, `Contract`, `DefaultAction`, `DictJoiner`,
`DisjointSplitter`, `DropRole`, `EpisodeContext`, `Fail`, `HumanTransportAgent`, `Loop`, `Par`,
`Primitive`, `RandomAgent`, `RunnerPolicy`, `ScriptedAgent`, `Seq`, `Session`, `SimpleLayer`,
`TableAgent`, `run`, `run_sync`.
