"""Voluntary carbon market: clearing, information, audits, reputation, replay, learners, LLM."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from gao_idl import AgentAdapter, ScriptedAgent, Session, run_sync
from gao_idl.core import events as ev
from gao_idl.core.errors import ContractError, Infeasible, SchemaError
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.environments.vcm import (
    Population,
    build_vcm,
    clear_call_auction,
    draw_population,
    lemons_population,
)
from gao_idl.environments.vcm.learning import MarkdownActCodec, RecordObsCodec
from gao_idl.environments.vcm.llm import (
    BuyerCodec,
    ProjectBuyerCodec,
    VCMReportRenderer,
    VerifierCodec,
)
from gao_idl.environments.vcm.scripted import (
    honest_verifier,
    naive_buyer,
    naive_project_buyer,
    skeptical_project_buyer,
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
from gao_idl.llm.agent import LLMAgent
from gao_idl.llm.mock import MockBackend
from gao_idl.testing import assert_replay_equivalent

ENV = "gao_idl.environments.vcm:build_vcm"
Policy = Callable[[DecisionRequest, list[ParticipantReport]], dict[str, Any]]
POP = lemons_population()


def aid(name: str) -> AgentId:
    return AgentId(name)


def params(pop: Population = POP, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "rounds": 3,
        "p_audit": 0.5,
        "market_form": "pooled",
        "sellers": [s.model_dump() for s in pop.sellers],
        "buyers": [b.model_dump() for b in pop.buyers],
        "verifiers": [v.model_dump() for v in pop.verifiers],
    }
    base.update(over)
    return base


def constant_verifier(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    """Report 0.5 for everything: independent of what the verifier measured."""
    return {p: 0.5 for p in request.observation["measurements"]}


def agents(
    pop: Population = POP,
    verifier: Policy = honest_verifier,
    seller: Policy = truthful_seller,
    buyer: Policy = naive_buyer,
) -> dict[AgentId, AgentAdapter]:
    out: dict[AgentId, AgentAdapter] = {}
    out |= {aid(v.agent): ScriptedAgent(verifier) for v in pop.verifiers}
    out |= {aid(s.agent): ScriptedAgent(seller) for s in pop.sellers}
    out |= {aid(b.agent): ScriptedAgent(buyer) for b in pop.buyers}
    return out


def play(
    p: dict[str, Any], who: dict[AgentId, AgentAdapter] | None = None, seed: int = 3
) -> Session:
    session = Session.start(ENV, p, seed)
    run_sync(session, who or agents())
    assert session.finished and not session.failed
    return session


def reports(session: Session, agent: str, layer: str) -> list[dict[str, Any]]:
    return [
        i.payload
        for i in session.ledger.information(aid(agent))
        if isinstance(i, ParticipantReport) and i.layer == layer
    ]


def requests(session: Session, agent: str, layer: str) -> list[dict[str, Any]]:
    return [
        i.observation
        for i in session.ledger.information(aid(agent))
        if isinstance(i, DecisionRequest) and i.layer == layer
    ]


# ------------------------------------------------------------------ clearing


def test_uniform_price_clearing_hand_examples() -> None:
    # 1. demand is rationed at the margin. Unit bids 10x5, 8x5; unit asks 6x4, 7x4. The 8th pair is
    #    (8, 7) and the 9th has no ask, so V = 8 and the price is (8 + 7) / 2 = 7.5. b1 (10) fills
    #    completely, b2 (8, the marginal bid) gets the remaining 3 of 5; both asks fill.
    one = clear_call_auction([("b1", 10, 5), ("b2", 8, 5)], [("s1", 6, 4), ("s2", 7, 4)])
    assert (one.price, one.volume) == (7.5, 8)
    assert one.bid_fills == {"b1": 5.0, "b2": 3.0} and one.ask_fills == {"s1": 4.0, "s2": 4.0}
    # 2. pro rata at the margin. Two bids of 9 for 6, one ask of 5 for 8: V = 8, price
    #    (9 + 5) / 2 = 7; the two equal bids share 8 credits by quantity: 4 each.
    two = clear_call_auction([("b1", 9, 6), ("b2", 9, 6)], [("s1", 5, 8)])
    assert (two.price, two.volume) == (7.0, 8)
    assert two.bid_fills == {"b1": 4.0, "b2": 4.0} and two.ask_fills == {"s1": 8.0}
    # 3. unequal margin shares and a supply-side margin. Bids 9x4, 7x4; asks 5x3, 5x3, 8x10.
    #    Unit asks 5 (x6) then 8; the 7th pair is (7, 8): no trade, so V = 6 and the price is
    #    (7 + 5) / 2 = 6. b1 fills 4, b2 (marginal at 7) the remaining 2; the two asks at 5 fill 3
    #    each; the ask of 8 does not trade.
    three = clear_call_auction(
        [("b1", 9, 4), ("b2", 7, 4)], [("s1", 5, 3), ("s2", 5, 3), ("s3", 8, 10)]
    )
    assert (three.price, three.volume) == (6.0, 6)
    assert three.bid_fills == {"b1": 4.0, "b2": 2.0}
    assert three.ask_fills == {"s1": 3.0, "s2": 3.0, "s3": 0.0}
    # indifference: a bid equal to the ask trades at that price; a bid below it does not
    assert clear_call_auction([("b", 6, 3)], [("s", 6, 3)]).price == 6.0
    assert clear_call_auction([("b", 5.99, 3)], [("s", 6, 3)]).price is None
    # supply-side rationing: the marginal ask is shared pro rata by quantity
    shared = clear_call_auction([("b", 9, 5)], [("s1", 4, 4), ("s2", 4, 6)])
    assert shared.ask_fills == {"s1": 2.0, "s2": 3.0} and shared.price == 6.5


def test_clearing_in_a_session_matches_the_hand_example() -> None:
    pop = Population.model_validate(
        {
            "sellers": [
                {"agent": "s1", "quantity": 4, "theta": 0.8, "cost": 6.0},
                {"agent": "s2", "quantity": 4, "theta": 0.8, "cost": 7.0},
            ],
            "buyers": [
                {"agent": "b1", "demand": 5, "value": 10.0},
                {"agent": "b2", "demand": 5, "value": 8.0},
            ],
            "verifiers": [{"agent": "v", "projects": ["s1", "s2"]}],
        }
    )

    def bid_value(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
        return {"price": request.observation["value"], "qty": request.observation["demand"]}

    session = play(params(pop, rounds=1, p_audit=0.0), agents(pop, buyer=bid_value))
    assert reports(session, "b1", "clearing") == [
        {"round": 0, "price": 7.5, "volume": 8, "own_fill": 5.0, "own_payment": 37.5}
    ]
    assert reports(session, "b2", "clearing")[0]["own_fill"] == 3.0
    assert reports(session, "s2", "clearing")[0] == {
        "round": 0,
        "price": 7.5,
        "volume": 8,
        "own_fill": 4.0,
        "own_revenue": 30.0,
    }
    # money: sellers earn fill * (price - cost), buyers pay fill * price (and, unaudited, are
    # credited value * reported quality: 0.8 for both projects)
    assert session.ledger.utility(aid("s1")) == pytest.approx(4 * (7.5 - 6.0))
    assert session.ledger.utility(aid("b1")) == pytest.approx(-5 * 7.5 + 5 * 10.0 * 0.8)
    assert session.ledger.utility(aid("b2")) == pytest.approx(-3 * 7.5 + 3 * 8.0 * 0.8)


# ------------------------------------------------------------------ information


def other_theta() -> Population:
    """The tutorial market with every true quality changed (same costs, values, assignment)."""
    return Population.model_validate(
        POP.model_dump()
        | {"sellers": [s.model_dump() | {"theta": 1.0 - s.theta} for s in POP.sellers]}
    )


def test_buyers_never_see_true_quality_except_via_audit() -> None:
    quiet, quiet2 = (
        play(params(pop, p_audit=0.0), agents(pop, verifier=constant_verifier))
        for pop in (POP, other_theta())
    )
    for who in ("b0", "b1", "b2", "s0", "s1", "s2", "s3"):
        a = [i.model_dump(mode="json") for i in quiet.ledger.information(aid(who))]
        b = [i.model_dump(mode="json") for i in quiet2.ledger.information(aid(who))]
        assert a == b, who
    # with audits the true quality does reach buyers, through the public revelation, and only that
    loud, loud2 = (
        play(params(pop, p_audit=1.0), agents(pop, verifier=constant_verifier))
        for pop in (POP, other_theta())
    )
    revealed = reports(loud, "b0", "audit")[0]["revealed"]
    assert revealed and all(r["true_quality"] == POP.seller(p).theta for p, r in revealed.items())
    assert reports(loud, "b0", "audit") != reports(loud2, "b0", "audit")
    # the verifiers saw something about theta (their noisy measurement): that is their job
    assert requests(quiet, "v0", "verification") != requests(quiet2, "v0", "verification")


def test_sellers_do_not_see_each_others_asks() -> None:
    def ask(price: float) -> Policy:
        def policy(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
            if request.agent_id == "s1":
                return {"price": price, "qty": 10}
            return truthful_seller(request, reports)

        return policy

    low, high = (play(params(rounds=1), agents(seller=ask(p))) for p in (5.11, 9.37))
    stream = json.dumps([i.model_dump(mode="json") for i in high.ledger.information(aid("s0"))])
    assert "9.37" not in stream and "5.11" not in stream
    # what s0 is told: the public price and volume and its own fill, nothing else
    for item in reports(high, "s0", "clearing"):
        assert set(item) <= {"round", "price", "volume", "own_fill", "own_revenue"}
    # and its decision request was identical in both runs: the other seller's ask cannot reach it
    assert requests(low, "s0", "market") == requests(high, "s0", "market")


def test_verifier_penalty_only_when_audited_and_wrong() -> None:
    def fees_and_penalties(session: Session, v: str) -> tuple[float, float]:
        got = reports(session, v, "audit")
        return sum(r["fees"] for r in got), sum(r["penalties"] for r in got)

    # honest v0 (|error| ~ 0.03 << tol) is never penalized; biased v1 (+0.3 > tol) is, per audited
    # traded project; both are paid a fee for each report
    audited = play(params(rounds=3, p_audit=1.0))
    assert fees_and_penalties(audited, "v0") == (6.0, 0.0)
    revealed = [r["revealed"] for r in reports(audited, "v1", "audit")]
    wrong = sum(1 for round_ in revealed for rev in round_.values() if rev["verifier"] == "v1")
    assert wrong >= 1
    assert fees_and_penalties(audited, "v1") == (6.0, 5.0 * wrong)
    assert audited.ledger.utility(aid("v1")) == pytest.approx(6.0 - 5.0 * wrong)
    # nothing is audited, nothing is penalized
    unaudited = play(params(rounds=3, p_audit=0.0))
    assert fees_and_penalties(unaudited, "v1") == (6.0, 0.0)
    assert unaudited.ledger.utility(aid("v1")) == pytest.approx(6.0)

    # a wrong report on a project that does not trade is not audited: nobody buys s3 here
    def no_s3(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
        order = truthful_seller(request, reports)
        return {**order, "qty": 0} if request.agent_id in ("s2", "s3") else order

    clean = play(params(rounds=3, p_audit=1.0), agents(seller=no_s3))
    assert fees_and_penalties(clean, "v1")[1] == 0.0


def test_reputation_appears_in_buyer_observation_only_when_visible() -> None:
    hidden = play(params(rounds=3, p_audit=1.0, reputation_visible=False))
    assert all("reputation" not in o for o in requests(hidden, "b0", "market"))
    shown = play(params(rounds=3, p_audit=1.0, reputation_visible=True))
    seen = [o["reputation"] for o in requests(shown, "b0", "market")]
    assert seen[0] == {
        "v0": {"audited": 0.0, "wrong": 0.0, "mean_error": 0.0},
        "v1": {"audited": 0.0, "wrong": 0.0, "mean_error": 0.0},
    }
    assert seen[2]["v1"]["audited"] > 0 and seen[2]["v1"]["wrong"] == seen[2]["v1"]["audited"]
    assert 0.2 < seen[2]["v1"]["mean_error"] < 0.4 and abs(seen[2]["v0"]["mean_error"]) < 0.1
    # the history of round r is what the audits of rounds < r revealed, so it grows with r
    assert [r["v1"]["audited"] for r in seen] == sorted(r["v1"]["audited"] for r in seen)
    # and the last round's public price and volume are shown (not before the first round)
    first, later = requests(shown, "b0", "market")[:2]
    assert "last_price" not in first and later["last_price"] is not None


def test_audit_randomness_is_path_keyed() -> None:
    def audited_projects(session: Session) -> list[list[str]]:
        return [sorted(r["revealed"]) for r in reports(session, "b0", "audit")]

    a, again = (play(params(rounds=5, p_audit=0.5), seed=7) for _ in range(2))
    assert audited_projects(a) == audited_projects(again)
    assert ev.dump(a.trace) == ev.dump(again.trace)
    low, high = (
        play(params(rounds=5, p_audit=0.5), seed=7),
        play(params(rounds=5, p_audit=0.9), seed=7),
    )
    # the same uniform draws are compared with a different threshold: the audited set can only grow
    for small, big in zip(audited_projects(low), audited_projects(high), strict=True):
        assert set(small) <= set(big)
    assert sum(map(len, audited_projects(high))) > sum(map(len, audited_projects(low)))
    # nothing outside the audit layer draws differently: the verifiers' measurements are equal
    for v in ("v0", "v1"):
        got = [[o["measurements"] for o in requests(s, v, "verification")] for s in (low, high)]
        assert got[0] == got[1]
    # another session seed draws differently
    other = play(params(rounds=5, p_audit=0.5), seed=8)
    assert audited_projects(other) != audited_projects(a) or requests(
        other, "v0", "verification"
    ) != requests(a, "v0", "verification")


# ------------------------------------------------------------------ settlement and reports


def test_settle_on_true_changes_the_ledger_not_what_buyers_are_told() -> None:
    reported = play(params(p_audit=0.0, settle_on="reported"))
    true = play(params(p_audit=0.0, settle_on="true"))
    for b in ("b0", "b1", "b2"):
        assert reports(reported, b, "audit") == reports(true, b, "audit")  # told the same
    # the biased verifier's projects are over-stated, so the true settlement is lower
    assert true.ledger.utility(aid("b0")) < reported.ledger.utility(aid("b0"))


def test_buyer_report_states_value_at_the_quality_it_knows() -> None:
    session = play(params(rounds=1, p_audit=1.0, settle_on="true"))
    got = reports(session, "b0", "audit")[0]
    clearing = reports(session, "b0", "clearing")[0]
    assert got["own_net"] == pytest.approx(got["own_value"] - clearing["own_payment"])
    # at p_audit = 1 every traded project is audited, so what b0 knows is the truth
    assert session.ledger.utility(aid("b0")) == pytest.approx(got["own_net"])


# ------------------------------------------------------------------ replay, restore, validation


def answer(req: DecisionRequest) -> dict[str, Any]:
    """The scripted policy of the request's role, called directly (no event loop)."""
    if req.layer == "verification":
        return honest_verifier(req, [])
    return (truthful_seller if req.role.startswith("seller") else naive_buyer)(req, [])


def finish(session: Session, skip: set[str] | None = None) -> Session:
    for _ in range(200):
        session.advance()
        if session.finished:
            return session
        for req in session.pending():
            if skip and req.agent_id in skip:
                continue
            session.submit(req.agent_id, req.decision_id, answer(req))
    raise AssertionError("did not finish")


def test_replay_and_partial_restore() -> None:
    p = params(rounds=3, reputation_visible=True)
    reference = play(p, seed=5)
    assert_replay_equivalent(reference)
    want = ev.dump(reference.trace)

    def resume(s: Session) -> Session:
        return Session.restore(json.loads(json.dumps(s.snapshot())))

    start = Session.start(ENV, p, 5)
    assert ev.dump(finish(resume(start)).trace) == want  # before anything happened
    s = Session.start(ENV, p, 5)
    s.advance()
    assert len(s.pending()) == 2  # the two verifiers
    assert ev.dump(finish(resume(s)).trace) == want
    first = s.pending()[0]
    s.submit(first.agent_id, first.decision_id, answer(first))
    assert ev.dump(finish(resume(s)).trace) == want  # one report already in
    s.submit(s.pending()[0].agent_id, s.pending()[0].decision_id, answer(s.pending()[0]))
    s.advance()
    assert len(s.pending()) == 7  # market stage: 4 sellers and 3 buyers
    one = s.pending()[3]
    s.submit(one.agent_id, one.decision_id, answer(one))
    partial = resume(s)
    assert len(partial.pending()) == 6
    assert ev.dump(finish(partial).trace) == want  # mid-market, one order already in


def test_factory_validation_and_default_population() -> None:
    for bad in (
        {"rounds": 0},
        {"p_audit": 1.5},
        {"fee": -1},
        {"settle_on": "never"},
        {"reputation_visible": 1},
        {"sellers": [], "buyers": [], "verifiers": []},
        {"sellers": [{"agent": "s0", "quantity": 1, "theta": 0.5, "cost": 1}]},
        {"n_verifiers": 0},
    ):
        with pytest.raises(ContractError):
            build_vcm(**bad)
    pop = draw_population(5, 4, 2, seed=3, bias=[0.0, 0.2])
    assert (
        pop == draw_population(5, 4, 2, seed=3, bias=[0.0, 0.2]) != draw_population(5, 4, 2, seed=4)
    )
    assert [v.projects for v in pop.verifiers] == [["s0", "s2", "s4"], ["s1", "s3"]]
    session = Session.start(ENV, {"rounds": 1, "population_seed": 3}, 0)
    session.advance()
    assert {r.agent_id for r in session.pending()} == {"v0", "v1"}


def test_unassigned_or_doubly_assigned_projects_are_refused() -> None:
    sellers = [s.model_dump() for s in POP.sellers]
    buyers = [b.model_dump() for b in POP.buyers]
    for verifiers in (
        [{"agent": "v0", "projects": ["s0", "s1"]}],  # s2, s3 unassigned
        [
            {"agent": "v0", "projects": ["s0", "s1", "s2", "s3"]},
            {"agent": "v1", "projects": ["s3"]},
        ],
    ):
        with pytest.raises(ContractError, match="exactly one verifier"):
            build_vcm(sellers=sellers, buyers=buyers, verifiers=verifiers)


@pytest.mark.parametrize("kind", ["drop", "default"])
def test_market_timeouts_mean_an_order_for_zero_credits(kind: str) -> None:
    # the two cheap sellers never answer; their roles time out. No credit of theirs trades.
    p = params(rounds=1, p_audit=0.0, timeout={"kind": kind})
    session = Session.start(ENV, p, 1)
    for _ in range(50):
        session.advance()
        if session.finished:
            break
        for req in session.pending():
            if req.layer == "market" and req.agent_id in ("s2", "s3"):
                session.timeout(req.decision_id)
            else:
                session.submit(req.agent_id, req.decision_id, answer(req))
    assert session.finished and not session.failed
    mine = reports(session, "s2", "clearing")[0]
    # a dropped role has no order at all (no fill to report); a defaulted one has an order for 0
    assert mine.get("own_fill") == (None if kind == "drop" else 0.0)
    assert reports(session, "b0", "clearing")[0]["volume"] == 10  # only s1 (ask 7) trades
    with pytest.raises(ContractError):
        build_vcm(timeout={"kind": "nonsense"})


# ------------------------------------------------------------------ learners


def learner(visible: bool, seed: int = 0) -> TableAgent:
    """A tabular buyer; its state is the biased verifier's mean error if shown, else the round."""
    field = "reputation.v1.mean_error" if visible else "round"
    return TableAgent(
        aid("b0"),
        FieldObsCodec([field]),
        SchemaActCodec(grid={"price": [3.0, 4.0, 5.0, 6.0, 7.0, 8.0], "qty": [10]}),
        RewardExtractor(fields=["own_net"]),
        QConfig(alpha=0.3, gamma=0.0, epsilon=0.3),
        seed,
    )


@pytest.mark.slow
@pytest.mark.parametrize("visible", [True, False])
def test_tabular_buyer_learns_to_stay_out_of_the_lemons_market(visible: bool) -> None:
    """Against naive rivals and a verifier that overstates its projects by 0.3, buying at the
    going price loses money (the pooled basket is far worse than reported). A buyer paid its audited
    net learns to bid below the going price whether or not the track record is shown; the record
    changes what it *can* condition on, not (in this market) what the best response is: the true
    qualities are the same in every episode, so reports carry no news. See NOTES.md.
    """
    spec = TrainSpec(
        env_ref=ENV,
        params=params(rounds=4, p_audit=1.0, reputation_visible=visible, market_form="pooled"),
        learner=aid("b0"),
        episodes=120,
        seed=1,
        opponents={
            **{aid(v.agent): "scripted:verifier" for v in POP.verifiers},
            **{aid(s.agent): "scripted:seller" for s in POP.sellers},
            aid("b1"): "scripted:buyer",
            aid("b2"): "scripted:buyer",
        },
    )
    factory = default_opponent_factory(
        scripted={"verifier": honest_verifier, "seller": truthful_seller, "buyer": naive_buyer}
    )
    result = train(spec, learner(visible), factory)
    played = evaluate(spec, result.checkpoint, factory, episodes=4, seed=9)
    naive = play(params(rounds=4, p_audit=1.0, reputation_visible=visible, market_form="pooled"))
    assert naive.ledger.utility(aid("b0")) < -10  # the naive buyer loses on every round
    assert played.mean is not None and played.mean >= 0.0  # the learner stays out, or breaks even


# ------------------------------------------------------------------ language-model players


def test_mocked_llm_verifier_and_buyer_complete_a_round() -> None:
    verifier_backend = MockBackend(lambda messages: {"s0": 0.9, "s1": 0.8})
    buyer_backend = MockBackend(lambda messages: {"price": 6.0, "qty": 10})
    who = agents()
    who[aid("v0")] = LLMAgent(
        aid("v0"), verifier_backend, VerifierCodec(), report_renderer=VCMReportRenderer()
    )
    who[aid("b0")] = LLMAgent(
        aid("b0"), buyer_backend, BuyerCodec(), report_renderer=VCMReportRenderer()
    )
    session = play(params(rounds=1, p_audit=0.0), who)
    assert len(verifier_backend.calls) == 1 and len(buyer_backend.calls) == 1
    sent = json.dumps([c.model_dump(mode="json") for c in buyer_backend.calls])
    assert "theta" not in sent and "true_quality" not in sent
    assert reports(session, "b0", "clearing")[0]["round"] == 0
    assert_replay_equivalent(session)


# ------------------------------------------------------------------ per-project orders


def pp(pop: Population = POP, **over: Any) -> dict[str, Any]:
    """Parameters of the default (per-project) market on the tutorial population."""
    return params(pop, market_form="per_project", **over)


def true_net_by_project(session: Session, buyer: str) -> dict[str, float]:
    """``fill * (value * true quality - price)`` per project, from the buyer's own reports."""
    value = next(b.value for b in POP.buyers if b.agent == buyer)
    net: dict[str, float] = {}
    for r in reports(session, buyer, "clearing"):
        for project, fill in r.get("own_fills", {}).items():
            price = r["projects"][project]["price"]
            net[project] = net.get(project, 0.0) + fill * (
                value * POP.seller(project).theta - price
            )
    return net


def test_the_default_market_is_per_project_on_the_default_fixture() -> None:
    session = Session.start(ENV, {"rounds": 1}, 0)
    run_sync(session, agents(POP, buyer=naive_project_buyer))
    assert session.finished and not session.failed
    assert "own_fills" in reports(session, "b0", "clearing")[0]
    # the default population is the tutorial fixture, and it has gains from trade: with truthful
    # verification every buyer values the honest sellers' projects above their cost
    assert build_vcm()[1].state == build_vcm(**pp(rounds=5))[1].state
    for buyer in POP.buyers:
        for project in ("s0", "s1"):
            assert buyer.value * POP.seller(project).theta > POP.seller(project).cost
    with pytest.raises(ContractError, match="market_form"):
        build_vcm(market_form="mixed")


def test_per_project_clearing_is_one_auction_per_project() -> None:
    pop = Population.model_validate(
        {
            "sellers": [
                {"agent": "s1", "quantity": 4, "theta": 0.8, "cost": 6.0},
                {"agent": "s2", "quantity": 4, "theta": 0.8, "cost": 7.0},
            ],
            "buyers": [
                {"agent": "b1", "demand": 5, "value": 10.0},
                {"agent": "b2", "demand": 5, "value": 8.0},
            ],
            "verifiers": [{"agent": "v", "projects": ["s1", "s2"]}],
        }
    )
    bids = {
        "b1": {"s1": {"price": 10.0, "qty": 3}, "s2": {"price": 9.0, "qty": 2}},
        "b2": {"s1": {"price": 8.0, "qty": 3}},
    }

    def policy(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
        if request.role.startswith("buyer"):
            return bids[request.agent_id]
        return (
            honest_verifier(request, reports)
            if request.layer == "verification"
            else (truthful_seller(request, reports))
        )

    who = {a: ScriptedAgent(policy) for a in ("v", "s1", "s2", "b1", "b2")}
    session = play(pp_for(pop), {aid(a): x for a, x in who.items()})
    # s1 alone: bids 10x3, 8x3 against ask 6x4: V = 4, marginal pair (8, 6), price 7, b1 fills 3
    # and b2 (marginal, 1 of its 3 left) fills 1. s2 alone: bid 9x2 against ask 7x4: price 8.
    alone = clear_call_auction([("b1", 10, 3), ("b2", 8, 3)], [("s1", 6, 4)])
    assert (alone.price, alone.volume, alone.bid_fills) == (7.0, 4, {"b1": 3.0, "b2": 1.0})
    mine = reports(session, "b1", "clearing")[0]
    assert mine["projects"] == {
        "s1": {"price": 7.0, "volume": 4},
        "s2": {"price": 8.0, "volume": 2},
    }
    assert mine["own_fills"] == {"s1": 3.0, "s2": 2.0}
    assert mine["own_payment"] == pytest.approx(3 * 7.0 + 2 * 8.0)
    assert reports(session, "b2", "clearing")[0]["own_fills"] == {"s1": 1.0}
    assert reports(session, "s2", "clearing")[0]["own_revenue"] == pytest.approx(2 * 8.0)
    # a buyer's basket is exactly what it bid for: b2 holds nothing of s2
    assert "s2" not in reports(session, "b2", "clearing")[0]["own_fills"]
    assert session.ledger.utility(aid("s1")) == pytest.approx(4 * (7.0 - 6.0))
    assert session.ledger.utility(aid("b1")) == pytest.approx(-(3 * 7.0 + 2 * 8.0) + 10.0 * 0.8 * 5)


def pp_for(pop: Population, **over: Any) -> dict[str, Any]:
    return params(pop, rounds=1, p_audit=0.0, market_form="per_project", **over)


def _first_buyer_request(market_form: str = "per_project") -> tuple[Session, DecisionRequest]:
    session = Session.start(ENV, params(rounds=1, market_form=market_form), 3)
    session.advance()
    for r in session.pending():
        session.submit(r.agent_id, r.decision_id, honest_verifier(r, []))
    session.advance()
    return session, session.pending_for(aid("b0"))[0]


def test_per_project_orders_are_checked() -> None:
    _, request = _first_buyer_request()
    assert set(request.action_schema["properties"]) == {"s0", "s1", "s2", "s3"}
    assert request.action_schema.get("required", []) == []  # every order is optional
    for bad, error in (
        ({"s0": {"price": 3.0, "qty": 6}, "s1": {"price": 3.0, "qty": 5}}, Infeasible),  # 11 > 10
        ({"zz": {"price": 3.0, "qty": 1}}, SchemaError),  # not a listed project
        ({"s0": {"price": 3.0, "qty": 11}}, SchemaError),  # more than the project has
        ({"s0": {"price": -1.0, "qty": 1}}, SchemaError),
    ):
        session, request = _first_buyer_request()
        with pytest.raises(error):
            session.submit(aid("b0"), request.decision_id, bad)
    session, request = _first_buyer_request()
    session.submit(aid("b0"), request.decision_id, {})  # an empty order set is fine
    session, request = _first_buyer_request()
    session.submit(
        aid("b0"),
        request.decision_id,
        {"s0": {"price": 3.0, "qty": 0}, "s1": {"price": 1, "qty": 10}},
    )  # qty 0 means no order; exactly the demand is fine


def test_per_project_buyers_do_not_see_each_others_bids_or_the_true_quality() -> None:
    def bidder(price: float) -> Policy:
        def policy(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
            if request.agent_id == "b1":
                return {"s0": {"price": price, "qty": 5}}
            return naive_project_buyer(request, reports)

        return policy

    low, high = (play(pp(rounds=1), agents(buyer=bidder(p))) for p in (5.11, 9.37))
    stream = json.dumps([i.model_dump(mode="json") for i in high.ledger.information(aid("b0"))])
    assert "9.37" not in stream and "5.11" not in stream
    assert requests(low, "b0", "market") == requests(high, "b0", "market")
    # no audits: flipping every true quality changes nothing any buyer or seller is told
    quiet, quiet2 = (
        play(
            pp(pop, rounds=1, p_audit=0.0),
            agents(pop, verifier=constant_verifier, buyer=naive_project_buyer),
        )
        for pop in (POP, other_theta())
    )
    for who in ("b0", "b1", "b2", "s0", "s1", "s2", "s3"):
        a = [i.model_dump(mode="json") for i in quiet.ledger.information(aid(who))]
        b = [i.model_dump(mode="json") for i in quiet2.ledger.information(aid(who))]
        assert a == b, who


def test_naive_buyer_loses_on_biased_projects_and_gains_on_honest_ones() -> None:
    gained: dict[str, float] = {}
    for seed in range(4):
        session = play(pp(rounds=4, p_audit=0.5), agents(buyer=naive_project_buyer), seed=seed)
        for buyer in ("b0", "b1", "b2"):
            for project, net in true_net_by_project(session, buyer).items():
                gained[project] = gained.get(project, 0.0) + net
    # v0 (honest) verified s0 and s1; v1 overstates s2 and s3 by 0.3
    assert gained["s0"] > 0 and gained["s1"] > 0
    assert gained["s2"] < 0 and gained["s3"] < 0


def test_skeptical_buyer_with_visible_record_outperforms_hidden() -> None:
    def total(visible: bool, seed: int, buyer: Policy) -> float:
        session = play(
            pp(rounds=5, p_audit=1.0, settle_on="true", reputation_visible=visible),
            agents(buyer=buyer),
            seed=seed,
        )
        return sum(session.ledger.utility(aid(b.agent)) for b in POP.buyers)

    for seed in range(3):
        shown = total(True, seed, skeptical_project_buyer)
        hidden = total(False, seed, skeptical_project_buyer)
        assert shown > hidden, seed
        # the record is all that separates them: with it hidden the skeptic is the naive buyer
        assert hidden == pytest.approx(total(False, seed, naive_project_buyer))
        # and it is not that the record helps a naive buyer, who ignores it
        assert total(True, seed, naive_project_buyer) == pytest.approx(hidden)


def test_per_project_replay_restore_and_timeouts() -> None:
    p = pp(rounds=3, reputation_visible=True)
    session = play(p, agents(buyer=naive_project_buyer), seed=5)
    assert_replay_equivalent(session)
    resumed = Session.restore(session.snapshot())
    assert resumed.finished and ev.dump(resumed.trace) == ev.dump(session.trace)
    for kind in ("drop", "default"):
        started = Session.start(ENV, pp(rounds=1, timeout={"kind": kind}), 2)
        started.advance()
        for r in started.pending():
            started.submit(r.agent_id, r.decision_id, honest_verifier(r, []))
        started.advance()
        for r in started.pending():
            if r.agent_id == "b0":
                started.timeout(r.decision_id)
            else:
                started.submit(
                    r.agent_id,
                    r.decision_id,
                    answer(r) if r.role.startswith("seller") else naive_project_buyer(r, []),
                )
        started.advance()
        assert started.finished and not started.failed
        fills = reports(started, "b0", "clearing")[0].get("own_fills")
        # a defaulted buyer sent an empty order set; a dropped one sent nothing: no fills either way
        assert fills == ({} if kind == "default" else None)


def test_mocked_llm_buyer_completes_a_per_project_round() -> None:
    backend = MockBackend(lambda messages: {"s0": {"price": 7.0, "qty": 4}})
    who = agents(buyer=naive_project_buyer)
    who[aid("b0")] = LLMAgent(
        aid("b0"), backend, ProjectBuyerCodec(), report_renderer=VCMReportRenderer()
    )
    session = play(pp(rounds=2, p_audit=0.0), who)
    assert len(backend.calls) == 2
    sent = json.dumps([c.model_dump(mode="json") for c in backend.calls])
    assert "theta" not in sent and "true_quality" not in sent
    assert reports(session, "b0", "clearing")[0]["own_fills"] != {}
    assert_replay_equivalent(session)


def inflating_verifier(
    request: DecisionRequest, reports: list[ParticipantReport]
) -> dict[str, Any]:
    return {p: min(1.0, x + 0.3) for p, x in request.observation["measurements"].items()}


def v1_inflates(episode_seed: int) -> bool:
    """One episode in ten: v1 inflates its reports; otherwise it reports its measurement."""
    return episode_seed % 10 == 3


class Recorder:
    """Wraps a frozen learner and keeps the orders it sends (researcher-side bookkeeping)."""

    def __init__(self, inner: TableAgent) -> None:
        self.inner = inner
        self.orders: list[dict[str, Any]] = []

    async def episode_start(self, ctx: Any) -> None:
        await self.inner.episode_start(ctx)

    async def observe(self, report: ParticipantReport) -> None:
        await self.inner.observe(report)

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        action = await self.inner.act(request)
        self.orders.append(action)
        return action

    async def episode_end(self, end: Any) -> None:
        await self.inner.episode_end(end)


def project_learner(visible: bool) -> TableAgent:
    return TableAgent(
        aid("b0"),
        RecordObsCodec("v1"),
        MarkdownActCodec("v1"),
        RewardExtractor(fields=["own_net"]),
        QConfig(alpha=0.3, gamma=0.0, epsilon=0.3),
        0,
    )


@pytest.mark.slow
def test_tabular_buyer_discounts_biased_verifier_when_record_visible() -> None:
    """v1 inflates by 0.3 in one episode in ten (an unknown type), is audited every round, and
    the learner is paid its audited net. Hidden, the best fixed policy trusts v1 (a loss in one
    episode in ten costs less than staying out of the other nine); with the record shown, the
    learner marks v1's projects down once v1 has been caught. Rivals are naive per-project buyers.
    """
    vs = [v.model_dump() for v in POP.verifiers]
    vs[1]["bias"] = 0.0  # the type is in the verifier's policy, below
    factory_names = {
        "verifier": honest_verifier,
        "seller": truthful_seller,
        "buyer": naive_project_buyer,
    }

    def factory(agent_id: Any, description: str, seed: int) -> Any:
        if description == "v1":
            return ScriptedAgent(inflating_verifier if v1_inflates(seed) else honest_verifier)
        return default_opponent_factory(scripted=factory_names)(agent_id, description, seed)

    def spec(visible: bool) -> TrainSpec:
        return TrainSpec(
            env_ref=ENV,
            params=params(
                rounds=4,
                p_audit=1.0,
                market_form="per_project",
                reputation_visible=visible,
                verifiers=vs,
            ),
            learner=aid("b0"),
            episodes=120,
            seed=1,
            opponents={
                aid("v0"): "scripted:verifier",
                aid("v1"): "v1",
                **{aid(s.agent): "scripted:seller" for s in POP.sellers},
                aid("b1"): "scripted:buyer",
                aid("b2"): "scripted:buyer",
            },
        )

    trained = {}
    for visible in (True, False):
        learner_ = project_learner(visible)
        trained[visible] = (spec(visible), train(spec(visible), learner_, factory).checkpoint)
    returns = {}
    for visible, (sp, ckpt) in trained.items():
        returns[visible] = evaluate(
            sp,
            ckpt,
            factory,
            episodes=20,
            seed=9,
            obs_codec=RecordObsCodec("v1"),
            act_codec=MarkdownActCodec("v1"),
        )
    caught = sum(v1_inflates(9 * 1_000_003 + e) for e in range(20))
    assert caught >= 1  # the evaluation includes episodes in which v1 inflates
    shown, hidden = returns[True].mean, returns[False].mean
    assert shown is not None and hidden is not None and shown > hidden

    def v1_bids(visible: bool) -> list[float]:
        """The learner's greedy prices on v1's projects in the rounds after v1 was caught."""
        sp, ckpt = trained[visible]
        agent = TableAgent.from_checkpoint(
            ckpt, RecordObsCodec("v1"), MarkdownActCodec("v1"), learning=False
        )
        seed = next(s for s in range(9 * 1_000_003, 9 * 1_000_003 + 50) if v1_inflates(s))
        probe = Recorder(agent)
        session = Session.start(ENV, sp.params, seed)
        who: dict[AgentId, Any] = {aid("b0"): probe}
        for a, d in sp.opponents.items():
            if a != aid("b0"):
                who[a] = factory(a, d, seed)
        run_sync(session, who)
        by_round = probe.orders[1:]  # round 0 is played before anything is known
        return [o[p]["price"] for o in by_round for p in ("s2", "s3") if p in o]

    low, high = v1_bids(True), v1_bids(False)
    assert low and high and max(low) < min(high)
