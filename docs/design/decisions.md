# Design decisions

!!! note
    This log is compiled from the project's invariants, the decisions confirmed at each milestone
    review, and the deviations recorded in `NOTES.md`. It is a reading aid, not the original
    planning document; where they differ, `NOTES.md` has the detail and the date.

## Invariants

These hold for every environment and every runtime; breaking one is a bug even when tests pass.

| # | Decision | Why |
| --- | --- | --- |
| I1 | Adapters never see the session, ledger, store, config or outcome; only the `DecisionRequest` and `ParticipantReport` addressed to their agent. | An agent that can read the world is not playing the game. Scripted, human and LLM agents are interchangeable. |
| I2 | Simultaneity barrier: all requests of a layer are issued before any action is accepted; nothing about an action is disclosed until every active role has submitted or timed out. | Simultaneous-move games must not leak through arrival order. |
| I3 | Determinism: `(factory, params, seed, input log)` fixes the trace. Randomness only from the `rng` a layer is handed, derived from the seed and the path. | Replay, debugging and fair comparison. |
| I4 | Event sourcing: the input log is the only source of truth; cached state equals `fold(input_log)`. | Pause for days, restore anywhere, audit. |
| I5 | Reports are evaluated after every completed layer, automatic layers included. An empty set is valid. | Skipping report evaluation is never an option; “nothing to report” is an explicit result. |
| I6 | Nothing is silently invented: a timeout is an input event, resolved by the layer's declared policy, and a defaulted action is distinguishable in the trace. | The record shows what people actually did. |
| I7 | Identifiers are path-based. | The same layer in two places never collides. |
| I8 | Pending decisions are keyed by `(path, role)`, never by agent. | One agent can hold several roles at once. |
| I9 | Credentials are `SecretStr` and never serialized. | Logs, traces and snapshots are shareable. |

## Composition

| Decision | Consequence |
| --- | --- |
| Composites are checked at construction: connector output schemas must equal the next entry schema, and every exit port must be routed. | A composite that exists is well wired. |
| Composites emit no trace events of their own; only `Loop` (`Truncated`, `Failed`) and failing connectors do. | `Seq` is associative modulo paths. |
| `Loop(max_iter=k)` runs iterations `0..k-1`; truncation fires when feedback would start iteration `k`, without running the connector. Exit `{"iterations", "last_exit"}` on the reserved `truncated` port. | A bound that is visible in the record. |
| `Par` applies inputs one at a time, in input-log order, each to quiescence. | The interleaving of branch events follows submission order, which is part of the log. |
| Arrival invariance is defined per projection: information streams, the ledger, each branch's events and the events outside any `Par` are order independent; the global trace need not be. | The strongest property that is still true. |
| `DictJoiner` keeps each branch's port: `{branch: {"port", "record"}}`. | Downstream connectors can tell whether a branch exited normally. |
| Quiescence idempotence is a requirement on every `Process`: stepping a waiting subtree with no input emits nothing. | `Par` correctness depends on it. |
| `Layer.observe` is pure in `(cfg, role, rng)` and has no store access; persistent information reaches an agent through the entry state, copied there by a connector. | What an agent can see is fully determined by its entry `Config`, which is what non-interference tests inspect. |
| Store scopes are glob patterns (`hist:*`); a violation fails the session instead of escaping `advance()`. | Scoped state shared between layers without global variables. |

## Runtime

| Decision | Consequence |
| --- | --- |
| Resolution events (`ActionAccepted`, `ActionDefaulted`, `RoleDropped`) are emitted when the layer settles, in role order, just before `LayerCompleted`. | The trace is a function of the set of resolutions, not of arrival order. |
| `ActionRejected` and `agent_failed` are trace-only: not replayable, excluded from `check_determinism`. | A rejection is information about a try, not part of the game. |
| `StepResult` has a `Continue` case; `advance()` loops on it and raises after 10 000 in a row. | Composites never loop internally. |
| The runner attaches by catching up: earlier reports are delivered and every pending decision is dispatched. | A restored session works with fresh adapters. |
| The runner reads the ledger in exactly one place, and only if `RunnerPolicy.disclose_final_utility`. | Final utility is not information unless the experimenter says so. |
| `DecisionRequest.deadline` is advisory; the runner enforces only `RunnerPolicy.deadline_seconds`. | One clock, one place. |
| An adapter exception becomes a timeout, and `AgentFailed` also leaves a trace-only `agent_failed` event. | The layer's timeout policy decides, never the adapter. |
| External responses (model replies, transport errors) are input events, written before the submission they led to. | A session with an LLM replays without the network. |

## Participants and learning

| Decision | Consequence |
| --- | --- |
| Learner rewards come only from report fields named in `RewardExtractor`; the trainer reads true returns from the ledger. | The learner cannot cheat with the ledger; researchers still see ground truth. |
| Failed episodes are rolled back; epsilon decay and counters advance only on completed episodes. | A crashed episode teaches nothing. |
| Truncation is terminal by default; bootstrapping from the last state is opt-in and documented as biased. | Honest default. |
| The trainer wraps the learner so that an exception in `act` surfaces as `LearnerError`. | The runner would otherwise turn it into a timeout and silently fail every episode. |
| `gao_idl.llm` is optional; nothing else imports it, and a subprocess test enforces that. Backends use raw HTTP; no provider SDKs. | The core installs with `pydantic` and `numpy` only. |
| Local validation of model output is authoritative; a provider's narrower schema support is handled by transforming the request schema, never by relaxing the validator. | What the layer accepts does not depend on the provider. |

## Environments

| Decision | Consequence |
| --- | --- |
| Auction reserve acts as a seller's bid; unsold items are reported to every bidder. | Textbook second-price semantics. |
| Repeated PD is `Loop(Seq(Par(pair_k), round_counter))` with histories private to the pair, kept in the store. | Pairs cannot see each other's play. |
| PB is a `Seq` pipeline: nomination, eligibility, voting, then one of four funding rules, which are pure functions with exact `Fraction` arithmetic and ties by id. | Rules are testable apart from the game. |
| Public reports are byte-identical across recipients; private parts go only to the recipient they concern. | No side channel through report wording. |

## Release

| Decision | Consequence |
| --- | --- |
| The public API is `gao_idl.__all__` (23 names) plus `gao_idl.llm.__all__`; a test pins both. | Additions are deliberate. |
| Every module states its tier (public, supporting, internal) in a `Stability:` paragraph, checked by a test. | The API reference says what is promised. |
| Version `0.1.0`; no license, author or URL metadata and no `py.typed` marker yet. | Not decided by the authors; see `NOTES.md`. |
