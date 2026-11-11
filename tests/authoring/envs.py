"""Small environments for the authoring tests: an ultimatum game with mutants, a tiny auction.

``build_ultimatum`` is the reference; ``mutant`` switches on one defect at a time, each of which
``check_spec`` must catch. ``build_tiny_auction`` has a module-level tie-break that tests change
to stand in for "the behaviour changed after a card was confirmed".
"""

from typing import Any

from numpy.random import Generator
from pydantic import BaseModel

from gao_idl import (
    Config,
    Connector,
    Contract,
    DefaultAction,
    DropRole,
    Primitive,
    Seq,
    SimpleLayer,
)
from gao_idl.authoring import EnvSpec
from gao_idl.composition.process import Process
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Exit
from gao_idl.core.store import StoreView

ULTIMATUM = "tests.authoring.envs:build_ultimatum"
TINY_AUCTION = "tests.authoring.envs:build_tiny_auction"
PROPOSER, RESPONDER = RoleId("proposer"), RoleId("responder")
MADE, DONE = Port("made"), Port("done")
ANN, BEN = AgentId("ann"), AgentId("ben")


class OfferState(BaseModel):
    pie: float
    cost: float
    mutant: str = "none"


class Offer(BaseModel):
    offer: float


class OfferObs(BaseModel):
    pie: float
    cost: float


class OfferMade(BaseModel):
    pie: float
    cost: float
    offer: float
    mutant: str = "none"


class Response(BaseModel):
    accept: bool


class ResponseObs(BaseModel):
    pie: float
    offer: float


class LeakyResponseObs(ResponseObs):
    proposer_cost: float


class Split(BaseModel):
    accepted: bool
    payoffs: dict[str, float]
    cost: float
    mutant: str


class Told(BaseModel):
    accepted: bool
    own_payoff: float


class LeakyTold(Told):
    proposer_cost: float


def _offer_layer() -> SimpleLayer:
    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        state = OfferState.model_validate(cfg.state)
        return OfferObs(pie=state.pie, cost=state.cost)

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = OfferState.model_validate(cfg.state)
        offer = actions[PROPOSER]
        assert isinstance(offer, Offer)
        return OfferMade(pie=state.pie, cost=state.cost, offer=offer.offer, mutant=state.mutant)

    return SimpleLayer(
        name="offer",
        entry=Contract(state_schema=OfferState, roles={PROPOSER: {}}),
        exits={MADE: OfferMade},
        observe=observe,
        action_schema=lambda cfg, role: Offer,
        feasible=lambda cfg, role, action: 0 <= action.offer <= cfg.state["pie"],  # type: ignore[attr-defined]
        outcome=outcome,
        export=lambda cfg, out: Exit(port=MADE, record=out.model_dump(mode="json")),
    )


def _response_layer(mutant: str) -> SimpleLayer:
    def observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        state = OfferMade.model_validate(cfg.state)
        if mutant == "leak_observation":
            return LeakyResponseObs(pie=state.pie, offer=state.offer, proposer_cost=state.cost)
        return ResponseObs(pie=state.pie, offer=state.offer)

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        state = OfferMade.model_validate(cfg.state)
        answer = actions.get(RESPONDER)  # absent when the responder was dropped: no deal
        accepted = isinstance(answer, Response) and answer.accept
        payoffs = {
            str(ANN): state.pie - state.offer - state.cost if accepted else 0.0,
            str(BEN): state.offer if accepted else 0.0,
        }
        return Split(accepted=accepted, payoffs=payoffs, cost=state.cost, mutant=mutant)

    def report(cfg: Config, out: BaseModel, recipient: AgentId, rng: Generator) -> BaseModel | None:
        assert isinstance(out, Split)
        own = out.payoffs[str(recipient)]
        if mutant == "leak_report" and recipient == BEN:
            return LeakyTold(accepted=out.accepted, own_payoff=own, proposer_cost=out.cost)
        return Told(accepted=out.accepted, own_payoff=own)

    return SimpleLayer(
        name="response",
        entry=Contract(state_schema=OfferMade, roles={RESPONDER: {}}),
        exits={DONE: Split},
        timeout=DefaultAction() if mutant == "default_timeout" else DropRole(),
        observe=observe,
        action_schema=lambda cfg, role: Response,
        default_action=lambda cfg, role: Response(accept=True),
        outcome=outcome,
        utilities=lambda cfg, out: {AgentId(a): u for a, u in out.payoffs.items()},  # type: ignore[attr-defined]
        export=lambda cfg, out: Exit(port=DONE, record=out.model_dump(mode="json")),
        audience=lambda cfg, out: {ANN, BEN},
        report=report,
    )


def build_ultimatum(
    pie: float = 10.0, cost: float = 1.0, mutant: str = "none"
) -> tuple[Process, Config]:
    """Proposer offers a share of ``pie``; responder accepts or not. ``cost`` is the proposer's
    private cost. Mutants: ``leak_report``, ``leak_observation``, ``default_timeout``,
    ``no_response`` (the second step is missing)."""
    cfg = Config(state={"pie": pie, "cost": cost, "mutant": mutant}, roles={PROPOSER: ANN})
    offer = Primitive(_offer_layer())
    if mutant == "no_response":
        return offer, cfg
    to_response = Connector(
        lambda rec, ledger, store: Config(state=rec, roles={RESPONDER: BEN}), OfferMade
    )
    return Seq(offer, {MADE: (to_response, Primitive(_response_layer(mutant)))}, name="deal"), cfg


def ultimatum_spec(mutant: str = "none") -> EnvSpec:
    """The spec of the reference ultimatum game, pointed at ``mutant``."""
    return EnvSpec.model_validate(
        {
            "title": "Ultimatum game",
            "summary": "A proposer offers a share of a pie; the responder takes it or leaves it.",
            "env_ref": ULTIMATUM,
            "params": {"pie": 10.0, "cost": 1.0, "mutant": mutant},
            "roles": [
                {"name": "proposer", "description": "Makes the offer and bears a private cost."},
                {"name": "responder", "description": "Accepts or rejects the offer."},
            ],
            "steps": [
                {
                    "name": "offer",
                    "kind": "decision",
                    "description": "The proposer names the responder's share.",
                    "sees": {"proposer": ["pie", "cost"]},
                    "chooses": {"proposer": ["offer"]},
                    "on_no_action": "fail",
                },
                {
                    "name": "response",
                    "kind": "decision",
                    "description": "The responder accepts or rejects.",
                    "sees": {"responder": ["pie", "offer"]},
                    "chooses": {"responder": ["accept"]},
                    "told": {
                        "proposer": ["accepted", "own_payoff"],
                        "responder": ["accepted", "own_payoff"],
                    },
                    "on_no_action": "drop",
                    "no_action_means": "No answer is a rejection.",
                },
            ],
            "secrets": [
                {
                    "what": "The proposer's cost is private.",
                    "param": "cost",
                    "alt_value": 3.0,
                    "hidden_from": ["responder"],
                }
            ],
            "payoffs": [
                {
                    "role": "proposer",
                    "description": "Pie minus offer minus cost if accepted.",
                    "report_field": "own_payoff",
                },
                {
                    "role": "responder",
                    "description": "The offer if accepted.",
                    "report_field": "own_payoff",
                },
            ],
        }
    )


TIE = "first"  # tests set this to "last" to change the behaviour


class BidState(BaseModel):
    values: dict[str, float]


class Bid(BaseModel):
    amount: float


class Sold(BaseModel):
    winner: str
    price: float


def build_tiny_auction(values: dict[str, float]) -> tuple[Process, Config]:
    """First-price sealed bid; a tie goes to the first name alphabetically (or the last: TIE)."""
    roles = {RoleId(f"bidder:{i}"): AgentId(a) for i, a in enumerate(sorted(values))}

    def outcome(
        cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        bids = {str(cfg.roles[r]): a.amount for r, a in actions.items()}  # type: ignore[attr-defined]
        top = max(bids.values())
        tied = sorted(a for a, b in bids.items() if b == top)
        return Sold(winner=tied[0] if TIE == "first" else tied[-1], price=top)

    def utilities(cfg: Config, out: Any) -> dict[AgentId, float]:
        return {AgentId(out.winner): values[out.winner] - out.price}

    layer = SimpleLayer(
        name="bidding",
        entry=Contract(state_schema=BidState),
        exits={DONE: Sold},
        action_schema=lambda cfg, role: Bid,
        outcome=outcome,
        utilities=utilities,
        export=lambda cfg, out: Exit(port=DONE, record=out.model_dump(mode="json")),
        report=lambda cfg, out, recipient, rng: out,
    )
    return Primitive(layer), Config(state={"values": dict(values)}, roles=roles)
