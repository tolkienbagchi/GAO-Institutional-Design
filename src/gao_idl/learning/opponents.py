"""Opponent descriptions: ``"random"``, ``"scripted:<name>"`` and ``"table:<path>"``."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gao_idl.agents.base import AgentAdapter
from gao_idl.agents.random_agent import RandomAgent
from gao_idl.agents.scripted import Policy, ScriptedAgent
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.learning.codecs import act_codec_from_spec, obs_codec_from_spec
from gao_idl.learning.qlearning import TableAgent

OpponentFactory = Callable[[AgentId, str, int], AgentAdapter]
"""``factory(agent_id, description, episode_seed) -> adapter``, called per agent and episode."""


def derive_seed(episode_seed: int, agent_id: AgentId, purpose: str) -> int:
    """A 64-bit seed that is a pure function of its arguments (blake2b, not ``hash``)."""
    digest = hashlib.blake2b(f"{episode_seed}|{agent_id}|{purpose}".encode(), digest_size=8)
    return int.from_bytes(digest.digest(), "big")


def _always(move: str) -> Policy:
    return lambda request, reports: {"move": move}


def _tit_for_tat(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    return {"move": reports[-1].payload["other_move"] if reports else "C"}


def _truthful(request: DecisionRequest, reports: list[ParticipantReport]) -> dict[str, Any]:
    return {"amount": request.observation["value"]}


SCRIPTED: dict[str, Policy] = {
    "always_cooperate": _always("C"),
    "always_defect": _always("D"),
    "tit_for_tat": _tit_for_tat,
    "truthful_bidder": _truthful,
}
"""Named scripted policies: PD ``C`` / ``D`` / tit-for-tat, and a bidder that bids its value."""


def default_opponent_factory(
    scripted: dict[str, Policy] | None = None,
) -> OpponentFactory:
    """Factory for the three description forms (``scripted`` adds or replaces named policies).

    * ``"random"``: a ``RandomAgent`` seeded from ``derive_seed(episode_seed, agent, "random")``.
    * ``"scripted:<name>"``: a ``ScriptedAgent`` running ``SCRIPTED[name]``.
    * ``"table:<path>"``: a frozen ``TableAgent`` (``learning=False``) restored from the JSON
      checkpoint at ``path`` and seated at the opponent's agent id. Its codecs are rebuilt from
      the checkpoint, so only the built-in codec classes are supported.
    """
    policies = {**SCRIPTED, **(scripted or {})}
    checkpoints: dict[str, dict[str, Any]] = {}

    def build(agent_id: AgentId, description: str, episode_seed: int) -> AgentAdapter:
        kind, _, arg = description.partition(":")
        if description == "random":
            return RandomAgent(derive_seed(episode_seed, agent_id, "random"))
        if kind == "scripted" and arg in policies:
            return ScriptedAgent(policies[arg])
        if kind == "table" and arg:
            if arg not in checkpoints:
                checkpoints[arg] = json.loads(Path(arg).read_text())
            d = checkpoints[arg]
            return TableAgent.from_checkpoint(
                d,
                obs_codec_from_spec(d["obs_codec"]),
                act_codec_from_spec(d["act_codec"]),
                learning=False,
                agent_id=agent_id,
            )
        raise ValueError(
            f"unknown opponent {description!r} for {agent_id!r} "
            f"(expected 'random', 'scripted:<one of {sorted(policies)}>' or 'table:<path>')"
        )

    return build
