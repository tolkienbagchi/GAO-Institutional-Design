"""Exception hierarchy for gao_idl."""


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
