# Concepts

**Layer.** A layer is one simultaneous decision round: every role is asked for an action, the
layer resolves them all at once, credits utilities, and leaves through a named exit port with a
record. You write one with `SimpleLayer`, passing the hooks you need (`observe`, `action_schema`,
`feasible`, `default_action`, `outcome`, `utilities`, `export`, `audience`, `report`) and a
`Contract` that says what entry state and roles it requires. Hooks are pure functions of their
arguments and the `rng` they are handed. See [`core.layer`](api/core.md).

**Composition.** `Primitive` turns a layer into a process. `Seq` runs a process, then routes each
of its exit ports through a `Connector` into the next process; `Par` splits a configuration into
branches that run side by side and joins their exits; `Loop` feeds an exit back into its own body
until an exit leaves the loop or `max_iter` is hit. A `Connector` is a function from an exit
record to the next entry `Config`; wiring is checked against the declared schemas when the
composite is constructed, so a composite that exists is well wired. See
[`composition`](api/composition.md).

**Information objects.** Four kinds of object carry everything that crosses a boundary. A
`DecisionRequest` is what an agent is asked: its observation and the JSON Schema of the action
it may return. A `ParticipantReport` is what an agent is told after a layer completes. An `Exit`
is the port and record a process leaves through, which connectors turn into the next entry state.
A `UtilityIncrement` credits an agent in the ledger and is never shown to the agent unless a
report discloses it. Adapters receive requests and reports only. See [`core.schemas`](api/core.md).

**Path.** Every instance of a process has a hierarchical path (`pd/loop[0]/round/branch[pair_0]`):
names, loop iterations and parallel branches. Decision ids, report ids and every trace event
carry it, so the same layer used in two places never collides, and pending decisions are keyed by
path and role rather than by agent (one agent may have several). See [`core.paths`](api/core.md).

**Event sourcing.** The only source of truth for a session is its input log: what was submitted,
what timed out, what an external service answered. The trace, ledger and process state are a fold
of that log, so a session can be snapshotted as plain JSON, restored in another process by
replaying the log, and checked with `Session.check_determinism()`. Randomness comes only from
streams derived from the seed and the path. See [`runtime.session`](api/runtime.md).

**Timeout policy.** A decision that is not answered is never silently invented. The `TimedOut`
input is resolved by the layer's declared policy: `Fail` fails the process, `DefaultAction`
substitutes the layer's `default_action`, and `DropRole` removes the role from the round. A
defaulted action is distinguishable from a submitted one in the trace. See
[`core.timeout`](api/core.md).
