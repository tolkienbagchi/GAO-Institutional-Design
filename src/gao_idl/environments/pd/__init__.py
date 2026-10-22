"""Repeated prisoner's dilemma: ``Loop(Seq(Par(pair rounds), round counter))``."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from gao_idl.composition.connector import Connector, LedgerReadOnly
from gao_idl.composition.loop import Loop
from gao_idl.composition.par import DictJoiner, DisjointSplitter, Par
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.composition.seq import Seq
from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port
from gao_idl.core.schemas import Config
from gao_idl.core.store import StoreView
from gao_idl.environments.pd.layers import (
    AGAIN,
    COL,
    FINISHED,
    ROW,
    pd_round_layer,
    read_history,
    round_counter_layer,
)
from gao_idl.environments.pd.matching import check_agents, pair_id, pairs_for_round
from gao_idl.environments.pd.schemas import (
    PAYOFF_KEYS,
    STANDARD_PAYOFFS,
    PDRoundInput,
    PDState,
    RoundCounterState,
    RoundJoin,
    RoundSummary,
)

__all__ = ["STANDARD_PAYOFFS", "build_repeated_pd", "pair_id", "pairs_for_round"]

ROUND_DONE = Port("round_done")
DONE = Port("done")


def _payoffs(payoffs: Mapping[str, Sequence[float]]) -> dict[str, tuple[float, float]]:
    if set(payoffs) != PAYOFF_KEYS or any(len(v) != 2 for v in payoffs.values()):
        raise ContractError(f"payoffs must map exactly {sorted(PAYOFF_KEYS)} to (row, col) pairs")
    return {k: (float(v[0]), float(v[1])) for k, v in payoffs.items()}


def build_repeated_pd(
    agents: list[AgentId],
    rounds: int,
    matching: Literal["fixed", "round_robin"] = "fixed",
    payoffs: Mapping[str, Sequence[float]] = STANDARD_PAYOFFS,
) -> tuple[Process, Config]:
    """Environment factory for the repeated PD; the root process is a ``Loop`` named ``pd``.

    Each iteration is one round: a ``Par`` with one ``pd_round`` branch per pair (``pair_k``),
    joined and handed to the automatic ``round_counter``, which exits ``finished`` after the last
    round (the loop exits through ``done``; truncation never happens) and ``again`` otherwise.
    Histories are private to the pair and live in the store under ``hist:{pair_id}``.
    """
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ContractError(f"rounds must be an int >= 1, got {rounds!r}")
    names = [str(a) for a in agents]
    check_agents(names)
    pairs_for_round(names, matching, 0)  # validates `matching`
    table = _payoffs(payoffs)

    def split(cfg: Config) -> dict[str, Config]:
        inp = PDRoundInput.model_validate(cfg.state)
        out: dict[str, Config] = {}
        for k, (row, col) in enumerate(pairs_for_round(names, matching, inp.round_index)):
            pid = pair_id(row, col)
            state = PDState(
                payoffs=table,
                pair_id=pid,
                round_index=inp.round_index,
                history_key=f"hist:{pid}",
                history=inp.histories.get(pid, []),
            )
            out[f"pair_{k}"] = Config(
                state=state.model_dump(mode="json"),
                roles={ROW: AgentId(row), COL: AgentId(col)},
            )
        return out

    def to_counter(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        joined = RoundJoin.model_validate(rec).root
        bad = {name: b.port for name, b in joined.items() if b.port != "done"}
        if bad:  # a pair did not finish its round normally: no round summary can be built
            raise ContractError(f"pairs did not exit through 'done': {bad}")
        indices = {b.record.round_index for b in joined.values()}
        if len(indices) != 1:
            raise ContractError(f"pairs disagree on the round index: {sorted(indices)}")
        state = RoundCounterState(
            round_index=indices.pop(),
            rounds=rounds,
            pairs={b.record.pair_id: dict(b.record.moves) for b in joined.values()},
        )
        return Config(state=state.model_dump(mode="json"), roles={})

    def next_round(rec: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        nxt = RoundSummary.model_validate(rec).round_index + 1
        histories: dict[str, list[dict[str, str]]] = {}
        for row, col in pairs_for_round(names, matching, nxt):
            pid = pair_id(row, col)
            if past := read_history(store, f"hist:{pid}"):
                histories[pid] = past
        state = PDRoundInput(round_index=nxt, histories=histories)
        return Config(state=state.model_dump(mode="json"), roles={})

    par = Par(
        {f"pair_{k}": Primitive(pd_round_layer()) for k in range(len(names) // 2)},
        DisjointSplitter(split, PDRoundInput),
        DictJoiner(ROUND_DONE, RoundJoin),
        name="round",
    )
    body = Seq(
        par,
        {ROUND_DONE: (Connector(to_counter, RoundCounterState), Primitive(round_counter_layer()))},
        name="round_seq",
    )
    feedback = Connector(next_round, PDRoundInput, store_reads=frozenset({"hist:*"}))
    loop = Loop(
        body,
        {AGAIN: feedback},
        {FINISHED: DONE},
        max_iter=rounds + 1,
        on_limit="fail",
        name="pd",
    )
    first = PDRoundInput(round_index=0)
    return loop, Config(state=first.model_dump(mode="json"), roles={})
