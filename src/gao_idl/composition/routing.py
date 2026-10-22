"""Helpers shared by the composites: decision-id ownership and name validation."""

from gao_idl.core.errors import ContractError
from gao_idl.core.ids import DecisionId
from gao_idl.core.paths import Path, path_str
from gao_idl.core.store import StoreScopeError

RESERVED_NAME_CHARS = "/#@[]"


def owns(path: Path, decision_id: DecisionId) -> bool:
    """Whether ``decision_id`` was issued at ``path`` or below it.

    ``seq/a`` owns ``seq/a#r`` and ``seq/a/b#r`` but not ``seq/ab#r`` (the boundary is checked).
    """
    own = str(decision_id).partition("#")[0]
    prefix = path_str(path)
    return own == prefix or own.startswith(prefix + "/")


def check_name(what: str, name: str) -> None:
    """Raise ContractError if ``name`` is empty or contains a character reserved by identifiers."""
    if not name or any(c in name for c in RESERVED_NAME_CHARS):
        raise ContractError(
            f"{what} name {name!r} must be non-empty and avoid the reserved characters "
            f"{RESERVED_NAME_CHARS!r}"
        )


def store_failure_reason(where: str, error: StoreScopeError) -> str:
    """Failure reason for a store access that left its declared scope."""
    detail = error.args[0] if error.args else ""
    return f"{where} violated its store scope: {type(error).__name__}: {detail}"
