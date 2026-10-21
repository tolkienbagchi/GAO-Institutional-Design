"""Opaque identifier types."""

from typing import NewType

AgentId = NewType("AgentId", str)
RoleId = NewType("RoleId", str)
Port = NewType("Port", str)
DecisionId = NewType("DecisionId", str)
ReportId = NewType("ReportId", str)
