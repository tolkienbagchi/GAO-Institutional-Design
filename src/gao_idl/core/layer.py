"""The Layer protocol (one simultaneous decision round), its contract, and SimpleLayer."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from numpy.random import Generator
from pydantic import BaseModel, ValidationError

from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.schemas import Config, Exit, FrozenModel
from gao_idl.core.store import StoreView
from gao_idl.core.timeout import Fail, TimeoutPolicy


class Contract(FrozenModel):
    """What a layer requires on entry: state schema, roles, and store scope."""

    state_schema: type[BaseModel]
    roles: dict[RoleId, dict[str, Any]] = {}  # role -> constraints (opaque in M0)
    store_reads: frozenset[str] = frozenset()
    store_writes: frozenset[str] = frozenset()


class Layer(Protocol):
    """One simultaneous decision round. All hooks must be pure given their arguments and rng."""

    @property
    def name(self) -> str: ...
    @property
    def entry(self) -> Contract: ...
    @property
    def exits(self) -> dict[Port, type[BaseModel]]: ...
    @property
    def timeout(self) -> TimeoutPolicy: ...

    def observe(self, cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
        """What the role's agent sees before acting."""
        ...

    def action_schema(self, cfg: Config, role: RoleId) -> type[BaseModel]:
        """Pydantic model that the role's action must parse into."""
        ...

    def feasible(self, cfg: Config, role: RoleId, action: BaseModel) -> bool:
        """Whether a parsed action is allowed."""
        ...

    def default_action(self, cfg: Config, role: RoleId) -> BaseModel:
        """Action substituted on timeout; only used if ``timeout`` is ``DefaultAction``."""
        ...

    def outcome(
        self, cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
    ) -> BaseModel:
        """Resolve the round from all accepted actions."""
        ...

    def utilities(self, cfg: Config, outcome: BaseModel) -> dict[AgentId, float]:
        """Utility credited to each agent for this outcome."""
        ...

    def export(self, cfg: Config, outcome: BaseModel) -> Exit:
        """The port and record this layer exits with."""
        ...

    def audience(self, cfg: Config, outcome: BaseModel) -> set[AgentId]:
        """Agents who may receive a report; default is every agent holding a role."""
        ...

    def report(
        self, cfg: Config, outcome: BaseModel, recipient: AgentId, rng: Generator
    ) -> BaseModel | None:
        """Report payload for ``recipient``, or None for no report."""
        ...


class Empty(BaseModel):
    """A model with no fields."""


def _default_observe(cfg: Config, role: RoleId, rng: Generator) -> BaseModel:
    return Empty()


def _default_action_schema(cfg: Config, role: RoleId) -> type[BaseModel]:
    raise NotImplementedError("SimpleLayer with roles needs an action_schema hook")


def _default_feasible(cfg: Config, role: RoleId, action: BaseModel) -> bool:
    return True


def _default_default_action(cfg: Config, role: RoleId) -> BaseModel:
    raise NotImplementedError("DefaultAction timeout needs a default_action hook")


def _default_outcome(
    cfg: Config, actions: dict[RoleId, BaseModel], rng: Generator, store: StoreView
) -> BaseModel:
    return Empty()


def _default_utilities(cfg: Config, outcome: BaseModel) -> dict[AgentId, float]:
    return {}


def _unset_export(cfg: Config, outcome: BaseModel) -> Exit:
    raise NotImplementedError("unreachable: replaced in SimpleLayer.__post_init__")


def _default_audience(cfg: Config, outcome: BaseModel) -> set[AgentId]:
    return set(cfg.roles.values())


def _default_report(
    cfg: Config, outcome: BaseModel, recipient: AgentId, rng: Generator
) -> BaseModel | None:
    return None


@dataclass(frozen=True)
class SimpleLayer:
    """A Layer whose every hook is a constructor argument with a sensible default.

    The default ``export`` returns an empty record through the layer's only exit port.
    """

    name: str
    entry: Contract
    exits: dict[Port, type[BaseModel]]
    timeout: TimeoutPolicy = field(default_factory=Fail)
    observe: Callable[[Config, RoleId, Generator], BaseModel] = _default_observe
    action_schema: Callable[[Config, RoleId], type[BaseModel]] = _default_action_schema
    feasible: Callable[[Config, RoleId, BaseModel], bool] = _default_feasible
    default_action: Callable[[Config, RoleId], BaseModel] = _default_default_action
    outcome: Callable[[Config, dict[RoleId, BaseModel], Generator, StoreView], BaseModel] = (
        _default_outcome
    )
    utilities: Callable[[Config, BaseModel], dict[AgentId, float]] = _default_utilities
    export: Callable[[Config, BaseModel], Exit] = _unset_export
    audience: Callable[[Config, BaseModel], set[AgentId]] = _default_audience
    report: Callable[[Config, BaseModel, AgentId, Generator], BaseModel | None] = _default_report

    def __post_init__(self) -> None:
        if self.export is _unset_export:
            ports = list(self.exits)
            if len(ports) != 1:
                raise ContractError(
                    f"layer {self.name!r}: export hook required unless there is exactly one exit"
                )
            only = ports[0]
            object.__setattr__(self, "export", lambda cfg, outcome: Exit(port=only, record={}))


def validate_entry(layer: Layer, cfg: Config) -> Config:
    """Check ``cfg`` against the layer's entry contract; return it with state normalized to JSON."""
    try:
        parsed = layer.entry.state_schema.model_validate(cfg.state)
    except ValidationError as e:
        raise ContractError(f"layer {layer.name!r}: invalid entry state: {e}") from e
    missing = sorted(r for r in layer.entry.roles if r not in cfg.roles)
    if missing:
        raise ContractError(f"layer {layer.name!r}: unassigned roles {missing}")
    return Config(state=parsed.model_dump(mode="json"), roles=dict(cfg.roles))
