"""Trainer and evaluator: train a ``TableAgent`` online, evaluate a frozen copy of its checkpoint.

Returns are the learner's *true* total utility, read from the session ledger by the trainer after
each episode. The learner never sees this number (the runner does not disclose final utility); it
learns only from disclosed report fields. Training and evaluation never share a ``TableAgent``:
``evaluate`` takes a checkpoint dict and builds a new frozen agent from it.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import asyncio
import weakref
from collections.abc import Iterable
from typing import Any, Literal, Self

import numpy as np
from pydantic import Field, model_validator

from gao_idl.agents.base import AgentAdapter, EpisodeContext, EpisodeEnd
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import DecisionRequest, FrozenModel, ParticipantReport
from gao_idl.learning.codecs import ActCodec, ObsCodec, act_codec_from_spec, obs_codec_from_spec
from gao_idl.learning.opponents import OpponentFactory, default_opponent_factory
from gao_idl.learning.qlearning import TableAgent
from gao_idl.runtime.runner import TRUNCATED_PORT, run
from gao_idl.runtime.session import Session

SESSION_SEED_STRIDE = 1_000_003
Terminal = Literal["normal", "truncated", "failed"]

_TRAINEES: "weakref.WeakSet[TableAgent]" = weakref.WeakSet()
_EVALUATORS: "weakref.WeakSet[TableAgent]" = weakref.WeakSet()


class TrainSpec(FrozenModel):
    """What to train: the environment, who learns, against whom, for how long.

    ``opponents`` maps every other agent of the environment to ``"random"``,
    ``"scripted:<name>"`` or ``"table:<path>"`` (see ``learning.opponents``).
    """

    env_ref: str
    params: dict[str, Any]
    learner: AgentId
    episodes: int = Field(ge=1)
    seed: int
    opponents: dict[AgentId, str]

    @model_validator(mode="after")
    def _learner_is_not_an_opponent(self) -> Self:
        if self.learner in self.opponents:
            raise ValueError(f"the learner {self.learner!r} cannot also be an opponent")
        return self


class TrainResult(FrozenModel):
    """Per-episode true return of the learner, how each episode ended, and the final checkpoint."""

    spec: TrainSpec
    returns: list[float]
    terminals: list[Terminal]
    failed_episodes: int
    checkpoint: dict[str, Any]


class EvalResult(FrozenModel):
    """Evaluation of a frozen checkpoint: per-episode returns and their mean / population std.

    ``mean`` and ``std`` are over the episodes that did not fail (``None`` if all failed);
    a failed episode's entry in ``returns`` is the utility accrued before it failed.
    """

    spec: TrainSpec
    episodes: int
    seed: int
    returns: list[float]
    terminals: list[Terminal]
    failed_episodes: int
    mean: float | None
    std: float | None


def episode_seed(seed: int, episode: int) -> int:
    """Session seed of episode ``episode``: ``seed * 1_000_003 + episode``."""
    return seed * SESSION_SEED_STRIDE + episode


class LearnerError(RuntimeError):
    """The learner raised inside ``act``. The runner would time the decision out and go on."""


class _Pinned:
    """Wraps the learner: pins ``keep_parameters=True`` and keeps ``act`` errors from vanishing.

    The runner turns an exception in ``act`` into a timeout; with a misconfigured codec that would
    make every episode fail quietly. The wrapper records the error so the trainer can raise it.
    """

    def __init__(self, inner: TableAgent) -> None:
        self.inner = inner
        self.errors: list[Exception] = []

    async def episode_start(self, ctx: EpisodeContext) -> None:
        await self.inner.episode_start(ctx.model_copy(update={"keep_parameters": True}))

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        try:
            return await self.inner.act(request)
        except Exception as e:
            self.errors.append(e)
            raise

    async def observe(self, report: ParticipantReport) -> None:
        await self.inner.observe(report)

    async def episode_end(self, end: EpisodeEnd) -> None:
        await self.inner.episode_end(end)


def _terminal(session: Session) -> Terminal:
    if session.failed:
        return "failed"
    if session.result is not None and session.result.port == TRUNCATED_PORT:
        return "truncated"
    return "normal"


async def _play(
    spec: TrainSpec,
    learner: TableAgent,
    factory: OpponentFactory,
    seeds: Iterable[int],
) -> tuple[list[float], list[Terminal]]:
    """Run one session per seed with ``learner`` against fresh opponents; collect true returns."""
    pinned = _Pinned(learner)
    returns: list[float] = []
    terminals: list[Terminal] = []
    for seed in seeds:
        session = Session.start(spec.env_ref, spec.params, seed)
        adapters: dict[AgentId, AgentAdapter] = {spec.learner: pinned}
        for agent in sorted(spec.opponents):
            adapters[agent] = factory(agent, spec.opponents[agent], seed)
        await run(session, adapters)
        if pinned.errors:
            raise LearnerError(
                f"the learner failed in act (session seed {seed})"
            ) from pinned.errors[0]
        returns.append(session.ledger.utility(spec.learner))
        terminals.append(_terminal(session))
    return returns, terminals


def train(
    spec: TrainSpec, learner: TableAgent, opponent_factory: OpponentFactory | None = None
) -> TrainResult:
    """Play ``spec.episodes`` sessions with ``learner`` learning online; return what happened.

    Episode ``e`` (0-based) uses session seed ``spec.seed * 1_000_003 + e``; opponents are built
    by ``opponent_factory`` (default: ``default_opponent_factory()``) with that seed, so a rerun
    with a fresh learner of the same seed reproduces the run. The learner is updated in place and
    its final checkpoint is in the result. Not callable from inside a running event loop.
    """
    if learner.agent_id != spec.learner:
        raise ValueError(f"spec.learner is {spec.learner!r} but the agent is {learner.agent_id!r}")
    if not learner.learning:
        raise ValueError("train needs a learning TableAgent (learning=True)")
    if learner in _EVALUATORS:
        raise AssertionError("this TableAgent was used for evaluation; training needs its own")
    _TRAINEES.add(learner)
    factory = default_opponent_factory() if opponent_factory is None else opponent_factory
    seeds = [episode_seed(spec.seed, e) for e in range(spec.episodes)]
    returns, terminals = asyncio.run(_play(spec, learner, factory, seeds))
    return TrainResult(
        spec=spec,
        returns=returns,
        terminals=terminals,
        failed_episodes=terminals.count("failed"),
        checkpoint=learner.checkpoint(),
    )


def evaluate(
    spec: TrainSpec,
    learner_ckpt: dict[str, Any],
    opponent_factory: OpponentFactory | None,
    episodes: int,
    seed: int,
    *,
    obs_codec: ObsCodec | None = None,
    act_codec: ActCodec | None = None,
) -> EvalResult:
    """Play ``episodes`` sessions with a frozen, greedy copy of the checkpoint (ε = 0, no updates).

    Episode ``e`` uses session seed ``seed * 1_000_003 + e``. The checkpoint dict is not modified
    and no learner of ``train`` is touched: a new ``TableAgent`` is built from the dict, seated at
    ``spec.learner``. Codecs are rebuilt from the checkpoint unless given (needed for custom
    codec classes). ``spec.episodes`` and ``spec.seed`` are the training values and are ignored.
    """
    if episodes < 1:
        raise ValueError("evaluate needs at least one episode")
    agent = TableAgent.from_checkpoint(
        learner_ckpt,
        obs_codec if obs_codec is not None else obs_codec_from_spec(learner_ckpt["obs_codec"]),
        act_codec if act_codec is not None else act_codec_from_spec(learner_ckpt["act_codec"]),
        learning=False,
        agent_id=spec.learner,
    )
    if agent.learning or agent in _TRAINEES:
        raise AssertionError("an evaluation agent must be new and frozen")
    _EVALUATORS.add(agent)
    factory = default_opponent_factory() if opponent_factory is None else opponent_factory
    seeds = [episode_seed(seed, e) for e in range(episodes)]
    returns, terminals = asyncio.run(_play(spec, agent, factory, seeds))
    played = [r for r, t in zip(returns, terminals, strict=True) if t != "failed"]
    return EvalResult(
        spec=spec,
        episodes=episodes,
        seed=seed,
        returns=returns,
        terminals=terminals,
        failed_episodes=terminals.count("failed"),
        mean=float(np.mean(played)) if played else None,
        std=float(np.std(played)) if played else None,
    )
