# 1. Define a layer

A **public-goods round**: `n` players each hold an endowment and choose how much to put into a
common pot. The pot is multiplied and split equally, so everybody's payoff is what they kept plus
their share. Contributing is collectively good and individually costly when the multiplier is
below `n`.

```python
--8<-- "examples/01_define_a_layer.py"
```

Run it with `python examples/01_define_a_layer.py`.

## What to notice

- **Models are the contract.** `PGState` is the entry state the layer requires, `PGObs` is all a
  player ever sees, `Contribution` (built per call) is the action, `PGOutcome` is both the
  resolved round and the record the layer exits with. Everything is a pydantic model, so
  everything round-trips through JSON.
- **Hooks are pure.** `outcome` receives all accepted actions at once and an `rng` (unused here:
  the game is deterministic). Nothing about one player's action reaches another before the round
  settles.
- **The action schema carries the bound.** `action_schema` builds `Contribution` with
  `le=endowment`, so agents that read the JSON Schema (`RandomAgent`, an LLM) are told the limit.
  Rules that cannot be written as a schema belong in the `feasible` hook, which rejects an
  action after parsing.
- **Timeouts are declared.** `DefaultAction()` makes an unanswered player contribute nothing;
  `Fail()` (the default) would fail the whole session, `DropRole()` would remove the player.
- **Two exit ports.** `again` and `done` cost nothing in a single round (it always leaves through
  `done`) and are what tutorial 2 needs to loop the layer.
- **`build_public_goods` is an environment factory.** `Session.start` takes the string
  `"module:factory"` plus JSON parameters, so a session can be rebuilt from a snapshot in another
  process.

Next: [compose it](02_compose.md).
