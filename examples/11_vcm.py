"""Tutorial 11: a carbon market with verifiers, then one experiment on verifier reputation."""

import json
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
from gao_idl.environments.vcm.learning import MarkdownActCodec, RecordObsCodec
from gao_idl.environments.vcm.scripted import (
    honest_verifier,
    naive_buyer,
    naive_project_buyer,
    skeptical_buyer,
    skeptical_project_buyer,
    truthful_seller,
)
from gao_idl.learning import (
    QConfig,
    RewardExtractor,
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
    pop: Population = POP, verifier: Policy = honest_verifier, buyer: Policy = naive_project_buyer
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
def trajectory(
    buyer: Policy, visible: bool, form: str, rounds: int = 6
) -> tuple[dict[str, list[float | None]], list[float]]:
    """Public clearing prices (the pool's, or two projects') and the biased verifier's share of
    the traded volume, per round."""
    session = play(
        params(rounds=rounds, p_audit=0.5, reputation_visible=visible, market_form=form),
        players(buyer=buyer),
    )
    rows = reports(session, "b0", "clearing")
    if form == "pooled":
        prices = {"pool": [r.get("price") for r in rows]}
    else:
        prices = {p: [r["projects"][p].get("price") for r in rows] for p in ("s0", "s2")}
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
    """Per-project orders, then the pooled market, each with and without the record shown."""
    for form, naive, skeptical in (
        ("per_project", naive_project_buyer, skeptical_project_buyer),
        ("pooled", naive_buyer, skeptical_buyer),
    ):
        for name, buyer in (("naive", naive), ("skeptical", skeptical)):
            for visible in (False, True):
                prices, shares = trajectory(buyer, visible, form)
                tag = f"step 5 {form:11s} {name:9s} {'visible' if visible else 'hidden '}"
                for key, series in prices.items():
                    print(tag, f"price {key}", [None if x is None else round(x, 2) for x in series])
                print(tag, "v1 volume share", [round(x, 2) for x in shares])
    # per project, the record changes what the skeptic buys: v1's volume share falls
    # (4 credits in 10 to 3), the honest projects keep their prices; pooled, the same buyers
    # collapse the whole market onto v1
    _, hidden = trajectory(skeptical_project_buyer, False, "per_project")
    _, shown = trajectory(skeptical_project_buyer, True, "per_project")
    assert shown[-1] < hidden[-1]
    _, hidden = trajectory(skeptical_buyer, False, "pooled")
    _, shown = trajectory(skeptical_buyer, True, "pooled")
    assert shown[-1] > hidden[-1]


# --8<-- [end:experiment]


# --8<-- [start:learning]
def v1_inflates(episode_seed: int) -> bool:
    """One episode in ten, v1 inflates its reports by 0.3; otherwise it reports what it measured."""
    return episode_seed % 10 == 3


def opponents(agent: AgentId, description: str, seed: int) -> AgentAdapter:
    """The rivals: honest v0, truthful sellers, naive per-project buyers, and v1 of unknown type."""
    if description == "v1":
        inflating = v1_inflates(seed)
        return ScriptedAgent(inflating_verifier if inflating else honest_verifier)
    return default_opponent_factory(
        scripted={
            "verifier": honest_verifier,
            "seller": truthful_seller,
            "buyer": naive_project_buyer,
        }
    )(agent, description, seed)


def inflating_verifier(
    request: DecisionRequest, reports: list[ParticipantReport]
) -> dict[str, Any]:
    return {p: min(1.0, x + 0.3) for p, x in request.observation["measurements"].items()}


def step6() -> None:
    """A tabular buyer chooses how far to mark down v1's projects; the record is its only cue."""
    verifiers = [v.model_dump() | {"bias": 0.0} for v in POP.verifiers]  # v1's type is its policy
    for visible in (False, True):
        spec = TrainSpec(
            env_ref=ENV,
            params=params(
                rounds=4,
                p_audit=1.0,
                market_form="per_project",
                reputation_visible=visible,
                verifiers=verifiers,
            ),
            learner=AgentId("b0"),
            episodes=8 if FAST else 120,
            seed=1,
            opponents={
                AgentId("v0"): "scripted:verifier",
                AgentId("v1"): "v1",
                **{AgentId(s.agent): "scripted:seller" for s in POP.sellers},
                AgentId("b1"): "scripted:buyer",
                AgentId("b2"): "scripted:buyer",
            },
        )
        agent = TableAgent(
            AgentId("b0"),
            RecordObsCodec("v1"),
            MarkdownActCodec("v1"),
            RewardExtractor(fields=["own_net"]),
            QConfig(alpha=0.3, gamma=0.0, epsilon=0.3),
            0,
        )
        result = train(spec, agent, opponents)
        played = evaluate(
            spec,
            result.checkpoint,
            opponents,
            episodes=4 if FAST else 20,
            seed=9,
            obs_codec=RecordObsCodec("v1"),
            act_codec=MarkdownActCodec("v1"),
        )
        best: dict[Any, tuple[float, float]] = {}
        for state, action, value in result.checkpoint["q"]:
            key = json.dumps(state)
            if key not in best or value > best[key][1]:
                best[key] = (action, value)
        tag = "step 6 learner (" + ("visible" if visible else "hidden ") + ")"
        print(tag, "mean return:", None if played.mean is None else round(played.mean, 1))
        print(tag, "greedy markdown of v1 by state:", {k: m for k, (m, _) in sorted(best.items())})


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
