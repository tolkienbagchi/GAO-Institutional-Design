# 2. Compose

Two compositions of the layer from tutorial 1.

1. `build_repeated_pg` loops it for five rounds. The `Loop` feeds the `again` exit back into the
   body through a `Connector`; the connector's output schema must equal the body's entry schema
   (`PGState`), and every exit port of the body must be routed, either fed back or out of the
   loop. The connector here carries the endowments over unchanged.
2. `build_pg_tournament` plays the layer in two groups side by side: a `Par` with one branch per
   group, a `DisjointSplitter` that hands each branch its own `Config` (and rejects an agent who
   would be in two branches), and a custom joiner that decides whether another round follows.
   The `Par` sits inside a `Loop` exactly as in the repeated prisoner's dilemma.

```python
--8<-- "examples/02_compose.py"
```

Run it with `python examples/02_compose.py`. The last two lines print the shape of the
prisoner's-dilemma tournament and of ours:

```text
PD shape:         loop(seq(par(primitive, primitive), primitive))
tournament shape: loop(par(primitive, primitive))
```

## Substitution by contract

`Par` does not know what its leaves are. It knows each branch's entry contract and exit ports,
and it hands the joiner the exits. The prisoner's-dilemma tournament has a `pd_round` layer in
each pair branch; ours has the public-goods layer. Everything else (splitter, loop, feedback
connector) is the same kind of object, and `describe()` shows the two trees are built from
the same pieces. The PD adds a `Seq` with an automatic round counter where we let the joiner choose the
port. Because the wiring is checked when the composite is built, a leaf whose contract does not
fit is rejected at construction, not half way through a run.

Next: [run it with different kinds of agents](03_mixed_agents.md).
