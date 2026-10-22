# 10. Congestion pricing as an institution

A city decides how to set a toll for entering its centre, and what it measures afterwards. The
**institution** is the rule set: who sets the toll (a committed rule or a regulator), who chooses
routes (commuters, simultaneously, with private values of time), and, the part that carries the
experiment, **which equity metric the regulator is told about**. This tutorial builds it layer by
layer and then runs one experiment on it: the same greedy regulator, the same commuters, two
reporting regimes.

The environment is `gao_idl.environments.congestion`. Steps 1 to 5 rebuild a miniature of it with
the public API so you can see each piece; steps 6 and 7 use the real one. Run the whole thing with
`python examples/10_congestion.py`.

!!! note "What the city is"
    A central zone `CBD` and three peripheral zones `Z1`, `Z2`, `Z3`; `Z3` is a disadvantaged
    community (DAC). Each peripheral zone has three routes to the centre: `tolled` (through the
    cordon), `bypass` (longer, emits more, and runs through the DAC zone) and `transit` (fixed
    time, no emissions). A commuter's cost is `value_of_time * time + toll + fare`, with BPR
    congestion `t0 * (1 + 0.15 (flow/cap)^4)`. The toll menu is `[0, 10, 15]`.

## 1. The travel layer alone

```python
--8<-- "examples/10_congestion.py:stage"
```

One record, `Stage`, is handed from stage to stage (the real environment has one model per
stage). The travel layer is a `SimpleLayer` like the one in tutorial 1, with one role per
commuter:

```python
--8<-- "examples/10_congestion.py:travel"
```

- `observe` shows a commuter its own zone, its private value of time and the toll, and nothing
  about anybody else.
- `feasible` forbids the car routes to a commuter without a car; `transit` is open to all.
- `outcome` receives *all* the routes at once, turns them into flows, applies BPR, and computes
  each commuter's cost, which `utilities` credits with a minus sign.

The scripted commuter in the example (`myopic_commuter`) takes the cheapest route at the times it
last saw; with two of them the layer runs as a one-layer session.

## 2. Add the exposure kernel

```python
--8<-- "examples/10_congestion.py:exposure"
```

The exposure layer is **automatic**: no roles, so no decisions. It turns route flows into
emissions, emissions into zone concentrations through a fixed dispersion matrix, and
concentrations into the two equity metrics: the number of **hotspots** (zones above a
concentration threshold, DAC zones weighted double) and the **population-weighted mean
concentration** (DAC weighted double). Both are always computed; what anybody is *told* is a
separate matter (step 6). `Seq` joins the layers through a connector that copies the record on.

At toll 0 both commuters use the tolled road; at toll 15 `c2` (a Z3 resident) takes the bypass,
which runs through its own zone, and `c0` takes transit, which empties the centre:

```text
step 2 toll  0: concentration CBD 0.50, Z3 0.15
step 2 toll 15: concentration CBD 0.00, Z3 0.42
```

The centre gets cleaner and the disadvantaged zone dirtier. That displacement is why the choice
of metric matters.

## 3 and 4. Toll setting: a rule, then a regulator

```python
--8<-- "examples/10_congestion.py:toll"
```

The two toll stages are **interchangeable**: same entry state, same exit record, same
announcement downstream. `committed_toll(10)` has no roles; `regulator_toll()` has a `regulator`
role that chooses from the menu. For the same toll, the commuters cannot tell them apart, and
the example checks it. The real environment's test compares the downstream trace event by event,
leaving out only the regulator's own events.

## 5. Loop over periods

```python
--8<-- "examples/10_congestion.py:loop"
```

`Loop` repeats the period; the feedback connector carries what the regulator will see next.
Termination is normal, through a counter (`exposure` exits `again` until the last period, then
`finished`), with `max_iter = periods + 1` and `on_limit="fail"` as a safety net. In the real
environment the feedback connector copies the previous period's route times and report out of the
store (`prev:*` keys), because a layer's `observe` has no access to the store.

## 6. The experiment: what you tell the regulator is what you get

`build_congestion(report_regime=...)` decides which metric the regulator's report contains:
`"hotspot"`, `"pop_weighted"` or `"both"`. The regulator is paid `-objective` of the metric it
sees. Everything else is held fixed: a six-commuter fixture (`tutorial_commuters()`), the same
`myopic_commuter` policy for them, and a `greedy_regulator` that tries each toll once and then
repeats the one with the lowest reported objective.

```python
--8<-- "examples/10_congestion.py:experiment"
```

```text
step 6 hotspot     : tolls [0, 10, 15, 15, 15, 15]; final-period exposure DAC 1,938, non-DAC 255
step 6 pop_weighted: tolls [0, 10, 15, 10, 10, 10]; final-period exposure DAC 1,020, non-DAC 638
```

Told only the hotspot count, the regulator settles on 15: at that toll no zone exceeds the
threshold, so its objective is 0. Told only the population-weighted concentration, it settles
on 10, because at 15 the bypass loads the disadvantaged zone and the weighted mean is higher.
Under the first regime the DAC residents' total exposure ends up about 1.9 times what it is under
the second, while the non-DAC residents' falls to 40 percent.

The fixture is deterministic and explained in the test
`test_equity_metrics_can_disagree`: every commuter's best route is at least one cost unit better
than the second best at every toll, so the outcome does not flip from period to period under
congestion. Exposure is `concentration x population x inhalation rate`, in the model's arbitrary
units.

## 7. A language model as the regulator

```python
--8<-- "examples/10_congestion.py:llm"
```

`RegulatorCodec` renders the menu and the previous period's report *as the regime filtered it*,
and nothing else; the renderer prints the reports the model was sent. The `MockBackend` answers
`{"toll": 10}` every time, so the run needs no network. To use a real model, replace the backend
(install the `llm` extra; the credential is a callable, so the key never sits in a log):

```python
import os
from pydantic import SecretStr
from gao_idl.llm import AnthropicBackend

backend = AnthropicBackend("<model name>", lambda: SecretStr(os.environ["ANTHROPIC_API_KEY"]))
```

`session.check_determinism()` replays the input log, including what the model said, so the
recorded run never calls the model again.

!!! note "What is stylized, and what would replace it"
    Every number here is stylized: the point is to compare institutions, not to predict New York.
    The exposure layer is a surrogate shaped after the UTECH pipeline of Lu et al.,
    *Transportation Research Part D* 161 (2026) 105628: route flows become emissions, a fixed
    weight matrix disperses them onto zones, concentrations become exposure. With the real
    pipeline you would replace `exposure_kernel` (one function, in
    `gao_idl.environments.congestion.model`) by a call to it. A slow external simulator
    is given a role (say `simulator`) whose adapter implements `ExternalRecorder`; the runner
    records what it returns as `ExternalResponse` events in the input log, so a replay or a
    restore never runs it again. An *automatic* layer that calls a slow function is not
    recorded, so such a function must be deterministic and cheap.

## The whole script

```python
--8<-- "examples/10_congestion.py"
```
