"""Live demo for the advisor meeting: Haiku agents in the congestion-pricing institution.

Runs the congestion environment with real Anthropic Messages API calls and writes everything the
slides need to ``demo/advisor_demo_results.json``: per period the toll, the regulator's
observation/report as the regime filtered it, the DAC / non-DAC exposure; every prompt and reply
(``record_text=True``); token usage and latency; and a replay check proving the recorded run
never calls the model again.

Three configurations, same six-commuter fixture, same seed:

  A  regime "hotspot"      Haiku regulator, scripted (myopic) commuters
  B  regime "pop_weighted" Haiku regulator, scripted commuters
  C  regime "hotspot"      Haiku regulator AND Haiku commuters (everyone is a model)

plus the scripted greedy-regulator baseline under both regimes (no model calls) for comparison.

Usage (from the repository root, inside the venv with the llm extra installed):

    export GAO_ANTHROPIC_API_KEY=sk-ant-...
    python demo/advisor_demo.py                    # 6 periods each, ~50 model calls total
    python demo/advisor_demo.py --periods 4        # fewer
    GAO_ANTHROPIC_MODEL=claude-haiku-5-5 python demo/advisor_demo.py
    GAO_ANTHROPIC_WORKSPACE_ID=wrkspc_... python demo/advisor_demo.py
        (the workspace id is only needed for a key that is not scoped to a workspace)

The key is read once into a SecretStr-returning callable and never written anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from gao_idl import AgentAdapter, RunnerPolicy, ScriptedAgent, Session, run_sync
from gao_idl.core.events import dump
from gao_idl.core.ids import AgentId
from gao_idl.environments.congestion import default_city, tutorial_commuters
from gao_idl.environments.congestion.llm import (
    CommuterCodec,
    CongestionReportRenderer,
    RegulatorCodec,
)
from gao_idl.environments.congestion.scripted import greedy_regulator, myopic_commuter
from gao_idl.llm import AnthropicBackend, LLMAgent

ENV = "gao_idl.environments.congestion:build_congestion"
REGULATOR = AgentId("regulator")
CITY = default_city()
OUT = Path(__file__).with_name("advisor_demo_results.json")


def backend() -> AnthropicBackend:
    key = os.environ.get("GAO_ANTHROPIC_API_KEY")
    if not key:
        sys.exit("set GAO_ANTHROPIC_API_KEY")
    model = os.environ.get("GAO_ANTHROPIC_MODEL", "claude-haiku-5-5")
    headers: dict[str, str] = {}
    workspace = os.environ.get("GAO_ANTHROPIC_WORKSPACE_ID")
    if workspace:  # only needed for an API key that is not scoped to a workspace
        headers["anthropic-workspace-id"] = workspace
    return AnthropicBackend(model, lambda: SecretStr(key), extra_headers=headers or None)


def params(regime: str, periods: int) -> dict[str, Any]:
    return {
        "periods": periods,
        "commuters": [c.model_dump() for c in tutorial_commuters()],
        "report_regime": regime,
    }


def llm_agent(agent_id: str, codec: Any) -> LLMAgent:
    return LLMAgent(
        AgentId(agent_id),
        backend(),
        codec,
        report_renderer=CongestionReportRenderer(),
        record_text=True,
        max_tokens=200,
    )


def extract(session: Session) -> dict[str, Any]:
    """Tolls, exposures and the regulator's information stream, period by period."""
    trace = dump(session.trace)
    exposures = [
        e["outcome"]
        for e in trace
        if e["kind"] == "layer_completed" and e["path"][-1] == "exposure"
    ]
    per_period = []
    for out in exposures:
        dac = sum(out["exposure"][z.name] for z in CITY.zones[1:] if z.is_dac)
        non = sum(out["exposure"][z.name] for z in CITY.zones[1:] if not z.is_dac)
        per_period.append(
            {
                "toll": out.get("toll"),
                "exposure_dac": round(dac, 1),
                "exposure_non_dac": round(non, 1),
                "concentration": {k: round(v, 3) for k, v in out["concentration"].items()},
                "metrics": {k: out[k] for k in ("hotspot", "pop_weighted") if k in out},
            }
        )
    regulator_stream = [
        {
            "kind": type(i).__name__,
            **(
                {"observation": i.observation}
                if hasattr(i, "observation")
                else {"report": i.payload}
            ),
        }
        for i in session.ledger.information(REGULATOR)
    ]
    tolls = [
        int(i.payload["toll"])
        for i in session.ledger.information(REGULATOR)
        if hasattr(i, "payload") and "toll" in i.payload
    ]
    return {
        "tolls": tolls,
        "per_period": per_period,
        "regulator_stream": regulator_stream,
        "ledger": {
            a: session.ledger.utility(AgentId(a))
            for a in ["regulator", *[c.agent for c in tutorial_commuters()]]
        },
        "finished": session.finished,
        "failed": session.failed,
    }


def calls(agents: dict[AgentId, AgentAdapter]) -> list[dict[str, Any]]:
    out = []
    for aid, a in agents.items():
        if isinstance(a, LLMAgent):
            for r in a.metadata():
                out.append(
                    {
                        "agent": str(aid),
                        "decision_id": r.decision_id,
                        "attempt": r.attempt,
                        "latency_s": round(r.latency_s, 2),
                        "usage": r.usage,
                        "model": r.model,
                        "repair_reason": r.repair_reason,
                        "failure": r.failure,
                        "prompt": [m.model_dump() for m in (r.prompt or [])],
                        "response": r.response,
                    }
                )
    return out


def run_config(
    name: str, regime: str, periods: int, llm_commuters: bool, seed: int = 1
) -> dict[str, Any]:
    print(f"== {name}: regime={regime} llm_commuters={llm_commuters} periods={periods}")
    session = Session.start(ENV, params(regime, periods), seed=seed)
    agents: dict[AgentId, AgentAdapter] = {}
    for c in tutorial_commuters():
        agents[AgentId(c.agent)] = (
            llm_agent(c.agent, CommuterCodec()) if llm_commuters else ScriptedAgent(myopic_commuter)
        )
    agents[REGULATOR] = llm_agent("regulator", RegulatorCodec())
    t0 = time.time()
    run_sync(session, agents, RunnerPolicy(deadline_seconds=120.0))
    wall = time.time() - t0
    result = extract(session)
    print(f"   tolls {result['tolls']}  wall {wall:.1f}s")
    print(f"   finished={result['finished']} failed={result['failed']}")

    # Replay: the input log holds every model reply as an ExternalResponse, so restoring the
    # snapshot rebuilds the identical trace without any backend at all.
    session.check_determinism()
    snapshot = session.snapshot()
    restored = Session.restore(snapshot)
    trace_only = {"agent_failed", "action_rejected"}  # not in the input log by design
    replay_ok = [e for e in dump(restored.trace) if e["kind"] not in trace_only] == [
        e for e in dump(session.trace) if e["kind"] not in trace_only
    ]
    print(f"   replay from snapshot identical (zero model calls): {replay_ok}")

    return {
        "name": name,
        "regime": regime,
        "llm_commuters": llm_commuters,
        "periods": periods,
        "seed": seed,
        "wall_seconds": round(wall, 1),
        **result,
        "calls": calls(agents),
        "replay_identical": replay_ok,
        "n_input_events": len(session.input_log),
        "snapshot": snapshot,
    }


def baseline(regime: str, periods: int) -> dict[str, Any]:
    session = Session.start(ENV, params(regime, periods), seed=1)
    agents: dict[AgentId, AgentAdapter] = {
        AgentId(c.agent): ScriptedAgent(myopic_commuter) for c in tutorial_commuters()
    }
    agents[REGULATOR] = ScriptedAgent(greedy_regulator)
    run_sync(session, agents)
    r = extract(session)
    return {"regime": regime, "tolls": r["tolls"], "per_period": r["per_period"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--periods", type=int, default=6)
    ap.add_argument("--skip-c", action="store_true", help="skip the all-model configuration")
    args = ap.parse_args()
    results: dict[str, Any] = {
        "model": os.environ.get("GAO_ANTHROPIC_MODEL", "claude-haiku-5-5"),
        "city": {z.name: {"population": z.population, "is_dac": z.is_dac} for z in CITY.zones},
        "commuters": [c.model_dump() for c in tutorial_commuters()],
        "baseline": [baseline("hotspot", args.periods), baseline("pop_weighted", args.periods)],
        "configs": [],
    }
    results["configs"].append(run_config("A", "hotspot", args.periods, llm_commuters=False))
    results["configs"].append(run_config("B", "pop_weighted", args.periods, llm_commuters=False))
    if not args.skip_c:
        results["configs"].append(run_config("C", "hotspot", args.periods, llm_commuters=True))
    total_calls = sum(len(c["calls"]) for c in results["configs"])
    tokens = sum(
        r["usage"].get("input_tokens", 0) + r["usage"].get("output_tokens", 0)
        for c in results["configs"]
        for r in c["calls"]
    )
    results["totals"] = {"model_calls": total_calls, "tokens": tokens}
    OUT.write_text(json.dumps(results, indent=1, default=str))
    print(f"\nwrote {OUT}  ({total_calls} model calls, {tokens} tokens)")
    print("send this file back for the slides; it contains prompts and replies but no credentials")


if __name__ == "__main__":
    main()
