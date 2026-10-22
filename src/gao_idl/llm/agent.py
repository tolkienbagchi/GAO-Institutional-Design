"""``LLMAgent``: an ordinary ``AgentAdapter`` backed by a language model.

It sees only the ``DecisionRequest`` and ``ParticipantReport`` objects addressed to its agent (and
its own memory); it never gets a session, ledger, store, config or outcome (invariant 1).
"""

import copy
import json
from typing import Any

from pydantic import Field

from gao_idl.agents.base import AgentAdapter, EpisodeContext, EpisodeEnd
from gao_idl.core.errors import AgentFailed
from gao_idl.core.ids import AgentId, DecisionId
from gao_idl.core.schemas import DecisionRequest, FrozenModel, ParticipantReport
from gao_idl.llm.backend import (
    BackendError,
    LLMBackend,
    Message,
    Response,
    current_decision,
    scrub,
)
from gao_idl.llm.codec import (
    CodecParseError,
    DecisionCodec,
    DefaultCodec,
    DefaultReportRenderer,
    ReportRenderer,
)
from gao_idl.llm.memory import Memory, NoMemory, Window
from gao_idl.llm.validate import against_json_schema, first_problems

INFEASIBLE_NOTE = "previous action was rejected as infeasible"


class RepairPolicy(FrozenModel):
    """How many times to ask in total for one decision, and whether to tell the model why."""

    max_attempts: int = Field(default=3, ge=1)
    include_rejection_reason: bool = True


class CallRecord(FrozenModel):
    """One backend call. ``repair_reason`` is why this call was a repair (the previous attempt's
    problem, or the infeasible note on a re-dispatch); ``failure`` is why this call's own result
    was rejected (``None`` if it was accepted). ``prompt`` / ``response`` only with ``record_text``.
    """

    decision_id: DecisionId
    attempt: int
    latency_s: float = 0.0
    usage: dict[str, int] = {}
    model: str = ""
    repair_reason: str | None = None
    failure: str | None = None
    prompt: list[Message] | None = None
    response: str | None = None


class LLMAgent(AgentAdapter):
    """Asks an ``LLMBackend`` for each action, checks it locally, repairs a bounded number of times.

    Each attempt renders the prompt with the codec, calls the backend, parses the reply and
    checks it against the request's JSON Schema. A rejected attempt adds its reason to the
    prompt of the next one. After ``repair.max_attempts`` failed attempts ``act`` raises
    ``AgentFailed``, which the runner turns into a timeout resolved by the layer's policy.
    A second dispatch of a decision this agent already answered means the session found the
    answer infeasible; the prompt then starts with that note.
    """

    def __init__(
        self,
        agent_id: AgentId,
        backend: LLMBackend,
        codec: DecisionCodec = DefaultCodec(),  # noqa: B008 (stateless)
        memory: Memory | None = Window(20),  # noqa: B008 (only a template, see Memory.fresh)
        repair: RepairPolicy = RepairPolicy(),  # noqa: B008 (frozen model)
        temperature: float = 0.0,
        max_tokens: int = 256,
        timeout_s: float = 60.0,
        report_renderer: ReportRenderer = DefaultReportRenderer(),  # noqa: B008 (stateless)
        *,
        record_text: bool = False,
    ) -> None:
        self.agent_id = agent_id
        self.backend = backend
        self.codec = codec
        self.repair = repair
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.report_renderer = report_renderer
        self.record_text = record_text
        self.last_decision_id: DecisionId | None = None
        self._memory: Memory = NoMemory() if memory is None else memory.fresh()
        self._provisional: list[tuple[DecisionId, list[Message]]] = []
        self._acted: set[DecisionId] = set()
        self._seen: set[str] = set()
        self._calls: list[CallRecord] = []

    # ------------------------------------------------------------- adapter protocol

    async def episode_start(self, ctx: EpisodeContext) -> None:
        """Forget the episode's bookkeeping (and the conversation unless ``keep_conversation``)."""
        if ctx.agent_id != self.agent_id:
            raise ValueError(f"episode for {ctx.agent_id!r} given to the agent {self.agent_id!r}")
        self._provisional.clear()
        self._acted.clear()
        self._seen.clear()
        self.last_decision_id = None
        if not ctx.keep_conversation:
            self._memory.clear()

    async def observe(self, report: ParticipantReport) -> None:
        """Remember a report addressed to this agent (once per ``report_id``)."""
        if report.agent_id != self.agent_id:
            raise ValueError(f"report for {report.agent_id!r} given to the agent {self.agent_id!r}")
        if report.report_id in self._seen:
            return
        self._seen.add(report.report_id)
        self._flush()
        self._memory.append([self.report_renderer.render_report(report)])

    async def act(self, request: DecisionRequest) -> dict[str, Any]:
        """A schema-valid action for ``request``, or ``AgentFailed`` after the allowed attempts."""
        if request.agent_id != self.agent_id:
            raise ValueError(
                f"request for {request.agent_id!r} given to the agent {self.agent_id!r}"
            )
        did = request.decision_id
        again = did in self._acted
        self._provisional = [(d, m) for d, m in self._provisional if d != did]
        self._flush()
        self._acted.add(did)
        self.last_decision_id = did
        policy = self.repair
        rejections: list[str] = [INFEASIBLE_NOTE] if again else []
        prior: str | None = INFEASIBLE_NOTE if again else None
        failures: list[str] = []
        history = self._memory.messages()
        for attempt in range(1, policy.max_attempts + 1):
            shown = list(rejections) if policy.include_rejection_reason else []
            prompt = self.codec.render(request, history, shown)
            token = current_decision.set(did)
            try:
                response = await self.backend.complete(
                    prompt,
                    request.action_schema,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    timeout_s=self.timeout_s,
                )
            except BackendError as e:
                problem = f"backend error: {scrub(str(e))}"
                self._record(did, attempt, None, prior, problem, prompt)
                failures.append(f"attempt {attempt}: {problem}")
                prior = problem
                continue
            finally:
                current_decision.reset(token)
            action, failure = self._check(response, request)
            self._record(did, attempt, response, prior, failure, prompt)
            if action is not None:
                turn = self._turn(request)
                reply = Message(role="assistant", content=json.dumps(action, sort_keys=True))
                self._provisional.append((did, [*turn, reply]))
                return copy.deepcopy(action)
            assert failure is not None
            rejections.append(failure)
            failures.append(f"attempt {attempt}: {failure}")
            prior = failure
        raise AgentFailed(failures)

    async def episode_end(self, end: EpisodeEnd) -> None:
        """Commit the last turn to memory; the conversation stays for ``keep_conversation``."""
        self._flush()

    # ------------------------------------------------------------------ inspection

    def metadata(self) -> list[CallRecord]:
        """One record per backend call so far (all episodes), oldest first."""
        return list(self._calls)

    # --------------------------------------------------------------------- internals

    def _check(
        self, response: Response, request: DecisionRequest
    ) -> tuple[dict[str, Any] | None, str | None]:
        """The parsed action, or the reason it was rejected."""
        try:
            action = self.codec.parse(response, request)
        except CodecParseError as e:
            return None, str(e)
        problems = against_json_schema(action, request.action_schema)
        if problems:
            return None, first_problems(problems)
        return action, None

    def _turn(self, request: DecisionRequest) -> list[Message]:
        """What this request adds to the conversation: its prompt without the system messages."""
        messages = self.codec.render(request, [], [])
        start = 0
        while start < len(messages) and messages[start].role == "system":
            start += 1
        return messages[start:]

    def _flush(self) -> None:
        """Move answered turns into memory (they stay provisional until something else happens)."""
        for _, messages in self._provisional:
            self._memory.append(messages)
        self._provisional.clear()

    def _record(
        self,
        did: DecisionId,
        attempt: int,
        response: Response | None,
        repair_reason: str | None,
        failure: str | None,
        prompt: list[Message],
    ) -> None:
        self._calls.append(
            CallRecord(
                decision_id=did,
                attempt=attempt,
                latency_s=0.0 if response is None else response.latency_s,
                usage={} if response is None else dict(response.usage),
                model="" if response is None else response.model,
                repair_reason=repair_reason,
                failure=failure,
                prompt=list(prompt) if self.record_text else None,
                response=response.text if self.record_text and response is not None else None,
            )
        )
