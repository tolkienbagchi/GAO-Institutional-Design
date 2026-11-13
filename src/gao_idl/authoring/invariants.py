"""Run a spec's invariants and secrets over many runs.

``check_invariants`` calls each ``InvariantSpec.predicate`` on every run and tests every secret
on every run. It is meant for runs from ``fuzz.edge_scripts``: a handful of cards is thin
evidence that a value never leaks or a budget is never overspent; a few hundred edge-biased
runs are better evidence, though still not a proof.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from collections.abc import Sequence

from gao_idl.authoring.check import Mismatch, Run, check_secrets
from gao_idl.authoring.play import play
from gao_idl.authoring.spec import EnvSpec
from gao_idl.runtime.registry import resolve


def check_invariants(spec: EnvSpec, runs: Sequence[Run], secrets: bool = True) -> list[Mismatch]:
    """Violations of the spec's invariants and secrets over ``runs``.

    Kinds: ``invariant_violated`` (one per invariant, for the first run that breaks it, with the
    run in ``found``), ``invariant_unchecked`` (a statement with no predicate: nothing tested
    it), and the ``secret_leak`` / ``not_exercised`` results of ``check.check_secrets``. A
    predicate that raises anything but ``AssertionError`` is a bug in the predicate and
    propagates. ``secrets=False`` skips the secret tests (``check_spec`` already ran them).
    """
    out: list[Mismatch] = []
    sessions = [play(spec.env_ref, run.params, run.seed, run.script) for run in runs]
    for inv in spec.invariants:
        if inv.predicate is None:
            out.append(
                Mismatch(
                    kind="invariant_unchecked",
                    where=inv.name,
                    message=f"nothing checks this automatically: {inv.statement}",
                )
            )
            continue
        predicate = resolve(inv.predicate)
        for index, (run, session) in enumerate(zip(runs, sessions, strict=True)):
            try:
                predicate(session, **inv.args)
            except AssertionError as e:
                out.append(
                    Mismatch(
                        kind="invariant_violated",
                        where=f"{inv.name} (run {index})",
                        expected=inv.statement,
                        found=run.model_dump(mode="json"),
                        message=f"{inv.statement} Broken in run {index}: {e}",
                    )
                )
                break
    if secrets:
        out += check_secrets(spec, runs, sessions)
    return out
