"""Tutorial 2: compose. Loop the public-goods layer, then put it into a Par tournament."""

import importlib
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from gao_idl import (
    Config,
    Connector,
    DisjointSplitter,
    Loop,
    Par,
    Primitive,
    RandomAgent,
    Session,
    SimpleLayer,
    run_sync,
)
from gao_idl.composition.contracts import describe
from gao_idl.core.ids import AgentId, Port
from gao_idl.core.schemas import Exit
from gao_idl.environments.pd import build_repeated_pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
pg = importlib.import_module("01_define_a_layer")  # tutorial 1; its name starts with a digit
AGAIN, DONE = pg.AGAIN, pg.DONE


def build_repeated_pg(
    agents: list[str],
    rounds: int = 5,
    endowment: float = 10.0,
    multiplier: float = 1.6,
    layer: SimpleLayer | None = None,
) -> tuple[Loop, Config]:
    """The public-goods layer looped ``rounds`` times; the connector carries the endowments.

    ``layer`` replaces the loop body (tutorial 6 uses it to swap in a rewritten layer).
    """
    roles = pg.pg_roles(agents)

    def next_round(rec: dict[str, Any], ledger: Any, store: Any) -> Config:
        last = pg.PGOutcome.model_validate(rec)
        state = pg.PGState(
            endowments=last.endowments,  # carried over unchanged: earnings are not reinvested
            multiplier=last.multiplier,
            round=last.round + 1,
            rounds=last.rounds,
        )
        return Config(state=state.model_dump(mode="json"), roles=roles)

    loop = Loop(
        Primitive(layer or pg.public_goods_layer(len(agents))),
        {AGAIN: Connector(next_round, pg.PGState)},  # the connector's schema must match the entry
        {DONE: DONE},
        max_iter=rounds,
        on_limit="fail",
        name="pg_rounds",
    )
    state = pg.PGState(
        endowments={r: endowment for r in roles}, multiplier=multiplier, rounds=rounds
    )
    return loop, Config(state=state.model_dump(mode="json"), roles=roles)


class Tournament(BaseModel):
    """Entry state of the tournament: one endowment per agent and the round counter."""

    endowments: dict[str, float]
    multiplier: float
    round: int = 0
    rounds: int = 1


class Joined(BaseModel):
    """What the joiner emits: ``{group: {"port": ..., "record": PGOutcome}}``."""

    group_0: dict[str, Any]
    group_1: dict[str, Any]


class RoundJoiner:
    """A custom Joiner: leaves through ``done`` after the last round, ``again`` before."""

    @property
    def exits(self) -> dict[Port, type[BaseModel]]:
        return {AGAIN: Joined, DONE: Joined}

    def join(self, exits: dict[str, Exit]) -> Exit:
        record = {k: {"port": str(e.port), "record": e.record} for k, e in exits.items()}
        last = all(e.port == DONE for e in exits.values())
        return Exit(port=DONE if last else AGAIN, record=record)


def build_pg_tournament(
    groups: list[list[str]], rounds: int = 3, endowment: float = 10.0, multiplier: float = 1.6
) -> tuple[Loop, Config]:
    """Two groups play the public-goods layer side by side, ``rounds`` times (``Loop(Par)``)."""
    assert len(groups) == 2

    def split(cfg: Config) -> dict[str, Config]:
        t = Tournament.model_validate(cfg.state)
        out = {}
        for k, members in enumerate(groups):
            roles = pg.pg_roles(members)
            state = pg.PGState(
                endowments={r: t.endowments[str(a)] for r, a in roles.items()},
                multiplier=t.multiplier,
                round=t.round,
                rounds=t.rounds,
            )
            out[f"group_{k}"] = Config(state=state.model_dump(mode="json"), roles=roles)
        return out

    par = Par(
        {f"group_{k}": Primitive(pg.public_goods_layer(len(m))) for k, m in enumerate(groups)},
        DisjointSplitter(split, Tournament),
        RoundJoiner(),
        name="groups",
    )

    def next_round(rec: dict[str, Any], ledger: Any, store: Any) -> Config:
        joined = Joined.model_validate(rec).model_dump()
        last = joined["group_0"]["record"]
        ends: dict[str, float] = {}
        for k, members in enumerate(groups):
            for role, agent in pg.pg_roles(members).items():
                ends[str(agent)] = joined[f"group_{k}"]["record"]["endowments"][role]
        state = Tournament(
            endowments=ends,
            multiplier=last["multiplier"],
            round=last["round"] + 1,
            rounds=last["rounds"],
        )
        return Config(state=state.model_dump(mode="json"), roles={})

    loop = Loop(
        par,
        {AGAIN: Connector(next_round, Tournament)},
        {DONE: DONE},
        max_iter=rounds,
        on_limit="fail",
        name="tournament",
    )
    everyone = {a: endowment for m in groups for a in m}
    first = Tournament(endowments=everyone, multiplier=multiplier, rounds=rounds)
    return loop, Config(state=first.model_dump(mode="json"), roles={})


def shape(node: dict[str, Any]) -> Any:
    """The nesting of ``describe`` output reduced to kinds: ``loop(seq(par(primitive...)))``."""
    kids: list[Any] = []
    if node["kind"] == "seq":
        kids = [shape(node["first"])] + [shape(r["then"]) for r in node["routes"].values()]
    elif node["kind"] == "par":
        kids = [shape(b) for b in node["branches"].values()]
    elif node["kind"] == "loop":
        kids = [shape(node["body"])]
    return node["kind"] + ("(" + ", ".join(kids) + ")" if kids else "")


if __name__ == "__main__":
    players = ["ann", "bo", "cy"]
    s = Session.start("02_compose:build_repeated_pg", {"agents": players}, seed=3)
    run_sync(s, {AgentId(a): RandomAgent(seed=i) for i, a in enumerate(players)})
    print("5 rounds:", {a: round(s.ledger.utility(AgentId(a)), 1) for a in players})

    teams = [["ann", "bo"], ["cy", "di"]]
    t = Session.start("02_compose:build_pg_tournament", {"groups": teams}, seed=3)
    run_sync(t, {AgentId(a): RandomAgent(seed=i) for i, a in enumerate(sum(teams, []))})
    print("tournament:", {a: round(t.ledger.utility(AgentId(a)), 1) for a in sum(teams, [])})

    pd_proc, _ = build_repeated_pd([AgentId("ann"), AgentId("bo"), AgentId("cy"), AgentId("di")], 3)
    print("PD shape:        ", shape(describe(pd_proc)))
    print("tournament shape:", shape(describe(build_pg_tournament(teams)[0])))
