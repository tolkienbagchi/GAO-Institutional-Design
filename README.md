# gao_idl

Composable multi-agent environments built from primitive layers (one simultaneous decision round
each) joined with `Seq`, `Par` and `Loop`, with resumable, replayable sessions. Participants can be
scripted, human, tabular-RL or LLM agents.

## Install

```bash
pip install gao-idl            # core: pydantic + numpy
pip install "gao-idl[llm]"     # adds httpx for the LLM backends
python -c "import gao_idl; gao_idl.selftest()"
```

From a checkout: `pip install -e ".[dev]"`, then `pytest -q`.

## Usage

```python
from gao_idl.runtime.session import Session

env = "gao_idl.environments.auction:build_bidding_only"
session = Session.start(env, {"values": {"alice": 10.0, "bob": 7.0}}, seed=1)
session.advance()  # issues one decision request per bidder

for agent in ("alice", "bob"):
    (request,) = session.pending_for(agent)  # each agent sees only its own observation
    assert "values" not in request.observation
    session.submit(agent, request.decision_id, {"amount": 5.0})  # applied on the next advance()

snapshot = session.snapshot()  # JSON-able: env_ref, params, seed and the input log
resumed = Session.restore(snapshot)  # rebuilt by replaying the log, then advanced
assert resumed.finished and resumed.result is not None
print(resumed.result.port)  # settle
print(resumed.reports_for("alice")[0].payload)  # {'status': 'bid accepted', 'own_bid': 5.0}
```

## Tutorials

- **Congestion pricing as an institution** (`docs/tutorials/congestion.md`, `examples/10_congestion.py`):
  a toll, commuters with private values of time, and a regulator told one equity metric or
  another; one experiment on what the reporting regime does to the toll a regulator settles on.
- **A carbon market with verifiers** (`docs/tutorials/vcm.md`, `examples/11_vcm.py`): a call auction
  for credits whose quality is certified by paid verifiers, audits, and a reputation loop; one
  experiment on whether showing buyers the verifiers' track record helps.

Both environments are stylized institutions to run experiments on, not models of any real city
or market. `docs/designing.md` is the nine questions to answer before building one of your own;
`skills/` holds the same protocol, and two more (writing a language-model codec, explaining an
environment), as instructions an AI assistant can follow, and `docs/for-agents/llms.md` is the
single page to give it.

## Command line

```bash
gao-idl run examples/configs/pd.json --out run.json   # play a JSON config, write the session
gao-idl replay run.json                               # rebuild it from the log, check it is identical
```

`python -m gao_idl.cli` does the same. See `docs/runs.md`.

## License

Apache License 2.0 (see `LICENSE`). Copyright 2026 Tolkien Bagchi. To cite the software, see
`CITATION.cff`.
