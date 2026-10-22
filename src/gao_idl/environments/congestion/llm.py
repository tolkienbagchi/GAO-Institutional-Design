"""Prompts for a language-model regulator (and commuter) in the congestion environment.

Everything is built from the ``DecisionRequest`` observation and from report payloads, nothing
else. The regulator's prompt states the menu and the previous period's report exactly as the
reporting regime filtered it, so a metric the regime hides cannot appear.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json

from gao_idl.core.schemas import DecisionRequest, ParticipantReport
from gao_idl.llm.backend import Message
from gao_idl.llm.codec import DefaultCodec, DefaultReportRenderer


class RegulatorCodec(DefaultCodec):
    """The menu and last period's report, then ask for ``{"toll": 0 | 10 | 15}``."""

    def system_prompt(self, request: DecisionRequest) -> str:
        """Who the model is and the reply format."""
        return (
            f"You are {request.agent_id}, the regulator of a congestion-pricing scheme. Each "
            "period you set the toll for entering the central zone; commuters then choose "
            "between a tolled road, a free bypass and transit. After each period you are told "
            "how the city fared on the equity measure you are judged on. Lower objective values "
            "are better for you. Reply with a JSON object only."
        )

    def task_prompt(self, request: DecisionRequest) -> str:
        """This period's index, the menu and the previous report (as sent, key by key)."""
        obs = request.observation
        lines = [f"This is period {obs['period']} (counting from 0).", ""]
        report = obs.get("prev_report")
        if report is None:
            lines.append("You have no report yet: this is the first period.")
        else:
            lines.append("Report for the previous period:")
            lines.append(json.dumps(report, sort_keys=True))
        menu = ", ".join(str(t) for t in obs["menu"])
        lines += [
            "",
            f'Choose the toll from [{menu}]: {{"toll": <value>}}.',
            "Reply with JSON only.",
        ]
        return "\n".join(lines)


class CommuterCodec(DefaultCodec):
    """Own zone, value of time, toll and last period's own-zone times; ask for a route."""

    def system_prompt(self, request: DecisionRequest) -> str:
        """Who the model is and the reply format."""
        return (
            f"You are {request.agent_id}, a commuter choosing a route to the central zone each "
            "period. You pay the toll on the tolled road and the fare on transit, and you value "
            "your time at the rate you are told. Reply with a JSON object only."
        )

    def task_prompt(self, request: DecisionRequest) -> str:
        """The commuter's situation this period."""
        obs = request.observation
        times = ", ".join(f"{k}: {v:g} h" for k, v in obs["route_times"].items())
        car = (
            "You have a car." if obs["car_available"] else "You have no car: only transit is open."
        )
        return "\n".join(
            [
                f"Period {obs['period']}. You live in zone {obs['zone']}. {car}",
                f"Your value of time is {obs['value_of_time']:g} per hour.",
                f"The toll is {obs['toll']}; the transit fare is {obs['fare']:g}.",
                f"Route times in the previous period: {times}.",
                "",
                'Choose: {"route": "tolled"}, {"route": "bypass"} or {"route": "transit"}.',
                "Reply with JSON only.",
            ]
        )


class CongestionReportRenderer(DefaultReportRenderer):
    """A report as one line of its own fields; nothing is added."""

    def render_report(self, report: ParticipantReport) -> Message:
        """The payload's fields in a fixed order, as sent."""
        if "period" not in report.payload:
            return super().render_report(report)
        body = ", ".join(f"{k}={json.dumps(v, sort_keys=True)}" for k, v in report.payload.items())
        return Message(role="user", content=f"Period result ({report.layer}): {body}")
