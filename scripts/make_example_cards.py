"""Write the proposed scenario cards under ``examples/cards/`` (auction, PD, carbon market).

Run from the repository root: ``python scripts/make_example_cards.py``. Every card is written
with status ``proposed`` and its outcome frozen from a run. This script never confirms a card:
only a person does, with ``gao-idl cards confirm``. Re-running it overwrites the files, so run it
only to add cards, and carry confirmed or rejected statuses over by hand.
"""

from pathlib import Path
from typing import Any

from gao_idl import ScriptedAgent, Session, run_sync
from gao_idl.authoring import Card, freeze, save_cards
from gao_idl.authoring.play import ActionScript, script_of
from gao_idl.core.ids import AgentId
from gao_idl.environments.vcm import lemons_population
from gao_idl.environments.vcm.scripted import (
    honest_verifier,
    naive_project_buyer,
    skeptical_project_buyer,
    truthful_seller,
)

OUT = Path(__file__).resolve().parents[1] / "examples" / "cards"
AUCTION = "gao_idl.environments.auction:build_auction"
PD = "gao_idl.environments.pd:build_repeated_pd"
VCM = "gao_idl.environments.vcm:build_vcm"


def card(
    env: str,
    id_: str,
    title: str,
    why: str,
    params: dict[str, Any],
    script: ActionScript,
    seed: int = 1,
) -> Card:
    return freeze(
        Card(id=id_, title=title, why=why, env_ref=env, params=params, seed=seed, script=script)
    )


def bids(alice: float | None, bob: float | None, carol: float | None) -> ActionScript:
    return {
        name: [None if amount is None else {"amount": amount}]
        for name, amount in (("alice", alice), ("bob", bob), ("carol", carol))
    }


def auction_cards() -> list[Card]:
    base: dict[str, Any] = {
        "values": {"alice": 10.0, "bob": 7.0, "carol": 4.0},
        "rule": "second_price",
        "reveal_bids": False,
        "reserve": 2.0,
    }

    def a(id_: str, title: str, why: str, script: ActionScript, **changes: Any) -> Card:
        return card(AUCTION, id_, title, why, {**base, **changes}, script)

    return [
        a(
            "truthful",
            "Everyone bids their value",
            "The plain case: the highest bidder wins and pays the second-highest bid.",
            bids(10, 7, 4),
        ),
        a(
            "tie-top",
            "Two bidders tie for the highest bid",
            "Who wins a tie, and what do they pay? The earlier name wins and pays the tied bid.",
            bids(8, 8, 1),
        ),
        a(
            "tie-lower-value-wins",
            "A tie that the lower-value bidder wins",
            "The tie-break goes by name, not by who values the item more.",
            bids(1, 9, 9),
        ),
        a(
            "one-above-reserve",
            "Only one bid clears the reserve",
            "Does a lone qualifying bidder pay its own bid, the next bid, or the reserve?",
            bids(5, 1, 1),
        ),
        a(
            "second-below-reserve",
            "The second-highest bid is below the reserve",
            "The reserve, not the losing bid, sets the price.",
            bids(6, 1.5, 0),
        ),
        a(
            "exactly-reserve",
            "A bid exactly equal to the reserve",
            "Is a bid at the reserve enough to win?",
            bids(2, 1, 1),
        ),
        a(
            "all-below-reserve",
            "No bid clears the reserve",
            "The item stays unsold and nobody pays.",
            bids(1, 1, 1),
        ),
        a(
            "no-bid",
            "One bidder sends no bid",
            "A missing bid stops the whole run; it is not treated as a bid of zero.",
            bids(10, 7, None),
        ),
        a(
            "overbid",
            "A bidder bids above its value and wins",
            "Bids above one's value are allowed and can lose money.",
            bids(10, 12, 4),
        ),
        a(
            "first-price",
            "First-price rule",
            "Under first_price the winner pays its own bid.",
            bids(9, 7, 4),
            rule="first_price",
        ),
        a(
            "reveal-bids",
            "Bids are revealed afterwards",
            "With reveal_bids on, every bidder is told every bid after settlement.",
            bids(10, 7, 4),
            reveal_bids=True,
        ),
        a(
            "no-reserve-lone-bid",
            "No reserve and only one positive bid",
            "Without a reserve the winner pays the highest other bid, here zero.",
            bids(5, 0, 0),
            reserve=None,
        ),
        a(
            "no-reserve-all-zero",
            "No reserve and everyone bids zero",
            "The item is still sold: the first name wins and pays nothing.",
            bids(0, 0, 0),
            reserve=None,
        ),
    ]


def moves(**by_agent: str) -> ActionScript:
    """``moves(a="CD")``: a plays C then D (the last move repeats); ``-`` means no move."""
    return {
        agent: [None if m == "-" else {"move": m} for m in sequence]
        for agent, sequence in by_agent.items()
    }


def pd_cards() -> list[Card]:
    two: dict[str, Any] = {"agents": ["a", "b"], "rounds": 2, "matching": "fixed"}
    four: dict[str, Any] = {"agents": ["a", "b", "c", "d"], "rounds": 3, "matching": "fixed"}

    def p(id_: str, title: str, why: str, params: dict[str, Any], script: ActionScript) -> Card:
        return card(PD, id_, title, why, params, script)

    return [
        p(
            "both-cooperate",
            "Both cooperate in both rounds",
            "The mutual-cooperation payoff, added over rounds.",
            two,
            moves(a="C", b="C"),
        ),
        p(
            "both-defect",
            "Both defect in both rounds",
            "The mutual-defection payoff.",
            two,
            moves(a="D", b="D"),
        ),
        p(
            "one-defects",
            "One cooperates, the other defects",
            "Who gets the temptation payoff and who gets nothing.",
            two,
            moves(a="C", b="D"),
        ),
        p(
            "alternate",
            "The players swap roles between rounds",
            "Each player is told the other's move after every round and totals add up.",
            two,
            moves(a="CD", b="DC"),
        ),
        p(
            "single-round",
            "A single round",
            "With one round the game ends after the first pair of moves.",
            {**two, "rounds": 1},
            moves(a="D", b="C"),
        ),
        p(
            "two-pairs",
            "Two pairs side by side",
            "A pair is told nothing about the other pair's moves.",
            four,
            moves(a="C", b="C", c="D", d="D"),
        ),
        p(
            "round-robin",
            "Four players, a new partner every round",
            "With round_robin matching each player meets each other player once.",
            {**four, "matching": "round_robin"},
            moves(a="C", b="D", c="C", d="D"),
        ),
        p(
            "no-move-stops",
            "A player does not move",
            "By default a missing move stops the whole run, for both pairs.",
            four,
            moves(a="C", b="C-", c="C", d="C"),
        ),
        p(
            "no-move-default",
            "A player does not move and a default is configured",
            "With a default move of D configured, silence counts as defecting.",
            {**two, "timeout": {"kind": "default"}, "default_move": "D"},
            moves(a="C", b="C-"),
        ),
        p(
            "payoffs-hidden",
            "The payoff table is not shown",
            "With disclose_payoffs off, players see no table and learn payoffs only from reports.",
            {**two, "disclose_payoffs": False},
            moves(a="C", b="D"),
        ),
        p(
            "custom-payoffs",
            "A different payoff table",
            "Payoffs come from the configured table, row player first.",
            {**two, "payoffs": {"CC": [2, 2], "CD": [-1, 4], "DC": [4, -1], "DD": [0, 0]}},
            moves(a="CD", b="DD"),
        ),
    ]


def vcm_params(**changes: Any) -> dict[str, Any]:
    pop = lemons_population()
    base: dict[str, Any] = {
        "rounds": 1,
        "fee": 1.0,
        "penalty": 5.0,
        "tol": 0.1,
        "p_audit": 1.0,
        "settle_on": "reported",
        "reputation_visible": True,
        "market_form": "per_project",
        "sigma": 0.03,
        "sellers": [s.model_dump() for s in pop.sellers],
        "buyers": [b.model_dump() for b in pop.buyers],
        "verifiers": [v.model_dump() for v in pop.verifiers],
    }
    return {**base, **changes}


def vcm_script(params: dict[str, Any], buyer: Any, seed: int = 1) -> ActionScript:
    """The actions of honest verifiers, sellers asking their cost and ``buyer``, as data."""
    session = Session.start(VCM, params, seed)
    agents = {AgentId(v["agent"]): ScriptedAgent(honest_verifier) for v in params["verifiers"]}
    agents |= {AgentId(s["agent"]): ScriptedAgent(truthful_seller) for s in params["sellers"]}
    agents |= {AgentId(b["agent"]): ScriptedAgent(buyer) for b in params["buyers"]}
    run_sync(session, agents)
    script = script_of(session)
    for entries in script.values():  # round for readability; still valid actions
        for entry in entries:
            _round(entry)
    return script


def _round(value: Any) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if isinstance(inner, float):
                value[key] = round(inner, 2)
            else:
                _round(inner)


def vcm_cards() -> list[Card]:
    def v(id_: str, title: str, why: str, params: dict[str, Any], script: ActionScript) -> Card:
        return card(VCM, id_, title, why, params, script)

    audited = vcm_params()
    base = vcm_script(audited, naive_project_buyer)
    unaudited = vcm_params(p_audit=0.0)
    two_rounds = vcm_params(rounds=2)
    hidden = vcm_params(rounds=2, reputation_visible=False)
    by_chance = vcm_params(p_audit=0.5)
    on_true = vcm_params(settle_on="true")

    def with_(script: ActionScript, **changes: Any) -> ActionScript:
        return {**script, **changes}

    return [
        v(
            "audited",
            "One round, every traded project audited",
            "The biased verifier's reports are found out: who pays what, and who is told what.",
            audited,
            base,
        ),
        v(
            "never-audited",
            "One round, no audits",
            "Without audits nobody learns the true qualities and no verifier is penalized.",
            unaudited,
            vcm_script(unaudited, naive_project_buyer),
        ),
        v(
            "verifier-reports-truth",
            "The biased verifier reports the true qualities anyway",
            "A verifier is judged on what it reports, not on what it measured.",
            audited,
            with_(base, v1=[{"s2": 0.4, "s3": 0.3}]),
        ),
        v(
            "within-tolerance",
            "A report that is off by less than the tolerance",
            "An audited report 0.08 too high is not penalized (tolerance 0.1).",
            audited,
            with_(base, v0=[{"s0": 0.98, "s1": 0.88}], v1=[{"s2": 0.4, "s3": 0.3}]),
        ),
        v(
            "beyond-tolerance",
            "A report that is off by more than the tolerance",
            "An audited report 0.15 too high costs the verifier one penalty per project.",
            audited,
            with_(base, v0=[{"s0": 0.95, "s1": 0.95}], v1=[{"s2": 0.4, "s3": 0.3}]),
        ),
        v(
            "understated",
            "A verifier understates quality",
            "Is a report that is too low penalized like one that is too high?",
            audited,
            with_(base, v0=[{"s0": 0.6, "s1": 0.5}]),
        ),
        v(
            "no-trade-no-audit",
            "A seller asks more than anyone bids",
            "A project that does not trade is not audited, so its verifier cannot be penalized.",
            audited,
            with_(base, s2=[{"price": 40.0, "qty": 10}]),
        ),
        v(
            "buyer-stays-out",
            "A buyer sends no orders",
            "An empty order is allowed: the buyer trades nothing and earns nothing.",
            audited,
            with_(base, b0=[{}]),
        ),
        v(
            "seller-silent",
            "A seller does not act",
            "A participant who does not act stops the whole run.",
            audited,
            with_(base, s0=[None]),
        ),
        v(
            "reputation-shown",
            "Two rounds, buyers see the audit record and use it",
            "After round one the buyers bid less for the projects of the verifier that was caught.",
            two_rounds,
            vcm_script(two_rounds, skeptical_project_buyer),
        ),
        v(
            "reputation-hidden",
            "Two rounds, the audit record is not shown",
            "With the record hidden the same buyers cannot react to it.",
            hidden,
            vcm_script(hidden, skeptical_project_buyer),
        ),
        v(
            "audit-by-chance",
            "Audits happen with probability one half",
            "Only some traded projects are audited; the others stay unknown and unpenalized.",
            by_chance,
            vcm_script(by_chance, naive_project_buyer),
        ),
        v(
            "settle-on-true",
            "Buyers are credited on true quality",
            "With settle_on = true a buyer's payoff uses the true quality, not the report.",
            on_true,
            vcm_script(on_true, naive_project_buyer),
        ),
    ]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, cards in (("auction", auction_cards()), ("pd", pd_cards()), ("vcm", vcm_cards())):
        save_cards(OUT / f"{name}.json", cards)
        print(f"{name}: {len(cards)} proposed cards")


if __name__ == "__main__":
    main()
