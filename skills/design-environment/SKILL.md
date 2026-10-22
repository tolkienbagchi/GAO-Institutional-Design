---
name: design-environment
description: Turn a researcher's verbal description of a multi-agent institution (who decides, who knows what, what repeats) into a gao_idl environment, by asking nine questions in order and mapping the answers to layers, connectors, reports and tests. Use before writing any environment code.
---

# Design a gao_idl environment

## When to use

Use this when a person describes a game, market, mechanism or institution in words and wants it
as a runnable `gao_idl` environment, or wants to change one ("what if the auctioneer committed
first?"). Do not write code until questions 1 to 9 have answers the person has confirmed. Signatures
are in `docs/for-agents/llms.md`; this file is the protocol, not the API reference.

Two rules apply throughout:

- **Never invent an answer.** If the person did not say what happens when someone does not act, or
  who sees the result, ask. A silent default is an unrecorded modelling choice.
- **Say what you chose when the description underdetermines the design**, in the environment's
  module docstring and in `NOTES.md`. Those gaps are the useful part of the review.

## Questions to ask, in this order

Ask them one or two at a time, in plain language, with an example of each possible answer. The
second column is what the answer decides.

| # | Ask | Why it matters | Decides |
|---|-----|----------------|---------|
| 1 | Who decides, and when? Do they move at the same time, or does someone see the other's move first? | Simultaneous moves are one layer; a move seen by the next mover is a second layer. | One `SimpleLayer` per simultaneous round; `Seq` between them |
| 2 | What does each decider know when they decide, and where does it come from? | What a role can see is exactly its layer's `observe`, which is a function of the entry state. | `observe`, the entry state schema, connectors that thread earlier results forward |
| 3 | Is anything committed in advance (a posted price, a rule) or chosen live? | A committed value needs no decider: an automatic layer. A chosen one is a role. The two must be interchangeable downstream. | automatic layer (no roles) vs role; the substitution test |
| 4 | What is private, and who must never learn it, even indirectly? | Each answer is a test: change the secret, nobody else's information may change. | `assert_noninterference` or a hand-rolled comparison |
| 5 | What repeats, and what carries over between rounds? | Repetition is a loop. Carried state goes through a connector or the store. | `Loop`, the feedback connector, store scopes (`hist:*`) |
| 6 | Who interacts in parallel with whom? | Independent groups run side by side with separate state. | `Par`, a splitter, a joiner, disjointness |
| 7 | What happens when someone does not act? | Fail the run, use a default, or drop the role. There is no neutral default for e.g. a nomination or a bid. | `TimeoutPolicy`: `Fail`, `DefaultAction`, `DropRole` |
| 8 | What are participants told afterwards, and is it the same for everyone? | A report is the only way information reaches a participant after a layer. | `report`, `audience`, the reporting regime |
| 9 | What is each participant's payoff and when is it realized? | The ledger holds utility; a learner or a language model sees it only if a report carries it. | `utilities`, and a payoff field in the report |

After the nine, restate the design as a table (role, sees, chooses, learns) and get a yes.

### A short elicitation

> **Person:** I want a sealed-bid second-price auction, five rounds, bidders get new values each
> round.
> **Agent:** (Q1) Do all bidders bid at once, with nobody seeing another's bid before the result?
> **Person:** Yes.
> **Agent:** (Q2) Does a bidder know its own value before bidding? Anything about the others'?
> **Person:** Own value only.
> **Agent:** (Q3) Is the reserve price fixed in advance, or does the seller choose it live?
> **Person:** Fixed.
> **Agent:** (Q4) So bids and other bidders' values must never reach a bidder. After the round, does
> everyone see the winner and the price, and does the loser see the winning bid?
> **Person:** Everyone sees winner and price; the winning bid is the price anyway.
> **Agent:** (Q5, Q7, Q9) Each round's values are drawn fresh; if a bidder does not bid, should it
> count as no bid? Payoff is value minus price for the winner, 0 otherwise, shown in the report?
> **Person:** No bid is fine; yes to payoff.
> **Agent:** That is one simultaneous layer, `Loop` with a counter connector, `DropRole`
> (a missing bid means no bid), a public report of winner and price, and a private payoff line.
> Tests: nobody's bid or value reaches another bidder; arrival order does not matter.

## Mapping table

| The person says | Use |
|-----------------|-----|
| "they move at the same time", "sealed", "without seeing" | roles in one `SimpleLayer` |
| "after seeing the result", "then", "second stage" | `Seq(first, second)` with a `Connector` |
| "every round", "repeated", "for N periods" | `Loop(body, {again: connector}, {done: ...}, max_iter=N)` |
| "paired up", "in groups", "each pair plays" | `Par` with a splitter |
| "the auctioneer always", "the rule is", "automatically" | automatic layer (no roles) |
| "if nobody bids", "if someone is late" | `TimeoutPolicy` (ask which) |
| "they are told", "they learn" | `report` and `audience` |
| "only they know", "secret" | observation of that role only; write the noninterference test |
| "it builds up", "remembers", "reputation" | store key under a glob scope, copied into state by a connector |
| "a regulator picks, or it is fixed" | role vs automatic layer, with the substitution test |

## Templates

A layer, a connector and a factory are in `examples/01_define_a_layer.py` and `examples/02_compose.py`
(run from the repository root). The factory has this shape, and `env_ref` is
`"module.path:factory_name"`:

```python
def build_x(agents: list[str], rounds: int = 3) -> tuple[Process, Config]:
    roles = {RoleId(f"p{i}"): AgentId(a) for i, a in enumerate(agents)}
    state = XState(...)  # a pydantic model the layer's Contract names
    return Loop(Primitive(x_layer(len(agents))), ...), Config(state=state.model_dump(), roles=roles)
```

The test file skeleton, with the generic fixtures:

```python
# runnable
from gao_idl import ScriptedAgent, Session, run_sync
from gao_idl.core.ids import AgentId
from gao_idl.testing import (
    assert_arrival_invariant,
    assert_noninterference,
    assert_replay_equivalent,
)

ENV = "gao_idl.environments.pd:build_repeated_pd"
PARAMS = {"agents": ["a", "b"], "rounds": 3}
ACTIONS = {AgentId("a"): {"move": "C"}, AgentId("b"): {"move": "D"}}


def test_replay_equivalent() -> None:
    session = Session.start(ENV, PARAMS, 1)
    run_sync(session, {a: ScriptedAgent(lambda req, reps, m=m: m) for a, m in ACTIONS.items()})
    assert session.finished and not session.failed
    assert_replay_equivalent(session)


def test_arrival_invariant() -> None:
    assert_arrival_invariant(ENV, PARAMS, 1, ACTIONS)


def test_a_parameter_that_must_not_matter() -> None:
    # `default_move` is only used on a timeout; with the default policy it must not change anything
    assert_noninterference(
        ENV, PARAMS, PARAMS | {"default_move": "D"}, 1, ACTIONS, [AgentId("a"), AgentId("b")]
    )


test_replay_equivalent()
test_arrival_invariant()
test_a_parameter_that_must_not_matter()
```

Fixture list in `gao_idl.testing`, and when each applies:

- `assert_replay_equivalent(session)`: always; replaying the input log reproduces the trace.
- `assert_incremental_equals_fold(session)`: always; cached state equals the fold of the log.
- `assert_arrival_invariant(env_ref, params, seed, actions)`: always for simultaneous layers;
  information, ledger and per-branch traces must not depend on submission order.
- `assert_noninterference(env_ref, params_a, params_b, seed, actions, agents)`: for each secret in
  Q4, when every listed agent has a decision in round one. Otherwise hand-roll the comparison (see
  `tests/environments/test_vcm.py`, `test_congestion.py`).
- `assert_substitution_equivalent(process_a, process_b, cfg, seed, actions)`: for each pair
  committed/chosen from Q3; deterministic layers only.
- `assert_branch_rng_independence(build_par_env, seed)`: for any `Par` whose layers draw
  randomness.

## Anti-patterns

| Symptom | Fix |
|---------|-----|
| `observe` reads the store and gives different answers across replays or restores | `observe` has no store access: a connector copies the value into entry state |
| a missing action quietly becomes a default (bid 0, move C) | ask Q7; use `DropRole` or `Fail` unless a neutral default exists and the person named it |
| a hidden quantity appears in a report because `LayerCompleted` or an exit record carried it | exit records are for connectors, reports for people: build reports from an explicit public model |
| payoff computed in `utilities` but absent from every report | learners and language models cannot see the ledger; add the payoff to the recipient's report |
| the trace differs when two agents submit in the other order | drive per-role state through the layer's `outcome`, which receives all actions at once; do not mutate between submissions |
| layer code uses `random`, `time`, `id()` or dict order | only the `rng` argument; sort before iterating |
| `DefaultAction` on a decision with no neutral action (a nomination, a verification) | `Fail` or `DropRole`; record the choice in `NOTES.md` |
| reports go to everyone though some are private | `audience` and `report(recipient)` decide per agent; forget neither |
| a continuous parameter is hidden from the layer by rounding in a connector | keep exact arithmetic (`Fraction`) where ties matter |
| only the happy path is tested | add a mutation check, below |

## Definition of done

1. Questions 1 to 9 have confirmed answers; underdetermined points are in `NOTES.md`.
2. The module docstring ends with a `Stability:` paragraph; the factory is documented.
3. Tests, at least: replay, arrival invariance, one noninterference or hand-rolled secrecy test
   per Q4 secret, timeout behaviour per Q7 answer, restore from a snapshot mid-run.
4. **Mutation checks**: temporarily leak a secret into a report, and swap an arrival-dependent
   write in; each named fixture must fail. If a fixture still passes, it is not testing that.
5. `ruff check`, `ruff format --check`, `mypy`, `pytest -q -m "not slow"` are green.
6. A short human explanation exists (see `skills/explain-environment`).
