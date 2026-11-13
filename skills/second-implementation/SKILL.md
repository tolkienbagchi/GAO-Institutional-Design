---
name: second-implementation
description: Write an independent second implementation of a gao_idl environment from its spec and design page alone, then run divergence search against the first to find what the spec left open. Use after an environment and its EnvSpec exist and before a person is asked to confirm scenario cards, or when asked "is this spec precise enough?".
---

# Write a second implementation and find where it disagrees

## When to use

An environment and its `EnvSpec` exist (`skills/design-environment`). You are a *different* agent
session from the one that wrote the environment. Your job is to find the places where the spec
admits more than one reading, by building what the spec says without looking at what was built.
Signatures: `docs/for-agents/llms.md`. Workflow for the person: `docs/verifying.md`.

## The one rule

**Do not open the first implementation.** Not its module, not its tests, not its tutorial, not
the cards' recorded outcomes. Your inputs are exactly three:

1. the spec, `SPEC.json`;
2. its rendering, `gao-idl spec render SPEC.json --out design.md` (steps, the diagram with the
   names of the ways out, who is told which fields, the unconfirmed assumptions);
3. `docs/for-agents/llms.md` for the API.

If you have already seen the first implementation in this session, say so and stop: a second
implementation written by someone who read the first finds nothing.

## Procedure

1. **Build from the spec.** One factory with the same parameters as `spec.params`, the same agent
   names, the step names of the spec, the same action field names (`chooses`) and the same names
   for the ways out as the diagram shows. Follow every assumption in the spec as written.
2. **Where the spec is silent, choose and write the choice down** in the module docstring. Do not
   ask the first author and do not guess what they did. These choices are what you are looking for.
3. **Run the search.**
   `gao-idl diverge SPEC.json your.module:build_x --n 200 --out choices.json`
   replays 200 edge-biased scripts on both and writes one question per distinct disagreement.
4. **Read the result by level.** `interface`: your factory cannot play their scripts; fix yours
   only if you misread the spec, otherwise it is a finding. `exit` and `utilities`: the two
   readings give different endings or payoffs; these go to the person. `information`: the same
   payoffs but different things shown or told; often an encoding the spec never fixed.
5. **Hand over the choice cards, unanswered.** `chosen` stays empty; only the person who described
   the institution answers "which did you mean?". For each answered card the spec gains an
   assumption (confirmed) and the losing implementation is fixed.

## Example

`examples/candidates/auction_second.py` is a second sealed-bid auction written from
`examples/specs/auction.json`. Endings and payoffs agree with the shipped one on every script; they
differ in what the text fields contain and which fields an unsold item reports, which the spec
does not say.

```python
# runnable
from pathlib import Path

from gao_idl.authoring import diverge, edge_scripts, load_spec, render_choice, to_choice_cards

spec = load_spec(Path("examples/specs/auction.json"))
second = "candidates.auction_second:build_auction"  # importable with examples/ on the path
scripts = edge_scripts(spec.env_ref, spec.params, 1, 60)
found = diverge([spec.env_ref, second], spec.params, [1], scripts)
assert {d.kind for d in found} == {"information"}  # same endings and payoffs everywhere
cards = to_choice_cards(found)
text = render_choice(cards[0], spec)
assert "**The same in every option.**" in text and "**Option B.**" in text
assert cards[0].chosen is None  # a person answers, not you
```

## Definition of done

1. The second factory was written without reading the first; its docstring lists every choice
   the spec did not settle.
2. `gao-idl diverge` ran with at least 200 scripts; the choice cards are saved and unanswered.
3. A short note says, per card, whether it is an ending, a payoff or an encoding difference, and
   which sentence of the spec (or which missing sentence) it comes from.
