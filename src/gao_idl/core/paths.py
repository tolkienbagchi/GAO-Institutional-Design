"""Hierarchical instance paths and the identifiers derived from them.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from dataclasses import dataclass

from gao_idl.core.ids import AgentId, DecisionId, ReportId, RoleId


@dataclass(frozen=True)
class LoopIter:
    """The i-th iteration of a loop."""

    i: int


@dataclass(frozen=True)
class Branch:
    """A named branch of a parallel composition."""

    name: str


PathElem = str | LoopIter | Branch  # str = layer/process name
Path = tuple[PathElem, ...]


def _elem_str(e: PathElem) -> str:
    if isinstance(e, LoopIter):
        return f"loop[{e.i}]"
    if isinstance(e, Branch):
        return f"branch[{e.name}]"
    return e


def path_str(p: Path) -> str:
    """Render a path like ``tournament/loop[3]/branch[pair_1]/pd_round``."""
    return "/".join(_elem_str(e) for e in p)


def child(p: Path, e: PathElem) -> Path:
    """Return ``p`` extended by ``e``."""
    return (*p, e)


def decision_id(p: Path, role: RoleId) -> DecisionId:
    """Id of the decision for ``role`` at path ``p``; unique per (path, role)."""
    return DecisionId(f"{path_str(p)}#{role}")


def report_id(p: Path, agent: AgentId, n: int) -> ReportId:
    """Id of the n-th report to ``agent`` at path ``p``."""
    return ReportId(f"{path_str(p)}@{agent}:{n}")
