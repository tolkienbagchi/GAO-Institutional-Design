"""Pictures for the review page, drawn in Python as SVG text.

``flow_svg`` draws the process tree returned by ``composition.contracts.describe`` (the same
structure ``authoring.render.to_mermaid`` writes as text): one box per step, an arrow per route
labelled with its exit port, a frame per ``Loop`` (with its limit) and per ``Par``, and the ways
out. ``timeline_svg`` draws one run (an ``authoring.cards.Story``) as lanes: one per participant
and one for the rules, one band per step.

The SVG carries no ids, no URLs and no ``xmlns`` (it is meant to be inlined in HTML), styles
itself with CSS classes and ``currentColor`` only, escapes every piece of text, and is a pure
function of its arguments: the same input gives the same bytes. The page does no layout.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import html
from dataclasses import dataclass, field
from typing import Any

from gao_idl.authoring.cards import Story, _pairs, _value
from gao_idl.core.ids import role_sort_key
from gao_idl.review.tell import split_told, summarize

FONT = 'font-family="system-ui, -apple-system, Segoe UI, sans-serif"'
CHAR = 7.0  # estimated width of one character at 13px, for layout only
MAX_LANES = 8
MAX_STEPS = 12


def esc(text: Any) -> str:
    """``text`` escaped for SVG or HTML text and attribute values (``& < > " '``)."""
    return html.escape(str(text), quote=True)


def _num(v: float) -> str:
    """A coordinate without float noise: integers bare, else one decimal."""
    r = round(v, 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def _text_w(text: str, size: float = 13) -> float:
    return len(text) * CHAR * size / 13


def cut(text: str, n: int) -> str:
    """``text`` cut to ``n`` characters, ending in an ellipsis when cut."""
    return text if len(text) <= n else text[: n - 1] + "…"


def _svg(cls: str, w: float, h: float, title: str, body: list[str]) -> str:
    head = (
        f'<svg class="{cls}" viewBox="0 0 {_num(w)} {_num(h)}" width="{_num(w)}" '
        f'height="{_num(h)}" role="img" aria-label="{esc(title)}" {FONT} font-size="13">'
    )
    return "\n".join([head, f"<title>{esc(title)}</title>", *body, "</svg>"]) + "\n"


def _arrowhead(x: float, y: float, direction: str) -> str:
    """A small filled triangle pointing ``down``, ``left`` or ``right`` with its tip at (x, y)."""
    if direction == "down":
        pts = [(x, y), (x - 4, y - 7), (x + 4, y - 7)]
    elif direction == "left":
        pts = [(x, y), (x + 7, y - 4), (x + 7, y + 4)]
    else:
        pts = [(x, y), (x - 7, y - 4), (x - 7, y + 4)]
    joined = " ".join(f"{_num(a)},{_num(b)}" for a, b in pts)
    return f'<polygon class="head" points="{joined}" fill="currentColor"/>'


# ------------------------------------------------------------------ flow


GAP = 40  # vertical room between consecutive blocks (holds an edge label)
PAD = 14
RAIL = 12  # horizontal room reserved for one rail of edges on a block's right


@dataclass
class _Exit:
    x: float
    y: float
    rail: float | None = None  # x of the rail to follow down when passing other blocks


@dataclass
class _Edge:
    sx: float
    sy: float
    tx: float
    ty: float
    label: str
    rail: float | None = None
    up: bool = False  # feedback: from the bottom of the body back to its entry


@dataclass
class _Canvas:
    shapes: list[str] = field(default_factory=list)
    edges: list[_Edge] = field(default_factory=list)


@dataclass
class _Block:
    """A laid-out subtree: its size and a function placing it at (x, y)."""

    w: float
    h: float
    kind: str
    node: dict[str, Any]
    children: list["_Block"] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def _role_lines(roles: list[str], width: int = 44) -> list[str]:
    lines: list[str] = []
    current = ""
    for role in roles:
        piece = role if not current else f"{current}, {role}"
        if len(piece) > width and current:
            lines.append(current + ",")
            current = role
        else:
            current = piece
    if current:
        lines.append(current)
    return lines


def _measure(node: dict[str, Any]) -> _Block:
    kind = node.get("kind", "process")
    if kind == "seq":
        first = _measure(node["first"])
        thens = [_measure(node["routes"][p]["then"]) for p in sorted(node["routes"])]
        row_w = sum(t.w for t in thens) + 24 * max(0, len(thens) - 1)
        row_h = max((t.h for t in thens), default=0)
        passing = [p for p in _exit_ports(node["first"]) if p not in node["routes"]]
        rail = RAIL if passing and thens else 0
        w = max(first.w, row_w) + rail
        h = first.h + (GAP + row_h if thens else 0)
        return _Block(w, h, "seq", node, [first, *thens])
    if kind == "loop":
        body = _measure(node["body"])
        label = _loop_label(node)
        w = max(body.w + 2 * PAD + RAIL + 8, _text_w(label) + 2 * PAD)
        return _Block(w, body.h + 30 + PAD + 10, "loop", node, [body])
    if kind == "par":
        branches = [_measure(node["branches"][b]) for b in sorted(node["branches"])]
        row_w = sum(b.w for b in branches) + 24 * max(0, len(branches) - 1)
        row_h = max((b.h for b in branches), default=0)
        label = f"in parallel: {node['name']}"
        w = max(row_w, _text_w(label)) + 2 * PAD
        h = 30 + 24 + GAP + row_h + GAP + 24 + PAD
        return _Block(w, h, "par", node, branches)
    roles = list(node["entry"]["roles"]) if kind == "primitive" else []
    lines = [node["name"], *_role_lines(roles)]
    w = max(120.0, max(_text_w(t) for t in lines) + 28)
    return _Block(w, 20 + 18 * len(lines), "step", node, lines=lines)


def _exit_ports(node: dict[str, Any]) -> list[str]:
    """The exit ports a ``describe`` subtree leaves through, as ``to_mermaid`` finds them."""
    kind = node.get("kind", "process")
    if kind == "seq":
        ports = {p for p in _exit_ports(node["first"]) if p not in node["routes"]}
        for p in node["routes"]:
            ports |= set(_exit_ports(node["routes"][p]["then"]))
        return sorted(ports)
    if kind == "loop":
        ports = set()
        for p in _exit_ports(node["body"]):
            if p in node["feedback"]:
                continue
            ports.add(node["until"].get(p, p))
        return sorted(ports)
    return sorted(node["exits"])


def _loop_label(node: dict[str, Any]) -> str:
    limit = f" (limit {node['max_iter']})" if node["max_iter"] is not None else ""
    return f"repeat: {node['name']}{limit}"


def _place(
    b: _Block, x: float, y: float, c: _Canvas
) -> tuple[float, float, dict[str, list[_Exit]]]:
    """Draw ``b`` with its top-left corner at (x, y); return its entry point and exits by port."""
    if b.kind == "step":
        rx = x
        c.shapes.append(
            f'<g class="step"><rect x="{_num(rx)}" y="{_num(y)}" width="{_num(b.w)}" '
            f'height="{_num(b.h)}" rx="6" fill="none" stroke="currentColor"/>'
        )
        for i, line in enumerate(b.lines):
            weight = ' font-weight="600"' if i == 0 else ' font-size="12" class="roles"'
            c.shapes.append(
                f'<text x="{_num(rx + b.w / 2)}" y="{_num(y + 24 + 18 * i)}" '
                f'text-anchor="middle" fill="currentColor"{weight}>{esc(line)}</text>'
            )
        c.shapes.append("</g>")
        cx = rx + b.w / 2
        ports = sorted(b.node["exits"])
        step = min(36.0, b.w / (len(ports) + 1))
        first_x = cx - step * (len(ports) - 1) / 2
        return cx, y, {p: [_Exit(first_x + step * i, y + b.h)] for i, p in enumerate(ports)}
    if b.kind == "seq":
        first, *thens = b.children
        inner_w = b.w - (RAIL if len(b.children) > 1 and _passing(b) else 0)
        fx = x + (inner_w - first.w) / 2
        entry_x, entry_y, first_exits = _place(first, fx, y, c)
        exits: dict[str, list[_Exit]] = {}
        rail = x + b.w - RAIL / 2
        for port, sources in first_exits.items():
            if port not in b.node["routes"]:
                exits[port] = [_Exit(s.x, s.y, rail if thens else s.rail) for s in sources]
        row_w = sum(t.w for t in thens) + 24 * max(0, len(thens) - 1)
        tx = x + (inner_w - row_w) / 2
        ty = y + first.h + GAP
        for port, then in zip(sorted(b.node["routes"]), thens, strict=True):
            then_x, then_y, then_exits = _place(then, tx, ty, c)
            for s in first_exits.get(port, []):
                c.edges.append(_Edge(s.x, s.y, then_x, then_y, port, s.rail))
            for p, sources in then_exits.items():
                exits.setdefault(p, []).extend(sources)
            tx += then.w + 24
        return entry_x, entry_y, exits
    if b.kind == "loop":
        (body,) = b.children
        label = _loop_label(b.node)
        c.shapes.append(
            f'<g class="frame loop"><rect x="{_num(x)}" y="{_num(y)}" width="{_num(b.w)}" '
            f'height="{_num(b.h - 10)}" rx="10" fill="none" stroke="currentColor" '
            f'stroke-dasharray="6 4"/><text x="{_num(x + PAD)}" y="{_num(y + 19)}" '
            f'fill="currentColor" font-style="italic">{esc(label)}</text></g>'
        )
        entry_x, entry_y, body_exits = _place(body, x + PAD, y + 30, c)
        rail = x + b.w - PAD
        exits = {}
        for port, sources in sorted(body_exits.items()):
            if port in b.node["feedback"]:
                for s in sources:
                    c.edges.append(_Edge(s.x, s.y, entry_x, entry_y, port, rail, up=True))
            else:
                exits.setdefault(b.node["until"].get(port, port), []).extend(sources)
        return entry_x, entry_y, exits
    if b.kind == "par":
        label = f"in parallel: {b.node['name']}"
        c.shapes.append(
            f'<g class="frame par"><rect x="{_num(x)}" y="{_num(y)}" width="{_num(b.w)}" '
            f'height="{_num(b.h)}" rx="10" fill="none" stroke="currentColor" '
            f'stroke-dasharray="2 3"/><text x="{_num(x + PAD)}" y="{_num(y + 19)}" '
            f'fill="currentColor" font-style="italic">{esc(label)}</text></g>'
        )
        mid = x + b.w / 2
        fork_y = y + 30
        c.shapes.append(_pill(mid, fork_y, "split", "fork"))
        row_w = sum(br.w for br in b.children) + 24 * max(0, len(b.children) - 1)
        bx = x + (b.w - row_w) / 2
        by = fork_y + 24 + GAP
        join_y = by + max(br.h for br in b.children) + GAP
        for name, branch in zip(sorted(b.node["branches"]), b.children, strict=True):
            ex, ey, branch_exits = _place(branch, bx, by, c)
            c.edges.append(_Edge(mid, fork_y + 24, ex, ey, name))
            for port, sources in sorted(branch_exits.items()):
                for s in sources:
                    c.edges.append(_Edge(s.x, s.y, mid, join_y, port, s.rail))
            bx += branch.w + 24
        c.shapes.append(_pill(mid, join_y, "join", "fork"))
        return mid, fork_y, {p: [_Exit(mid, join_y + 24)] for p in sorted(b.node["exits"])}
    raise AssertionError(b.kind)


def _passing(b: _Block) -> bool:
    node = b.node
    return any(p not in node["routes"] for p in _exit_ports(node["first"]))


def _pill(cx: float, y: float, text: str, cls: str) -> str:
    w = max(64.0, _text_w(text) + 28)
    return (
        f'<g class="{cls}"><rect x="{_num(cx - w / 2)}" y="{_num(y)}" width="{_num(w)}" '
        f'height="24" rx="12" fill="none" stroke="currentColor"/><text x="{_num(cx)}" '
        f'y="{_num(y + 16.5)}" text-anchor="middle" fill="currentColor" font-size="12">'
        f"{esc(text)}</text></g>"
    )


def _draw_edges(edges: list[_Edge]) -> list[str]:
    out: list[str] = []
    incoming: dict[tuple[float, float], int] = {}
    outgoing: dict[tuple[float, float], int] = {}
    for e in edges:
        incoming[(e.tx, e.ty)] = incoming.get((e.tx, e.ty), 0) + 1
        outgoing[(e.sx, e.sy)] = outgoing.get((e.sx, e.sy), 0) + 1
    for e in edges:
        bend = e.ty - 14
        if e.up:
            below = e.sy + 12
            d = (
                f"M{_num(e.sx)} {_num(e.sy)} V{_num(below)} H{_num(e.rail or e.sx)} "
                f"V{_num(bend)} H{_num(e.tx)} V{_num(e.ty)}"
            )
            lx, ly, anchor = (e.rail or e.sx) - 4, (below + bend) / 2, "end"
        elif e.rail is not None:
            below = e.sy + 10
            d = (
                f"M{_num(e.sx)} {_num(e.sy)} V{_num(below)} H{_num(e.rail)} V{_num(bend)} "
                f"H{_num(e.tx)} V{_num(e.ty)}"
            )
            lx, ly, anchor = e.rail - 4, (below + bend) / 2, "end"
        else:
            if incoming[(e.tx, e.ty)] > 1:  # many arrive here: bend near the target
                d = f"M{_num(e.sx)} {_num(e.sy)} V{_num(bend)} H{_num(e.tx)} V{_num(e.ty)}"
            else:
                knee = e.sy + 12 if outgoing[(e.sx, e.sy)] > 1 else bend
                d = f"M{_num(e.sx)} {_num(e.sy)} V{_num(knee)} H{_num(e.tx)} V{_num(e.ty)}"
            if incoming[(e.tx, e.ty)] > 1:
                lx, ly, anchor = e.sx + 5, e.sy + 15, "start"
            else:
                lx, ly, anchor = e.tx + 5, e.ty - 5, "start"
        out.append(
            f'<g class="edge"><path d="{d}" fill="none" stroke="currentColor"/>'
            f"{_arrowhead(e.tx, e.ty, 'down')}"
            f'<text x="{_num(lx)}" y="{_num(ly)}" text-anchor="{anchor}" font-size="11" '
            f'class="port" fill="currentColor">{esc(e.label)}</text></g>'
        )
    return out


def flow_svg(tree: dict[str, Any]) -> str:
    """The process tree as an SVG flow chart, top to bottom in traversal order.

    One box per primitive step (with its declared roles), an arrow per route labelled with the
    exit port it follows, a dashed frame per ``Loop`` (with its limit; repeat arrows run up its
    right side) and per ``Par`` (branches side by side between a split and a join), and one end
    marker per way out of the whole process. ``tree`` is ``describe(process)``.
    """
    root = _measure(tree)
    canvas = _Canvas()
    margin = 16
    entry_x, entry_y, exits = _place(root, margin, margin, canvas)
    ends = sorted(exits)
    widths = [max(96.0, _text_w(f"ends: {p}") + 28) for p in ends]
    row = sum(widths) + 24 * max(0, len(ends) - 1)
    total_w = max(root.w, row) + 2 * margin
    ex = margin + (max(root.w, row) - row) / 2
    ey = margin + root.h + GAP
    for port, w in zip(ends, widths, strict=True):
        cx = ex + w / 2
        canvas.shapes.append(_pill(cx, ey, f"ends: {port}", "end"))
        for s in exits[port]:
            canvas.edges.append(_Edge(s.x, s.y, cx, ey, port, s.rail))
        ex += w + 24
    total_h = ey + (24 if ends else 0) + margin
    title = f"How the steps of {tree['name']} connect"
    return _svg("gao-flow", total_w, total_h, title, canvas.shapes + _draw_edges(canvas.edges))


# ------------------------------------------------------------------ timeline


MAX_WIDTH = 920  # the timeline is never wider than this, whatever the number of lanes
WHAT_MIN = 300  # the text column on the right
LANE_MAX = 90  # lane spacing when there is room
RULES_X = 44
ROW = 20
BAND = 26
TOP = 52
MIN_SCALE = 0.9  # 12-unit text stays at about 11 px or more: below this width the picture scrolls


def _names(names: list[str]) -> str:
    return ", ".join(names)


def timeline_svg(story: Story, title: str) -> str:
    """One run as a sequence picture: a lane per participant, one for the rules, at most 920 wide.

    Each step is a band whose header repeats the lane names. Each row has its arrow in the lanes
    and its text in a column on the right: an arrow from a participant who acted to the rules
    (dashed when a default was used, a dashed stub when left out), or from the rules to whoever
    was told something. What a step told everyone alike is one row ("everyone: ..."), drawn
    across all lanes; recipients told the same remaining fields share a row with a head on each of
    their lanes (``tell.split_told``). Row texts are ``tell.summarize``d, the full text is in a
    ``<title>``. Total payoffs sit under the lanes. Beyond 8 participants or 12 steps the first
    ones are drawn and the picture says how many more there are.
    """
    people: set[str] = set(story.utilities)
    for step in story.steps:
        people |= {m.agent for m in step.moves} | {t.agent for t in step.told}
    everyone = sorted(people, key=role_sort_key)
    lanes = everyone[:MAX_LANES]
    hidden_people = len(everyone) - len(lanes)
    n = max(1, len(lanes))
    room = MAX_WIDTH - 8 - WHAT_MIN - 16 - RULES_X
    spacing = min(float(LANE_MAX), room / (n + 0.5))
    lane_x = {a: RULES_X + spacing * (i + 1) for i, a in enumerate(lanes)}
    lanes_right = RULES_X + spacing * (n + 0.5)
    what_x = lanes_right + 16
    width = min(float(MAX_WIDTH), what_x + max(WHAT_MIN, 560) + 8)
    what_w = width - 8 - what_x
    limit = max(30, int(what_w / 6.1))  # characters of 12-unit text that fit in the column
    name_chars = max(3, int(spacing / 6.2))
    steps = story.steps[:MAX_STEPS]
    hidden_steps = len(story.steps) - len(steps)

    def what(y: float, text: str, extra: str = "") -> str:
        return (
            f'<text class="what" x="{_num(what_x)}" y="{_num(y + 14)}" font-size="12" '
            f'fill="currentColor"{extra}>{esc(text)}</text>'
        )

    def lane_names(y: float, size: int) -> list[str]:
        out = [
            f'<text x="{_num(RULES_X)}" y="{_num(y)}" text-anchor="middle" font-size="{size}" '
            f'fill="currentColor">rules</text>'
        ]
        for name, x in lane_x.items():
            out.append(
                f'<text class="lane-name" x="{_num(x)}" y="{_num(y)}" text-anchor="middle" '
                f'font-size="{size}" fill="currentColor"><title>{esc(name)}</title>'
                f"{esc(cut(name, name_chars))}</text>"
            )
        return out

    def heads(xs: list[float], y: float) -> str:
        return "".join(_arrowhead(x - 1, y, "right") for x in xs)

    body: list[str] = []
    rows = 0
    y = TOP
    for step in steps:
        heading = ", ".join(p for p in (step.place, step.layer) if p) or "the rules"
        told_rows = split_told(step.told)
        off = sum(1 for m in step.moves if m.agent not in lane_x)
        off += sum(1 for r in told_rows if r.to and not any(a in lane_x for a in r.to))
        note = ""
        if off:
            note = f"  +{off} not drawn"
        body.append(
            f'<g class="band"><rect x="4" y="{_num(y)}" width="{_num(width - 8)}" '
            f'height="{BAND}" rx="4" fill="currentColor" fill-opacity="0.07"/>'
            + "".join(lane_names(y + 17, 10))
            + f'<text x="{_num(what_x)}" y="{_num(y + 17)}" font-weight="600" font-size="13" '
            f'fill="currentColor"><title>{esc(heading.capitalize() + note)}</title>'
            f"{esc(cut(heading.capitalize(), limit - len(note)))}"
            f'<tspan dx="8" font-weight="400" font-style="italic" font-size="11">'
            f"{esc(note.strip())}</tspan>"
            "</text></g>"
        )
        y += BAND + 4
        for move in step.moves:
            if move.agent not in lane_x:
                continue
            px = lane_x[move.agent]
            if move.kind == "dropped":
                full = f"{move.agent} does not act and is left out of this step"
                body.append(
                    f'<g class="move dropped"><title>{esc(full)}</title>'
                    f'<path d="M{_num(px)} {_num(y + 10)} H{_num(px - spacing * 0.6)}" '
                    f'fill="none" stroke="currentColor" stroke-dasharray="4 3"/>'
                    + what(y, f"{move.agent}: did not act", ' font-style="italic"')
                    + "</g>"
                )
            else:
                action = move.action or {}
                verb = "has the default used" if move.kind == "defaulted" else "chooses"
                full = f"{move.agent} {verb}: {_pairs(action)}"
                prefix = (
                    f"{move.agent} (default): " if move.kind == "defaulted" else f"{move.agent}: "
                )
                dash = ' stroke-dasharray="5 3"' if move.kind == "defaulted" else ""
                body.append(
                    f'<g class="move {move.kind}"><title>{esc(full)}</title>'
                    f'<path d="M{_num(px)} {_num(y + 10)} H{_num(RULES_X + 8)}" fill="none" '
                    f'stroke="currentColor"{dash}/>{_arrowhead(RULES_X + 1, y + 10, "left")}'
                    + what(y, prefix + summarize(action, max(20, limit - len(prefix))))
                    + "</g>"
                )
            y += ROW
            rows += 1
        for row in told_rows:
            if row.to:
                xs = [lane_x[a] for a in row.to if a in lane_x]
                if not xs:
                    continue
                who = _names(row.to)
                cls = "told"
            else:  # what everyone told in this step got alike
                told_here = sorted({t.agent for t in step.told}, key=role_sort_key)
                xs = [lane_x[a] for a in told_here if a in lane_x]
                if not xs:
                    continue
                who = "everyone" if set(told_here) == set(everyone) else _names(told_here)
                cls = "told everyone"
            full = f"{who} {'is' if len(row.to) == 1 else 'are'} told: {_pairs(row.payload)}"
            prefix = f"{cut(who, 30)}: "
            body.append(
                f'<g class="{cls}"><title>{esc(full)}</title>'
                f'<path d="M{_num(RULES_X)} {_num(y + 10)} H{_num(max(xs) - 8)}" fill="none" '
                f'stroke="currentColor" stroke-opacity="0.8"/>{heads(xs, y + 10)}'
                + what(y, prefix + summarize(row.payload, max(20, limit - len(prefix))))
                + "</g>"
            )
            y += ROW
            rows += 1
        if step.stopped is not None:
            body.append(
                f'<g class="stopped"><title>{esc(step.stopped)}</title>'
                + what(y, f"■ stops: {cut(step.stopped, limit - 9)}", ' font-weight="600"')
                + "</g>"
            )
            y += ROW
            rows += 1
        if not step.moves and not step.told and step.stopped is None:
            body.append(what(y, "nobody is asked or told anything here", ' font-style="italic"'))
            y += ROW
            rows += 1
        y += 6
    if hidden_steps:
        body.append(
            f'<text x="10" y="{_num(y + 14)}" font-style="italic" font-size="12" '
            f'fill="currentColor">+{hidden_steps} more '
            f"{'step' if hidden_steps == 1 else 'steps'} not drawn (see the steps in words)</text>"
        )
        y += ROW
    bottom = y + 2

    top: list[str] = []
    for name, x in [("the rules", float(RULES_X)), *lane_x.items()]:
        rules = name == "the rules"
        cls = "lane rules" if rules else "lane"
        stroke = ' stroke-width="2"' if rules else ' stroke-dasharray="1 3"'
        label = "rules" if rules else cut(name, name_chars)
        top.append(
            f'<g class="{cls}"><title>{esc(name)}</title>'
            f'<text x="{_num(x)}" y="38" text-anchor="middle" font-weight="600" font-size="12" '
            f'fill="currentColor">{esc(label)}</text>'
            f'<path d="M{_num(x)} 44 V{_num(bottom)}" fill="none" stroke="currentColor"'
            f'{stroke} stroke-opacity="0.55"/></g>'
        )
    top.append(
        f'<text x="{_num(what_x)}" y="38" font-weight="600" font-size="12" '
        f'fill="currentColor">what</text>'
    )
    if hidden_people:
        more = "participant" if hidden_people == 1 else "participants"
        top.append(
            f'<text x="8" y="16" font-style="italic" font-size="12" fill="currentColor">'
            f"+{hidden_people} more {more} not drawn (see the steps in words)</text>"
        )
    pay: list[str] = [
        f'<text x="{_num(what_x)}" y="{_num(bottom + 20)}" fill="currentColor" font-size="12">'
        "← payoff in total</text>"
    ]
    for name, x in lane_x.items():
        value = _value(story.utilities[name]) if name in story.utilities else "—"
        pay.append(
            f'<g class="payoff"><title>{esc(name)}: {esc(value)}</title>'
            f'<text x="{_num(x)}" y="{_num(bottom + 20)}" text-anchor="middle" font-size="12" '
            f'font-weight="600" fill="currentColor">{esc(cut(value, name_chars + 1))}</text></g>'
        )
    height = bottom + 34
    head = (
        f'<svg class="gao-timeline" viewBox="0 0 {_num(width)} {_num(height)}" width="100%" '
        f'style="max-width:{_num(width)}px;min-width:{_num(width * MIN_SCALE)}px" '
        f'role="img" aria-label="{esc(title)}" data-rows="{rows}" {FONT} font-size="13">'
    )
    return "\n".join([head, f"<title>{esc(title)}</title>", *top, *body, *pay, "</svg>"]) + "\n"
