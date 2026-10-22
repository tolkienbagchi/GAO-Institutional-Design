"""Agent adapters: the only things that supply actions to a session run.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from gao_idl.agents.base import AgentAdapter, EpisodeContext, EpisodeEnd, ExternalRecorder
from gao_idl.agents.human import HumanTransportAgent
from gao_idl.agents.random_agent import RandomAgent, sample_schema
from gao_idl.agents.scripted import ScriptedAgent

__all__ = [
    "AgentAdapter",
    "EpisodeContext",
    "EpisodeEnd",
    "ExternalRecorder",
    "HumanTransportAgent",
    "RandomAgent",
    "ScriptedAgent",
    "sample_schema",
]
