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
