# Verifying an environment

These tools check one thing: that the environment in code is the institution its designer
described. They do not check that the described institution is an adequate model of a real
market, city or organization. That judgement stays with the people who know the real one.

The situation they are built for: a person describes an institution in words, someone else (often
an AI assistant following `skills/design-environment`) writes the environment, and the person who
described it cannot read the code. Three artefacts stand between the description and the code.

| Artefact | What it is | Who writes it | Who checks it |
| --- | --- | --- | --- |
| Spec (`spec.json`) | The answers to the nine design questions, as data | The author of the environment | The designer, by reading its rendering |
| Design page | The spec in words, with a diagram and a who-knows-what table taken from the built environment | `gao-idl spec render` | The designer |
| Cards (`cards.json`) | Worked scenarios: who does what, what each is told, each payoff | The author proposes them | The designer confirms or rejects each one |

Nothing in this chain is written by a language model at the moment it is checked: the diagram
comes from the process tree, the table and the cards from real runs.

## For the designer

1. **Read the design page.** `gao-idl spec render spec.json --cards cards.json --out design.md`.
   The section "Who knows what" is what the built environment actually showed each role in the
   runs. The last section lists choices the author made that you never stated. Each is a
   modelling decision that is currently nobody's.
2. **Go through the cards.** `gao-idl cards show cards.json --spec spec.json`. Each card is one
   concrete case: "alice bids 8, bob bids 8, carol bids 1", then what everyone is told and paid.
   The line under the title says which readings of your description the case tells apart.
3. **Confirm or reject each card.** `gao-idl cards confirm cards.json tie-top` if that is what you
   meant; `gao-idl cards reject cards.json no-bid --note "a missing bid should count as zero"` if
   it is not. Only you do this. A card its author confirms is not evidence.

A confirmed card is from then on a regression test: the environment must keep doing exactly that.
A rejected card is one too: `gao-idl cards verify` fails until the environment stops doing it.

## For the author

```bash
gao-idl spec check  examples/specs/auction.json --cards examples/cards/auction.json
gao-idl cards verify examples/cards/auction.json
```

`spec check` exits 1 and prints one line per mismatch. The kinds:

| Kind | Meaning |
| --- | --- |
| `step_missing`, `step_extra`, `step_order` | The steps of the spec and the layers of the environment differ |
| `undeclared_sees`, `undeclared_told`, `undeclared_chooses` | The environment shows or allows a field the spec does not list (a possible leak) |
| `unfulfilled_sees`, `unfulfilled_told`, `unfulfilled_chooses` | The spec lists a field that no run produced |
| `timeout_policy` | What happens when someone does not act differs |
| `repeat` | One says something repeats, the other does not |
| `secret_leak` | Changing a secret changed what a role it is hidden from was shown |
| `payoff_invisible` | A role is paid but never told so in the declared report field |
| `not_exercised` | No run reached this step, role, secret or payoff, so nothing is claimed about it |

Each card supplies one run. Without `--cards` almost everything is `not_exercised`: the check
never guesses actions. In a test, `gao_idl.testing.assert_cards(path)` runs `cards verify`.

Cards are data: a script maps each agent to its actions in the order its decisions come, and
`null` means the agent does not act. To make a card from a run driven by policies, play it and
call `gao_idl.authoring.script_of(session)`. `scripts/make_example_cards.py` shows both.

## What the check cannot see

- **Values, only field names.** The who-knows-what table compares field names. A report field
  that is declared but carries more than it should is caught only by a secret test or a card.
- **Secrets that are not parameters.** A secret is tested by changing one factory parameter.
  "A pair never learns another pair's moves" has no parameter to change, so it stays a written
  invariant until a predicate checks it.
- **One parameter regime per spec.** A parameter that changes the fields of an action (the carbon
  market's `market_form`) needs its own spec.
- **Field names that are data.** Where an action has one field per project, the spec lists the
  project names and holds only for that population.
- **Anything no card exercises.** A branch of the rules that no scenario reaches is reported as
  not exercised, never as correct.
