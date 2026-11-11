"""Compare a built environment with its spec, mechanically.

``check_spec`` builds the environment the spec names, plays the given runs, and reports every
difference between what the spec says and what the environment did: steps, who sees, chooses and
is told what, timeout policies, repetition, secrets and payoff visibility. An empty list means
no difference was found *in the runs given*; a step no run reached is reported as
``not_exercised`` rather than passed.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from collections.abc import Sequence
from typing import Any

from gao_idl.authoring.play import ActionScript, play
from gao_idl.authoring.render import KnowledgeTable, has_loop, knowledge_table, primitives
from gao_idl.authoring.spec import EnvSpec, SecretSpec, role_kind
from gao_idl.composition.contracts import describe
from gao_idl.core import events as ev
from gao_idl.core.errors import GaoError
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import FrozenModel
from gao_idl.runtime.registry import resolve
from gao_idl.runtime.session import Session

KINDS = (
    "step_missing",
    "step_extra",
    "step_order",
    "undeclared_sees",
    "undeclared_told",
    "undeclared_chooses",
    "unfulfilled_sees",
    "unfulfilled_told",
    "unfulfilled_chooses",
    "timeout_policy",
    "repeat",
    "secret_leak",
    "payoff_invisible",
    "not_exercised",
)


class Run(FrozenModel):
    """One run to check against: factory parameters, session seed and the actions taken."""

    params: dict[str, Any]
    seed: int
    script: ActionScript


class Mismatch(FrozenModel):
    """One difference between the spec and the built environment (``kind`` is one of ``KINDS``)."""

    kind: str
    where: str
    expected: Any = None
    found: Any = None
    message: str


def _structure(spec: EnvSpec, tree: dict[str, Any]) -> list[Mismatch]:
    out: list[Mismatch] = []
    built = {node["name"]: node for node in primitives(tree)}
    for step in spec.steps:
        node = built.get(step.name)
        if node is None:
            out.append(
                Mismatch(
                    kind="step_missing",
                    where=step.name,
                    expected=step.name,
                    message=f"the spec has a step {step.name!r} that the environment lacks",
                )
            )
            continue
        if step.kind == "decision" and step.on_no_action != node["timeout"]:
            out.append(
                Mismatch(
                    kind="timeout_policy",
                    where=step.name,
                    expected=step.on_no_action,
                    found=node["timeout"],
                    message=(
                        f"when someone does not act in {step.name!r} the spec says "
                        f"{step.on_no_action!r} but the environment does {node['timeout']!r}"
                    ),
                )
            )
    declared = {s.name for s in spec.steps}
    for name in built:
        if name not in declared:
            out.append(
                Mismatch(
                    kind="step_extra",
                    where=name,
                    found=name,
                    message=f"the environment has a step {name!r} that the spec does not describe",
                )
            )
    loops = has_loop(tree)
    if (spec.repeats is not None) != loops:
        out.append(
            Mismatch(
                kind="repeat",
                where="structure",
                expected=spec.repeats is not None,
                found=loops,
                message=(
                    "the spec says something repeats but the environment has no loop"
                    if spec.repeats is not None
                    else "the environment repeats but the spec says nothing repeats"
                ),
            )
        )
    return out


def _knowledge(spec: EnvSpec, table: KnowledgeTable) -> list[Mismatch]:
    out: list[Mismatch] = []
    declared = [s.name for s in spec.steps]
    seen = [s for s in table.step_order if s in declared]
    wanted = [s for s in declared if s in seen]
    if seen != wanted:
        out.append(
            Mismatch(
                kind="step_order",
                where="structure",
                expected=wanted,
                found=seen,
                message="steps were first entered in a different order than the spec lists them",
            )
        )
    for step in spec.steps:
        if step.name not in table.step_order:
            out.append(
                Mismatch(
                    kind="not_exercised",
                    where=step.name,
                    message=f"no run reached the step {step.name!r}; nothing was checked for it",
                )
            )
            continue
        roles = set(step.sees) | set(step.chooses) | set(step.told)
        roles |= {r.role for r in table.rows if r.step == step.name}
        for role in sorted(roles):
            row = table.row(step.name, role)
            where = f"{step.name}/{role}"
            if row is None:
                out.append(
                    Mismatch(
                        kind="not_exercised",
                        where=where,
                        message=f"no run involved a {role!r} in {step.name!r}",
                    )
                )
                continue
            for column, expected, found in (
                ("sees", step.sees.get(role, []), row.sees),
                ("chooses", step.chooses.get(role, []), row.chooses),
                ("told", step.told.get(role, []), row.told),
            ):
                extra = sorted(set(found) - set(expected))
                lacking = sorted(set(expected) - set(found))
                verb = {"sees": "sees", "chooses": "can choose", "told": "is told"}[column]
                if extra:
                    out.append(
                        Mismatch(
                            kind=f"undeclared_{column}",
                            where=where,
                            expected=sorted(expected),
                            found=sorted(found),
                            message=f"in {step.name!r} a {role} {verb} {extra}, not in the spec",
                        )
                    )
                if lacking:
                    out.append(
                        Mismatch(
                            kind=f"unfulfilled_{column}",
                            where=where,
                            expected=sorted(expected),
                            found=sorted(found),
                            message=(
                                f"the spec says a {role} {verb} {lacking} in {step.name!r}; "
                                "no run showed it"
                            ),
                        )
                    )
    return out


def information(session: Session, agent: str) -> list[dict[str, Any]]:
    """Everything delivered to ``agent`` (requests and reports), in order, as JSON."""
    return [x.model_dump(mode="json") for x in session.ledger.information(AgentId(agent))]


def _kinds_by_agent(session: Session) -> dict[str, set[str]]:
    held: dict[str, set[str]] = {}
    for event in session.trace:
        if isinstance(event, ev.DecisionRequested):
            held.setdefault(str(event.request.agent_id), set()).add(
                role_kind(str(event.request.role))
            )
        elif isinstance(event, ev.ReportDelivered):
            held.setdefault(str(event.report.agent_id), set())
    return {agent: kinds or {"observer"} for agent, kinds in held.items()}


def _secret(
    spec: EnvSpec, secret: SecretSpec, index: int, run: Run, base: Session
) -> list[Mismatch]:
    where = f"secret[{index}] {secret.param}"
    changed = {**run.params, secret.param: secret.alt_value}
    if any(run.params.get(k) != v for k, v in secret.only_when.items()):
        return [
            Mismatch(
                kind="not_exercised",
                where=where,
                message=f"the secret only holds when {secret.only_when}; no such run was given",
            )
        ]
    if changed == run.params:
        return [
            Mismatch(
                kind="not_exercised",
                where=where,
                message=f"a run already has {secret.param!r} at its alternative value",
            )
        ]
    try:
        other = play(spec.env_ref, changed, run.seed, run.script)
    except (GaoError, ValueError, RuntimeError) as e:
        return [
            Mismatch(
                kind="not_exercised",
                where=where,
                message=(
                    f"the run could not be repeated with {secret.param!r} changed "
                    f"({type(e).__name__}: {e}); the secret was not tested on it"
                ),
            )
        ]
    out: list[Mismatch] = []
    kinds = _kinds_by_agent(base)
    for agent in sorted(set(kinds) | set(_kinds_by_agent(other))):
        if agent in secret.except_agents:
            continue
        if not kinds.get(agent, {"observer"}) & set(secret.hidden_from):
            continue
        a, b = information(base, agent), information(other, agent)
        if a != b:
            at = next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), None)
            at = min(len(a), len(b)) if at is None else at
            out.append(
                Mismatch(
                    kind="secret_leak",
                    where=f"{where} -> {agent}",
                    expected=a[at] if at < len(a) else None,
                    found=b[at] if at < len(b) else None,
                    message=(
                        f"changing {secret.param!r} changed what {agent} is shown "
                        f"(item {at} of its information): {secret.what}"
                    ),
                )
            )
    return out


def _payoffs(spec: EnvSpec, sessions: Sequence[Session]) -> list[Mismatch]:
    out: list[Mismatch] = []
    for payoff in spec.payoffs:
        if payoff.report_field is None:
            continue
        paid = False
        shown = False
        for session in sessions:
            for agent, kinds in _kinds_by_agent(session).items():
                if payoff.role not in kinds:
                    continue
                if session.ledger.increments(AgentId(agent)):
                    paid = True
                if any(
                    payoff.report_field in r.payload for r in session.reports_for(AgentId(agent))
                ):
                    shown = True
        if paid and not shown:
            out.append(
                Mismatch(
                    kind="payoff_invisible",
                    where=payoff.role,
                    expected=payoff.report_field,
                    message=(
                        f"a {payoff.role} is credited utility but no report it receives has "
                        f"the field {payoff.report_field!r}"
                    ),
                )
            )
        elif not paid:
            out.append(
                Mismatch(
                    kind="not_exercised",
                    where=f"payoff/{payoff.role}",
                    message=f"no run credited any utility to a {payoff.role}",
                )
            )
    return out


def check_spec(spec: EnvSpec, runs: Sequence[Run]) -> list[Mismatch]:
    """Every difference found between ``spec`` and the environment it names.

    The structure (steps, timeout policies, repetition) is checked on the process built from
    ``spec.env_ref`` and ``spec.params``. Each run is then played with its own parameters and
    seed; what roles saw, chose and were told is compared with the spec in both directions, each
    secret is tested on each run by changing its parameter and comparing what the roles it is
    hidden from were shown, and each declared payoff field is looked for in reports. With no
    runs, everything that needs one is reported as ``not_exercised``.
    """
    process, _ = resolve(spec.env_ref)(**spec.params)
    out = _structure(spec, describe(process))
    sessions = [play(spec.env_ref, run.params, run.seed, run.script) for run in runs]
    out += _knowledge(spec, knowledge_table(sessions))
    for index, secret in enumerate(spec.secrets):
        found: list[Mismatch] = []
        for run, session in zip(runs, sessions, strict=True):
            found += _secret(spec, secret, index, run, session)
        leaks = {m.where: m for m in found if m.kind == "secret_leak"}  # one per agent
        skipped = [m for m in found if m.kind == "not_exercised"]
        out += list(leaks.values())
        if not runs:
            out.append(
                Mismatch(
                    kind="not_exercised",
                    where=f"secret[{index}] {secret.param}",
                    message="no runs were given; the secret was not tested",
                )
            )
        elif len(skipped) == len(runs):  # no run could test it
            out.append(skipped[0])
    out += _payoffs(spec, sessions)
    return out
