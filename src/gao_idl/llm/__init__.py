"""LLM-driven agents (optional extra ``gao_idl[llm]``). Nothing outside this package imports it.

Only the Anthropic pieces are re-exported here; everything else is imported from its module
(``gao_idl.llm.agent``, ``gao_idl.llm.openai_compat``, ...). Importing the package never imports
``httpx``: the transports import it lazily.
"""

from gao_idl.llm.anthropic import AnthropicBackend
from gao_idl.llm.schema_transform import to_anthropic_schema

__all__ = ["AnthropicBackend", "to_anthropic_schema"]
