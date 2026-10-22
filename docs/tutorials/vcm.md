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
    credit *at full quality*; credits are pooled, so a buyer's basket is a quantity-weighted mix
    of the projects that sold. Prices are per credit. Every number is stylized.

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
we change *every* true quality to its complement and require that no buyer's or seller's
information stream changes by a single event. The verifiers' own streams do change, and the
example asserts that too. This is the `assert_noninterference` idea from tutorial 6, written as a
direct comparison because the market has no decision all roles share in round one (see `NOTES.md`).

## 3. Add audits

```python
--8<-- "examples/11_vcm.py:step3"
```

```text
step 3 v0: fees 2.0 penalties 0.0
step 3 v1: fees 2.0 penalties 10.0
step 3 revealed to every buyer: {'s1': 0.8, 's2': 0.4, 's3': 0.3}
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
step 4 b0 sees, in round 3: {'v0': {'audited': 2.0, 'wrong': 0.0, 'mean_error': -0.02}, 'v1': {'audited': 4.0, 'wrong': 4.0, 'mean_error': 0.31}}
```

The audit layer exits `again` until the last round. It also writes each verifier's audit history
to the store; the connector into the next round copies it into the buyers' state (a layer's
`observe` cannot read the store). With `reputation_visible=False` that copy is simply not made, and
the buyers' observations contain no trace of it.

## 5. The experiment: should buyers be shown the track record?

Same market, same seeds, same sellers and verifiers; only two things vary: whether the record is
shown, and whether the buyers use it. `naive_buyer` bids its value times the reported quality;
`skeptical_buyer` subtracts each verifier's past mean over-statement when it can see it.
Sellers offer at cost. We record the clearing price and the biased verifier's share of the traded
volume.

```python
--8<-- "examples/11_vcm.py:experiment"
```

```text
step 5 naive     visible price [7.27, 7.32, 7.21, 7.2, 7.26, 7.15]
step 5 naive     visible v1 volume share [0.67, 0.67, 0.67, 0.67, 0.67, 0.67]
step 5 skeptical hidden  price [7.27, 7.32, 7.21, 7.2, 7.26, 7.15]
step 5 skeptical visible price [7.27, 4.72, 4.61, 4.6, 4.66, 4.52]
step 5 skeptical visible v1 volume share [0.67, 1.0, 1.0, 1.0, 1.0, 1.0]
```

(The naive buyers' and the hidden-record rows are identical, as they must be: a buyer that
ignores the record, or never gets it, behaves the same.) Showing the record to skeptical buyers
does what the lemons story predicts. They discount *the whole pool*, because they bid for a
pooled basket and cannot pick `v0`'s projects out of it. The price falls from about 7.2 to
about 4.6, below the honest sellers' costs (8 and 7), so the high-quality projects stop selling,
and the biased verifier's projects are all that clears: its volume share goes from two thirds to
one. A transparency rule that looks like it would punish the biased verifier ends up concentrating
the market on it.

Two things this does not show. It is one fixture and one pricing rule; pooling is what drives the
result, and I expect that with per-project orders a skeptical buyer could avoid `v1`'s projects (not tested). And
the buyers here are scripted.

## 6. Learning buyers

```python
--8<-- "examples/11_vcm.py:learning"
```

```text
step 6 naive buyer b0 utility: -90.1
step 6 learner (hidden ) greedy mean return: 0.0
step 6 learner (visible) greedy mean return: 0.0
```

A tabular buyer, paid its audited net surplus (`own_net`), with either the round or `v1`'s mean
error as its state, trained against the scripted rivals. On this fixture it learns what the naive
buyer does not: every bid that trades loses money (the pooled basket is far worse than reported),
so it stays out of the market, and gets 0 rather than -90, whether or not the record is shown.
So there is no visible-versus-hidden difference for this learner to find; that needs a market
where a discounted bid can be profitable (a larger gap between the pools' values and costs, or
per-project orders). We report that rather than tune the fixture until the effect appears. The
`slow` test `test_tabular_buyer_learns_to_stay_out_of_the_lemons_market` checks the same two
facts.

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

Unlike the first two, the last two are layer swaps, not one-line parameters.

## The whole script

```python
--8<-- "examples/11_vcm.py"
```
