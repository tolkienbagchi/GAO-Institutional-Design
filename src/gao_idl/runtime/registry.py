"""Resolve ``"module.path:factory_name"`` references to environment factories."""

import importlib
from collections.abc import Callable

from gao_idl.composition.process import Process
from gao_idl.core.schemas import Config


def resolve(env_ref: str) -> Callable[..., tuple[Process, Config]]:
    """Import the factory named by ``env_ref``; it maps params to ``(process, cfg)``."""
    module_path, sep, attr = env_ref.partition(":")
    if not sep or not module_path or not attr:
        raise ValueError(f"env_ref must look like 'module.path:factory_name', got {env_ref!r}")
    module = importlib.import_module(module_path)
    try:
        factory = getattr(module, attr)
    except AttributeError as e:
        raise ValueError(f"module {module_path!r} has no attribute {attr!r}") from e
    if not callable(factory):
        raise ValueError(f"{env_ref!r} does not refer to a callable")
    return factory  # type: ignore[no-any-return]
