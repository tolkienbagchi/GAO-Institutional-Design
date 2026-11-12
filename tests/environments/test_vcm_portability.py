"""Carbon-market payoffs are bit-identical across Python versions.

Built-in ``sum`` over floats is compensated from Python 3.12 on, so the last bits of a float sum
differ between 3.11 and 3.12+. The carbon market uses ``math.fsum`` instead. This test pins the
exact bits of one run's payoffs; CI runs it on every supported Python, so a float ``sum`` creeping
back into a payoff shows up as a failure on one of them.
"""

from pathlib import Path

from gao_idl.authoring import load_cards, play
from gao_idl.core.ids import AgentId

ROOT = Path(__file__).resolve().parents[2]

EXACT = {
    "b0": "0x0.0p+0",
    "b1": "0x1.3333333333000p-4",
    "b2": "0x1.a999999999980p+1",
    "s0": "0x1.eb851eb851ec0p+1",
    "s1": "0x1.9333333333338p+1",
    "s2": "0x1.1c28f5c28f5c4p+3",
    "s3": "0x1.0f5c28f5c28f6p+3",
    "v0": "0x1.0000000000000p+1",
    "v1": "-0x1.0000000000000p+3",
}


def test_payoffs_have_the_same_bits_on_every_python() -> None:
    cards = {c.id: c for c in load_cards(ROOT / "examples" / "cards" / "vcm.json")}
    card = cards["buyer-stays-out"]  # the run whose last bits differed between 3.11 and 3.13
    session = play(card.env_ref, card.params, card.seed, card.script)
    got = {agent: session.ledger.utility(AgentId(agent)).hex() for agent in sorted(card.script)}
    assert got == EXACT
