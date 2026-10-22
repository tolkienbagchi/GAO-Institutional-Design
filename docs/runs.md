# Reproducible runs

One small command-line tool (`gao-idl`, or `python -m gao_idl.cli`) runs a session from a JSON file and checks that it reproduces. Three demo
configs are in `examples/configs/`: `auction.json`, `pd.json` and `pb.json`.

```bash
gao-idl run examples/configs/pd.json --out run.json
gao-idl replay run.json
```

The first plays the session to the end and writes `run.json`; the second rebuilds it from the
recorded input log and exits with status 0 only if the replay is identical.

## The config

```json
--8<-- "examples/configs/pd.json"
```

| Key | Meaning |
| --- | --- |
| `env_ref` | `"module:factory"`, as for `Session.start` |
| `params` | JSON arguments of the factory |
| `seed` | the session seed; random agents are seeded from it, so the same file always gives the same run |
| `agents` | one adapter per agent that has a role |

An adapter is one of

- `"random"`: a `RandomAgent` that samples the action's JSON Schema;
- `"scripted:<name>"`: `always_cooperate`, `always_defect`, `tit_for_tat` (prisoner's dilemma) or
  `truthful_bidder` (auction);
- `"table:<path>"`: a frozen `TableAgent` restored from a checkpoint written by the trainer
  (tutorial 3 writes one);
- `{"kind": "mock_llm", "reply": {...}}`: an `LLMAgent` on a mock backend that answers every
  prompt with that JSON object. Real model backends are not available from the command line,
  because they need credentials.

## The output

`run.json` holds the config, the result (`finished`, `failed`, exit `port`, `record`), the ledger
totals per agent, and the session `snapshot` (environment, parameters, seed and input log).
`replay` needs nothing else: replies from a language model are in the log, so no backend is
involved. It fails (status 1) if the replayed trace is not the logged one, or if the result or any
total differs from what the file says.

The snapshot can also be restored in your own code with `Session.restore(record["snapshot"])`
and continued, as in [tutorial 4](tutorials/04_pause_resume.md).

!!! note "Knapsack"
    The `knapsack` funding rule needs whole-cent costs and refuses the float costs that
    `RandomAgent` invents, so a participatory-budgeting config with random proposers should use
    `greedy`, `mes` or `phragmen`.
