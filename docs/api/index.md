# API reference

Everything importable from `gao_idl` is the **public API**: it is covered by the stability promise below. `gao_idl.llm` exports the language-model symbols (it needs no extra to import; the HTTP backends need `httpx`, from the `llm` extra, when first used).

The other pages document **supporting** modules: reachable and described, but not re-exported, and free to change in a minor release before 1.0. Modules marked *internal* in their docstring are not documented here. Every module states its tier in a `Stability:` paragraph.

## Public names

| Import | Defined in | Reference |
| --- | --- | --- |
| `from gao_idl import AgentAdapter` | `gao_idl.agents.base` | [agents](agents.md) |
| `from gao_idl import Config` | `gao_idl.core.schemas` | [core](core.md) |
| `from gao_idl import Connector` | `gao_idl.composition.connector` | [composition](composition.md) |
| `from gao_idl import Contract` | `gao_idl.core.layer` | [core](core.md) |
| `from gao_idl import DefaultAction` | `gao_idl.core.timeout` | [core](core.md) |
| `from gao_idl import DictJoiner` | `gao_idl.composition.par` | [composition](composition.md) |
| `from gao_idl import DisjointSplitter` | `gao_idl.composition.par` | [composition](composition.md) |
| `from gao_idl import DropRole` | `gao_idl.core.timeout` | [core](core.md) |
| `from gao_idl import EpisodeContext` | `gao_idl.agents.base` | [agents](agents.md) |
| `from gao_idl import Fail` | `gao_idl.core.timeout` | [core](core.md) |
| `from gao_idl import HumanTransportAgent` | `gao_idl.agents.human` | [agents](agents.md) |
| `from gao_idl import Loop` | `gao_idl.composition.loop` | [composition](composition.md) |
| `from gao_idl import Par` | `gao_idl.composition.par` | [composition](composition.md) |
| `from gao_idl import Primitive` | `gao_idl.composition.primitive` | [composition](composition.md) |
| `from gao_idl import RandomAgent` | `gao_idl.agents.random_agent` | [agents](agents.md) |
| `from gao_idl import RunnerPolicy` | `gao_idl.runtime.runner` | [runtime](runtime.md) |
| `from gao_idl import ScriptedAgent` | `gao_idl.agents.scripted` | [agents](agents.md) |
| `from gao_idl import Seq` | `gao_idl.composition.seq` | [composition](composition.md) |
| `from gao_idl import Session` | `gao_idl.runtime.session` | [runtime](runtime.md) |
| `from gao_idl import SimpleLayer` | `gao_idl.core.layer` | [core](core.md) |
| `from gao_idl import TableAgent` | `gao_idl.learning.qlearning` | [learning](learning.md) |
| `from gao_idl import run` | `gao_idl.runtime.runner` | [runtime](runtime.md) |
| `from gao_idl import run_sync` | `gao_idl.runtime.runner` | [runtime](runtime.md) |
| `from gao_idl.llm import AnthropicBackend` | `gao_idl.llm.anthropic` | [llm](llm.md) |
| `from gao_idl.llm import BackendError` | `gao_idl.llm.backend` | [llm](llm.md) |
| `from gao_idl.llm import CallRecord` | `gao_idl.llm.agent` | [llm](llm.md) |
| `from gao_idl.llm import CodecParseError` | `gao_idl.llm.codec` | [llm](llm.md) |
| `from gao_idl.llm import DecisionCodec` | `gao_idl.llm.codec` | [llm](llm.md) |
| `from gao_idl.llm import DefaultCodec` | `gao_idl.llm.codec` | [llm](llm.md) |
| `from gao_idl.llm import DefaultReportRenderer` | `gao_idl.llm.codec` | [llm](llm.md) |
| `from gao_idl.llm import FullTranscript` | `gao_idl.llm.memory` | [llm](llm.md) |
| `from gao_idl.llm import LLMAgent` | `gao_idl.llm.agent` | [llm](llm.md) |
| `from gao_idl.llm import LLMBackend` | `gao_idl.llm.backend` | [llm](llm.md) |
| `from gao_idl.llm import Memory` | `gao_idl.llm.memory` | [llm](llm.md) |
| `from gao_idl.llm import Message` | `gao_idl.llm.backend` | [llm](llm.md) |
| `from gao_idl.llm import MockBackend` | `gao_idl.llm.mock` | [llm](llm.md) |
| `from gao_idl.llm import MockCall` | `gao_idl.llm.mock` | [llm](llm.md) |
| `from gao_idl.llm import MockExhausted` | `gao_idl.llm.mock` | [llm](llm.md) |
| `from gao_idl.llm import NoMemory` | `gao_idl.llm.memory` | [llm](llm.md) |
| `from gao_idl.llm import OpenAICompatBackend` | `gao_idl.llm.openai_compat` | [llm](llm.md) |
| `from gao_idl.llm import RepairPolicy` | `gao_idl.llm.agent` | [llm](llm.md) |
| `from gao_idl.llm import ReplayBackend` | `gao_idl.llm.replay` | [llm](llm.md) |
| `from gao_idl.llm import ReplayExhausted` | `gao_idl.llm.replay` | [llm](llm.md) |
| `from gao_idl.llm import ReportRenderer` | `gao_idl.llm.codec` | [llm](llm.md) |
| `from gao_idl.llm import Response` | `gao_idl.llm.backend` | [llm](llm.md) |
| `from gao_idl.llm import Window` | `gao_idl.llm.memory` | [llm](llm.md) |
| `from gao_idl.llm import to_anthropic_schema` | `gao_idl.llm.schema_transform` | [llm](llm.md) |

`gao_idl.__version__` and `gao_idl.selftest()` (a quick end-to-end check used by the clean-room install) are also available but are not part of `__all__`.

## Pages

- [`core`](core.md): Identifiers, paths, events, schemas, the layer protocol, the store, the ledger and timeout policies.
- [`composition`](composition.md): `Primitive`, `Seq`, `Par`, `Loop` and `Connector`, and `describe` for inspecting a composite.
- [`runtime`](runtime.md): The event-sourced `Session` and the async runner.
- [`agents`](agents.md): Adapters: the only things that supply actions to a run.
- [`learning`](learning.md): Tabular Q-learning, codecs, scripted opponents, trainer and evaluator.
- [`llm`](llm.md): Language-model agents and backends. Requires the `llm` extra for the HTTP backends.
- [`environments`](environments.md): Ready-made environments: auction, participatory budgeting, repeated prisoner's dilemma, toy tasks.
- [`testing`](testing.md): Invariant checks to run on your own environments.
