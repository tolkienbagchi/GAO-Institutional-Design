"""``gao_idl.selftest()``: a few seconds of end-to-end checks for a fresh install.

Stability: internal module; the public entry point is ``gao_idl.selftest``.
"""

import asyncio
from typing import Any


def _tit_for_tat(request: Any, reports: list[Any]) -> dict[str, Any]:
    return {"move": reports[-1].payload["other_move"] if reports else "C"}


def _always_defect(request: Any, reports: list[Any]) -> dict[str, Any]:
    return {"move": "D"}


def _check(condition: bool, what: str) -> None:
    if not condition:
        raise RuntimeError(f"gao_idl selftest failed: {what}")


def selftest() -> None:
    """Run the sealed-bid auction (two random agents) and the repeated PD (two scripted agents).

    Prints each environment's exit port and per-agent ledger totals, and checks that the runs
    finish, replay to the same trace, and survive a snapshot/restore. Raises ``RuntimeError`` on
    any failure. Needs only the core dependencies (no ``llm`` extra). Call it from plain
    scripts: it uses ``asyncio.run``, so not from inside a running event loop.
    """
    from gao_idl import __version__
    from gao_idl.agents import RandomAgent, ScriptedAgent
    from gao_idl.agents.base import AgentAdapter
    from gao_idl.core.ids import AgentId
    from gao_idl.runtime.runner import run
    from gao_idl.runtime.session import Session

    def play(
        env_ref: str, params: dict[str, Any], seed: int, adapters: dict[str, AgentAdapter]
    ) -> Session:
        session = Session.start(env_ref, params, seed)
        asyncio.run(run(session, {AgentId(a): x for a, x in adapters.items()}))
        _check(session.finished and not session.failed, f"{env_ref} did not finish normally")
        session.check_determinism()
        restored = Session.restore(session.snapshot())
        _check(restored.finished and restored.result == session.result, f"{env_ref} restore")
        return session

    def totals(session: Session, agents: list[str]) -> dict[str, float]:
        return {a: session.ledger.utility(AgentId(a)) for a in agents}

    def port(session: Session) -> str:
        if session.result is None:
            raise RuntimeError("gao_idl selftest failed: a session finished without a result")
        return session.result.port

    print(f"gao_idl {__version__} selftest")

    agents = ["alice", "bob"]
    auction = play(
        "gao_idl.environments.auction:build_auction",
        {"values": {"alice": 10.0, "bob": 7.0}},
        seed=0,
        adapters={"alice": RandomAgent(1), "bob": RandomAgent(2)},
    )
    print(f"auction  exit={port(auction):<8} utility={totals(auction, agents)}")

    pd = play(
        "gao_idl.environments.pd:build_repeated_pd",
        {"agents": agents, "rounds": 3},
        seed=0,
        adapters={"alice": ScriptedAgent(_tit_for_tat), "bob": ScriptedAgent(_always_defect)},
    )
    pd_totals = totals(pd, agents)
    print(f"pd       exit={port(pd):<8} utility={pd_totals}")
    # tit-for-tat is exploited once (0 vs 5), then both defect twice (1 vs 1)
    _check(pd_totals == {"alice": 2.0, "bob": 7.0}, f"unexpected PD totals {pd_totals}")
    print("ok")
