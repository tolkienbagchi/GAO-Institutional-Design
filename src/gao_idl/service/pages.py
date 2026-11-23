"""The service's own HTML pages: the library index, sign-in, the run page, and the review page.

Every page is generated here from the library and the M10 page assets, is one HTML file, and
makes no request to any other host: its Content-Security-Policy allows scripts and styles only
inline and network requests only to its own origin. Pages carry no user data. They read the
token from ``sessionStorage`` and fetch everything about the user from ``/api/...`` with it,
putting client-supplied text into the page with ``textContent`` only. The one piece of markup a
script inserts is the run view, which ``render_run`` rendered and escaped on the server.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json
from typing import Any

from gao_idl._version import __version__
from gao_idl.authoring.spec import EnvSpec
from gao_idl.review.bundle import Bundle
from gao_idl.review.draw import esc
from gao_idl.review.page import asset, render_page
from gao_idl.service.library import Entry, Library

TOKEN_KEY = "gao-idl-token"
CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'"
)

SERVICE_CSS = """
nav.service { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; }
nav.service a { color: var(--accent); }
ul.library { list-style: none; padding: 0; }
ul.library > li { border: 1px solid var(--line); border-radius: 6px; padding: 0.6rem 0.9rem;
  margin: 0 0 0.8rem; }
ul.library h3 { margin: 0.2rem 0; }
.links { display: flex; flex-wrap: wrap; gap: 1rem; }
.links a { color: var(--accent); font-weight: 600; }
fieldset { border: 1px solid var(--line); border-radius: 6px; margin: 0 0 1rem;
  padding: 0.6rem 0.9rem; }
.param { margin: 0 0 0.8rem; }
.param label, .param .label { display: block; font-weight: 600; }
.param input[type=number], .param select, .param input[type=text], .param input[type=password] {
  font: inherit; padding: 0.3rem 0.4rem; min-width: 12rem; }
.cast { display: block; margin: 0 0 0.5rem; }
.run-result { border-top: 2px solid var(--line); margin-top: 1.5rem; padding-top: 0.5rem; }
.run-result h3 { margin-top: 0.4rem; }
.signed-out[hidden], .signed-in[hidden] { display: none; }
"""

COMMON_JS = r"""
(function () {
  "use strict";
  var TOKEN_KEY = "gao-idl-token";
  function token() {
    try { return window.sessionStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
  }
  function setToken(value) {
    try {
      if (value) { window.sessionStorage.setItem(TOKEN_KEY, value); }
      else { window.sessionStorage.removeItem(TOKEN_KEY); }
    } catch (e) { /* no storage: the user signs in again on each page */ }
  }
  function api(path, method, body) {
    var headers = { "Accept": "application/json" };
    var t = token();
    if (t) { headers["Authorization"] = "Bearer " + t; }
    var init = { method: method || "GET", headers: headers, credentials: "omit" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    return fetch(path, init).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          var err = new Error(data && data.message ? data.message : "status " + response.status);
          err.status = response.status;
          throw err;
        }
        return data;
      });
    });
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }
  function say(text) {
    var line = document.getElementById("status");
    if (line) { line.textContent = text || ""; }
  }
  function readJson(id) {
    var node = document.getElementById(id);
    return node ? JSON.parse(node.textContent) : null;
  }
  function serverMarkup(html) {
    // markup rendered and escaped by the server (render_run); parsed, never run
    var doc = new DOMParser().parseFromString("<!DOCTYPE html><body>" + html, "text/html");
    Array.prototype.slice.call(doc.querySelectorAll("script")).forEach(function (s) {
      s.parentNode.removeChild(s);
    });
    var out = document.createDocumentFragment();
    Array.prototype.slice.call(doc.body.childNodes).forEach(function (n) {
      out.appendChild(document.importNode(n, true));
    });
    return out;
  }
  function showSignedIn() {
    var signedIn = !!token();
    Array.prototype.slice.call(document.querySelectorAll(".signed-in")).forEach(function (n) {
      n.hidden = !signedIn;
    });
    Array.prototype.slice.call(document.querySelectorAll(".signed-out")).forEach(function (n) {
      n.hidden = signedIn;
    });
    var out = document.getElementById("sign-out");
    if (out) {
      out.addEventListener("click", function () {
        setToken(null);
        window.location.assign("/login");
      });
    }
    return signedIn;
  }
  window.gaoService = {
    token: token, setToken: setToken, api: api, el: el, say: say, readJson: readJson,
    serverMarkup: serverMarkup, showSignedIn: showSignedIn
  };
})();
"""

INDEX_JS = r"""
(function () {
  var g = window.gaoService;
  if (!g.showSignedIn()) { return; }
  g.api("/api/environments").then(function () {
    g.say("");
  }).catch(function (err) {
    g.say("Could not reach the library with your token (" + err.message + "). Sign in again.");
  });
})();
"""

LOGIN_JS = r"""
(function () {
  var g = window.gaoService;
  g.showSignedIn();
  var field = document.getElementById("token");
  var button = document.getElementById("sign-in");
  function signIn() {
    var value = field.value.trim();
    if (!value) { g.say("Paste the token you were given, then press Sign in."); return; }
    g.setToken(value);
    g.api("/api/environments").then(function () {
      window.location.assign("/");
    }).catch(function (err) {
      g.setToken(null);
      g.say(err.status === 401 ? "That token is not valid."
        : "Could not sign in (" + err.message + ").");
    });
  }
  button.addEventListener("click", signIn);
  field.addEventListener("keydown", function (e) { if (e.key === "Enter") { signIn(); } });
})();
"""

RUN_JS = r"""
(function () {
  var g = window.gaoService;
  var entry = g.readJson("gao-entry");
  if (!g.showSignedIn()) { return; }
  var results = document.getElementById("results");
  var runButton = document.getElementById("run");

  function value(p) {
    var input = document.getElementById("param-" + p.name);
    if (p.kind === "bool") { return input.checked; }
    if (p.kind === "choice") { return p.choices[Number(input.value)]; }
    var text = input.value.trim();
    if (text === "") { return null; }
    return Number(text);
  }

  function collect() {
    var params = {};
    entry.params.forEach(function (p) {
      var v = value(p);
      if (v !== null) { params[p.name] = v; }
    });
    var cast = null;
    Array.prototype.slice.call(document.querySelectorAll("input[name=cast]")).forEach(function (r) {
      if (r.checked) { cast = r.value; }
    });
    var seeds = document.getElementById("seeds").value.split(/[\s,]+/).filter(function (s) {
      return s !== "";
    }).map(Number);
    return { params: params, cast: cast, seeds: seeds };
  }

  function describe(run) {
    var s = run.summary;
    var ending = s.failed ? "the run failed" : "the run ended through " + s.exit_port;
    var params = Object.keys(run.params).sort().map(function (k) {
      return k + " = " + JSON.stringify(run.params[k]);
    }).join(", ");
    return "Cast " + run.cast + ", seed " + run.seed + "; " + ending + ". Parameters: " +
      params + ".";
  }

  function show(run) {
    var box = g.el("article", "run-result");
    box.appendChild(g.el("h3", "", "Run " + run.id + ": " + run.title));
    box.appendChild(g.el("p", "muted", describe(run)));
    box.appendChild(g.serverMarkup(run.html));
    results.appendChild(box);
  }

  function load() {
    return g.api("/api/runs").then(function (data) {
      var mine = data.runs.filter(function (r) { return r.slug === entry.slug; }).slice(0, 20);
      while (results.firstChild) { results.removeChild(results.firstChild); }
      if (!mine.length) { results.appendChild(g.el("p", "muted", "No runs yet.")); return null; }
      return mine.reduce(function (chain, r) {
        return chain.then(function () { return g.api("/api/runs/" + r.id).then(show); });
      }, Promise.resolve());
    }).catch(function (err) { g.say("Could not load your runs (" + err.message + ")."); });
  }

  runButton.addEventListener("click", function () {
    var body = collect();
    runButton.disabled = true;
    g.say("Running...");
    g.api("/api/environments/" + entry.slug + "/runs", "POST", body).then(function (data) {
      g.say(data.runs.length + " runs done.");
      return load();
    }).catch(function (err) {
      g.say("Not run: " + err.message);
    }).then(function () { runButton.disabled = false; });
  });
  load();
})();
"""

REVIEW_SHIM = r"""<script>
(function () {
  // Added by the library service: the review page's verdicts go to this service, which needs the
  // token from sessionStorage. Same-origin paths only; the page itself is unchanged.
  var t = null;
  try { t = window.sessionStorage.getItem("gao-idl-token"); } catch (e) { t = null; }
  if (!t || !window.fetch) { return; }
  var plain = window.fetch.bind(window);
  window.fetch = function (url, init) {
    init = init || {};
    if (typeof url === "string" && url.charAt(0) === "/" && url.charAt(1) !== "/") {
      var headers = new Headers(init.headers || {});
      headers.set("Authorization", "Bearer " + t);
      init.headers = headers;
      init.credentials = "omit";
    }
    return plain(url, init);
  };
})();
</script>
"""


def _json_for_script(data: Any) -> str:
    """JSON safe inside ``<script>``: ``<``, ``>`` and ``&`` as ``\\u`` escapes."""
    text = json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _shell(title: str, body: str, script: str, data: dict[str, Any] | None = None) -> str:
    embedded = (
        f'<script type="application/json" id="gao-entry">{_json_for_script(data)}</script>\n'
        if data is not None
        else ""
    )
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
            '<meta name="referrer" content="no-referrer">',
            f'<meta name="generator" content="gao_idl {esc(__version__)}">',
            f"<title>{esc(title)}</title>",
            "<style>",
            asset("page.css"),
            SERVICE_CSS,
            "</style>",
            "</head>",
            '<body class="service">',
            '<header class="top">',
            f"<h1>{esc(title)}</h1>",
            '<nav class="service" aria-label="Service">',
            '<a href="/">Library</a>',
            '<a class="signed-out" href="/login">Sign in</a>',
            '<button type="button" class="signed-in" id="sign-out" hidden>Sign out</button>',
            "</nav>",
            '<p id="status" role="status" aria-live="polite"></p>',
            "</header>",
            "<main>",
            '<noscript><p class="warning"><strong>Scripts are off.</strong> This service needs '
            "scripts to sign in and to show your results.</p></noscript>",
            body,
            "</main>",
            embedded + "<script>",
            COMMON_JS,
            script,
            "</script>",
            "</body>",
            "</html>",
            "",
        ]
    )


_SIGNED_OUT = (
    '<p class="signed-out warning"><strong>You are not signed in.</strong> '
    '<a href="/login">Sign in</a> with the token you were given.</p>'
)


def index_page(library: Library) -> str:
    """The library index: each entry with links to its review page and its run page."""
    items = []
    for entry in library.entries:
        spec = library.spec_of(entry)
        items.append(
            "<li>"
            f"<h3>{esc(entry.title)}</h3>"
            f"<p>{esc(spec.summary)}</p>"
            '<p class="links">'
            f'<a href="/environments/{esc(entry.slug)}/review">Review the design and its cards</a>'
            f'<a href="/environments/{esc(entry.slug)}/run">Run it with a cast</a>'
            "</p></li>"
        )
    body = "\n".join(
        [
            _SIGNED_OUT,
            "<h2>Library</h2>",
            '<p class="lead">Environments an administrator has vetted. Review one, or run it with '
            "scripted participants and see what happens.</p>",
            '<ul class="library">' + "".join(items) + "</ul>",
        ]
    )
    return _shell("gao_idl library", body, INDEX_JS)


def login_page() -> str:
    """The sign-in page: one field for the token, kept in ``sessionStorage`` for this tab."""
    body = "\n".join(
        [
            "<h2>Sign in</h2>",
            "<p>Paste the token an administrator gave you. It is kept only in this browser tab "
            "and is sent with every request to this service.</p>",
            '<div class="param"><label for="token">Your token</label>',
            '<input type="password" id="token" autocomplete="off" spellcheck="false"></div>',
            '<div class="actions"><button type="button" id="sign-in" class="primary">'
            "Sign in</button></div>",
        ]
    )
    return _shell("Sign in", body, LOGIN_JS)


def _param_field(p: Any, current: Any) -> str:
    name = esc(p.name)
    fid = f"param-{name}"
    help_text = f'<p class="muted small">{esc(p.help)}</p>' if p.help else ""
    value = p.default
    if p.kind == "bool":
        checked = " checked" if value is True else ""
        field = f'<label><input type="checkbox" id="{fid}"{checked}> {esc(p.label)}</label>'
        return f'<div class="param">{field}{help_text}</div>'
    if p.kind == "choice":
        options = "".join(
            f'<option value="{i}"{" selected" if c == value and type(c) is type(value) else ""}>'
            f"{esc(c if isinstance(c, str) else json.dumps(c))}</option>"
            for i, c in enumerate(p.choices)
        )
        field = f'<select id="{fid}">{options}</select>'
    else:
        bounds = ""
        if p.minimum is not None:
            bounds += f' min="{p.minimum:g}"'
        if p.maximum is not None:
            bounds += f' max="{p.maximum:g}"'
        step = "1" if p.kind == "int" else "any"
        field = (
            f'<input type="number" id="{fid}" step="{step}"{bounds} '
            f'value="{esc(json.dumps(value))}">'
        )
    spec_value = f' <span class="muted small">(in the design: {esc(json.dumps(current))})</span>'
    return (
        f'<div class="param"><label for="{fid}">{esc(p.label)}</label>{field}{spec_value}'
        f"{help_text}</div>"
    )


def run_page(entry: Entry, spec: EnvSpec) -> str:
    """The run page of ``entry``: a form from its ``ParamSpec``s, a cast picker, and the user's
    results (loaded with the token), each shown as a run view with its diagram folded."""
    params = "".join(_param_field(p, spec.params.get(p.name)) for p in entry.params)
    casts = "".join(
        f'<label class="cast"><input type="radio" name="cast" value="{esc(c.name)}"'
        f"{' checked' if i == 0 else ''}> <strong>{esc(c.label)}</strong>: "
        f"{esc(c.description)}</label>"
        for i, c in enumerate(entry.casts)
    )
    body = "\n".join(
        [
            _SIGNED_OUT,
            f"<h2>Run: {esc(entry.title)}</h2>",
            f'<p class="lead">{esc(spec.summary)}</p>',
            "<fieldset><legend>Parameters you may change</legend>",
            params or "<p>None: this environment runs as designed.</p>",
            "</fieldset>",
            "<fieldset><legend>Cast</legend>",
            casts,
            "</fieldset>",
            '<div class="param"><label for="seeds">Seeds (up to 10, separated by commas)</label>',
            '<input type="text" id="seeds" value="1, 2, 3" spellcheck="false"></div>',
            '<div class="actions signed-in" hidden><button type="button" id="run" class="primary">'
            "Run</button></div>",
            "<h2>Your runs of this environment</h2>",
            '<div id="results" aria-live="polite"></div>',
            f'<p class="muted small"><a href="/environments/{esc(entry.slug)}/review">'
            "Review the design and its cards</a></p>",
        ]
    )
    data = {"slug": entry.slug, "params": [p.model_dump(mode="json") for p in entry.params]}
    return _shell(f"Run: {entry.title}", body, RUN_JS, data)


def review_page(bundle: Bundle, slug: str) -> str:
    """The M10 review page of ``bundle``, posting verdicts to this entry's verdicts route.

    The page is ``render_page(bundle, api)`` unchanged, with one script added before its own that
    sends the token from ``sessionStorage`` with the page's same-origin requests.
    """
    page = render_page(bundle, f"/api/environments/{slug}/verdicts")
    marker = '<script type="application/json" id="gao-bundle">'
    if page.count(marker) != 1:
        raise RuntimeError("the review page does not have the expected layout")
    return page.replace(marker, REVIEW_SHIM + marker)


def message_page(title: str, text: str) -> str:
    """A small page for a page route that cannot be shown (an unknown environment)."""
    return _shell(title, f"<h2>{esc(title)}</h2><p>{esc(text)}</p>", "")
