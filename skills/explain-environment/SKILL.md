---
name: explain-environment
description: Read an existing gao_idl environment and explain it to a human, from describe(), its contracts, the information each role receives, and its timeout policies, using a fixed template. Use when asked "what does this environment do?" or before changing one.
---

# Explain a gao_idl environment

## When to use

A person asks what an environment does, who sees what, or what a change would affect. You answer from
the code and a short run, not from its name or docstring. Signatures: `docs/for-agents/llms.md`.

## Procedure

1. **Structure.** Build the process with its factory (`env_ref = "module:factory"`) and call
   `describe(process)` from `gao_idl.composition.contracts`. It returns a JSON-able tree: kind
   (`primitive`, `seq`, `par`, `loop`), name, entry contract, exit ports and, for a `Seq`, the
   connector on each route. Read it top-down.
2. **Roles per layer.** In each primitive's entry contract, `roles` lists the decider roles. A
   layer with none is automatic: it decides nothing but computes (clearing, exposure, a counter).
3. **Information per role.** Start a session, `advance()`, and read `session.pending()`: each
   `DecisionRequest.observation` is exactly what that role sees; `action_schema` is exactly what
   it may do. Play a round with a simple scripted policy, then read
   `session.reports_for(agent)` for what it is told afterwards. Compare two roles' reports: which
   fields are public (identical across recipients) and which are private.
4. **Timeouts.** `describe` shows each primitive's timeout kind: `fail` (the run aborts),
   `default` (a layer-supplied action, flagged in the trace as defaulted), `drop` (the role is
   absent from the outcome). Say what a missing action *means* in this environment.
5. **What repeats.** For a `Loop`, name the exit that continues, the exit that ends, `max_iter`,
   and which values the feedback connector carries (and from the store, if any).
6. **Payoffs.** Where the ledger's utility increments come from (`utilities`), and which report
   carries each agent's payoff. A payoff that is in the ledger but in no report is invisible to
   learners and models: flag it.
7. **Tests.** List which invariants the environment's tests pin (replay, arrival order,
   noninterference, substitution) and which secrets they protect.

```python
# runnable
from gao_idl import ScriptedAgent, Session, run_sync
from gao_idl.composition.contracts import describe
from gao_idl.core.ids import AgentId
from gao_idl.environments.pd import build_repeated_pd

process, cfg = build_repeated_pd([AgentId("a"), AgentId("b")], rounds=2)
tree = describe(process)
assert tree["kind"] == "loop"

session = Session.start(
    "gao_idl.environments.pd:build_repeated_pd", {"agents": ["a", "b"], "rounds": 2}, 1
)
session.advance()
for request in session.pending():
    print(
        request.role,
        "sees",
        sorted(request.observation),
        "may do",
        request.action_schema["properties"].keys(),
    )
agents = {AgentId(a): ScriptedAgent(lambda req, reps: {"move": "C"}) for a in "ab"}
run_sync(session, agents)
for agent in ("a", "b"):
    first = session.reports_for(AgentId(agent))[0]
    print(agent, "is told", sorted(first.payload))
```

## Template for the explanation

Keep it to what the person needs. Use these headings, in this order, and drop any that do not apply.

1. **In one paragraph:** the institution, its parts, and what is being studied with it.
2. **Round by round:** the layers in order; for each, who decides, simultaneously or not, and what
   they choose (one line each).
3. **Who knows what:** a small table, role by "sees when deciding" and "is told afterwards".
4. **What is committed vs chosen:** the automatic layers and the parameters that fix them.
5. **If someone does not act:** the timeout policy per layer, in plain words.
6. **What repeats and what carries over.**
7. **What the parameters change:** each factory parameter in a line.
8. **What is stylized:** say plainly that the numbers are illustrative. Do not claim fidelity to a
   real market or city.
9. **Open points:** anything the code decides that the description above did not (point to
   `NOTES.md`).

## Pitfalls

- Do not explain from the docstring alone. The docstring can be stale; the observation is not.
- Do not assume a private field is private: check that it is absent from every other role's
  observation and from every other recipient's reports.
- Names of layers are path elements; use them when pointing at a trace event, not a bare layer name.
- A role's decision may recur (several pending decisions per agent); say so if the environment does
  that.
