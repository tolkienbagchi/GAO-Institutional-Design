"""Prompts for language-model verifiers and buyers in the carbon market.

Everything is built from the ``DecisionRequest`` observation and from report payloads, nothing
else: a buyer's prompt carries only reported qualities (and the track records, if they are
shown), never a project's true quality unless an audit revealed it in a report.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json

from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.llm.backend import Message
from gao_idl.llm.codec import DefaultCodec, DefaultReportRenderer


class VerifierCodec(DefaultCodec):
    """Own measurements and the fee/penalty rules; ask for one number per project."""

    def system_prompt(self, request: DecisionRequest) -> str:
        """Who the model is and the reply format."""
        return (
            f"You are {request.agent_id}, a verifier of carbon-credit projects. For each project "
            "you are assigned you report a quality between 0 and 1. You are paid a fee for every "
            "report; if a traded project is audited and your report is off by more than the "
            "tolerance you pay a penalty. Reply with a JSON object only."
        )

    def task_prompt(self, request: DecisionRequest) -> str:
        """The measurements and the rules."""
        obs = request.observation
        lines = [f"Round {obs['round']}.", "Your measurements:"]
        lines += [f"  {p}: {x:.3f}" for p, x in sorted(obs["measurements"].items())]
        lines += [
            f"Fee per report: {obs['fee']:g}. Penalty: {obs['penalty']:g} if audited and off by "
            f"more than {obs['tolerance']:g}. Audit probability for a traded project: "
            f"{obs['audit_probability']:g}.",
            "",
            "Reply with one number per project, for example "
            + json.dumps({p: 0.5 for p in sorted(obs["measurements"])})
            + ".",
        ]
        return "\n".join(lines)


class BuyerCodec(DefaultCodec):
    """The listed projects (as verified), maybe track records; ask for a price and a quantity."""

    def system_prompt(self, request: DecisionRequest) -> str:
        """Who the model is and the reply format."""
        return (
            f"You are {request.agent_id}, a buyer of carbon credits in a sealed-bid call "
            "auction. All credits trade at one uniform price. You value each credit at your "
            "value times its true quality, which you can only estimate from verifiers' reports. "
            "Reply with a JSON object only."
        )

    def task_prompt(self, request: DecisionRequest) -> str:
        """The round, own demand and value, the listed projects and the history, if shown."""
        obs = request.observation
        lines = [
            f"Round {obs['round']}. You want up to {obs['demand']} credits; your value per unit "
            f"of quality is {obs['value']:g}.",
            "Listed projects (credits for sale, verifier, reported quality):",
        ]
        lines += [
            f"  {p['project']}: {p['quantity']} credits, {p['verifier']}, "
            f"{p['verified_quality']:.3f}"
            for p in obs["projects"]
        ]
        if obs.get("reputation"):
            lines.append("Verifiers' audit history (audited, wrong, mean report minus truth):")
            for v, rec in obs["reputation"].items():
                err = "n/a" if not rec["audited"] else f"{rec['mean_error']:+.2f}"
                lines.append(f"  {v}: {rec['audited']:g}, {rec['wrong']:g}, {err}")
        if obs.get("last_price") is not None:
            lines.append(
                f"Last round cleared {obs['last_volume']} credits at {obs['last_price']:g}."
            )
        lines += ["", 'Reply: {"price": <per credit>, "qty": <credits>}. JSON only.']
        return "\n".join(lines)


class VCMReportRenderer(DefaultReportRenderer):
    """A report as one line of its own fields; nothing is added."""

    def render_report(self, report: ParticipantReport) -> Message:
        """The payload's fields, as sent."""
        if "round" not in report.payload:
            return super().render_report(report)
        body = ", ".join(f"{k}={json.dumps(v, sort_keys=True)}" for k, v in report.payload.items())
        return Message(role="user", content=f"Round result ({report.layer}): {body}")
