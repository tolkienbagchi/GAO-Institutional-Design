"""The two layers of the repeated PD: one pair's round, and the automatic round counter."""

from numpy.random import Generator
from pydantic import BaseModel

from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config, Exit
from gao_idl.core.store import StoreReadDenied, StoreView
from gao_idl.environments.pd.schemas import (
    PDAction,
    PDObs,
    PDOutcome,
    PDReport,
    PDRoundExit,
    PDState,
    RoundCounterState,
    RoundSummary,
)

ROW, COL = RoleId("row"), RoleId("col")
DONE = Port("done")
AGAIN, FINISHED = Port("again"), Port("finished")
HISTORY_SCOPE = frozenset({"hist:*"})


def read_history(store: StoreView, key: str) -> list[dict[str, str]]:
    """The stored history at ``key``; ``[]`` if the pair has never played (unset in-scope key)."""
    try:
        value: list[dict[str, str]] = store.get(key)
    except StoreReadDenied:
        raise
    except KeyError:
        return []
    return value


def _other(role: RoleId) -> RoleId:
    return COL if role == ROW else ROW


def _observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
    state = PDState.model_validate(cfg.state)
    me, other = cfg.roles[role], cfg.roles[_other(role)]
    return PDObs(
        round_index=state.round_index,
        own_history=[(rec[me], rec[other]) for rec in state.history],
    )


def _action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
    return PDAction


def _outcome(
    cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
) -> BaseModel:
    state = PDState.model_validate(cfg.state)
    moves: dict[RoleId, str] = {}
    for role in (ROW, COL):
        action = actions[role]
        if not isinstance(action, PDAction):
            raise TypeError(f"expected PDAction for role {role!r}, got {type(action).__name__}")
        moves[role] = action.move
    row_pay, col_pay = state.payoffs[moves[ROW] + moves[COL]]
    row_agent, col_agent = cfg.roles[ROW], cfg.roles[COL]
    entry = {row_agent: moves[ROW], col_agent: moves[COL]}
    store.set(state.history_key, [*read_history(store, state.history_key), entry])
    return PDOutcome(moves=moves, payoffs={row_agent: row_pay, col_agent: col_pay})


def _outcome_model(outcome: BaseModel) -> PDOutcome:
    if not isinstance(outcome, PDOutcome):
        raise TypeError(f"expected PDOutcome, got {type(outcome).__name__}")
    return outcome


def _utilities(cfg: Config, outcome: BaseModel) -> dict[AgentId, float]:
    return dict(_outcome_model(outcome).payoffs)


def _export(cfg: Config, outcome: BaseModel) -> Exit:
    out = _outcome_model(outcome)
    state = PDState.model_validate(cfg.state)
    record = PDRoundExit(
        pair_id=state.pair_id,
        moves={cfg.roles[role]: move for role, move in out.moves.items()},
        round_index=state.round_index,
    )
    return Exit(port=DONE, record=record.model_dump(mode="json"))


def _report(
    cfg: Config, outcome: BaseModel, recipient: AgentId, rng: Generator
) -> BaseModel | None:
    out = _outcome_model(outcome)
    state = PDState.model_validate(cfg.state)
    mine = [role for role, agent in cfg.roles.items() if agent == recipient]
    if len(mine) != 1:
        return None
    return PDReport(
        round_index=state.round_index,
        own_move=out.moves[mine[0]],
        other_move=out.moves[_other(mine[0])],
        own_payoff=out.payoffs[recipient],
    )


def pd_round_layer() -> SimpleLayer:
    """One pair's round: roles ``row`` and ``col``, history in the store under ``hist:{pair}``.

    The report discloses only this pair's round to its two members. Timeout policy: Fail.
    """
    return SimpleLayer(
        name="pd_round",
        entry=Contract(
            state_schema=PDState,
            roles={ROW: {}, COL: {}},
            store_reads=HISTORY_SCOPE,
            store_writes=HISTORY_SCOPE,
        ),
        exits={DONE: PDRoundExit},
        observe=_observe,
        action_schema=_action_schema,
        outcome=_outcome,
        utilities=_utilities,
        export=_export,
        report=_report,
    )


# ------------------------------------------------------------------ round counter


def _counter_outcome(
    cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
) -> BaseModel:
    state = RoundCounterState.model_validate(cfg.state)
    return RoundSummary(round_index=state.round_index, rounds=state.rounds, pairs=state.pairs)


def _counter_export(cfg: Config, outcome: BaseModel) -> Exit:
    if not isinstance(outcome, RoundSummary):
        raise TypeError(f"expected RoundSummary, got {type(outcome).__name__}")
    done = outcome.round_index + 1
    if done > outcome.rounds:
        raise ContractError(f"round {outcome.round_index} is beyond rounds={outcome.rounds}")
    port = FINISHED if done == outcome.rounds else AGAIN
    return Exit(port=port, record=outcome.model_dump(mode="json"))


def round_counter_layer() -> SimpleLayer:
    """Automatic layer after the Par: exits ``finished`` after the last round, else ``again``."""
    exits: dict[Port, type[BaseModel]] = {AGAIN: RoundSummary, FINISHED: RoundSummary}
    return SimpleLayer(
        name="round_counter",
        entry=Contract(state_schema=RoundCounterState),
        exits=exits,
        outcome=_counter_outcome,
        export=_counter_export,
    )
