"""The environment spec: a structured record of what the designer said the institution is.

An ``EnvSpec`` holds the confirmed answers to the nine design questions (see
``docs/designing.md``): who decides in which step, what each role sees, chooses and is told, what
happens when someone does not act, what repeats, what is secret, and how payoffs arise. It is
stored as JSON; people read a rendering of it (``render.render_design``), and the built
environment is compared against it (``check.check_spec``). Specs name role *kinds*: the kind of
the role id ``bidder:0`` is ``bidder``.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, model_validator

from gao_idl.core.schemas import FrozenModel

OBSERVER = "observer"


def role_kind(role: str) -> str:
    """The kind of a role id: its text before the first ``:`` (``bidder:0`` -> ``bidder``)."""
    return role.split(":", 1)[0]


class _Strict(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RoleSpec(_Strict):
    """A kind of participant. ``many`` says several role ids share the kind (``bidder:0``, ...)."""

    name: str
    description: str
    many: bool = False


class StepSpec(_Strict):
    """One step of the institution; ``name`` must equal the layer name in the built environment.

    ``sees``, ``chooses`` and ``told`` map a role kind to field names: what it observes when
    deciding, what its action contains, and what its report after the step contains. A role kind
    that only receives reports appears in ``told`` alone; the reserved kind ``observer`` stands
    for agents that never decide anywhere.
    """

    name: str
    kind: Literal["decision", "automatic"]
    description: str
    sees: dict[str, list[str]] = {}
    chooses: dict[str, list[str]] = {}
    told: dict[str, list[str]] = {}
    on_no_action: Literal["fail", "default", "drop"] | None = None
    no_action_means: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> "StepSpec":
        if self.kind == "decision":
            if not self.chooses:
                raise ValueError(f"step {self.name!r}: a decision step needs at least one decider")
            if self.on_no_action is None:
                raise ValueError(f"step {self.name!r}: say what happens when someone does not act")
            if self.on_no_action != "fail" and not self.no_action_means:
                raise ValueError(
                    f"step {self.name!r}: no_action_means is required unless the run fails"
                )
        else:
            if self.chooses or self.sees:
                raise ValueError(f"step {self.name!r}: an automatic step has no deciders")
            if self.on_no_action is not None:
                raise ValueError(f"step {self.name!r}: an automatic step has no timeout")
        extra = sorted(set(self.sees) - set(self.chooses))
        if extra:
            raise ValueError(f"step {self.name!r}: {extra} see something but choose nothing")
        return self


class RepeatSpec(_Strict):
    """What repeats, which parameter sets the number of rounds, and what carries over."""

    what: str
    rounds_param: str | None = None
    carries_over: list[str] = []


class SecretSpec(_Strict):
    """Something that must never reach certain roles, stated as a testable change.

    Replacing the factory parameter ``param`` by ``alt_value`` must leave the information of
    every agent whose kind is in ``hidden_from`` unchanged, except the agents named in
    ``except_agents`` (the owners of the secret, who may share the kind: a bidder knows its own
    value). ``only_when`` restricts the claim to runs whose parameters have those values (a
    quality "revealed only by an audit" is secret only when ``p_audit`` is 0).
    """

    what: str
    param: str
    alt_value: Any
    hidden_from: list[str]
    except_agents: list[str] = []
    only_when: dict[str, Any] = {}


class PayoffSpec(_Strict):
    """How a role is paid, in words, and the report field that tells it (None: it is not told)."""

    role: str
    description: str
    report_field: str | None = None


class InvariantSpec(_Strict):
    """A property that must hold in every run; ``predicate`` is ``"module:function"`` or None."""

    name: str
    statement: str
    predicate: str | None = None


class Assumption(_Strict):
    """A modelling choice the description did not settle (``question``: which of the nine)."""

    question: int | None = None
    text: str
    confirmed: bool = False

    @model_validator(mode="after")
    def _question(self) -> "Assumption":
        if self.question is not None and not 1 <= self.question <= 9:
            raise ValueError("question must be between 1 and 9")
        return self


class EnvSpec(_Strict):
    """The designer's intent for one environment, with the factory and parameters it describes."""

    spec_version: Literal[1] = 1
    title: str
    summary: str
    env_ref: str
    params: dict[str, Any]
    roles: list[RoleSpec]
    steps: list[StepSpec]
    repeats: RepeatSpec | None = None
    secrets: list[SecretSpec] = []
    payoffs: list[PayoffSpec] = []
    invariants: list[InvariantSpec] = []
    assumptions: list[Assumption] = []

    @model_validator(mode="after")
    def _consistent(self) -> "EnvSpec":
        names = [s.name for s in self.steps]
        if len(set(names)) != len(names):
            raise ValueError("step names must be unique")
        kinds = [r.name for r in self.roles]
        if len(set(kinds)) != len(kinds):
            raise ValueError("role names must be unique")
        known = set(kinds) | {OBSERVER}
        for step in self.steps:
            used = set(step.sees) | set(step.chooses) | set(step.told)
            unknown = sorted(used - known)
            if unknown:
                raise ValueError(f"step {step.name!r} uses undeclared roles {unknown}")
        for secret in self.secrets:
            unknown = sorted(set(secret.hidden_from) - known)
            if unknown:
                raise ValueError(f"secret {secret.what!r} names undeclared roles {unknown}")
            if secret.param not in self.params:
                raise ValueError(f"secret {secret.what!r}: {secret.param!r} is not a parameter")
        for payoff in self.payoffs:
            if payoff.role not in known:
                raise ValueError(f"payoff names undeclared role {payoff.role!r}")
        rounds = None if self.repeats is None else self.repeats.rounds_param
        if rounds is not None and rounds not in self.params:
            raise ValueError(f"rounds_param {rounds!r} is not a parameter")
        return self

    def step(self, name: str) -> StepSpec | None:
        """The step called ``name``, or None."""
        return next((s for s in self.steps if s.name == name), None)


def load_spec(path: Path) -> EnvSpec:
    """Read an ``EnvSpec`` from a JSON file."""
    return EnvSpec.model_validate_json(Path(path).read_text())
