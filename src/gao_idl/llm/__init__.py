"""LLM-driven agents (optional extra ``gao_idl[llm]``). Nothing outside this package imports it.

This is the one place LLM symbols are exported from; ``gao_idl`` itself exports none of them.
Importing the package never imports ``httpx``: the real HTTP transports import it lazily, so the
agent, the mock and the replay backend work without the extra.

Stability: the names in ``gao_idl.llm.__all__`` are public; the modules behind them are internal.
"""

from gao_idl.llm.agent import CallRecord, LLMAgent, RepairPolicy
from gao_idl.llm.anthropic import AnthropicBackend
from gao_idl.llm.backend import BackendError, LLMBackend, Message, Response
from gao_idl.llm.codec import (
    CodecParseError,
    DecisionCodec,
    DefaultCodec,
    DefaultReportRenderer,
    ReportRenderer,
)
from gao_idl.llm.memory import FullTranscript, Memory, NoMemory, Window
from gao_idl.llm.mock import MockBackend, MockCall, MockExhausted
from gao_idl.llm.openai_compat import OpenAICompatBackend
from gao_idl.llm.replay import ReplayBackend, ReplayExhausted
from gao_idl.llm.schema_transform import to_anthropic_schema

__all__ = [
    "AnthropicBackend",
    "BackendError",
    "CallRecord",
    "CodecParseError",
    "DecisionCodec",
    "DefaultCodec",
    "DefaultReportRenderer",
    "FullTranscript",
    "LLMAgent",
    "LLMBackend",
    "Memory",
    "Message",
    "MockBackend",
    "MockCall",
    "MockExhausted",
    "NoMemory",
    "OpenAICompatBackend",
    "RepairPolicy",
    "ReplayBackend",
    "ReplayExhausted",
    "ReportRenderer",
    "Response",
    "Window",
    "to_anthropic_schema",
]
