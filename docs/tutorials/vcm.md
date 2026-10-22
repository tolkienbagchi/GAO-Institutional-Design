# 11. A carbon market with verifiers

Buyers want carbon credits of good quality, but quality is hard to see. **Verifiers**, paid a fee
per report, tell the market how good each project is. Sellers and buyers then send sealed orders
to a uniform-price call auction, and a random **audit** sometimes reveals a traded project's true
quality, penalizing verifiers who were far off. The institution is the rule set around the verifier:
who pays whom, what is audited, and what buyers are told about past audits. This tutorial builds it
up one piece at a time and ends with an experiment on verifier reputation.

The environment is `gao_idl.environments.vcm`. Unlike tutorial 10, nothing is rebuilt by hand: the
layers are internal, so each step switches one piece of `build_vcm` on and looks at what changes.
Run everything with `python examples/11_vcm.py`.

!!! note "The market"
    Sellers own projects of true quality `theta` (in `[0, 1]`) with a cost per credit. Each
    project is verified by exactly one verifier, who measures `theta` with noise (and, for a
    biased verifier, an offset) and publishes a report. Buyers have a demand and a value per
    credit *at full quality*. Prices are per credit. In the default **per-project** form
    (`market_form="per_project"`) a buyer sends one optional `(price, qty)` order for each project
    and every project clears in its own auction, so a buyer holds exactly the projects it bid for.
    The **pooled** form (`market_form="pooled"`) has one order per buyer and one auction; credits
    are then pooled across sellers and a buyer's basket is a quantity-weighted mix of whatever
    sold. Every number is stylized.

```python
--8<-- "examples/11_vcm.py:helpers"
```

`lemons_population()` is the fixture used throughout: two good projects (`theta` .9 and .8,
verified by an honest `v0`), two poor ones (.4 and .3) verified by `v1`, whose reports run 0.3 too
high, and three buyers.

## 1. The call auction alone

```python
--8<-- "examples/11_vcm.py:step1"
```

`clear_call_auction` is a plain function on orders `(who, limit price, quantity)`. It sorts bids
down and asks up, finds the largest volume `V` at which the `V`-th bid still covers the `V`-th
ask, and sets the price at the **midpoint** of that marginal pair (`k = 1/2`). Everyone trades at
that one price. Ties at the margin are rationed pro rata by quantity, in exact `Fraction`
arithmetic, so the result does not depend on who arrived first. The tests check three hand-worked
instances, including this one.

## 2. Add verification, and prove what buyers can learn

```python
--8<-- "examples/11_vcm.py:step2"
```

The first layer of a round is verification: each verifier sees its noisy measurements of its own
projects and reports a number per project. Buyers see only those reports (and who made them).

That claim is a test, not a hope. With `p_audit=0` and verifiers that ignore what they measured,
every true quality is replaced by its complement, and no buyer's or seller's information stream
may change by a single event. The verifiers' own streams do change, and the
example asserts that too. This is the `assert_noninterference` idea from tutorial 6, written as a
direct comparison because the market has no decision all roles share in round one (see `NOTES.md`).

## 3. Add audits

```python
--8<-- "examples/11_vcm.py:step3"
```

```text
step 3 v0: fees 2.0 penalties 0.0
step 3 v1: fees 2.0 penalties 10.0
step 3 revealed to every buyer: {'s0': 0.9, 's1': 0.8, 's2': 0.4, 's3': 0.3}
```

After clearing, each *traded* project is audited with probability `p_audit`, using one uniform
draw per project per round keyed by its path, so changing `p_audit` changes only the audit layer's
outcomes. An audited report that is off by more than `tol` costs its verifier `penalty`; fees are
paid on every report. Revelations are public. Here `v1` pays 10 (two audited projects, penalty 5
each) and `v0` nothing.

## 4. Loop, with reputation

```python
--8<-- "examples/11_vcm.py:step4"
```

```text
step 4 b0 sees, in round 3: {'v0': {'audited': 4.0, 'wrong': 0.0, 'mean_error': 0.0}, 'v1': {'audited': 4.0, 'wrong': 4.0, 'mean_error': 0.31}}
```

The audit layer exits `again` until the last round. It also writes each verifier's audit history
to the store; the connector into the next round copies it into the buyers' state (a layer's
`observe` cannot read the store). With `reputation_visible=False` that copy is simply not made, and
the buyers' observations contain no trace of it.

## 5. The experiment: should buyers be shown the track record?

Same market, same seeds, same sellers and verifiers; only two things vary: whether the record is
shown, and whether the buyers use it. The naive buyer bids its value times the reported quality
of each project; the skeptical buyer subtracts each verifier's past mean over-statement when the
record is shown. Sellers offer at cost. The experiment records public clearing prices and the
biased verifier's share of the traded volume, once for each market form.

```python
--8<-- "examples/11_vcm.py:experiment"
```

**Per-project orders.** Both buyers split their demand over the projects in proportion to the
quality they believe, and bid that quality times their value on each.

```text
step 5 per_project naive     hidden  price s2 [5.02, 5.16, 4.89, 5.14, 5.09, 4.91]
step 5 per_project naive     hidden  v1 volume share [0.4, 0.4, 0.4, 0.4, 0.4, 0.4]
step 5 per_project skeptical visible price s0 [8.58, 8.62, 8.31, 8.38, 8.62, 8.46]
step 5 per_project skeptical visible price s2 [5.02, 3.66, 3.39, 3.64, 3.59, 3.36]
step 5 per_project skeptical visible v1 volume share [0.4, 0.32, 0.32, 0.32, 0.32, 0.32]
```

The naive buyer ignores the record (shown or not), and the skeptical buyer without it is the
naive buyer: those rows are identical. Once a skeptic is shown the record, the first audit
changes what it does. It bids less for `v1`'s projects (the price of `s2` falls from about 5 to
about 3.5, close to its cost of 3), buys fewer of them (the biased verifier's share of volume
falls from 0.40 to 0.32), and the honest projects keep their prices (`s0` stays around 8.5). A
buyer's losses on the biased projects stop, and the sellers of those projects are paid for
quality that is not there only up to what the market now believes.

**Pooled orders.** The same experiment with one order and one auction.

```text
step 5 pooled      skeptical hidden  price pool [7.27, 7.32, 7.21, 7.2, 7.26, 7.15]
step 5 pooled      skeptical visible price pool [7.27, 4.72, 4.61, 4.6, 4.66, 4.52]
step 5 pooled      skeptical visible v1 volume share [0.67, 1.0, 1.0, 1.0, 1.0, 1.0]
```

Here the record does damage. A skeptical buyer discounts the whole pool, since it cannot pick
`v0`'s projects out of a basket that is a mix, so the price falls from about 7.2 to about 4.6,
below the honest sellers' costs (8 and 7). The high-quality projects stop selling and the biased
verifier's projects are all that clears: its volume share goes from two thirds to one. This is the
lemons collapse, and pooling is what drives it: with the same buyers, the same record and the
same verifiers, per-project orders let the information be used where it applies.

Limits of the comparison: one fixture, two scripted buyer rules, six rounds, and a seller that
asks its cost whatever happens. The pooled result is reproduced by the tests; the per-project one
by `test_skeptical_buyer_with_visible_record_outperforms_hidden`.

## 6. Learning buyers

```python
--8<-- "examples/11_vcm.py:learning"
```

```text
step 6 learner (hidden ) mean return: 13.8
step 6 learner (hidden ) greedy markdown of v1 by state: {'["hidden"]': 0.0}
step 6 learner (visible) mean return: 15.6
step 6 learner (visible) greedy markdown of v1 by state: {'["record", 0, 0]': 0.0, '["record", 0, 1]': 0.0, '["record", 1, 1]': 0.3}
```

A tabular buyer, paid its audited net surplus (`own_net`), chooses one number per round: how far
to mark down the reported quality of `v1`'s projects (0 to 0.4); every other project is bid at its
reported quality and the demand is spread evenly. In its state it has, depending on the run,
nothing (`hidden`) or a three-level summary of `v1`'s record (never audited, audited and clean,
caught). The rivals are naive per-project buyers; `v1` is of an unknown type, inflating its
reports by 0.3 in one episode in ten and honest in the others; audits are certain, so the reward
is the true surplus.

The type matters for the design. If `v1` inflated in every episode a learner would mark down
whether or not it saw the record, and the record would be worth nothing. With a verifier that is
usually honest, the best fixed policy trusts `v1` (a loss in one episode in ten costs less than
staying out of the other nine), so the hidden learner learns to mark down by 0. The learner that
sees the record learns the same until `v1` is caught and then marks down by 0.3: a lower bid on
`v1`'s projects, and a higher return, in the episodes in which `v1` inflates. The gap is a few
units of return on a small number of episodes and depends on the learner's seed; the `slow` test
`test_tabular_buyer_discounts_biased_verifier_when_record_visible` pins one run and checks
both the bids and the returns. The pooled market has no such result: there every bid that trades
loses money, and a learner stays out of the market whatever it is shown
(`test_tabular_buyer_learns_to_stay_out_of_the_lemons_market`).

## What a researcher would change

- **Fee structure.** `fee` is a number in `build_vcm`; paying per audited-and-correct report
  instead is a change to the audit layer's payout.
- **Liability.** `penalty` and `tol` set who pays and how much; making sellers or buyers liable
  too means a new payout rule in the same audit layer.
- **Audit targeting.** `p_audit` is uniform. Targeting (audit the largest or the least-trusted
  project) means swapping the automatic audit layer for one that reads the store's record: the
  other layers do not change, and the substitution test from tutorial 6 applies unchanged.
- **Transparency.** `reputation_visible`, as above; what a record contains is `RepStat` and the
  connector that copies it.
- **Market form.** `market_form="per_project"` or `"pooled"` decides whether the information in
  the record can be acted on project by project; step 5 shows that this alone can reverse the
  effect of showing the record.

Fee, liability, transparency and market form are parameters; audit targeting and a fee on correct
reports are layer swaps.

## The whole script

```python
--8<-- "examples/11_vcm.py"
```
