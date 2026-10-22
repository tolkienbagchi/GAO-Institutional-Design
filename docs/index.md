# gao_idl

`gao_idl` is a Python package for defining **composable multi-agent environments** and running
them with scripted, human, tabular-RL or LLM participants.

- An environment is built from **layers**: one simultaneous decision round each.
- Layers are joined with `Seq`, `Par` and `Loop`; a `Connector` turns one layer's exit record into
  the next layer's entry state, so two layers fit together exactly when their contracts do.
- A **session** is event sourced. It pauses at pending decisions, accepts actions seconds or days
  later, and can be rebuilt by replaying its input log.
- Participants only ever see the requests and reports addressed to them; they never get the
  session, the ledger or the store.
- Given the same environment, parameters, seed and input log, the trace is identical.

## Architecture

```text
 participants               runner                      session                     environment
 ------------               ------                      -------                     -----------
 RandomAgent       act()    one task per      submit()  input log ---fold--> trace  Loop
 ScriptedAgent  <-------->  pending decision <------->  (event sourced)      ledger   +- Seq
 HumanTransportAgent        deadlines,        advance()  store (scoped)               |  +- Par
 TableAgent                 retries,                          |                       |  |  +- Primitive(layer)
 LLMAgent (gao_idl.llm)     timeouts                          v                       |  |  +- Primitive(layer)
        ^                                              DecisionRequest ---------------+  +- Connector
        |                                              ParticipantReport                    (record -> Config)
        +-- sees only requests and reports addressed to its agent
```

A layer is a set of pure hooks (`observe`, `action_schema`, `feasible`, `outcome`, `utilities`,
`export`, `report`, ...). Composition is checked when you build it, not when you run it.

## Install

```bash
pip install gao-idl            # core: pydantic + numpy
pip install "gao-idl[llm]"     # adds httpx for the LLM backends
python -c "import gao_idl; gao_idl.selftest()"
```

## Where to go next

- [Concepts](concepts.md): the vocabulary, one paragraph each.
- Tutorials: [define a layer](tutorials/01_define_a_layer.md), [compose](tutorials/02_compose.md),
  [mix agents](tutorials/03_mixed_agents.md), [pause and resume](tutorials/04_pause_resume.md),
  [train](tutorials/05_train_and_evaluate.md), [test your layer](tutorials/06_information_tests.md).
  Every tutorial is a runnable script in `examples/`.
- [API reference](api/index.md) and the [decision log](design/decisions.md).
