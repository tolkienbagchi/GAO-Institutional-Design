"""gao_idl: composable multi-agent environments with resumable sessions.

Stability: the names in ``gao_idl.__all__`` are the public API. The optional LLM agents are
public in ``gao_idl.llm`` (the ``llm`` extra). Everything else is reachable but classified in
its module docstring as "supporting" (documented in the API reference, may change in a minor
release before 1.0) or "internal" (may change without notice).
"""

from gao_idl._selftest import selftest as selftest
from gao_idl._version import __version__ as __version__
from gao_idl.agents.base import AgentAdapter, EpisodeContext
from gao_idl.agents.human import HumanTransportAgent
from gao_idl.agents.random_agent import RandomAgent
from gao_idl.agents.scripted import ScriptedAgent
from gao_idl.composition.connector import Connector
from gao_idl.composition.loop import Loop
from gao_idl.composition.par import DictJoiner, DisjointSplitter, Par
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.seq import Seq
from gao_idl.core.layer import Contract, SimpleLayer
from gao_idl.core.schemas import Config
from gao_idl.core.timeout import DefaultAction, DropRole, Fail
from gao_idl.learning.qlearning import TableAgent
from gao_idl.runtime.runner import RunnerPolicy, run, run_sync
from gao_idl.runtime.session import Session

__all__ = [
    "AgentAdapter",
    "Config",
    "Connector",
    "Contract",
    "DefaultAction",
    "DictJoiner",
    "DisjointSplitter",
    "DropRole",
    "EpisodeContext",
    "Fail",
    "HumanTransportAgent",
    "Loop",
    "Par",
    "Primitive",
    "RandomAgent",
    "RunnerPolicy",
    "ScriptedAgent",
    "Seq",
    "Session",
    "SimpleLayer",
    "TableAgent",
    "run",
    "run_sync",
]
