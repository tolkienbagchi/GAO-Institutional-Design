# 3. Mixed agents

The five-round game from tutorial 2 with three different kinds of participant:

- `ann` is a `ScriptedAgent`: a plain function from the request (and the reports so far) to an
  action. This one never contributes.
- `bo` is an `LLMAgent` on a `MockBackend`: the same agent class you would point at a real model,
  here answering every prompt with a fixed JSON object.
- `cy` is a `TableAgent` restored from a checkpoint. The script first trains it against two free
  riders, writes the checkpoint as JSON, and loads a frozen copy.

```python
--8<-- "examples/03_mixed_agents.py"
```

Run it with `python examples/03_mixed_agents.py`.

## What to notice

- All three are `AgentAdapter`s. The runner talks to them through the same four calls
  (`episode_start`, `act`, `observe`, `episode_end`) and none of them is handed the session.
- The `MockBackend` script may be a list of replies or a function of the messages. Replies that
  are not valid JSON for the action schema are retried and then rejected exactly as a real model's
  would be, which is how you test an `LLMAgent`'s failure handling without a network. Swap in
  `OpenAICompatBackend` or `AnthropicBackend` from `gao_idl.llm` for a real one (install the
  `llm` extra; credentials are passed as a callable returning a `SecretStr`).
- `SchemaActCodec(grid=...)` turns the continuous action into three levels for the table; the
  grid values must satisfy the schema's bounds.
- `session.check_determinism()` replays the input log, including whatever the model said, and
  asserts the trace is identical.

Next: [pause and resume](04_pause_resume.md).
