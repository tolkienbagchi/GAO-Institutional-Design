"""Exception hierarchy for gao_idl.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""


class GaoError(Exception):
    """Base class for all gao_idl errors."""


class ContractError(GaoError):
    """Construction-time wiring mismatch (state, roles, or exit records violating a contract)."""


class UnknownDecision(GaoError):
    """The decision id is not currently pending."""


class WrongAgent(GaoError):
    """The submitting agent is not the one the decision was addressed to."""


class SchemaError(GaoError):
    """The submitted action does not parse against the role's action schema."""


class Infeasible(GaoError):
    """The submitted action parsed but the layer declares it infeasible."""


class SessionFinished(GaoError):
    """The session has already finished."""


class DeterminismError(GaoError):
    """Replay of an input log did not reproduce the original behaviour."""


class AgentFailed(GaoError):
    """An agent adapter could not produce a valid action (e.g. its repair attempts ran out).

    Defined in core so that the runner can recognise it without importing any adapter package.
    The runner resolves the decision through the layer's timeout policy and traces the reasons.
    """

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons) if reasons else "agent failed")
        self.reasons = list(reasons)
