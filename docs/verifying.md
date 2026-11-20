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

The author hands you one file, `review.html`. Open it in any browser: it needs no server, no
internet connection and no installation, and it makes no network request.

1. **What was checked.** How many runs of the environment were compared with the design, every
   difference found, and the rules of the design that nothing checks automatically. These checks
   compare the code with the design, not the design with the world.
2. **The design.** Who takes part, the steps, a picture of how they connect, and who knows what
   (taken from runs of the built environment, not from the description), with secrets, payoffs and
   parameters.
3. **Choices nobody confirmed.** Decisions the author made because your description did not settle
   them. Press **Agree**, or press **Disagree…**: a box opens for why, with **Submit** and
   **Cancel**. A disagreement needs a note, as a "No" on a card does.
4. **Cards, one at a time.** Each card is one concrete case: "alice bids 8, bob bids 8, carol bids
   1". The line under the title says which readings of your description the case tells apart.
   Then come the situation, a timeline (one column per participant; what each did or was told is
   written in the column on the left, and what a step told everyone alike is one line starting
   "everyone"), each payoff and how the run ends. Below are two folded lists, the steps in words
   and everything each participant was told, in full. They are open on short cards and closed on
   long ones; click a heading to open it. The answer bar stays at the bottom of the window while
   you read: **Yes, this is what I meant**, **No…** (a box opens; write what should happen
   instead and press **Submit**, or **Cancel**), **Skip** or **Back**. Keys: `y`, `n` (opens the
   box), `s`, and the left and right arrows; in the box, Enter submits, Shift+Enter starts a new
   line and Esc closes it. A card marked out of date shows what the environment does
   now and cannot be confirmed. On a phone the timeline scrolls sideways rather than shrinking
   its text.
5. **Finish.** Type your name and press **Download verdicts**. If your viewer blocks downloads,
   copy the text in the box into a file called `verdicts.json`. Give that file to the author.

Your answers are kept in the page while it is open (and, where the browser allows, when you reopen
it on the same computer). Nothing is sent anywhere.

The author applies your file with `gao-idl cards import cards.json verdicts.json --spec spec.json`.
A verdict is about one exact behaviour: if the environment changed after the page was made, that
card's verdict is refused and the card comes back to you. A "No" or a disagreement without a note
is refused too. Only you give verdicts. A card its author
confirms is not evidence.

**In a terminal instead.** `gao-idl spec render spec.json --cards cards.json --out design.md`
writes the design as a page, and `gao-idl cards review cards.json --spec spec.json` shows one card
at a time, waits for `c` (yes), `r` (no, followed by one line on what should happen instead), `s`
(skip), `b` (back) or `q` (stop), and saves each answer as it goes. `gao-idl cards show` prints all
cards at once, and `gao-idl cards confirm` / `reject` record a verdict by card id, for scripts.

A confirmed card is from then on a regression test: the environment must keep doing exactly that.
A rejected card is one too: `gao-idl cards verify` fails until the environment stops doing it.

## For the author

```bash
gao-idl spec check  examples/specs/auction.json --cards examples/cards/auction.json
gao-idl cards verify examples/cards/auction.json
gao-idl review page examples/specs/auction.json --cards examples/cards/auction.json --out review.html
```

`review page` builds the page the designer reviews (add `--fuzz 200` to put fuzzed runs into its
"What was checked" screen). Everything on it comes from the spec, the built process and real runs;
no part of it is written by hand or by a model. `gao-idl review bundle ... --out bundle.json`
writes the same content as JSON (`bundle_version: 1`), the format any other front end reads, and
`gao-idl review page --bundle bundle.json --out review.html` turns a bundle into the page. When the
designer sends back `verdicts.json`, `gao-idl cards import cards.json verdicts.json --spec spec.json`
applies it and exits 1 if any verdict could not be applied (each is listed); choices the designer
disagreed with are printed for you to resolve. Never import a file you wrote yourself.

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

## More evidence than a handful of cards

Cards are few and chosen by the author. Two tools widen the net; neither needs a person until it
finds something.

**Fuzzing.** `gao-idl spec check spec.json --cards cards.json --fuzz 200` adds 200 runs whose
actions lean on the edges: the smallest and largest allowed number, zero, a number another
participant just chose (a tie), a number the participant was just shown (bidding exactly the
reserve), an empty selection, and silence. Every check above runs on all of them, so a secret is
tested 200 times instead of once, and each invariant of the spec that names a predicate is checked
on every run. An invariant without a predicate is printed as a note: nothing checks it.
`gao_idl.authoring.predicates` has three generic predicates (payoffs of some roles sum to zero, no
role ends below zero, a reported number never exceeds a parameter); write others with the
signature `(session, **args) -> None`.

**A second implementation.** Have a different agent session build the environment again from the
spec and its design page only (`skills/second-implementation`), then

```bash
gao-idl diverge spec.json other.module:build_x --n 200 --out choices.json
```

replays the same edge-biased scripts on both and writes one question per distinct disagreement:
the situation, what every reading agrees on, and where they part. Each disagreement is a place the
spec left open. The designer answers "which did you mean?"; the answer becomes a confirmed
assumption in the spec. `examples/candidates/auction_second.py` is a worked example: it agrees with
the shipped auction on every ending and payoff and differs only in what some text fields say.

A third agent can write tests from the spec without reading the code
(`skills/write-spec-tests`). None of this is proof: a few hundred runs that find nothing are
better evidence than thirteen cards, and no more than that.

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
- **Anything no run exercises.** A branch of the rules that no card and no fuzzed run reaches is
  reported as not exercised, never as correct.
- **Shared misreadings.** Divergence search finds what two implementations read differently. Where
  both read the spec the same wrong way, it is silent.
