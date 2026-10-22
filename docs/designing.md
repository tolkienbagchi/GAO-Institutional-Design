# Designing an environment

You have an institution in mind: an auction, a toll, a market with auditors. This page is the
human version of the `design-environment` skill (`skills/design-environment/SKILL.md`, which an AI
assistant can follow to do the same job with you). It is nine questions, in an order that works,
each with the construct the answer decides. Two worked cases follow the questions: tutorial 10
(congestion pricing) and tutorial 11 (a carbon market with verifiers).

Write the answers down before you write code. Where your description leaves something open (what
happens if nobody bids?), the right move is to decide it on purpose and record it, not to let a
default decide it.

## The four things a participant can know

Everything a participant ever knows reaches it through one of four objects. Most design mistakes
are a fact sitting in the wrong one.

```text
                  entry state (Config)
                         |
          +--------------+---------------+
          |                              |
   observe(cfg, role)            action_schema(cfg, role)
   = the OBSERVATION             = the ACTION SCHEMA
   what you see when             what you may do
   you decide                    (bounds, menus)
          |                              |
          +------------> decision <------+
                         |
                    outcome(actions)
                         |
          +--------------+---------------+
          |                              |
 report(cfg, outcome, recipient)    export(cfg, outcome)
 = the REPORT                       = the EXIT RECORD
 what you are told afterwards;      the state handed to the next
 the only channel for payoffs       stage; seen by connectors,
 and results                        never by a participant
```

The ledger (utilities) and the trace belong to the researcher. A learner or a language model sees a
payoff only if a report carries it.

## The nine questions

1. **Who decides, and when?** If everyone moves at once without seeing the others, that is one
   layer with several roles. If someone sees a result first, that is a second layer after the
   first (`Seq`). Simultaneity is enforced: nobody learns anything about anyone's action until all
   have acted.
2. **What does each decider know, and where does it come from?** What a role can see is exactly
   its layer's observation, a function of the layer's entry state. Anything that must persist
   (a track record, last period's price) is copied into that state by a connector between stages.
3. **Is it committed in advance or chosen live?** A posted price or a rule is an automatic layer
   (no roles). A choice is a role. If you want to compare "the toll is fixed" with "a regulator
   picks it", build both so that everything downstream cannot tell them apart, and test that.
4. **What is private, and who must never learn it?** Each answer is a test: change the secret and
   check that nobody else's information stream changes by one event.
5. **What repeats, and what carries over?** Repetition is a `Loop`; what carries over passes through
   the feedback connector, or the store for things that accumulate.
6. **Who interacts in parallel?** Independent groups (pairs, markets) run side by side in a `Par`
   with disjoint state, and their results are joined.
7. **What happens when someone does not act?** Three options: the run fails, a default action is
   used (and is marked as a default in the trace), or the role is dropped. Some decisions have no
   neutral default (a nomination, a verification report); do not invent one.
8. **What are participants told afterwards, and is it the same for everyone?** Decide the audience
   and the content of each report. Public parts should be identical across recipients; private
   parts should carry only the recipient's own numbers.
9. **What is the payoff, and when is it realized?** Utility goes to the ledger; to reach a
   participant it must also be in a report. Say when it is realized (each round, at the end, only
   if audited).

## Worked case: congestion pricing (tutorial 10)

| Question | Answer |
|----------|--------|
| 1 | commuters choose routes at once; the regulator sets the toll before they do |
| 2 | commuters see their zone, value of time, toll and last period's route times; the regulator sees only what its reporting regime allows |
| 3 | the toll is a committed rule *or* a regulator role; the two are interchangeable downstream |
| 4 | a commuter's value of time stays with the commuter; the regulator never sees individuals |
| 5 | periods repeat; last period's report carries over, through the store, into the next regulator observation |
| 6 | none (a single city) |
| 7 | the toll stage fails by default; a default toll can be set explicitly |
| 8 | the report regime (`hotspot`, `pop_weighted`, `both`) is the experimental variable |
| 9 | commuters pay minus cost; the regulator is paid minus the objective it is shown |

## Worked case: a carbon market (tutorial 11)

| Question | Answer |
|----------|--------|
| 1 | verifiers report simultaneously, then sellers and buyers send sealed orders |
| 2 | buyers see verifiers' reports, not true quality; with `reputation_visible`, also verifiers' track records |
| 3 | clearing and audits are automatic (committed rules), with audit probability a parameter |
| 4 | true quality never reaches buyers except through audits (proved by a test that flips every quality) |
| 5 | rounds repeat; audit history accumulates in the store |
| 6 | none |
| 7 | a missing verification fails the run; a missing order is an order for zero |
| 8 | everyone sees price and volume and audit revelations; fills and payments are private |
| 9 | fees and penalties for verifiers; surplus for buyers and sellers, settled on reported or true quality |

The carbon-market case shows how the questions pay for themselves: nothing in the first description
said whether a missing verification should be fatal, whether buyers should see the track record, or
whether credits are pooled. Each had to be decided, and each decision is a parameter, a timeout
policy or a layer you can swap.

## What to build once the questions are answered

Start from `examples/01_define_a_layer.py` and `examples/02_compose.py`. Add the generic checks from
`gao_idl.testing` (replay, arrival order, noninterference for each private fact, substitution for
each committed/chosen pair), then check that the checks have teeth: temporarily leak a secret into a
report and confirm the right test fails. Tutorial 6 shows how. For language-model participants,
write a codec as in the `write-llm-codec` skill. A single page for pasting into an assistant's
context is [for AI agents](for-agents/llms.md).
