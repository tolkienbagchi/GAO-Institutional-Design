"""Generic invariant predicates for ``InvariantSpec.predicate``.

Each takes a finished ``Session`` plus keyword arguments from ``InvariantSpec.args`` and raises
``AssertionError`` with a plain-language message when the property fails. Role arguments are
role *kinds* (``bidder``, not ``bidder:0``). Write environment-specific predicates with the same
signature in your own module and name them as ``"module:function"``.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import math

from gao_idl.authoring.spec import role_kind
from gao_idl.core import events as ev
from gao_idl.core.ids import AgentId
from gao_idl.runtime.session import Session

TOLERANCE = 1e-9


def _agents(session: Session, roles: list[str]) -> list[str]:
    found: set[str] = set()
    for event in session.trace:
        if isinstance(event, ev.DecisionRequested) and role_kind(str(event.request.role)) in roles:
            found.add(str(event.request.agent_id))
    return sorted(found)


def transfers_sum_to_zero(session: Session, roles: list[str]) -> None:
    """The final payoffs of all agents holding one of ``roles`` add up to zero."""
    agents = _agents(session, roles)
    total = math.fsum(session.ledger.utility(AgentId(a)) for a in agents)
    assert abs(total) <= TOLERANCE, f"payoffs of {', '.join(roles)} sum to {total}, not 0"


def never_below_zero(session: Session, roles: list[str]) -> None:
    """No agent holding one of ``roles`` ends with a negative total payoff."""
    for agent in _agents(session, roles):
        total = session.ledger.utility(AgentId(agent))
        assert total >= -TOLERANCE, f"{agent} ends with a negative payoff ({total})"


def report_field_at_most_param(session: Session, field: str, param: str) -> None:
    """No report carries ``field`` above the factory parameter ``param``."""
    limit = float(session.snapshot()["params"][param])
    for event in session.trace:
        if isinstance(event, ev.ReportDelivered) and field in event.report.payload:
            value = event.report.payload[field]
            if isinstance(value, int | float) and not isinstance(value, bool):
                assert value <= limit + TOLERANCE, (
                    f"{event.report.agent_id} was told {field} = {value}, above {param} = {limit}"
                )
