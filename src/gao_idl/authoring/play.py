"""Runs described by data: play an environment from a JSON-able script of actions.

An ``ActionScript`` maps each agent to the actions it takes, in the order its decisions are
issued. Scripts are keyed by agent, not by role id: the same role id (``row``) can occur once in
every branch of a ``Par``, while an agent's decisions always have one order.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from typing import Any

from gao_idl.core import events as ev
from gao_idl.core.ids import AgentId
from gao_idl.runtime.session import Session

ActionScript = dict[str, list[dict[str, Any] | None]]

MAX_DECISIONS = 10_000


def play(env_ref: str, params: dict[str, Any], seed: int, script: ActionScript) -> Session:
    """Run the environment to its end, answering every decision from ``script``.

    The i-th decision issued to an agent gets entry i of its list; a list shorter than the run
    repeats its last entry. ``None`` means the agent does not act: the decision is timed out and
    the layer's timeout policy resolves it. Decisions pending together are answered in
    decision-id order. An agent with a decision and no entry raises ``ValueError``; more than
    10 000 decisions raise ``RuntimeError``. A rejected action raises the session's error.
    """
    session = Session.start(env_ref, params, seed)
    answered: dict[str, int] = {}
    decisions = 0
    session.advance()
    while not session.finished:
        pending = sorted(session.pending(), key=lambda r: str(r.decision_id))
        if not pending:
            raise RuntimeError("the session is waiting but no decision is pending")
        for request in pending:
            agent = str(request.agent_id)
            entries = script.get(agent)
            if not entries:
                raise ValueError(f"the script has no action for agent {agent!r}")
            index = answered.get(agent, 0)
            action = entries[min(index, len(entries) - 1)]
            answered[agent] = index + 1
            decisions += 1
            if decisions > MAX_DECISIONS:
                raise RuntimeError(f"more than {MAX_DECISIONS} decisions; is the run unbounded?")
            if action is None:
                session.timeout(request.decision_id)
            else:
                session.submit(AgentId(agent), request.decision_id, action)
        session.advance()
    return session


def script_of(session: Session) -> ActionScript:
    """The script that reproduces ``session``: what each agent did, in the order it was asked.

    Use it to turn a run driven by adapters (scripted policies, a trained agent) into data for a
    card. Decisions are listed per agent in the order they were issued, which is the order
    ``play`` answers them in as long as the agent never has two decisions pending at once.
    """
    owner: dict[str, str] = {}
    issued: dict[str, list[str]] = {}
    for event in session.trace:
        if isinstance(event, ev.DecisionRequested):
            request = event.request
            owner[str(request.decision_id)] = str(request.agent_id)
            issued.setdefault(str(request.agent_id), []).append(str(request.decision_id))
    answers: dict[str, dict[str, Any] | None] = {}
    for entry in session.input_log:
        if isinstance(entry, ev.Submitted):
            answers[str(entry.decision_id)] = dict(entry.action)
        elif isinstance(entry, ev.TimedOut):
            answers[str(entry.decision_id)] = None
    return {
        agent: [answers[d] for d in decisions if d in answers]
        for agent, decisions in sorted(issued.items())
    }
