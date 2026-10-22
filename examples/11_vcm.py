"""Tutorial 11: a carbon market with verifiers, then one experiment on verifier reputation."""

import os
from collections.abc import Callable
from typing import Any

from gao_idl import AgentAdapter, ScriptedAgent, Session, run_sync
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.environments.vcm import (
    Population,
    clear_call_auction,
    lemons_population,
)
from gao_idl.environments.vcm.scripted import (
    honest_verifier,
    naive_buyer,
    skeptical_buyer,
    truthful_seller,
)
from gao_idl.learning import (
    FieldObsCodec,
    QConfig,
    RewardExtractor,
    SchemaActCodec,
    TableAgent,
    TrainSpec,
    default_opponent_factory,
    evaluate,
    train,
)

FAST = bool(os.environ.get("GAO_EXAMPLE_FAST"))
ENV = "gao_idl.environments.vcm:build_vcm"
POP = lemons_population()
Policy = Callable[[DecisionRequest, list[ParticipantReport]], dict[str, Any]]


# --8<-- [start:helpers]
def params(pop: Population = POP, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sellers": [s.model_dump() for s in pop.sellers],
        "buyers": [b.model_dump() for b in pop.buyers],
        "verifiers": [v.model_dump() for v in pop.verifiers],
    }
    return base | over


def players(
    pop: Population = POP, verifier: Policy = honest_verifier, buyer: Policy = naive_buyer
) -> dict[AgentId, AgentAdapter]:
    who: dict[AgentId, AgentAdapter] = {}
    who |= {AgentId(v.agent): ScriptedAgent(verifier) for v in pop.verifiers}
    who |= {AgentId(s.agent): ScriptedAgent(truthful_seller) for s in pop.sellers}
    who |= {AgentId(b.agent): ScriptedAgent(buyer) for b in pop.buyers}
    return who


def play(p: dict[str, Any], who: dict[AgentId, AgentAdapter], seed: int = 3) -> Session:
    session = Session.start(ENV, p, seed)
    run_sync(session, who)
    return session


def reports(session: Session, agent: str, layer: str) -> list[dict[str, Any]]:
    return [
        i.payload
        for i in session.ledger.information(AgentId(agent))
        if isinstance(i, ParticipantReport) and i.layer == layer
    ]


# --8<-- [end:helpers]


# --8<-- [start:step1]
def step1() -> None:
    """The call auction alone: unit bids 10x5 and 8x5 against unit asks 6x4 and 7x4."""
    out = clear_call_auction([("b1", 10, 5), ("b2", 8, 5)], [("s1", 6, 4), ("s2", 7, 4)])
    print("step 1 price:", out.price, "volume:", out.volume, "fills:", out.bid_fills)
    # eight credits trade; the eighth pair is (bid 8, ask 7), so the price is their midpoint 7.5;
    # b1 (bid 10) fills all 5 and b2, the marginal bidder, gets the remaining 3
    assert (out.price, out.volume) == (7.5, 8) and out.bid_fills == {"b1": 5.0, "b2": 3.0}


# --8<-- [end:step1]


# --8<-- [start:step2]
def constant_verifier(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Report 0.5 for every project, whatever was measured."""
    return {p: 0.5 for p in request.observation["measurements"]}


def flipped() -> Population:
    """The same market with every true quality replaced by 1 - theta."""
    return Population.model_validate(
        POP.model_dump()
        | {"sellers": [s.model_dump() | {"theta": 1.0 - s.theta} for s in POP.sellers]}
    )


def information(session: Session, agent: str) -> list[Any]:
    return [i.model_dump(mode="json") for i in session.ledger.information(AgentId(agent))]


def step2() -> None:
    """No audits: the true qualities may change completely and no buyer or seller can tell."""
    runs = [
        play(params(pop, rounds=1, p_audit=0.0), players(pop, verifier=constant_verifier))
        for pop in (POP, flipped())
    ]
    same = all(information(runs[0], a) == information(runs[1], a) for a in ("b0", "b1", "b2", "s0"))
    print("step 2 buyers and sellers cannot tell the qualities apart:", same)
    assert same
    # the verifiers did measure something: their observations differ (that is their job)
    v_seen = [
        [i.observation for i in r.ledger.information(AgentId("v0")) if hasattr(i, "observation")]
        for r in runs
    ]
    assert v_seen[0] != v_seen[1]


# --8<-- [end:step2]


# --8<-- [start:step3]
def step3() -> None:
    """Add audits: with p_audit=1 every traded project is revealed, and the biased verifier pays."""
    session = play(params(rounds=1, p_audit=1.0), players())
    for v in ("v0", "v1"):
        got = reports(session, v, "audit")[0]
        print(
            f"step 3 {v}: fees {got.get('fees', 0.0):.1f} penalties {got.get('penalties', 0.0):.1f}"
        )
    revealed = reports(session, "b0", "audit")[0]["revealed"]
    print("step 3 revealed to every buyer:", {p: r["true_quality"] for p, r in revealed.items()})
    assert reports(session, "v1", "audit")[0].get("penalties", 0.0) > 0.0
    assert reports(session, "v0", "audit")[0].get("penalties", 0.0) == 0.0


# --8<-- [end:step3]


# --8<-- [start:step4]
def step4() -> None:
    """Loop it: with reputation_visible the buyers' next-round observation carries the record."""
    session = play(params(rounds=3, p_audit=1.0, reputation_visible=True), players())
    seen = [
        i.observation
        for i in session.ledger.information(AgentId("b0"))
        if hasattr(i, "observation") and "reputation" in i.observation
    ]
    print("step 4 b0 sees, in round 3:", seen[-1]["reputation"])
    assert seen and seen[0].get("reputation") in (None, {}) or "v1" in seen[-1]["reputation"]
    assert seen[-1]["reputation"]["v1"]["mean_error"] > seen[-1]["reputation"]["v0"]["mean_error"]


# --8<-- [end:step4]


# --8<-- [start:experiment]
def trajectory(buyer: Policy, visible: bool, rounds: int) -> tuple[list[float | None], list[float]]:
    """Clearing price and the biased verifier's share of traded volume, per round."""
    session = play(
        params(rounds=rounds, p_audit=0.5, reputation_visible=visible), players(buyer=buyer)
    )
    prices = [r.get("price") for r in reports(session, "b0", "clearing")]
    own = {
        s: [r.get("own_fill", 0.0) for r in reports(session, s, "clearing")]
        for s in ["s0", "s1", "s2", "s3"]
    }
    shares = []
    for t in range(rounds):
        total = sum(own[s][t] for s in own)
        shares.append((own["s2"][t] + own["s3"][t]) / total if total else 0.0)
    return prices, shares


def step5() -> None:
    rounds = 6
    for name, buyer in (("naive", naive_buyer), ("skeptical", skeptical_buyer)):
        for visible in (False, True):
            prices, shares = trajectory(buyer, visible, rounds)
            tag = "visible" if visible else "hidden "
            print(
                f"step 5 {name:9s} {tag} price",
                [None if p is None else round(p, 2) for p in prices],
            )
            print(f"step 5 {name:9s} {tag} v1 volume share", [round(x, 2) for x in shares])
    # the lemons dynamic: once the record is shown, skeptical buyers discount the whole pool, so
    # the honest high-quality projects (cost 8 and 7) stop selling and v1's share rises
    _, hidden = trajectory(skeptical_buyer, False, rounds)
    _, shown = trajectory(skeptical_buyer, True, rounds)
    assert shown[-1] > hidden[-1]


# --8<-- [end:experiment]


# --8<-- [start:learning]
def learner(visible: bool) -> TableAgent:
    field = "reputation.v1.mean_error" if visible else "round"
    return TableAgent(
        AgentId("b0"),
        FieldObsCodec([field]),
        SchemaActCodec(grid={"price": [3.0, 4.0, 5.0, 6.0, 7.0, 8.0], "qty": [10]}),
        RewardExtractor(fields=["own_net"]),
        QConfig(alpha=0.3, gamma=0.0, epsilon=0.3),
        0,
    )


def step6() -> None:
    factory = default_opponent_factory(
        scripted={"verifier": honest_verifier, "seller": truthful_seller, "buyer": naive_buyer}
    )
    naive = play(params(rounds=4, p_audit=1.0), players())
    print("step 6 naive buyer b0 utility: {:.1f}".format(naive.ledger.utility(AgentId("b0"))))
    for visible in (False, True):
        spec = TrainSpec(
            env_ref=ENV,
            params=params(rounds=4, p_audit=1.0, reputation_visible=visible),
            learner=AgentId("b0"),
            episodes=20 if FAST else 120,
            seed=1,
            opponents={
                **{AgentId(v.agent): "scripted:verifier" for v in POP.verifiers},
                **{AgentId(s.agent): "scripted:seller" for s in POP.sellers},
                AgentId("b1"): "scripted:buyer",
                AgentId("b2"): "scripted:buyer",
            },
        )
        result = train(spec, learner(visible), factory)
        played = evaluate(spec, result.checkpoint, factory, episodes=4, seed=9)
        print(
            f"step 6 learner ({'visible' if visible else 'hidden '}) greedy mean return:",
            played.mean,
        )


# --8<-- [end:learning]


def main() -> None:
    step1()
    step2()
    step3()
    step4()
    step5()
    step6()
    print("done: carbon market")


if __name__ == "__main__":
    main()
