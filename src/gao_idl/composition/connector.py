"""Connectors: pure functions from a process's exit record to the next process's entry Config."""

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from gao_idl.composition.process import Ctx
from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId
from gao_idl.core.ledger import Information, Ledger
from gao_idl.core.schemas import Config, UtilityIncrement
from gao_idl.core.store import StoreView


class LedgerReadOnly(Protocol):
    """The part of the ledger a connector may read."""

    def utility(self, agent_id: AgentId) -> float:
        """Total utility credited to ``agent_id``."""
        ...

    def increments(self, agent_id: AgentId) -> list[UtilityIncrement]:
        """Increments credited to ``agent_id``."""
        ...

    def information(self, agent_id: AgentId) -> list[Information]:
        """Everything delivered to ``agent_id``."""
        ...


class ReadOnlyLedger:
    """Wraps a Ledger so connectors can read it but cannot credit utility or deliver information."""

    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger

    def utility(self, agent_id: AgentId) -> float:
        """Total utility credited to ``agent_id``."""
        return self._ledger.utility(agent_id)

    def increments(self, agent_id: AgentId) -> list[UtilityIncrement]:
        """Increments credited to ``agent_id`` (a copy)."""
        return self._ledger.increments(agent_id)

    def information(self, agent_id: AgentId) -> list[Information]:
        """Everything delivered to ``agent_id`` (a copy)."""
        return self._ledger.information(agent_id)


@dataclass(frozen=True)
class Connector:
    """``fn(exit_record, ledger, store) -> Config`` plus the contract it must satisfy.

    ``output_schema`` must be the entry ``state_schema`` of the process that receives the Config.
    Connectors never emit participant information: they get a read-only ledger and no trace sink.
    """

    fn: Callable[[dict[str, Any], LedgerReadOnly, StoreView], Config]
    output_schema: type[BaseModel]
    store_reads: frozenset[str] = frozenset()
    store_writes: frozenset[str] = frozenset()

    def __call__(self, record: dict[str, Any], ledger: LedgerReadOnly, store: StoreView) -> Config:
        """Run ``fn`` (no validation; see :meth:`apply`)."""
        return self.fn(record, ledger, store)

    def apply(self, record: dict[str, Any], ctx: Ctx) -> Config:
        """Run the connector in ``ctx`` and check its output against ``output_schema``.

        The record is deep-copied, the store view is limited to the declared scopes, and a Config
        whose state does not parse as ``output_schema`` raises ContractError.
        """
        view = ctx.store.view(self.store_reads, self.store_writes, ctx.trace, ctx.path)
        cfg = self.fn(copy.deepcopy(record), ReadOnlyLedger(ctx.ledger), view)
        if not isinstance(cfg, Config):
            raise ContractError(f"connector returned {type(cfg).__name__}, not Config")
        try:
            self.output_schema.model_validate(cfg.state)
        except ValidationError as e:
            raise ContractError(
                f"connector output violates {self.output_schema.__name__}: {e}"
            ) from e
        return cfg
