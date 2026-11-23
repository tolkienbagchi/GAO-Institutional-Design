# `gao_idl.service`

The library service: a small WSGI application around the engine, on the standard library and
`sqlite3`, through which a person with only a browser opens the review page of a vetted
environment and runs it with a cast of scripted participants. See
[The library service](../service.md) for how to run it.

## `gao_idl.service`

::: gao_idl.service

## `gao_idl.service.library`

::: gao_idl.service.library

## `gao_idl.service.casts`

::: gao_idl.service.casts

## `gao_idl.service.store`

`Store(path, clock=time.time, token_source=new_token)` migrates the file and gives one
connection per request with `Store.open()`, a `Db`. Methods, all parameterised SQL:

| Method | Does |
|---|---|
| `Store.add_user(name)` | Adds a user with a new token; returns `(User, token)`. The token is shown once and only its SHA-256 is stored |
| `Db.add_user(name, token)` | The same with a given token |
| `Db.user_by_token(token)`, `Db.user_by_name(name)` | A `User` or `None` |
| `Db.add_runs(user, slug, params, cast, [(seed, snapshot, summary), ...])` | Stores the runs of one request in one transaction; returns their ids |
| `Db.runs(user, slug=None, limit=200)` | This user's runs, newest first, without snapshots |
| `Db.run(user, run_id)` | One of this user's runs with its snapshot, or `None` (also for another user's run) |
| `Db.add_card_verdict(user, slug, card_id, verdict, note, outcome_hash, spec_title="")` | Records a card verdict as given |
| `Db.add_assumption_verdict(user, slug, index, text, verdict, note, spec_title="")` | Records an answer to a choice as given |
| `Db.card_verdicts(user, slug)`, `Db.assumption_verdicts(user, slug)` | The latest verdict per card, and per choice |
| `Db.spec_title(user, slug)` | The spec title of this user's latest verdict on the entry, or `None` |

::: gao_idl.service.store

## `gao_idl.service.runs`

::: gao_idl.service.runs

## `gao_idl.service.pages`

::: gao_idl.service.pages

## `gao_idl.service.app`

::: gao_idl.service.app
