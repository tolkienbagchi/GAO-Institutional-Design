# 4. Pause and resume

A `HumanTransportAgent` is a stand-in for a user interface: its `act` waits until outside code
calls `provide(decision_id, action)`. The script plays the part of the interface, answers one
decision, then abandons the run while the human is looking at the second one, as if the process
had died. It writes `session.snapshot()` to a JSON file, starts a **new Python process** with
`subprocess`, restores the session there, answers the remaining decisions and compares the totals
with a run that was never interrupted.

```python
--8<-- "examples/04_pause_resume.py"
```

Run it with `python examples/04_pause_resume.py`; set `GAO_EXAMPLE_FAST=1` for a shorter game.

## What to notice

- The snapshot is plain JSON: the `env_ref`, parameters, seed and the input log. It contains no
  agent and no secret. `Session.restore` rebuilds the session by replaying the log.
- The runner catches a fresh adapter up on attach: it delivers the reports already in the session
  and dispatches every pending decision. The new process answers only what is still open.
- The restored process must be able to import the environment named by `env_ref`. Here that is
  `02_compose:build_repeated_pg`, found because the script's directory is on `sys.path`.
- The pause point is arbitrary. Nothing about the snapshot depends on being taken between rounds.

Next: [train and evaluate](05_train_and_evaluate.md).
