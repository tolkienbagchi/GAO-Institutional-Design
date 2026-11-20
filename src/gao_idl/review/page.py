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
from gao_idl.review.draw import esc
from gao_idl.review.tell import addressed, participants, split_told

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
        '<h2 id="h-checked">What was checked</h2>',
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
        f'<h2 id="h-design">The design: {esc(spec.title)}</h2>',
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
        '<h2 id="h-choices">Choices nobody confirmed</h2>',
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
                f'<label for="note-a{i}">Note (needed if you disagree; '
                "Ctrl+Enter or ⌘+Enter records Disagree)</label>",
                f'<textarea id="note-a{i}" class="note" rows="1"></textarea>',
                '<div class="actions only-js">',
                '<button type="button" data-verdict="agreed" aria-pressed="false">Agree</button>',
                '<button type="button" data-verdict="disputed" aria-pressed="false">'
                "Disagree</button>",
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


FOLD_AFTER = 12  # a card with more told rows than this starts with its two lists closed


def _payoffs_html(story: Story) -> list[str]:
    ending = "the run fails" if story.failed else f"the run ends through {story.exit_port}"
    return [
        "<h3>Payoffs in total</h3>",
        _ul([esc(f"{a}: {_value(story.utilities[a])}") for a in sorted(story.utilities)]),
        _p(f"How it ends: {ending}."),
    ]


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
    """Payoffs and ending, then the steps in words and what each is told, both folded."""
    steps = _steps_items(story)
    told = _told_items(story)
    is_open = len(told) <= FOLD_AFTER
    out = _payoffs_html(story)
    out.append(
        _fold(
            "What happens, step by step",
            _plural(len(steps), "step", "steps"),
            '<ol class="steps">' + "".join(steps) + "</ol>",
            is_open,
        )
    )
    out.append(
        _fold(
            "What each participant is told",
            _plural(len(told), "message", "messages"),
            _ul(told) if told else _p("Nobody is told anything."),
            is_open,
        )
    )
    return out


def _situation(view: CardView, spec: EnvSpec) -> list[str]:
    card = view.card
    shown = sorted(card.params)
    out = ["<h3>The situation</h3>"]
    lead = ""
    if spec.env_ref == card.env_ref:
        shown = [k for k in shown if k not in spec.params or spec.params[k] != card.params[k]]
        dropped = sorted(k for k in spec.params if k not in card.params)
        lead = (
            "As in the design, except:"
            if shown or dropped
            else "Exactly the parameters of the design."
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


def _card(view: CardView, k: int, n: int, spec: EnvSpec) -> str:
    card = view.card
    i = k - 1
    out = [
        f'<article class="card" data-index="{i}" aria-labelledby="h-card{i}">',
        f'<p class="progress">Card {k} of {n}</p>',
        f'<h2 id="h-card{i}" tabindex="-1">{esc(card.title)}</h2>',
        _p(card.why, "lead"),
        _p(f"Card {card.id}; in the file it is {card.status}.", "muted"),
    ]
    if view.stale:
        out.append(
            '<p class="warning" role="note"><strong>This card is out of date.</strong> '
            "The environment no longer does what this card recorded. What follows is what it "
            "does now. A card that is out of date cannot be confirmed; you can still say No.</p>"
        )
    out += _situation(view, spec)
    out += [
        "<h3>Timeline</h3>",
        f'<div class="picture">{view.timeline_svg}</div>',
    ]
    out += _story_html(view.story)
    if card.note:
        out.append(_p(f"Note already on this card: {card.note}", "muted"))
    yes_disabled = " disabled" if view.stale else ""
    out += [
        f'<div class="verdict only-js" role="group" aria-labelledby="ask-c{i}">',
        f'<p class="ask"><strong id="ask-c{i}">Is this what you meant?</strong> '
        '<span class="answer"></span></p>',
        f'<label class="sr-small" for="note-c{i}">If not, what should happen instead? '
        "(needed for No)</label>",
        f'<textarea id="note-c{i}" class="note" rows="1" '
        'placeholder="If not, what should happen instead?"></textarea>',
        '<div class="actions">',
        f'<button type="button" data-act="yes" aria-pressed="false"{yes_disabled}>'
        "Yes, this is what I meant</button>",
        '<button type="button" data-act="no" aria-pressed="false">No</button>',
        '<button type="button" data-act="skip">Skip</button>',
        '<button type="button" data-act="back">Back</button>',
        '<button type="button" data-clear="1">Remove answer</button>',
        "</div>",
        '<p class="keys muted">Keys: <kbd>y</kbd> yes, <kbd>n</kbd> no, <kbd>s</kbd> skip, '
        "<kbd>←</kbd> back, <kbd>→</kbd> next; in the note, "
        "<kbd>Ctrl</kbd>+<kbd>Enter</kbd> (<kbd>⌘</kbd>+<kbd>Enter</kbd>) records No.</p>",
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
    out += [_card(v, k, n, bundle.spec) for k, v in enumerate(bundle.cards, 1)]
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
    JSON object, to ``api`` as it is given. Progress is keyed in ``localStorage`` by the
    SHA-256 of the bundle.
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
