# 6. Information tests

`gao_idl.testing` has checks for the properties that matter in this framework. They raise
`AssertionError`, need no pytest, and work on any environment, including yours. The script is
written as five pytest tests and runs them in turn.

```python
--8<-- "examples/06_information_tests.py"
```

Run it with `python examples/06_information_tests.py`, or copy the functions into a test file.

## What the checks say

| Helper | Fails when |
| --- | --- |
| `assert_replay_equivalent`, `assert_incremental_equals_fold` | replaying the session's input log does not reproduce its trace |
| `assert_arrival_invariant` | outcomes or information depend on the order in which agents submit |
| `assert_noninterference` | information delivered to some agents differs between two parameter sets |
| `assert_substitution_equivalent` | two structurally different processes are distinguishable apart from paths |
| `assert_branch_rng_independence` | a branch's events change when a sibling branch is added (layers that draw random numbers) |

- **Non-interference is a statement about what someone is told.** Team one must learn nothing
  about team two's endowment; the test varies it and compares team one's observations and
  reports. The next test runs the same helper for `cy`, who is told their own endowment, and
  expects it to fail: a check you have never seen fail is a check you cannot trust.
- **Substitution needs deterministic layers.** Random streams are keyed by path, so a layer that
  draws random numbers sees a different stream once its path changes. The public-goods layer
  draws none, which is why it can be compared; `assert_branch_rng_independence` is the helper for
  the other case.
- The helpers answer every decision of an agent with the same action. They are a limitation of
  the helpers, not of the runtime.
