"""The bundle: everything the review page shows, as one JSON document (``bundle_version`` 1).

The bundle is the only contract between the engine and a front end. ``make_bundle`` builds it
from a spec and its cards: the design, the flow picture, the who-knows-what table, every card
with its story, timeline and a hash of what it does now, and the result of the mechanical
checks. ``dump_bundle`` writes it with sorted keys and no timestamp, so the same inputs give
the same bytes.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict

from gao_idl._version import __version__
from gao_idl.authoring.cards import Card, CardExpect, Story, _card_agents, _expect, _story
from gao_idl.authoring.cards import same_outcome as _same_outcome
from gao_idl.authoring.check import Mismatch, Run, check_spec
from gao_idl.authoring.fuzz import edge_scripts
from gao_idl.authoring.invariants import check_invariants
from gao_idl.authoring.play import play
from gao_idl.authoring.render import KnowledgeTable, knowledge_table, to_outline
from gao_idl.authoring.spec import EnvSpec
from gao_idl.composition.contracts import describe
from gao_idl.core.errors import GaoError
from gao_idl.core.schemas import FrozenModel
from gao_idl.review.draw import flow_svg, timeline_svg
from gao_idl.runtime.registry import resolve

STATUSES = ("proposed", "confirmed", "rejected")


class _Strict(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckStatus(_Strict):
    """What the mechanical checks found.

    ``runs`` counts every run checked (one per card plus the fuzzed ones), ``fuzzed`` the
    fuzzed ones among them. ``mismatches`` are those of ``check_spec`` and of the spec's
    invariants (``not_exercised`` included: it is never a pass). ``unchecked_invariants`` holds
    the statements no predicate checks. ``cards`` counts cards by status (all three keys).
    """

    runs: int
    fuzzed: int
    mismatches: list[Mismatch]
    unchecked_invariants: list[str]
    assumptions_open: int
    cards: dict[str, int]


class CardView(_Strict):
    """A card as the page shows it: its story and timeline from a fresh run, and that run's hash.

    ``stale`` is true when the card has a recorded outcome that the fresh run no longer matches
    (``authoring.cards.same_outcome``).
    """

    card: Card
    story: Story
    timeline_svg: str
    outcome_hash: str
    stale: bool


class Bundle(_Strict):
    """Everything the review page shows. ``made_with`` is the ``gao_idl`` version; no timestamp."""

    bundle_version: Literal[1] = 1
    made_with: str
    spec: EnvSpec
    flow_svg: str
    outline: str
    table: KnowledgeTable
    cards: list[CardView]
    status: CheckStatus


def _canonical(value: Any) -> Any:
    """Floats rounded to 9 decimal places (``-0.0`` as ``0.0``), recursively."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        r = round(value, 9)
        return 0.0 if r == 0 else r
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    return value


def outcome_hash(expect: CardExpect) -> str:
    """SHA-256 (hex) of ``expect`` as canonical JSON, floats rounded to 9 places.

    Canonical: sorted keys, no whitespace, ASCII only. The rounding makes the hash agree across
    Python versions whose float sums differ in the last bits (see ``same_outcome``).
    """
    data = _canonical(expect.model_dump(mode="json"))
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def make_bundle(spec: EnvSpec, cards: Sequence[Card], fuzz: int = 0, seed: int = 0) -> Bundle:
    """Build the bundle of ``spec`` and its ``cards``, adding ``fuzz`` edge-biased runs.

    Every card is played once, now: its story, timeline and ``outcome_hash`` describe that run,
    whatever outcome it recorded. The who-knows-what table and the checks use the card runs and
    the fuzzed runs (``fuzz.edge_scripts(spec.env_ref, spec.params, seed, fuzz)``). A card for
    another environment, or one that cannot be played, raises ``ValueError``: the page shows
    only what runs.
    """
    views: list[CardView] = []
    sessions = []
    runs: list[Run] = []
    for card in cards:
        if card.env_ref != spec.env_ref:
            raise ValueError(
                f"card {card.id!r} is for {card.env_ref!r}, the spec for {spec.env_ref!r}"
            )
        try:
            session = play(card.env_ref, card.params, card.seed, card.script)
        except (GaoError, ValueError, RuntimeError) as e:
            raise ValueError(f"card {card.id!r} cannot be played: {type(e).__name__}: {e}") from e
        now = _expect(session, card.script)
        story, _ = _story(session, spec, _card_agents(session, card.script))
        views.append(
            CardView(
                card=card,
                story=story,
                timeline_svg=timeline_svg(story, card.title),
                outcome_hash=outcome_hash(now),
                stale=card.expect is not None and not _same_outcome(card.expect, now),
            )
        )
        sessions.append(session)
        runs.append(Run(params=card.params, seed=card.seed, script=card.script))
    scripts = edge_scripts(spec.env_ref, spec.params, seed, fuzz) if fuzz else []
    fuzzed = [Run(params=spec.params, seed=seed, script=s) for s in scripts]
    sessions += [play(spec.env_ref, r.params, r.seed, r.script) for r in fuzzed]
    runs += fuzzed
    found = check_spec(spec, runs) + check_invariants(spec, runs, secrets=False)
    process, _cfg = resolve(spec.env_ref)(**spec.params)
    tree = describe(process)
    status = CheckStatus(
        runs=len(runs),
        fuzzed=len(fuzzed),
        mismatches=[m for m in found if m.kind != "invariant_unchecked"],
        unchecked_invariants=[i.statement for i in spec.invariants if i.predicate is None],
        assumptions_open=sum(not a.confirmed for a in spec.assumptions),
        cards={s: sum(c.status == s for c in cards) for s in STATUSES},
    )
    return Bundle(
        made_with=__version__,
        spec=spec,
        flow_svg=flow_svg(tree),
        outline=to_outline(tree),
        table=knowledge_table(sessions),
        cards=views,
        status=status,
    )


def dump_bundle(bundle: Bundle) -> str:
    """The bundle as JSON text: sorted keys, one space of indent per level, trailing newline."""
    return json.dumps(bundle.model_dump(mode="json"), sort_keys=True, indent=1) + "\n"


def save_bundle(path: Path, bundle: Bundle) -> None:
    """Write ``dump_bundle(bundle)`` to ``path``."""
    Path(path).write_text(dump_bundle(bundle))


def load_bundle(path: Path) -> Bundle:
    """Read a bundle; refuses any ``bundle_version`` but 1 and unknown keys."""
    return Bundle.model_validate_json(Path(path).read_text())
