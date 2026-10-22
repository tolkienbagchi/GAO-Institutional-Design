# gao_idl

Composable multi-agent environments built from primitive layers, with resumable, replayable sessions.
Milestone M0: core types and a single-layer session (sealed-bid bidding as the reference example).
Install with `pip install -e .[dev]`, run the tests with `pytest -q`.

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
