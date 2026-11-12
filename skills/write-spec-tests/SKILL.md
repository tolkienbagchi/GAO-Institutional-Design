---
name: write-spec-tests
description: Write tests for a gao_idl environment from its spec and design page alone, without reading the environment's code, so the tests check the design and not the implementation. Use after an EnvSpec exists, when asked for independent or "blind" tests, or before trusting an environment someone else wrote.
---

# Write tests from the spec, blind

## When to use

An environment and its `EnvSpec` exist. The tests its author wrote pass, which says little: the
same understanding produced both. You write a second set of tests from the spec only. A test of
yours that fails is either a bug in the environment or a sentence in the spec that reads two ways;
both are worth finding. Signatures: `docs/for-agents/llms.md`.

## The one rule

**Do not read the environment's module, its tests or the recorded outcomes of its cards.** Your
inputs are the spec (`SPEC.json`), its rendering (`gao-idl spec render SPEC.json --out design.md`)
and `docs/for-agents/llms.md`. Your tests reach the environment only through its `env_ref` string:
`gao_idl.authoring.play(spec.env_ref, params, seed, script)`. A test file that imports the
environment's package has broken the rule.

## Procedure

1. **One test per sentence.** Go through the spec: each step description, each `told` list, each
   secret, each payoff, each invariant and each assumption is a claim. Write the smallest scenario
   that would make the claim false, and assert on what participants are told and paid.
2. **Use scripts, not policies.** A script maps each agent to its actions in order; `None` means
   the agent does not act. Read results from `session.reports_for(agent)`, `session.ledger` and
   `session.result`, which is all a participant or an analyst could see.
3. **Aim at the edges**: ties, the boundary value named in a rule, nobody acting, the last round.
   Compute the expected numbers by hand from the spec and put the arithmetic in a comment.
4. **Turn stated invariants into predicates** `(session, **args) -> None` raising
   `AssertionError`, name them in the spec's `invariants[].predicate`, and run
   `gao-idl spec check SPEC.json --fuzz 200`.
5. **Report failures without fixing them.** For each failing test say which sentence of the spec
   it tests and what happened instead. Do not adjust the expected value to what the environment
   does: whether the spec or the environment is wrong is the designer's call.

## Example

Three claims from `examples/specs/auction.json`, tested without importing the auction.

```python
# runnable
from pathlib import Path

from gao_idl.authoring import load_spec, play
from gao_idl.core.ids import AgentId

spec = load_spec(Path("examples/specs/auction.json"))


def told(script: dict, agent: str) -> dict:
    session = play(spec.env_ref, spec.params, 0, script)
    return session.reports_for(AgentId(agent))[-1].payload


def bids(alice: float, bob: float, carol: float) -> dict:
    return {"alice": [{"amount": alice}], "bob": [{"amount": bob}], "carol": [{"amount": carol}]}


# Assumption: "If two bidders tie for the highest bid, the one whose name comes first wins."
assert told(bids(8, 8, 1), "bob")["winner"] == "alice"
# Assumption: "the winner pays the larger of the reserve and the highest other bid" (reserve 2).
assert told(bids(6, 1.5, 0), "alice")["own_payment"] == 2.0  # max(2, 1.5)
# Payoff: "The winner gets its value minus the price; everyone else gets 0." alice's value is 10.
assert told(bids(6, 5, 0), "alice")["own_utility"] == 10 - 5
assert told(bids(6, 5, 0), "bob")["own_utility"] == 0
```

## Definition of done

1. Every step, secret, payoff, invariant and assumption of the spec has at least one test, or a
   line saying why it cannot be tested through `play`.
2. The test file imports nothing from the environment's package.
3. Failing tests are listed with the spec sentence they test; none was "fixed" by changing the
   expectation.
