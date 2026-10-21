import pytest
from pydantic import BaseModel

from gao_idl.core.errors import ContractError
from gao_idl.core.ids import AgentId, Port, RoleId
from gao_idl.core.layer import Contract, Empty, SimpleLayer, validate_entry
from gao_idl.core.schemas import Config, Exit
from gao_idl.core.timeout import DefaultAction, Fail


class State(BaseModel):
    n: int
    label: str = "x"


def make(roles: dict[RoleId, dict[str, object]] | None = None, **kw: object) -> SimpleLayer:
    return SimpleLayer(
        name="L",
        entry=Contract(state_schema=State, roles=roles or {}),
        exits={Port("done"): Empty},
        **kw,  # type: ignore[arg-type]
    )


def test_validate_entry_normalizes_state() -> None:
    cfg = Config(state={"n": 3}, roles={RoleId("a"): AgentId("alice")})
    out = validate_entry(make(), cfg)
    assert out.state == {"n": 3, "label": "x"}  # defaults filled, JSON-normalized
    assert out.roles == cfg.roles


def test_validate_entry_bad_state() -> None:
    with pytest.raises(ContractError):
        validate_entry(make(), Config(state={"n": "not an int"}, roles={}))
    with pytest.raises(ContractError):
        validate_entry(make(), Config(state={}, roles={}))


def test_validate_entry_unassigned_role() -> None:
    layer = make(roles={RoleId("a"): {}, RoleId("b"): {}})
    cfg = Config(state={"n": 1}, roles={RoleId("a"): AgentId("alice")})
    with pytest.raises(ContractError, match="b"):
        validate_entry(layer, cfg)


def test_simple_layer_defaults() -> None:
    layer = make()
    cfg = Config(state={"n": 1}, roles={RoleId("a"): AgentId("alice"), RoleId("b"): AgentId("bob")})
    assert isinstance(layer.timeout, Fail)
    assert layer.feasible(cfg, RoleId("a"), Empty()) is True
    assert layer.utilities(cfg, Empty()) == {}
    assert layer.audience(cfg, Empty()) == {AgentId("alice"), AgentId("bob")}
    assert layer.report(cfg, Empty(), AgentId("alice"), None) is None  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        layer.default_action(cfg, RoleId("a"))
    with pytest.raises(NotImplementedError):
        layer.action_schema(cfg, RoleId("a"))


def test_default_export_uses_only_exit() -> None:
    layer = make()
    exit_ = layer.export(Config(state={"n": 1}, roles={}), Empty())
    assert exit_ == Exit(port=Port("done"), record={})


def test_export_required_with_multiple_exits() -> None:
    with pytest.raises(ContractError):
        SimpleLayer(
            name="L",
            entry=Contract(state_schema=State),
            exits={Port("a"): Empty, Port("b"): Empty},
        )


def test_overridden_hooks_are_used() -> None:
    layer = make(timeout=DefaultAction(), feasible=lambda cfg, role, action: False)
    assert isinstance(layer.timeout, DefaultAction)
    assert layer.feasible(Config(state={}, roles={}), RoleId("a"), Empty()) is False
