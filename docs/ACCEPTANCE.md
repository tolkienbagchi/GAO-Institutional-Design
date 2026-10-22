# Acceptance

This is the release test of `gao_idl`, and it is run by a person, not by the test suite. It checks
the statement the package is built to satisfy:

> Someone who has never seen the code can install the package in a clean virtualenv, follow a
> tutorial to define a new layer and compose it with an existing environment, run it with
> scripted, tabular and mocked-LLM agents, pause and resume a session, and find every public
> symbol documented.

**Who:** a Python programmer who has not worked on `gao_idl` (and is not its author), plus a
facilitator who only watches and keeps the clock. **How long:** one hour, hard stop. **What to
record:** the minutes each step took and every place you were stuck or confused. A step that
needs the facilitator's help has failed, and that is a finding, not an embarrassment.

| Step | What | Budget |
| --- | --- | --- |
| 1 | Install the wheel in a clean virtualenv | 5 min |
| 2 | Run tutorials 1 to 4 | 15 min |
| 3 | Define a layer of your own | 15 min |
| 4 | Wire it into the prisoner's dilemma or participatory budgeting | 10 min |
| 5 | Run it with scripted, table and mock-LLM agents | 10 min |
| 6 | Find the public symbols in the documentation | 5 min |

## Before you start (facilitator)

You need the built artifacts and nothing else from the repository:

```bash
python -m build            # writes dist/gao_idl-0.1.0-py3-none-any.whl and dist/gao_idl-0.1.0.tar.gz
twine check dist/*         # both files must pass
bash scripts/clean_install.sh   # the automatic version of step 1; it must pass first
```

Give the participant the wheel, the source archive (it contains `docs/` and `examples/`), and
this page. Do **not** give them the repository, `NOTES.md`, or the directory
`examples/acceptance/`, which holds a worked answer to steps 3 to 5. Hand that out only if the
participant has been stuck for ten minutes, and write down where.

## Step 1: install (5 min)

```bash
python3 -m venv acc-venv
. acc-venv/bin/activate
pip install path/to/gao_idl-0.1.0-py3-none-any.whl
python -c "import gao_idl; gao_idl.selftest()"
```

Expected output (the auction utility is a random draw from a fixed seed; it is the same on every
run, but do not worry about its digits):

```text
gao_idl 0.1.0 selftest
auction  exit=done     utility={'alice': 7.383878657506836, 'bob': 0.0}
pd       exit=done     utility={'alice': 2.0, 'bob': 7.0}
ok
```

Then check that the optional dependencies are not needed:

```bash
python -c "import sys, gao_idl; print([m for m in ('httpx', 'hypothesis', 'pytest') if m in sys.modules])"
```

- [ ] the selftest prints the four lines above and exits with status 0
- [ ] the second command prints `[]`
- [ ] `pip list` shows `gao-idl`, `pydantic` and `numpy` (with their own dependencies) and no `httpx`

## Step 2: tutorials 1 to 4 (15 min)

```bash
tar xzf path/to/gao_idl-0.1.0.tar.gz && cd gao_idl-0.1.0
```

Read each tutorial page in `docs/tutorials/` (they are plain Markdown; `pip install mkdocs-material`
and `mkdocs serve` shows them as a site) and run its script with the virtualenv's Python. The
scripts print the following.

**Tutorial 1**, `python examples/01_define_a_layer.py`:

```text
exit: done
ann 11.15
bo 12.4
cy 14.91
```

**Tutorial 2**, `python examples/02_compose.py`:

```text
5 rounds: {'ann': 68.0, 'bo': 57.1, 'cy': 65.1}
tournament: {'ann': 41.0, 'bo': 34.4, 'cy': 36.2, 'di': 38.7}
PD shape:         loop(seq(par(primitive, primitive), primitive))
tournament shape: loop(par(primitive, primitive))
```

**Tutorial 3**, `python examples/03_mixed_agents.py` (a few seconds: it trains a learner first):

```text
ann:  63.33
bo:  38.33
cy:  63.33
model calls: 5
```

**Tutorial 4**, `python examples/04_pause_resume.py`:

```text
paused after 1 answered decision(s); snapshot: 561 bytes
fresh process answered 3 more; totals {'hu': 36.0, 'bot': 56.0}
identical to an uninterrupted run
```

- [ ] 1: output as above (the three numbers are exact)
- [ ] 2: output as above, and you can say in a sentence what `Loop` and `Par` each did
- [ ] 3: output as above; you can point at the line that makes `bo` a language-model agent
- [ ] 4: output as above (the byte count may differ a little); you can say what a snapshot contains
- [ ] you noted anything in the four pages that was wrong, missing or confusing

## Step 3: define a layer of your own (15 min)

Pick a one-round game that is not the public-goods game: an ultimatum offer, a stag hunt, a
rock-paper-scissors round, a sealed-bid first-price auction for one item, a rating or a vote.
Write it in a new directory, in a module called `my_env.py`, using tutorial 1 as your guide.

Your layer must have

- a `Contract` with an entry-state model and at least one role (two or more makes a better game);
- an action model for each role, so that `RandomAgent` can play it (bounded numbers, enums);
- an `outcome`, `utilities` that credit each agent, and an `export` (or a single exit port);
- a factory `build(...)` returning `(process, config)`, and nothing that uses `random`, `time` or
  global state (determinism is checked).

A skeleton to fill in:

```python
from gao_idl import Config, Contract, Primitive, SimpleLayer
from gao_idl.core.ids import AgentId, Port, RoleId
from pydantic import BaseModel

class State(BaseModel): ...        # what the layer needs on entry
class Action(BaseModel): ...       # what a role may do
class Outcome(BaseModel): ...      # the resolved round; also the exit record

def my_layer() -> SimpleLayer:
    return SimpleLayer(
        name="my_layer",
        entry=Contract(state_schema=State, roles={RoleId("a"): {}, RoleId("b"): {}}),
        exits={Port("done"): Outcome},
        action_schema=lambda cfg, role: Action,
        outcome=...,       # (cfg, actions by role, rng, store) -> Outcome
        utilities=...,     # (cfg, outcome) -> {agent: float}
        export=...,        # (cfg, outcome) -> Exit(port=..., record=...)   [optional with one exit]
    )

def build(...):
    return Primitive(my_layer()), Config(state=..., roles={RoleId("a"): AgentId("x"), ...})
```

Run it alone with random agents and check it:

```python
from gao_idl import RandomAgent, Session, run_sync
session = Session.start("my_env:build", {...}, seed=1)
run_sync(session, {agent: RandomAgent(seed=i) for i, agent in enumerate(agents)})
session.check_determinism()
print(session.result, [session.ledger.utility(a) for a in agents])
```

- [ ] the session finishes with the exit port you declared (`session.finished` is true)
- [ ] `check_determinism()` passes
- [ ] the utilities agree with a hand calculation for one set of actions
      (use `Session.start`, `advance`, `submit` as in the README, or `ScriptedAgent`s)
- [ ] no step needed the author

## Step 4: wire it into PD or PB (10 min)

Play your layer **after** the repeated prisoner's dilemma (`gao_idl.environments.pd.build_repeated_pd`)
or participatory budgeting (`gao_idl.environments.pb.build_pb`), or before it if that makes more
sense, with `Seq` and a `Connector`:

```python
from gao_idl import Config, Connector, Seq
from gao_idl.composition.contracts import check, describe

pd, entry = build_repeated_pd(agents, rounds=3)
to_mine = Connector(lambda record, ledger, store: Config(state=..., roles=...), State)
combined = Seq(pd, {Port("done"): (to_mine, Primitive(my_layer()))}, name="pd_then_mine")
```

Things you will need to know, all in the documentation:

- the exit port and the record shape of the thing you follow (PD: port `done`, record
  `{"round_index", "rounds", "pairs"}`; PB: ports `done` and `none`; a port you do not route simply
  becomes an exit of the combined process);
- a connector's output model must be exactly the next layer's entry-state model, and the `Config`
  it builds assigns roles to agents;
- `check(process)` and `describe(process)` show the wiring and fail early if it is wrong.

Export a factory `build(agents, ...)` from `my_env.py` again, so that a session can find it by
name (`"my_env:build"`).

- [ ] `check(combined)` passes and `describe(combined)["kind"]` is `"seq"`
- [ ] four random agents play the combined environment to the end, in both layers
- [ ] `session.check_determinism()` passes

## Step 5: scripted, table and mock-LLM agents (10 min)

Seat four agents in your combined environment, one of each kind and a random one:

| Agent | Adapter |
| --- | --- |
| `alice` | `ScriptedAgent(policy)` |
| `bob` | a `TableAgent` restored from a checkpoint you train |
| `carol` | `LLMAgent(agent_id, MockBackend(...))` from `gao_idl.llm` |
| `dan` | `RandomAgent` |

Three things to watch for (they are the normal sharp edges, not bugs):

- A scripted policy and a mock backend must answer **every** layer the agent takes part in. Look at
  `request.layer` (scripted) or at the action schema in the prompt (mock backend, which is given
  the messages) and answer accordingly.
- To train `bob`, use `TrainSpec` and `train` from `gao_idl.learning.trainer` as in tutorials 3
  and 5, with `opponents={...: "scripted:<name>"}` and a `default_opponent_factory({...})` that
  knows your policy. Write the checkpoint with `json.dumps(result.checkpoint)`.
- A `FieldObsCodec` field must exist in the observation of every layer `bob` plays. If your layer
  shares no field with the PD, use `FieldObsCodec([])` (one state): the learner is then not very
  smart, which is fine for this exercise. `SchemaActCodec` needs bounded integers, enums or a
  `grid=` for numbers.

Then run it, check it, and prove you can resume it:

```python
session = run_sync(Session.start("my_env:build", {...}, seed=1), adapters)
session.check_determinism()
Path("session.json").write_text(json.dumps(session.snapshot()))
again = Session.restore(json.loads(Path("session.json").read_text()))
assert again.result == session.result
```

The shape of a good run:

```text
exit: done {...}
alice    2.0
bob      7.0
carol    0.0
dan     15.0
replay identical
```

(your numbers will differ with your layer).

- [ ] all four agents take part in both layers and the session finishes with the expected exit port
- [ ] `check_determinism()` passes with the mock-LLM agent in the session
- [ ] restoring from `session.json` gives the same result
- [ ] for bonus: `gao-idl run` and `gao-idl replay` (see
      [Reproducible runs](runs.md)) work on `examples/configs/pd.json`

## Step 6: every public symbol is documented (5 min)

```python
import gao_idl, gao_idl.llm
print(len(gao_idl.__all__), len(gao_idl.llm.__all__))     # 23 24
```

Choose five names at random from the two lists. For each: `help(name)` shows a description, and the
name is found in the API reference (`docs/api/index.md` lists every public name with its page; with
`pip install mkdocs-material "mkdocstrings[python]"` and `mkdocs serve` the pages are rendered).

- [ ] the two counts are 23 and 24
- [ ] five names: each has a docstring and a place in the API reference
- [ ] you could tell, from the documentation alone, which modules are stable and which are not
      (each module's docstring has a `Stability:` paragraph)

## Sign-off

| | |
| --- | --- |
| Participant | |
| Facilitator | |
| Date and version (`gao_idl.__version__`) | |
| Minutes per step (1 to 6) | |
| Steps that needed help from the author | |
| Places the documentation was wrong, missing or confusing | |
| Verdict | **Pass** if every box above is ticked, within one hour, and no step needed the author. Otherwise fail, and list what to fix. |

## Facilitator notes

- The worked answer is in `examples/acceptance/` (`my_env.py`, `train_table.py`, `run_mixed.py`)
  and is exercised by `tests/test_acceptance.py`, so the three steps it covers are known to be
  possible. It plays a rating layer after the prisoner's dilemma.
- Known limits you may see, none of which fail the exercise: the helpers in `gao_idl.testing`
  answer every decision of an agent with the same action.
- If the participant's layer is more ambitious than the hour allows, stop at step 5 and record how
  far they got.
