# 5. Train and evaluate

Train `cy`, a tabular Q-learner, on the public-goods game against two scripted free riders, then
evaluate the learned policy and compare it with three fixed policies.

```python
--8<-- "examples/05_train_and_evaluate.py"
```

Run it with `python examples/05_train_and_evaluate.py` (about fifteen seconds; with
`GAO_EXAMPLE_FAST=1` a couple of seconds). The output looks like this:

```text
training return, first 60 episodes:  43.62
training return, last  60 episodes:  46.15

policy                  mean     std   (return of cy over 5 rounds)
trained (greedy)       50.00    0.00
random                 38.53    2.60
always contribute      26.67    0.00
free rider             50.00    0.00
```

## What to notice

- Against two free riders, a unit contributed comes back as `1.6 / 3` (about 0.53) of a unit, so
  contributing is a loss and the best response is to give nothing. The learner finds it; the
  greedy evaluation matches the free rider's 50.
- **True return versus what the learner sees.** The learner learns only from the report field
  named in `RewardExtractor(fields=["own_payoff"])`. The trainer reads each episode's true return
  from the session ledger afterwards, on the researcher's side; the agent never sees it.
- `evaluate` builds a new, frozen, greedy agent from the checkpoint dict. It never touches the
  agent that was trained; its episodes use their own seed (`seed * 1_000_003 + e`).
- Episode `e` of a training run uses session seed `spec.seed * 1_000_003 + e`, so the whole run
  reproduces from the spec.
- Opponents are named by strings: `"random"`, `"scripted:<name>"` (here a policy registered with
  `default_opponent_factory`) or `"table:<path>"` for a frozen checkpoint on disk.

Next: [test your layer](06_information_tests.md).
