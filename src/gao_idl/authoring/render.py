"""Pictures and tables generated from the built environment, never written by hand or by a model.

``knowledge_table`` reads what each role actually saw, could choose and was told in real runs;
``to_mermaid`` and ``to_outline`` draw the process tree returned by
``composition.contracts.describe``; ``render_design`` assembles a reviewable markdown page from a
spec, the tree and the table. Output is a pure function of the arguments.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json
from collections.abc import Sequence
from typing import Any

from gao_idl.authoring.spec import OBSERVER, EnvSpec, role_kind
from gao_idl.composition.contracts import describe
from gao_idl.composition.process import Process
from gao_idl.core import events as ev
from gao_idl.core.schemas import FrozenModel
from gao_idl.runtime.registry import resolve
from gao_idl.runtime.session import Session


class KnowledgeRow(FrozenModel):
    """What one role kind saw, could choose and was told in one step (field names, sorted)."""

    step: str
    role: str
    sees: list[str]
    chooses: list[str]
    told: list[str]


class KnowledgeTable(FrozenModel):
    """Who knows what, by step and role kind, as observed in runs.

    ``step_order`` lists steps in the order they first asked for a decision or completed;
    ``timeouts`` maps every step in which some run asked for a decision to its timeout kind
    (``fail``, ``default`` or ``drop``).
    """

    rows: list[KnowledgeRow]
    timeouts: dict[str, str]
    step_order: list[str]

    def row(self, step: str, role: str) -> KnowledgeRow | None:
        """The row for ``step`` and ``role``, or None."""
        return next((r for r in self.rows if r.step == step and r.role == role), None)


def primitives(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """The primitive nodes of a ``describe`` tree, depth first, each name once."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: dict[str, Any]) -> None:
        kind = node.get("kind")
        if kind == "primitive":
            if node["name"] not in seen:
                seen.add(node["name"])
                found.append(node)
        elif kind == "seq":
            walk(node["first"])
            for port in sorted(node["routes"]):
                walk(node["routes"][port]["then"])
        elif kind == "par":
            for name in sorted(node["branches"]):
                walk(node["branches"][name])
        elif kind == "loop":
            walk(node["body"])

    walk(tree)
    return found


def has_loop(tree: dict[str, Any]) -> bool:
    """Whether a ``describe`` tree contains a ``Loop``."""
    kind = tree.get("kind")
    if kind == "loop":
        return True
    if kind == "seq":
        return has_loop(tree["first"]) or any(has_loop(r["then"]) for r in tree["routes"].values())
    if kind == "par":
        return any(has_loop(b) for b in tree["branches"].values())
    return False


def knowledge_table(sessions: Sequence[Session]) -> KnowledgeTable:
    """Union, over ``sessions``, of the fields each role kind saw, could choose and was told.

    An agent's kinds are those of the roles it held in any decision request of its session. A
    report delivered in a step where the agent held a role is listed under that role's kind;
    otherwise under every kind the agent held elsewhere, or under ``observer`` if it never
    decided.
    """
    cells: dict[tuple[str, str], dict[str, set[str]]] = {}
    order: list[str] = []
    timeouts: dict[str, str] = {}

    def cell(step: str, role: str) -> dict[str, set[str]]:
        return cells.setdefault((step, role), {"sees": set(), "chooses": set(), "told": set()})

    for session in sessions:
        snap = session.snapshot()
        process, _ = resolve(snap["env_ref"])(**snap["params"])
        for node in primitives(describe(process)):
            timeouts.setdefault(node["name"], node["timeout"])
        trace = session.trace
        held: dict[str, set[str]] = {}
        held_in: dict[tuple[str, str], set[str]] = {}
        for event in trace:
            if isinstance(event, ev.DecisionRequested):
                request = event.request
                kind = role_kind(str(request.role))
                held.setdefault(str(request.agent_id), set()).add(kind)
                held_in.setdefault((request.layer, str(request.agent_id)), set()).add(kind)
        for event in trace:
            layer = (
                event.request.layer
                if isinstance(event, ev.DecisionRequested)
                else event.layer
                if isinstance(event, ev.LayerCompleted)
                else None
            )
            if layer is not None and layer not in order:
                order.append(layer)
            if isinstance(event, ev.DecisionRequested):
                request = event.request
                entry = cell(request.layer, role_kind(str(request.role)))
                entry["sees"].update(request.observation)
                entry["chooses"].update(request.action_schema.get("properties", {}))
            elif isinstance(event, ev.ReportDelivered):
                report = event.report
                agent = str(report.agent_id)
                kinds = held_in.get((report.layer, agent)) or held.get(agent) or {OBSERVER}
                for kind in sorted(kinds):
                    cell(report.layer, kind)["told"].update(report.payload)
    position = {step: i for i, step in enumerate(order)}
    rows = [
        KnowledgeRow(
            step=step,
            role=role,
            sees=sorted(entry["sees"]),
            chooses=sorted(entry["chooses"]),
            told=sorted(entry["told"]),
        )
        for (step, role), entry in sorted(
            cells.items(), key=lambda kv: (position.get(kv[0][0], len(order)), kv[0])
        )
    ]
    decision_steps = {r.step for r in rows if r.chooses}
    kept = {s: k for s, k in sorted(timeouts.items()) if s in decision_steps}
    return KnowledgeTable(rows=rows, timeouts=kept, step_order=order)


# ------------------------------------------------------------------ structure


def to_outline(tree: dict[str, Any]) -> str:
    """The process tree as indented plain text, one line per process."""
    lines: list[str] = []

    def walk(node: dict[str, Any], depth: int, label: str) -> None:
        pad = "  " * depth
        kind = node.get("kind", "process")
        name = node["name"]
        exits = ", ".join(sorted(node["exits"]))
        if kind == "primitive":
            roles = node["entry"]["roles"]
            who = f" (roles: {', '.join(roles)}; if no action: {node['timeout']})" if roles else ""
            lines.append(f"{pad}{label}step {name}{who} -> {exits}")
        elif kind == "seq":
            lines.append(f"{pad}{label}sequence {name} -> {exits}")
            walk(node["first"], depth + 1, "first: ")
            for port in sorted(node["routes"]):
                walk(node["routes"][port]["then"], depth + 1, f"on {port}: ")
        elif kind == "par":
            lines.append(f"{pad}{label}parallel {name} -> {exits}")
            for branch in sorted(node["branches"]):
                walk(node["branches"][branch], depth + 1, f"branch {branch}: ")
        elif kind == "loop":
            again = ", ".join(sorted(node["feedback"])) or "nothing"
            until = ", ".join(f"{p} (as {t})" for p, t in sorted(node["until"].items()))
            limit = f" (limit {node['max_iter']})" if node["max_iter"] is not None else ""
            lines.append(
                f"{pad}{label}repeat {name}{limit}: again on {again}; stop on {until} -> {exits}"
            )
            walk(node["body"], depth + 1, "body: ")
        else:
            lines.append(f"{pad}{label}process {name} -> {exits}")

    walk(tree, 0, "")
    return "\n".join(lines) + "\n"


def to_mermaid(tree: dict[str, Any]) -> str:
    """The process tree as a Mermaid ``flowchart TD``: steps, routes by exit port, loops, branches.

    Steps are boxes (with their roles where the layer's contract declares them; a contract may
    leave dynamic role sets undeclared, so a bare name does not mean nobody decides), every arrow
    carries the exit port it follows, a ``Loop`` and a ``Par`` are subgraphs, and the ways out
    of the whole process are the final nodes. Node ids are assigned in traversal order, so the
    text is identical across runs.
    """
    lines: list[str] = ["flowchart TD"]
    edges: list[str] = []
    counter = [0]

    def new_id() -> str:
        counter[0] += 1
        return f"n{counter[0]}"

    def quote(text: str) -> str:
        return '"' + text.replace('"', "'") + '"'

    def walk(node: dict[str, Any], depth: int) -> tuple[str, dict[str, list[str]]]:
        """Declare ``node``; return its entry node id and, per exit port, the nodes it leaves."""
        pad = "  " * (depth + 1)
        kind = node.get("kind", "process")
        name = node["name"]
        if kind == "seq":
            entry, first_exits = walk(node["first"], depth)
            exits: dict[str, list[str]] = {
                p: list(s) for p, s in first_exits.items() if p not in node["routes"]
            }
            for port in sorted(node["routes"]):
                then_entry, then_exits = walk(node["routes"][port]["then"], depth)
                for source in first_exits.get(port, []):
                    edges.append(f"  {source} -->|{port}| {then_entry}")
                for p, sources in then_exits.items():
                    exits.setdefault(p, []).extend(sources)
            return entry, exits
        if kind == "loop":
            box = new_id()
            limit = f" (limit {node['max_iter']})" if node["max_iter"] is not None else ""
            lines.append(f"{pad}subgraph {box}[{quote(f'repeat: {name}{limit}')}]")
            entry, body_exits = walk(node["body"], depth + 1)
            lines.append(f"{pad}end")
            exits = {}
            for port, sources in sorted(body_exits.items()):
                if port in node["feedback"]:
                    for source in sources:
                        edges.append(f"  {source} -->|{port}| {entry}")
                elif port in node["until"]:
                    exits.setdefault(node["until"][port], []).extend(sources)
                else:
                    exits.setdefault(port, []).extend(sources)
            return entry, exits
        if kind == "par":
            box = new_id()
            lines.append(f"{pad}subgraph {box}[{quote(f'in parallel: {name}')}]")
            fork = new_id()
            join = new_id()
            lines.append(f"{pad}  {fork}{{{{{quote('split')}}}}}")
            for branch in sorted(node["branches"]):
                branch_entry, branch_exits = walk(node["branches"][branch], depth + 1)
                edges.append(f"  {fork} -->|{branch}| {branch_entry}")
                for port, sources in sorted(branch_exits.items()):
                    for source in sources:
                        edges.append(f"  {source} -->|{port}| {join}")
            lines.append(f"{pad}  {join}{{{{{quote('join')}}}}}")
            lines.append(f"{pad}end")
            return fork, {port: [join] for port in sorted(node["exits"])}
        me = new_id()
        roles = ", ".join(node["entry"]["roles"]) if kind == "primitive" else ""
        lines.append(f"{pad}{me}[{quote(f'{name}: {roles}' if roles else name)}]")
        return me, {port: [me] for port in sorted(node["exits"])}

    _, exits = walk(tree, 0)
    for port in sorted(exits):
        end = new_id()
        lines.append(f"  {end}([{quote(f'ends: {port}')}])")
        for source in exits[port]:
            edges.append(f"  {source} -->|{port}| {end}")
    return "\n".join(lines + edges) + "\n"


# --------------------------------------------------------------------- design

_NO_ACTION = {
    "fail": "the run stops",
    "default": "a default action is used in its place",
    "drop": "the participant is left out of the step",
}


def _fields(names: Sequence[str]) -> str:
    return ", ".join(f"`{n}`" for n in names) if names else "nothing"


def render_design(spec: EnvSpec, process: Process, table: KnowledgeTable) -> str:
    """A markdown page for a person to review: the spec in words, with the built structure.

    Sections follow ``skills/explain-environment``. The round-by-round text, secrets, payoffs and
    assumptions come from ``spec``; the diagram and the timeout kinds from ``process``; the
    who-knows-what table from ``table`` (what the built environment did in runs). Compare the
    last with the spec using ``check.check_spec``.
    """
    tree = describe(process)
    out: list[str] = [f"# {spec.title}", "", spec.summary, ""]

    out += ["## Who takes part", ""]
    for role in spec.roles:
        many = " (several)" if role.many else ""
        out.append(f"- **{role.name}**{many}: {role.description}")
    out.append("")

    out += ["## Step by step", ""]
    for i, step in enumerate(spec.steps, 1):
        if step.kind == "automatic":
            out.append(f"{i}. **{step.name}** (automatic, nobody decides): {step.description}")
        else:
            who = "; ".join(
                f"{role} chooses {_fields(step.chooses[role])}" for role in sorted(step.chooses)
            )
            out.append(f"{i}. **{step.name}**: {step.description} At the same time: {who}.")
    out.append("")

    out += ["## How the steps connect", "", "```mermaid", to_mermaid(tree).rstrip(), "```", ""]
    out += ["```text", to_outline(tree).rstrip(), "```", ""]

    out += [
        "## Who knows what",
        "",
        "Taken from runs of the built environment, not from the description above.",
        "",
        "| Step | Role | Sees when deciding | Chooses | Is told afterwards |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in table.rows:
        sees = _fields(row.sees) if row.chooses else "(does not decide)"
        chooses = _fields(row.chooses) if row.chooses else "(does not decide)"
        out.append(f"| {row.step} | {row.role} | {sees} | {chooses} | {_fields(row.told)} |")
    if not table.rows:
        out.append("| (no runs were given) | | | | |")
    out.append("")

    automatic = [s.name for s in spec.steps if s.kind == "automatic"]
    chosen = [s.name for s in spec.steps if s.kind == "decision"]
    out += [
        "## What is committed and what is chosen",
        "",
        f"- Chosen live by participants: {', '.join(chosen) or 'nothing'}.",
        f"- Fixed by the rules (automatic): {', '.join(automatic) or 'nothing'}.",
        "",
    ]

    out += ["## If someone does not act", ""]
    for step in spec.steps:
        if step.kind != "decision":
            continue
        built = next((n["timeout"] for n in primitives(tree) if n["name"] == step.name), None)
        effect = _NO_ACTION.get(built or "", "unknown: the step is not in the built environment")
        meaning = f" Meaning: {step.no_action_means}" if step.no_action_means else ""
        out.append(f"- **{step.name}**: {effect}.{meaning}")
    out.append("")

    out += ["## What repeats and what carries over", ""]
    if spec.repeats is None:
        out.append("Nothing repeats.")
    else:
        rounds = ""
        if spec.repeats.rounds_param is not None:
            value = json.dumps(spec.params[spec.repeats.rounds_param])
            rounds = f" The number of rounds is `{spec.repeats.rounds_param}` = {value}."
        out.append(f"{spec.repeats.what}{rounds}")
        if spec.repeats.carries_over:
            out.append("")
            out += [f"- Carries over: {item}" for item in spec.repeats.carries_over]
    out.append("")

    out += ["## What must stay secret", ""]
    if not spec.secrets:
        out.append("Nothing was declared secret.")
    for secret in spec.secrets:
        owners = (
            f" (except {', '.join(secret.except_agents)}, who may know it)"
            if secret.except_agents
            else ""
        )
        out.append(
            f"- {secret.what} Must never reach: {', '.join(secret.hidden_from)}{owners}. "
            f"Tested by changing `{secret.param}`."
        )
    out.append("")

    out += ["## Payoffs", ""]
    if not spec.payoffs:
        out.append("No payoffs were declared.")
    for payoff in spec.payoffs:
        told = (
            f"told to them in the report field `{payoff.report_field}`"
            if payoff.report_field
            else "not told to them"
        )
        out.append(f"- **{payoff.role}**: {payoff.description} It is {told}.")
    out.append("")

    if spec.invariants:
        out += ["## What must always hold", ""]
        for inv in spec.invariants:
            checked = "checked automatically" if inv.predicate else "not checked automatically"
            out.append(f"- {inv.statement} ({checked})")
        out.append("")

    out += ["## Parameters of this design", ""]
    for key in sorted(spec.params):
        out.append(f"- `{key}` = `{json.dumps(spec.params[key], sort_keys=True)}`")
    out.append("")

    out += [
        "## What is stylized",
        "",
        "The numbers are illustrative. This page describes the rules of the simulated institution; "
        "it makes no claim that they match a real market, city or organization.",
        "",
    ]

    open_items = [a for a in spec.assumptions if not a.confirmed]
    out += ["## Choices nobody has confirmed yet", ""]
    if not open_items:
        out.append("None.")
    for item in open_items:
        ref = f" (question {item.question})" if item.question is not None else ""
        out.append(f"- [ ] {item.text}{ref}")
    out.append("")
    return "\n".join(out)
