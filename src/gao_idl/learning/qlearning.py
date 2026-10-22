"""Tabular ε-greedy Q-learning as an ordinary ``AgentAdapter``.

.. warning:: Rewards come from *reports*, never from the ledger.

   ``TableAgent`` learns only from what the environment discloses to its agent: the decision
   requests and the ``ParticipantReport`` payloads. A ``RewardExtractor`` names the payload field(s)
   that carry reward (``own_payoff``, ``own_utility`` ...). If a layer never reports utility, the
   learner receives **zero reward from that layer**. That is correct behaviour and a disclosure
   decision of the environment designer, not a bug of the learner. (Utilities that were credited
   to the ledger but never reported are invisible to the agent, by design: invariant 1.)

Transition boundary
-------------------
A transition runs from one of the agent's decisions to its *next* decision, so
``(s_k, a_k, r_k, s_{k+1}, done)`` where

* ``s_k`` = the codec's encoding of request ``k`` and the reports received since decision ``k-1``;
* ``r_k`` = the sum of the reward fields of the reports received after decision ``k`` and before
  decision ``k+1`` (reports received before the first decision have no transition to attach to
  and are counted in ``dropped_reward``);
* at ``episode_end`` the last transition is ``(s_K, a_K, r_K, None, done=True)``; ``r_K`` also
  includes the rewards reported after the last decision.

``"normal"`` ends are terminal; ``"truncated"`` ends bootstrap from ``max_a Q(s_K, a)`` when
``QConfig.bootstrap_on_truncation`` (the agent never sees a state after its last decision, so the
last state itself is the only available proxy); ``"failed"`` episodes are discarded from learning
(updates already applied during the episode are rolled back) and counted in ``failed_episodes``.

Checkpoints are plain JSON-able dicts. Table keys are nested tuples of JSON scalars, written as
nested lists (``codecs.to_jsonable``) and read back as tuples (``codecs.from_jsonable``).
"""

import copy
import json
import math
from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from pydantic import Field, model_validator

from gao_idl import __version__
from gao_idl.agents.base import AgentAdapter, EpisodeContext, EpisodeEnd
from gao_idl.core.ids import AgentId, ReportId
from gao_idl.core.schemas import DecisionRequest, FrozenModel, ParticipantReport
from gao_idl.learning.codecs import (
    ActCodec,
    ObsCodec,
    codec_spec,
    from_jsonable,
    lookup,
    to_jsonable,
)

CHECKPOINT_FORMAT = "gao_idl.table_agent/1"

QKey = tuple[Hashable, Hashable]


class QConfig(FrozenModel):
    """Hyperparameters. ``epsilon`` decays once per learning episode: ``max(min, eps * decay)``."""

    alpha: float = Field(default=0.1, gt=0, le=1)
    gamma: float = Field(default=0.95, ge=0, le=1)
    epsilon: float = Field(default=0.1, ge=0, le=1)
    epsilon_decay: float = Field(default=1.0, gt=0, le=1)
    epsilon_min: float = Field(default=0.0, ge=0, le=1)
    bootstrap_on_truncation: bool = True
    discount_by_elapsed: bool = False

    @model_validator(mode="after")
    def _floor_below_start(self) -> "QConfig":
        if self.epsilon_min > self.epsilon:
            raise ValueError("epsilon_min must not exceed epsilon")
        return self


class RewardExtractor(FrozenModel):
    """Which report payload fields (dotted paths) carry reward; the learner sums them.

    A field absent from a report contributes 0 (reports of different layers disclose different
    things). A field that is present must be a finite number, else ``ValueError``.
    """

    fields: list[str]

    def extract(self, payload: dict[str, Any]) -> float:
        """The summed reward fields of one report payload (0.0 if none are present)."""
        total = 0.0
        for path in self.fields:
            try:
                value = lookup(payload, path)
            except KeyError:
                continue
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"reward field {path!r} is not a number: {value!r}")
            if not math.isfinite(value):
                raise ValueError(f"reward field {path!r} is not finite: {value!r}")
            total += float(value)
        return total


@dataclass(frozen=True)
class Transition:
    """One recorded transition. ``s_next is None`` only for the last one of an episode.

    ``elapsed`` is the number of reports received between the two decisions; ``terminal`` is
    ``"normal"`` / ``"truncated"`` for the last transition and ``None`` otherwise.
    """

    s: Hashable
    a: Hashable
    r: float
    s_next: Hashable | None
    done: bool
    elapsed: int
    terminal: Literal["normal", "truncated"] | None = None


@dataclass
class _Open:
    """The transition that started at the agent's latest decision and is not closed yet."""

    s: Hashable
    a: Hashable
    actions: list[Hashable]  # keys of the action set of that decision (for truncation bootstrap)
    reward: float = 0.0
    elapsed: int = 0


class TableAgent(AgentAdapter):
    """ε-greedy tabular Q-learner; sees only its own requests and reports (invariant 1).

    ``q`` maps ``(state key, action key)`` to a value (missing = 0). Each ``act`` draws one uniform
    number from the agent's own generator (plus one integer if it explores). With
    ``learning=False`` the table is never updated and ε is 0, so ``act`` is greedy; ties go to the
    first action of the codec's order. ``record_transitions`` keeps every transition of every
    non-failed episode in ``transitions``.
    """

    def __init__(
        self,
        agent_id: AgentId,
        obs_codec: ObsCodec,
        act_codec: ActCodec,
        reward: RewardExtractor,
        cfg: QConfig,
        seed: int,
        learning: bool = True,
        record_transitions: bool = False,
    ) -> None:
        self.agent_id = agent_id
        self.obs_codec = obs_codec
        self.act_codec = act_codec
        self.reward = reward
        self.cfg = cfg
        self.seed = seed
        self.learning = learning
        self.record_transitions = record_transitions
        self.q: dict[QKey, float] = {}
        self.epsilon = cfg.epsilon
        self.episodes_seen = 0  # completed (non-failed) episodes learned from
        self.failed_episodes = 0
        self.dropped_reward = 0.0  # reward reported before a first decision (no transition)
        self.transitions: list[Transition] = []
        self._rng = np.random.default_rng(seed)
        self._since_last: list[ParticipantReport] = []
        self._seen: set[ReportId] = set()
        self._open: _Open | None = None
        self._undo: list[tuple[QKey, float | None]] = []
        self._first_transition = 0

    # ------------------------------------------------------------ adapter protocol

    async def episode_start(self, ctx: EpisodeContext) -> None:
        """Clear the episode memory; keep ``q`` iff ``ctx.keep_parameters``."""
        self._check_agent(ctx.agent_id)
        if self._open is not None or self._undo:
            self._discard_episode()  # the previous episode never ended: treat it as failed
            self.failed_episodes += 1
        self._since_last, self._seen = [], set()
        self._first_transition = len(self.transitions)
        if not ctx.keep_parameters:
            self.q = {}

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        """Close the previous transition with this decision's state, then pick ε-greedily."""
        self._check_agent(request.agent_id)
        s = self.obs_codec.encode(request, list(self._since_last))
        actions = self.act_codec.actions(request)
        if not actions:
            raise ValueError(f"empty action set for {request.decision_id}")
        keys = [self.act_codec.encode(a) for a in actions]
        if len(set(keys)) != len(keys):
            raise ValueError(f"duplicate action keys for {request.decision_id}")
        self._close(s, keys)
        epsilon = self.epsilon if self.learning else 0.0
        explore = float(self._rng.random()) < epsilon
        index = int(self._rng.integers(len(keys))) if explore else self._greedy_index(s, keys)
        self._open = _Open(s=s, a=keys[index], actions=keys)
        self._since_last = []
        return copy.deepcopy(actions[index])

    async def observe(self, report: ParticipantReport) -> None:
        """Record a report addressed to this agent; its reward goes to the open transition."""
        self._check_agent(report.agent_id)
        if report.report_id in self._seen:
            return
        reward = self.reward.extract(report.payload)
        self._seen.add(report.report_id)
        self._since_last.append(report)
        if self._open is None:
            self.dropped_reward += reward
        else:
            self._open.reward += reward
            self._open.elapsed += 1

    async def episode_end(self, end: EpisodeEnd) -> None:
        """Close the last transition (terminal or bootstrapped), decay ε, or discard if failed."""
        self._check_agent(end.agent_id)
        if end.terminal == "failed":
            self._discard_episode()
            self.failed_episodes += 1
            return
        last = self._open
        if last is not None:
            terminal: Literal["normal", "truncated"] = end.terminal
            target = last.reward
            if terminal == "truncated" and self.cfg.bootstrap_on_truncation:
                best = max(self.q.get((last.s, a), 0.0) for a in last.actions)
                target += self._discount(last.elapsed) * best
            self._record(
                Transition(last.s, last.a, last.reward, None, True, last.elapsed, terminal)
            )
            self._update((last.s, last.a), target)
        if self.learning:
            self.episodes_seen += 1
            self.epsilon = max(self.cfg.epsilon_min, self.epsilon * self.cfg.epsilon_decay)
        self._clear_episode()

    # ------------------------------------------------------------ inspection

    def greedy_action(
        self, request: DecisionRequest, since_last: list[ParticipantReport] | None = None
    ) -> dict[str, Any]:
        """The greedy action for ``request``; touches no state (no RNG, no episode memory)."""
        s = self.obs_codec.encode(request, [] if since_last is None else list(since_last))
        actions = self.act_codec.actions(request)
        keys = [self.act_codec.encode(a) for a in actions]
        return copy.deepcopy(actions[self._greedy_index(s, keys)])

    # ------------------------------------------------------------ checkpoints

    def checkpoint(self) -> dict[str, Any]:
        """A JSON-able snapshot: table, config, RNG state, counters and codec identifiers."""
        entries = [[to_jsonable(s), to_jsonable(a), v] for (s, a), v in self.q.items()]
        entries.sort(key=lambda e: json.dumps(e[:2]))
        return {
            "format": CHECKPOINT_FORMAT,
            "agent_id": str(self.agent_id),
            "q": entries,
            "cfg": self.cfg.model_dump(mode="json"),
            "reward": self.reward.model_dump(mode="json"),
            "seed": self.seed,
            "rng_state": copy.deepcopy(self._rng.bit_generator.state),
            "epsilon": self.epsilon,
            "episodes_seen": self.episodes_seen,
            "failed_episodes": self.failed_episodes,
            "obs_codec": codec_spec(self.obs_codec),
            "act_codec": codec_spec(self.act_codec),
            "created_by": f"gao_idl {__version__}",
        }

    @classmethod
    def from_checkpoint(
        cls,
        d: dict[str, Any],
        obs_codec: ObsCodec,
        act_codec: ActCodec,
        *,
        learning: bool = True,
        record_transitions: bool = False,
        agent_id: AgentId | None = None,
    ) -> "TableAgent":
        """Rebuild an agent from ``checkpoint()`` output; ``d`` is not modified or aliased.

        The codecs must be the ones the checkpoint was made with (same class name and constructor
        arguments), else ``ValueError``. ``agent_id`` seats the restored table at another agent
        (the table does not depend on who played it); default: the checkpoint's own.
        """
        if d.get("format") != CHECKPOINT_FORMAT:
            raise ValueError(f"unknown checkpoint format {d.get('format')!r}")
        for field, codec in (("obs_codec", obs_codec), ("act_codec", act_codec)):
            wanted, given = d[field], json.loads(json.dumps(codec_spec(codec)))
            if wanted != given:
                raise ValueError(f"{field} mismatch: checkpoint has {wanted!r}, given {given!r}")
        agent = cls(
            AgentId(d["agent_id"]) if agent_id is None else agent_id,
            obs_codec,
            act_codec,
            RewardExtractor.model_validate(d["reward"]),
            QConfig.model_validate(d["cfg"]),
            int(d["seed"]),
            learning=learning,
            record_transitions=record_transitions,
        )
        for s, a, value in d["q"]:
            key = (from_jsonable(s), from_jsonable(a))
            if key in agent.q:
                raise ValueError(f"duplicate table entry {key!r} in checkpoint")
            agent.q[key] = float(value)
        agent._rng.bit_generator.state = copy.deepcopy(d["rng_state"])
        agent.epsilon = float(d["epsilon"])
        agent.episodes_seen = int(d["episodes_seen"])
        agent.failed_episodes = int(d["failed_episodes"])
        return agent

    # ------------------------------------------------------------ internals

    def _check_agent(self, agent_id: AgentId) -> None:
        if agent_id != self.agent_id:
            raise ValueError(f"{agent_id!r} is not this learner ({self.agent_id!r})")

    def _discount(self, elapsed: int) -> float:
        return self.cfg.gamma**elapsed if self.cfg.discount_by_elapsed else self.cfg.gamma

    def _greedy_index(self, s: Hashable, keys: list[Hashable]) -> int:
        values = [self.q.get((s, a), 0.0) for a in keys]
        return values.index(max(values))  # the first maximum: deterministic tie-break

    def _close(self, s_next: Hashable, next_actions: list[Hashable]) -> None:
        """Close the open transition with a non-terminal next state and update ``q``."""
        last = self._open
        if last is None:
            return
        best = max(self.q.get((s_next, a), 0.0) for a in next_actions)
        target = last.reward + self._discount(last.elapsed) * best
        self._record(Transition(last.s, last.a, last.reward, s_next, False, last.elapsed))
        self._update((last.s, last.a), target)
        self._open = None

    def _update(self, key: QKey, target: float) -> None:
        if not self.learning:
            return
        old = self.q.get(key)
        self._undo.append((key, old))
        base = 0.0 if old is None else old
        self.q[key] = base + self.cfg.alpha * (target - base)

    def _record(self, transition: Transition) -> None:
        if self.record_transitions:
            self.transitions.append(transition)

    def _clear_episode(self) -> None:
        self._open, self._undo, self._since_last = None, [], []
        self._first_transition = len(self.transitions)

    def _discard_episode(self) -> None:
        """Undo this episode's table updates and recorded transitions; forget its memory."""
        for key, old in reversed(self._undo):
            if old is None:
                del self.q[key]
            else:
                self.q[key] = old
        del self.transitions[self._first_transition :]
        self._clear_episode()
