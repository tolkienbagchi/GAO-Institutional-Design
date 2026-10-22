"""Opaque identifier types and ordering helpers.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import re
from typing import NewType

AgentId = NewType("AgentId", str)
RoleId = NewType("RoleId", str)
Port = NewType("Port", str)
DecisionId = NewType("DecisionId", str)
ReportId = NewType("ReportId", str)

_DIGITS = re.compile(r"(\d+)")


def role_sort_key(role: str) -> tuple[tuple[tuple[int, int, str], ...], str]:
    """Natural-sort key: digit runs compare numerically, so ``bidder:2`` < ``bidder:10``.

    The raw string is the final tiebreak, so the order is total (``"01"`` vs ``"1"``).
    """
    parts = tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p) for p in _DIGITS.split(role) if p != ""
    )
    return parts, role
