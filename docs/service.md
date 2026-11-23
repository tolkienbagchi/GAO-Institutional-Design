# The library service

The library service is a small web service around the engine. A person with only a browser can:

- see a **library** of environments an administrator has vetted;
- open the [review page](verifying.md) of one of them, with each verdict stored on the server
  as they give it;
- change the parameters the administrator declared changeable, pick a **cast** of scripted
  participants, run it over a few seeds, and see what happened, shown the way a scenario card shows
  a run.

No language model runs on the server, and no code written by a user or an agent runs on it: only
what ships in the package or is named in the administrator's library file.

The service is a WSGI application on the standard library with `sqlite3`
(`gao_idl.service.app.make_app`). It adds no dependency.

## Setting it up

```text
gao-idl service init --db service.db
gao-idl service add-user "Ana Reviewer" --db service.db     # prints Ana's token once
gao-idl service serve --library examples/library/library.json --db service.db
```

`serve` runs wsgiref's development server on `127.0.0.1:8000` and says so: it is for development
only. Give each person their token by a channel you trust; it is shown once and only its SHA-256
is stored. A person opens `/login`, pastes the token (it is kept in that browser tab's
`sessionStorage`) and is taken to the library.

To bring a reviewer's answers back into the repository:

```text
gao-idl service export-verdicts auction --user "Ana Reviewer" --db service.db --out verdicts.json
gao-idl cards import examples/cards/auction.json verdicts.json --spec examples/specs/auction.json
```

`export-verdicts` writes the review page's `Verdicts` format; `cards import` applies the same rule
as for a downloaded file (a verdict counts only for the exact behaviour it was given on).

## Running it for real

Run the application under a production WSGI server behind a reverse proxy that terminates TLS.
For example, with a WSGI server of your choice installed separately (none is a dependency of this
package), a module like

```python
# wsgi.py
from pathlib import Path

from gao_idl.service import load_library
from gao_idl.service.app import make_app
from gao_idl.service.store import Store

application = make_app(
    load_library(Path("/srv/gao/library.json")), Store(Path("/srv/gao/service.db"))
)
```

served on a local port, with the proxy forwarding `https://your-host/` to it. Requirements:

- **TLS only.** Tokens travel in the `Authorization` header; without TLS anyone on the path can
  read them.
- **Keep the `Host` header.** A `POST` whose `Origin` names another host than `Host` is refused
  (403); a proxy that rewrites `Host` breaks posting.
- **Limit body size at the proxy too.** The application refuses bodies over 1,000,000 bytes, unread.
- **One process, or several sharing the file.** SQLite in WAL mode allows several readers and one
  writer; each request opens its own connection. Each process builds each environment's bundle once,
  at the first request for it, and keeps it in memory (a few seconds for the carbon market).
- Run requests execute inside the request (see the caps below), so give the WSGI server a request
  timeout above 10 seconds.

## What is stored, and for how long

One SQLite file (plus its `-wal` and `-shm` files while in use):

| Table | Holds |
|---|---|
| `users` | id, name, SHA-256 of the token, when added |
| `runs` | id, user, entry slug, parameters, cast, seed, the session snapshot (the input log), the summary, when |
| `card_verdicts` | user, entry, card id, verdict, note, outcome hash, spec title, when |
| `assumption_verdicts` | user, entry, choice index and text, verdict, note, spec title, when |
| `schema_version` | the migrations applied |

Everything is kept until an administrator deletes it: there is no expiry yet. Verdicts are
appended, never overwritten; the latest per user and card (or choice) is the one that counts.
A run's story is not stored: it is replayed from the snapshot with the library's current
environment each time it is shown (a run the environment can no longer replay answers 409).

## Backing up

Back up the database with SQLite's online backup while the service runs, for example
`sqlite3 service.db ".backup backup.db"`, or stop the service and copy `service.db` (and any
`service.db-wal`). Copying the file alone while the service writes can give an inconsistent copy.
Also keep the library file and the specs and cards it points to: a run is replayed from them.

## What is logged

One line per request: method, path, status and the user's numeric id. Never a token, a request
body or any other header. An unexpected error logs its stack trace to the server's log; the client
gets `{"error": "internal", "message": ...}` and never a trace.

## Who is trusted

- **Trusted: whoever edits the library file**, and the specs and cards it points to. The library
  file is the only place a `module:function` string may come from: environments are built from the
  `env_ref` of an entry's spec, and casts name scripted policies. `load_library` refuses a file in
  which any policy does not resolve.
- **Not trusted: every client.** A client names an entry by its slug and a cast by its name, sends
  parameter values only for the parameters the entry declares (checked for type and bounds), and
  never a `module:function` string: a run request with any other field is refused (422). Names, notes
  and parameter values from clients are stored and returned as data, inserted into pages with
  `textContent`, and escaped in SVG and HTML made on the server. All SQL is parameterised.
- A token is a bearer credential: whoever holds it acts as that user. There are no passwords.

## Tokens and pages

Every `/api/...` route requires `Authorization: Bearer <token>` and answers 401 without a valid
one. `/health` needs no token. **Page routes are the exception:** `/`, `/login`,
`/environments/<slug>/review` and `/environments/<slug>/run` answer 200 without a token, because a
browser navigating to a page cannot send the header. These pages carry no user data: they show
library data (titles, the parameter form, the casts, the library's review bundle) and fetch
everything about the user from `/api/...`, sending the token from `sessionStorage`. The review page
is the review page of `gao-idl review page` plus one small script that adds the token to its
requests. When it opens it asks `GET /api/environments/<slug>/verdicts` once and shows the answers
the server already holds for this user (given on any device) wherever this browser has none of its
own; an answer for a card whose outcome has changed since is left out. "Remove answer" on the page
is local only: the server keeps the last verdict it was sent, so a removed answer returns on the
next visit until another answer replaces it.

The run page lists the user's 20 newest runs of the environment in one table (cast, seed, what was
changed from the design, each participant's payoff, whether the run finished) so runs can be
compared; "Show" opens one run step by step below the table. A page request that does
carry a token must carry a valid one (401 otherwise); the review page then shows that user's
verdicts.

Every page is one HTML file generated by the package, with a Content-Security-Policy that allows
inline scripts and styles only and network requests only to the service itself: no CDN, web font,
analytics or other host.

## The API

JSON in, JSON out. Errors are `{"error": code, "message": plain words}` with 401, 403, 404, 409,
413 or 422 (500 for an unexpected error).

| Method and path | Does |
|---|---|
| `GET /health` | Liveness; no token |
| `GET /api/environments` | Slug, title and summary of each entry |
| `GET /api/environments/{slug}` | Parameters (with the design's values), casts, card counts |
| `GET /api/environments/{slug}/bundle` | The M10 bundle, with this user's verdicts shown on card status and choices |
| `GET /api/environments/{slug}/verdicts` | This user's verdicts in the M10 `Verdicts` format |
| `POST /api/environments/{slug}/verdicts` | One `CardVerdict` or `AssumptionVerdict` (what the review page sends), or a whole `Verdicts`. A verdict on a behaviour that changed is not stored: 409 naming the cards |
| `POST /api/environments/{slug}/runs` | `{"params": {...}, "cast": "...", "seeds": [...]}`; runs inline, stores each run, returns ids and summaries |
| `GET /api/runs` | This user's runs, newest first |
| `GET /api/runs/{id}` | One run: parameters, cast, seed, summary, its story, its run view as HTML and its timeline SVG |

## The caps, and why

A run request may name at most **10 seeds**; each run may take at most **2,000 decisions**; all
runs of one request together may take at most **10 seconds**. Beyond any of them the answer is 422
and nothing of that request is stored. Runs execute inside the request, with no job queue, so the
caps keep one request from holding a server process for long; they are well above what the shipped
library's entries need (a 20-round prisoner's dilemma is 80 decisions). The administrator's
parameter bounds are the first line of defence: a library entry whose bounds allow very long runs
will hit the caps.

## What it does not do yet

No language-model or learning participants, no job queue and no spend caps; no playing a role
yourself in the browser; no editing a spec, a card or the library in the browser; no hosted
authoring or sandbox (nothing that did not ship with the package or the library file is executed);
no passwords, SSO, email, organisations, quotas or billing; no expiry of stored data, no way to
revoke a token other than deleting the user's row; no Dockerfile or production server in this
package. Choosing where it runs, TLS and issuing tokens are for the people operating it.
