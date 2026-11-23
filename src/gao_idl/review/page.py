"""The review page: one self-contained HTML file generated from a bundle.

``render_page`` writes the five screens (what was checked, the design, choices nobody
confirmed, the cards one per screen, finish) as HTML, with every piece of text from the spec,
the cards or a parameter escaped, and the pictures inlined from the bundle. The bundle itself
is embedded as ``<script type="application/json">``; ``page.js`` reads it to record and export
verdicts. The page makes no network request unless ``api`` is given (then it also posts each
verdict there), and a Content-Security-Policy says so to the browser.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import hashlib
import json
import re
from importlib import resources
from typing import Any

from gao_idl._version import __version__
from gao_idl.authoring.cards import Story, _pairs, _value
from gao_idl.authoring.spec import EnvSpec
from gao_idl.review.bundle import Bundle, CardView, dump_bundle
from gao_idl.review.draw import esc, timeline_svg
from gao_idl.review.tell import (
    Tile,
    addressed,
    participants,
    plain_ending,
    run_steps,
    split_told,
    words,
)

FIXED_SENTENCE = "These checks compare the code with the design, not the design with the world."
_SLOT = re.compile(r"\{\{([A-Z]+)\}\}")


def asset(name: str) -> str:
    """The text of a file shipped with this package (``page.html``, ``page.css``, ``page.js``)."""
    return resources.files("gao_idl.review").joinpath(name).read_text(encoding="utf-8")


def _json_for_script(data: Any) -> str:
    """JSON safe inside ``<script>``: ``<``, ``>`` and ``&`` as ``\\u`` escapes (so no ``</``)."""
    text = json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _csp(api: str | None) -> str:
    if api is None:
        connect = "'none'"
    elif re.fullmatch(r"/(?!/)[A-Za-z0-9._~/\-]*", api):
        connect = "'self'"
    else:
        match = re.fullmatch(r"(https?://[A-Za-z0-9.\-]+(?::\d+)?)(/[A-Za-z0-9._~/\-]*)?", api)
        if match is None:
            raise ValueError(
                f"api must be a path such as '/verdicts' or an http(s) URL without a query: {api!r}"
            )
        connect = match.group(1)
    return (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        f"connect-src {connect}; base-uri 'none'; form-action 'none'"
    )


def _p(text: str, cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    return f"<p{attr}>{esc(text)}</p>"


def _ul(items: list[str], cls: str = "") -> str:
    """A list of already-escaped HTML items."""
    attr = f' class="{cls}"' if cls else ""
    return f"<ul{attr}>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ------------------------------------------------------------------ screens


def _checked(bundle: Bundle) -> str:
    st = bundle.status
    from_cards = st.runs - st.fuzzed
    out = [
        '<section class="screen" id="screen-0" aria-labelledby="h-checked">',
        '<h2 id="h-checked">Checks</h2>',
        _p(
            f"The environment in code was run {_plural(st.runs, 'time', 'times')}: "
            f"{_plural(from_cards, 'run', 'runs')} from the scenario cards and "
            f"{_plural(st.fuzzed, 'run', 'runs')} chosen automatically to probe edge cases. "
            "Each run was compared with the design.",
            "lead",
        ),
        "<h3>Differences found between the design and the code</h3>",
    ]
    if st.mismatches:
        out.append(
            _ul(
                [
                    f"{esc(m.message)} <span class='muted'>({esc(m.kind)})</span>"
                    for m in st.mismatches
                ]
            )
        )
    else:
        out.append(_p("None in these runs."))
    out.append("<h3>Rules of the design that nothing checks automatically</h3>")
    if st.unchecked_invariants:
        out.append(_ul([esc(s) for s in st.unchecked_invariants]))
    else:
        out.append(_p("None: every stated rule has an automatic check."))
    c = st.cards
    out += [
        "<h3>Where the review stands</h3>",
        _ul(
            [
                esc(
                    f"Scenario cards: {c.get('proposed', 0)} waiting for a verdict, "
                    f"{c.get('confirmed', 0)} confirmed, {c.get('rejected', 0)} rejected."
                ),
                esc(f"Choices nobody has confirmed: {st.assumptions_open}."),
            ]
        ),
        _p(FIXED_SENTENCE, "fixed"),
        '<div class="actions only-js"><button type="button" data-next-screen="1">'
        "Next: the design</button></div>",
        "</section>",
    ]
    return "\n".join(out)


def _fields(names: list[str]) -> str:
    return ", ".join(names) if names else "nothing"


def _design(bundle: Bundle) -> str:
    spec = bundle.spec
    out = [
        '<section class="screen" id="screen-1" aria-labelledby="h-design">',
        f'<h2 id="h-design">Design: {esc(spec.title)}</h2>',
        _p(spec.summary, "lead"),
        "<h3>Who takes part</h3>",
        _ul(
            [
                f"<strong>{esc(r.name)}</strong>{' (several)' if r.many else ''}: "
                f"{esc(r.description)}"
                for r in spec.roles
            ]
        ),
        "<h3>Step by step</h3>",
        '<ol class="steps">',
    ]
    for step in spec.steps:
        if step.kind == "automatic":
            who = " <span class='muted'>(automatic: nobody decides)</span>"
        else:
            who = (
                " At the same time: "
                + esc(
                    "; ".join(
                        f"{r} chooses {_fields(step.chooses[r])}" for r in sorted(step.chooses)
                    )
                )
                + "."
            )
        out.append(f"<li><strong>{esc(step.name)}</strong>: {esc(step.description)}{who}</li>")
    out.append("</ol>")
    if spec.repeats is not None:
        rounds = ""
        if spec.repeats.rounds_param is not None:
            value = json.dumps(spec.params.get(spec.repeats.rounds_param), sort_keys=True)
            rounds = f" The number of rounds is {spec.repeats.rounds_param} = {value}."
        out.append(_p(f"What repeats: {spec.repeats.what}{rounds}"))
        if spec.repeats.carries_over:
            out.append(_ul([esc(f"Carries over: {x}") for x in spec.repeats.carries_over]))
    out += [
        "<h3>How the steps connect</h3>",
        f'<div class="picture">{bundle.flow_svg}</div>',
        "<details><summary>The same structure as text</summary>",
        f"<pre>{esc(bundle.outline)}</pre></details>",
        "<h3>Who knows what</h3>",
        _p("Taken from runs of the built environment, not from the description above.", "muted"),
        '<div class="table-wrap"><table class="knows"><thead><tr><th scope="col">Step</th>'
        '<th scope="col">Role</th><th scope="col">Sees when deciding</th>'
        '<th scope="col">Chooses</th><th scope="col">Is told afterwards</th></tr></thead><tbody>',
    ]
    for row in bundle.table.rows:
        decides = bool(row.chooses)
        sees = _fields(row.sees) if decides else "(does not decide)"
        chooses = _fields(row.chooses) if decides else "(does not decide)"
        cells = [row.step, row.role, sees, chooses, _fields(row.told)]
        out.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>")
    if not bundle.table.rows:
        out.append('<tr><td colspan="5">No runs were given.</td></tr>')
    out += ["</tbody></table></div>", "<h3>What must stay secret</h3>"]
    if spec.secrets:
        items = []
        for s in spec.secrets:
            owners = (
                f" (except {', '.join(s.except_agents)}, who may know it)"
                if s.except_agents
                else ""
            )
            items.append(esc(f"{s.what} Must never reach: {', '.join(s.hidden_from)}{owners}."))
        out.append(_ul(items))
    else:
        out.append(_p("Nothing was declared secret."))
    out.append("<h3>Payoffs</h3>")
    if spec.payoffs:
        items = []
        for pay in spec.payoffs:
            told = (
                f"told in the report field {pay.report_field}" if pay.report_field else "not told"
            )
            items.append(
                f"<strong>{esc(pay.role)}</strong>: {esc(pay.description)} {esc(f'It is {told}.')}"
            )
        out.append(_ul(items))
    else:
        out.append(_p("No payoffs were declared."))
    out.append("<h3>Parameters of this design</h3>")
    out.append(
        _ul(
            [
                f"<span class='mono'>{esc(k)}</span> = "
                f"<span class='mono'>{esc(json.dumps(spec.params[k], sort_keys=True))}</span>"
                for k in sorted(spec.params)
            ]
        )
    )
    out += [
        '<div class="actions only-js"><button type="button" data-next-screen="2">'
        "Next: choices nobody confirmed</button></div>",
        "</section>",
    ]
    return "\n".join(out)


def _assumptions(bundle: Bundle) -> str:
    out = [
        '<section class="screen" id="screen-2" aria-labelledby="h-choices">',
        '<h2 id="h-choices">Choices</h2>',
        _p(
            "Each of these is a modelling decision the author of the environment made because "
            "the description did not settle it. Say whether you agree.",
            "lead",
        ),
    ]
    items = [(i, a) for i, a in enumerate(bundle.spec.assumptions) if not a.confirmed]
    if not items:
        out.append(_p("None: every choice has been confirmed."))
    else:
        out.append('<ol class="choices">')
        for i, a in items:
            question = f" (design question {a.question})" if a.question is not None else ""
            out += [
                f'<li class="assumption" data-index="{i}">',
                f"<p>{esc(a.text)}<span class='muted'>{esc(question)}</span></p>",
                '<div class="actions only-js">',
                '<button type="button" data-verdict="agreed" aria-pressed="false">Agree</button>',
                '<button type="button" data-open-note="1" aria-pressed="false" '
                f'aria-expanded="false" aria-controls="panel-a{i}">Disagree…</button>',
                "</div>",
                f'<div class="note-panel only-js" id="panel-a{i}" hidden>',
                f'<label for="note-a{i}">Why do you disagree, and what should it be instead?'
                "</label>",
                f'<textarea id="note-a{i}" class="note" rows="3"></textarea>',
                '<div class="actions">',
                '<button type="button" class="primary" data-submit-note="1">'
                "Submit: I disagree</button>",
                '<button type="button" data-cancel-note="1">Cancel</button>',
                "</div>",
                '<p class="hint muted">Enter submits. Shift+Enter starts a new line.</p>',
                "</div>",
                '<p class="answer only-js"></p>',
                "</li>",
            ]
        out.append("</ol>")
    out += [
        '<div class="actions only-js"><button type="button" data-next-screen="3">'
        "Next: the cards</button></div>",
        "</section>",
    ]
    return "\n".join(out)


def _where(place: str, layer: str) -> str:
    return f"{place}, step {layer}" if place else f"step {layer}"


PAGE_SCALE = "Bars use one scale for every card on this page."
RUN_SCALE = "Bars are scaled to this run's largest payoff."


def _payoffs_html(story: Story, top: float, scale: str = PAGE_SCALE) -> list[str]:
    """Final payoffs as bars; ``top`` is the largest payoff size on the page (one scale)."""
    if not story.utilities:
        return []
    rows = []
    for agent in participants(story):
        if agent not in story.utilities:
            continue
        value = story.utilities[agent]
        share = 0 if top <= 0 else round(min(abs(value) / top, 1.0) * 100, 1)
        side = "neg" if value < 0 else "pos"
        rows.append(
            f'<li><span class="who">{esc(agent)}</span>'
            f'<span class="track"><span class="bar {side}" style="width:{share}%"></span></span>'
            f'<span class="value">{esc(_value(value))}</span></li>'
        )
    return [
        '<div class="run-step payoffs">',
        "<h4>Final payoffs</h4>",
        '<ul class="bars">' + "".join(rows) + "</ul>",
        _p(f"{scale} A hatched bar is a loss.", "muted small"),
        "</div>",
    ]


def _simple(value: Any) -> bool:
    return not isinstance(value, dict | list)


def _rows(value: Any) -> bool:
    """Whether ``value`` is a table: a non-empty dict of non-empty dicts of simple values."""
    return (
        isinstance(value, dict)
        and bool(value)
        and all(
            isinstance(r, dict) and r and all(_simple(v) for v in r.values())
            for r in value.values()
        )
    )


def _table(value: dict[str, dict[str, Any]]) -> str:
    cols = sorted({c for r in value.values() for c in r})
    head = "<tr><th></th>" + "".join(f"<th>{esc(words(c))}</th>" for c in cols) + "</tr>"
    body = "".join(
        f"<tr><th>{esc(words(str(k)))}</th>"
        + "".join(f"<td>{esc(_value(value[k][c])) if c in value[k] else ''}</td>" for c in cols)
        + "</tr>"
        for k in sorted(value)
    )
    return f'<table class="choice">{head}{body}</table>'


def _lines(payload: dict[str, Any]) -> str:
    """A payload as label and value lines; a nested value becomes a small table or a count."""
    out = []
    tables = []
    for key in sorted(payload):
        value = payload[key]
        if _simple(value):
            out.append(f"<dt>{esc(words(key))}</dt><dd>{esc(_value(value))}</dd>")
        elif isinstance(value, dict) and value and all(_simple(v) for v in value.values()):
            inner = ", ".join(f"{words(str(k))} {_value(value[k])}" for k in sorted(value))
            out.append(f"<dt>{esc(words(key))}</dt><dd>{esc(inner)}</dd>")
        elif _rows(value):
            tables.append(f'<p class="what">{esc(words(key))}</p>{_table(value)}')
        else:
            n = len(value)
            count = f"{n} {'entry' if n == 1 else 'entries'}, in the full record"
            out.append(f'<dt>{esc(words(key))}</dt><dd class="muted">{esc(count)}</dd>')
    return ("<dl>" + "".join(out) + "</dl>" if out else "") + "".join(tables)


def _choice(action: dict[str, Any]) -> str:
    """A choice inside a tile: one value large, a few as lines, a table of rows as a table."""
    if not action:
        return '<p class="did">nothing</p>'
    if len(action) == 1 and _simple(next(iter(action.values()))):
        ((key, value),) = action.items()
        return f'<p class="what">{esc(words(key))}</p><p class="did">{esc(_value(value))}</p>'
    if _rows(action):
        return _table(action)
    return _lines(action)


def _tile(tile: Tile) -> str:
    out = [f'<li class="tile"><p class="who">{esc(tile.agent)}']
    if tile.kind == "chose":
        out.append(" chose</p>" + _choice(tile.action or {}))
    elif tile.kind == "defaulted":
        out.append(
            ' did not answer</p><p class="what">the standard choice was used</p>'
            + _choice(tile.action or {})
        )
    elif tile.kind == "dropped":
        out.append(' did not answer</p><p class="what">left out of this step</p>')
    else:
        out.append("</p>")
    if tile.told:
        out.append('<p class="what private">told privately</p>' + _lines(tile.told))
    out.append("</li>")
    return "".join(out)


def _run_html(story: Story, top: float, scale: str = PAGE_SCALE) -> list[str]:
    """The run by participant: a block per step, a tile per participant, then the payoffs."""
    out = ["<h3>What happened</h3>"]
    for step in run_steps(story):
        out.append('<div class="run-step">')
        out.append(f"<h4>{esc(step.where)}</h4>")
        if step.rule:
            out.append(_p(step.rule, "rule muted"))
        if step.tiles:
            out.append('<ul class="tiles">' + "".join(_tile(t) for t in step.tiles) + "</ul>")
        for said in step.announced:
            # equal messages, not a broadcast: a field named "own payoff" can be here too
            to = "Everyone was told the same" if said.to == "everyone" else f"Told to {said.to}"
            out.append(
                f'<div class="announced"><p class="what">{esc(to)}</p>{_lines(said.payload)}</div>'
            )
        if step.stopped is not None:
            out.append(f'<p class="stopped"><strong>{esc(step.stopped)}</strong></p>')
        out.append("</div>")
    out.append(_p(" ".join(plain_ending(story)[:1]), "ending"))
    out += _payoffs_html(story, top, scale)
    return out


def render_run(story: Story, title: str, top: float | None = None) -> str:
    """One run as a scenario card shows it: "What happened", the payoff bars, a folded diagram.

    An HTML fragment, rendered and escaped here: the run by participant, the final payoffs as
    bars scaled to ``top`` (default: the largest payoff size in ``story``), and the
    ``timeline_svg(story, title)`` folded under "Detailed diagram". The same markup as a card of
    ``render_page`` shows for that story.
    """
    scale = PAGE_SCALE
    if top is None:
        top = max((abs(u) for u in story.utilities.values()), default=0.0)
        scale = RUN_SCALE
    out = _run_html(story, top, scale)
    out.append(
        _fold(
            "Detailed diagram",
            "every message, in order",
            f'<div class="picture">{timeline_svg(story, title)}</div>',
            False,
        )
    )
    return "\n".join(out)


def _steps_items(story: Story) -> list[str]:
    out: list[str] = []
    for step in story.steps:
        lines: list[str] = []
        for move in step.moves:
            if move.kind == "chose":
                lines.append(esc(f"{move.agent} chooses {_pairs(move.action or {})}."))
            elif move.kind == "defaulted":
                lines.append(
                    esc(
                        f"{move.agent} does not act; the default is used instead: "
                        f"{_pairs(move.action or {})}."
                    )
                )
            else:
                lines.append(esc(f"{move.agent} does not act and is left out of this step."))
        if step.stopped is not None:
            lines.append(f"<strong>{esc(step.stopped)}</strong>")
        if step.layer:
            head = esc(_where(step.place, step.layer).capitalize() + ".")
            text = f" {esc(step.description)}" if step.description else ""
            out.append(f"<li><em>{head}</em>{text}{_ul(lines) if lines else ''}</li>")
        elif lines:
            out.append(f"<li>{_ul(lines)}</li>")
    return out


def _told_items(story: Story) -> list[str]:
    """One line per row of ``split_told``: per step, what all got first, then each one's own."""
    everyone = participants(story)
    out: list[str] = []
    for step in story.steps:
        for row in split_told(step.told):
            who = addressed(row, step.told, everyone)
            out.append(esc(f"{who}, after {_where(step.place, step.layer)}: {_pairs(row.payload)}"))
    return out


def _fold(title: str, count: str, body: str, is_open: bool) -> str:
    """A ``<details>`` block (works without scripts) whose summary says what is inside."""
    attr = " open" if is_open else ""
    return (
        f'<details class="fold"{attr}><summary><h3>{esc(title)}</h3> '
        f'<span class="muted">({esc(count)})</span></summary>{body}</details>'
    )


def _story_html(story: Story) -> list[str]:
    """The full record: every step and every message, both folded shut."""
    steps = _steps_items(story)
    told = _told_items(story)
    out = ["<h3>The full record</h3>"]
    out.append(
        _p("Every choice and every message of this run, exactly as the environment made it.")
    )
    out.append(
        _fold(
            "Every step",
            _plural(len(steps), "step", "steps"),
            '<ol class="steps">' + "".join(steps) + "</ol>",
            False,
        )
    )
    out.append(
        _fold(
            "Everything each participant was told",
            _plural(len(told), "message", "messages"),
            _ul(told) if told else _p("Nobody is told anything."),
            False,
        )
    )
    return out


def _situation(view: CardView, spec: EnvSpec) -> list[str]:
    card = view.card
    shown = sorted(card.params)
    out = ["<h3>The setup</h3>"]
    lead = ""
    if spec.env_ref == card.env_ref:
        shown = [k for k in shown if k not in spec.params or spec.params[k] != card.params[k]]
        dropped = sorted(k for k in spec.params if k not in card.params)
        lead = (
            "The same as the design, except:"
            if shown or dropped
            else "The same as the design, with nothing changed."
        )
        items = [esc(f"{k}: left at its default") for k in dropped]
    else:
        items = []
    items += [esc(f"{k}: {_value(card.params[k])}") for k in shown]
    if lead:
        out.append(_p(lead))
    if items:
        out.append(_ul(items))
    return out


def _card(view: CardView, k: int, n: int, spec: EnvSpec, top: float) -> str:
    card = view.card
    i = k - 1
    out = [
        f'<article class="card" data-index="{i}" aria-labelledby="h-card{i}">',
        f'<p class="progress">Card {k} of {n}</p>',
        f'<h2 id="h-card{i}" tabindex="-1">{esc(card.title)}</h2>',
        _p(card.why, "lead"),
    ]
    if view.stale:
        out.append(
            '<p class="warning" role="note"><strong>This card is out of date.</strong> '
            "The environment no longer does what this card recorded. What follows is what it "
            "does now. A card that is out of date cannot be confirmed; you can still say No.</p>"
        )
    out += _situation(view, spec)
    out += _run_html(view.story, top)
    out.append(
        _fold(
            "Detailed diagram",
            "every message, in order",
            f'<div class="picture">{view.timeline_svg}</div>',
            False,
        )
    )
    out += _story_html(view.story)
    if card.note:
        out.append(_p(f"Note already on this card: {card.note}", "muted"))
    out.append(_p(f"Card name: {card.id}.", "muted small"))
    yes_disabled = " disabled" if view.stale else ""
    out += [
        f'<div class="verdict only-js" role="group" aria-labelledby="ask-c{i}">',
        f'<p class="ask"><strong id="ask-c{i}">Is this what you meant?</strong> '
        '<span class="answer"></span></p>',
        '<div class="actions">',
        f'<button type="button" data-act="yes" aria-pressed="false"{yes_disabled}>'
        "Yes, this is what I meant</button>",
        '<button type="button" data-act="no" aria-pressed="false" aria-expanded="false" '
        f'aria-controls="panel-c{i}">No…</button>',
        '<button type="button" data-act="skip">Skip</button>',
        '<button type="button" data-act="back">Back</button>',
        '<button type="button" data-clear="1">Remove answer</button>',
        "</div>",
        f'<div class="note-panel" id="panel-c{i}" hidden>',
        f'<label for="note-c{i}">What should happen instead?</label>',
        f'<textarea id="note-c{i}" class="note" rows="3"></textarea>',
        '<div class="actions">',
        '<button type="button" class="primary" data-submit-note="1">'
        "Submit: No, with this note</button>",
        '<button type="button" data-cancel-note="1">Cancel</button>',
        "</div>",
        '<p class="hint muted">Enter submits. Shift+Enter starts a new line.</p>',
        "</div>",
        '<p class="keys muted">Keys: <kbd>y</kbd> yes, <kbd>n</kbd> no (opens a box for what '
        "should happen instead), <kbd>s</kbd> skip, <kbd>←</kbd> back, <kbd>→</kbd> next.</p>",
        "</div>",
        "</article>",
    ]
    return "\n".join(out)


def _cards(bundle: Bundle) -> str:
    n = len(bundle.cards)
    out = [
        '<section class="screen" id="screen-3" aria-label="Cards">',
    ]
    if not n:
        out.append(_p("There are no scenario cards."))
    top = max((abs(u) for v in bundle.cards for u in v.story.utilities.values()), default=0.0)
    out += [_card(v, k, n, bundle.spec, top) for k, v in enumerate(bundle.cards, 1)]
    out.append("</section>")
    return "\n".join(out)


def _finish(bundle: Bundle) -> str:
    return "\n".join(
        [
            '<section class="screen" id="screen-4" aria-labelledby="h-finish">',
            '<h2 id="h-finish">Finish</h2>',
            '<p id="summary" class="lead only-js"></p>',
            '<div class="only-js">',
            '<label for="reviewer">Your name (it is stored with your verdicts)</label>',
            '<input type="text" id="reviewer" autocomplete="name">',
            '<div class="actions">',
            '<button type="button" id="download">Download verdicts</button>',
            "</div>",
            _p(
                "Give the downloaded file to the author of the environment. If your viewer "
                "blocks downloads, copy all of the text below into a file called verdicts.json."
            ),
            '<label for="export">Your verdicts as text</label>',
            '<textarea id="export" class="export" readonly></textarea>',
            "</div>",
            _p(
                "Without scripts nothing can be recorded here; the author can run "
                "the terminal review instead.",
                "no-js",
            ),
            _p(f"Made with gao_idl {bundle.made_with}.", "muted"),
            "</section>",
        ]
    )


def render_page(bundle: Bundle, api: str | None = None) -> str:
    """The review page for ``bundle`` as one HTML document (a pure function of the arguments).

    With ``api`` unset the page only exports verdicts. With ``api`` set (a same-origin path or
    an http(s) URL) it also posts each verdict, as one ``CardVerdict`` or ``AssumptionVerdict``
    JSON object, to ``api`` as it is given, and when it opens it asks ``api`` once with ``GET``
    for a ``Verdicts`` object: answers in it that still fit (same outcome hash, same choice text)
    are shown where this browser has none of its own. A failed ``GET`` is ignored. Progress is
    also keyed in ``localStorage`` by the SHA-256 of the bundle.
    """
    key = hashlib.sha256(dump_bundle(bundle).encode("utf-8")).hexdigest()
    body = "\n".join(
        [_checked(bundle), _design(bundle), _assumptions(bundle), _cards(bundle), _finish(bundle)]
    )
    slots = {
        "CSP": _csp(api),  # only fixed text and a validated api: nothing to escape
        "VERSION": esc(__version__),
        "TITLE": esc(f"Review: {bundle.spec.title}"),
        "HEADING": esc(f"Review: {bundle.spec.title}"),
        "CSS": asset("page.css"),
        "BODY": body,
        "BUNDLE": _json_for_script(bundle.model_dump(mode="json")),
        "CONFIG": _json_for_script({"api": api, "key": key}),
        "JS": asset("page.js"),
    }
    return _SLOT.sub(lambda m: slots[m.group(1)], asset("page.html"))
