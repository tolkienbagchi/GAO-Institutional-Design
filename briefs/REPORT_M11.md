PyPI was unreachable in this sandbox (`pip download` finds no versions), so these gate commands did not run as written: the install of the `dev`/`docs` extras (tests ran with `PYTHONPATH=src` on Python 3.13 only), the two hypothesis test files (`tests/core/test_paths_property.py`, `tests/environments/test_pb_rules_property.py`, ignored), `pytest-timeout` (the 30 s limit was not enforced), `mkdocs build --strict` with the real config (aborts: the `mkdocstrings` plugin is missing; the reduced build in `tests/test_docs.py` passes), and `python -m build` (`build` and `hatchling` missing). Nobody has looked at the service's pages as a reviewer would; I only drove them headlessly (section 4).

# REPORT — M11: the library service

## 1. Could not run

See the first line. Also: Python 3.11 is present but has no pydantic, so everything ran on 3.13 only. One consequence: the byte-identity test of `render_page` pins SHA-256s recorded on 3.13 (see Unsure). CI (`test (3.11)`, `test (3.13)`, `docs`, `package`, nightly `slow`) is the full gate and has not run on this branch.

## 2. Gate status

Starting point (`m10-fixup` at `34fe6ed`), same environment: ruff, format and mypy pass; fast suite 960 passed, 5 skipped, 12 deselected, 115 s; slow suite 12 passed, 154 s (recorded in `NOTES.md`).

| Command | Result |
|---|---|
| `ruff check .` | **Pass** |
| `ruff format --check .` | **Pass** (248 files) |
| `mypy src/gao_idl` | **Pass** (105 source files, 6.6 s uncached) |
| `pytest -q -m "not slow"` (hypothesis files ignored) | **Pass**: 1060 passed, 5 skipped, 13 deselected, 120–127 s. `tests/service/` alone: 100 passed, 1 deselected, 14 s |
| `pytest -q -m slow` (same ignores) | **Pass**: 13 passed (the 12 old ones and the new socket test), 156 s |
| `mkdocs build --strict` | **Not run as written** (mkdocstrings missing). The reduced build in `tests/test_docs.py` passes, including `docs/service.md` and `docs/api/service.md` |
| `python -m build` | **Not run** (`No module named build`). `tests/test_packaging.py`'s wheel test skips. No new package data was added (the service reads `page.css` through the review package's `asset`) |
| `tests/authoring/golden/`, `tests/review/golden/`, `examples/specs`, `examples/cards` unchanged | **Pass**: `git diff 34fe6ed` touches none of them; every card is still `proposed`, no assumption is `confirmed` |
| `core`, `composition`, `runtime`, the bundle | **Unchanged** (`git diff 34fe6ed` is empty there); in `review` only `render_run` was added and exported |

## 3. What was built

| Commit | Content |
|---|---|
| `M11 task 0: the library` | `service/library.py` (`ParamSpec`, `Cast`, `Entry`, `Library`, `load_library`, `resolve_params`, `check_value`), `service/casts.py`, `examples/library/library.json`, tests |
| `M11 task 1: storage` | `service/store.py` (`Store`, `Db`, `init_db`, migrations, `User`, row types), tests |
| `M11 task 2: the application` | `review.render_run`; `service/runs.py` (`play_cast`, `Budget`, caps), `service/pages.py` (index, login, run, review pages), `service/app.py` (`make_app`, `overlay`, `user_verdicts`), tests including byte identity of `render_page` |
| `M11 task 3: administration` | `gao-idl service init|add-user|serve|export-verdicts`; migration 2 (`spec_title` on verdicts), tests |
| `M11 task 4: tests` | `tests/service/test_security.py`, `test_end_to_end.py`, `test_socket.py` (`slow`) |
| `M11 task 5: operating notes` | `docs/service.md`, `docs/api/service.md`, nav, `llms.md`, `verifying.md`, `README.md`, API index, `CHANGELOG.md` |

New files: `src/gao_idl/service/{__init__,library,casts,store,runs,pages,app}.py`, `examples/library/library.json`, `docs/service.md`, `docs/api/service.md`, `tests/service/{__init__,helpers,test_library,test_store,test_render_run,test_app,test_cli_service,test_security,test_end_to_end,test_socket}.py`, `tests/service/golden/m10_pages.json`, this report. Changed: `src/gao_idl/review/page.py` (+`render_run`), `src/gao_idl/review/__init__.py` (export), `src/gao_idl/cli.py`, docs listed above, `NOTES.md`.

## 4. Exit tests

| Task | Criterion | Met | Test |
|---|---|---|---|
| 0 | The library loads | Yes | `test_library.py::test_the_shipped_library_loads` |
| 0 | Out-of-range, wrongly typed and undeclared overrides refused, naming the field | Yes | `test_bad_overrides_are_refused_naming_the_field` (9 cases), `test_int_params_refuse_fractions` |
| 0 | A policy that does not resolve is refused at load | Yes | `test_a_policy_that_does_not_resolve_is_refused_at_load` (missing function, missing module, malformed) |
| 0 | Other load checks (slugs, files, params, casts) | Yes | `test_load_checks_*` |
| 1 | A fresh database migrates to the current version; reopening does not migrate again | Yes | `test_store.py::test_a_fresh_database_migrates_once`; a version-1 file migrates to 2: `test_cli_service.py::test_a_version_1_database_migrates_to_the_current_version` |
| 1 | Two users' rows never mix in any query | Yes | `test_store.py::test_two_users_rows_never_mix` (every query method) |
| 2 | Each route's happy path, by calling the WSGI application | Yes | `test_app.py` (health, list, describe, bundle with overlay, verdicts GET/POST in all three shapes, runs POST, runs list, one run, the four pages) |
| 2 | Each listed error code | Yes | 401 `test_every_api_route_needs_a_valid_token` (8 routes × 3 headers), `test_a_page_with_a_wrong_token_is_401`; 403 `test_posts_from_another_site_are_403`; 404 `test_unknown_slug_and_route_are_404`, `test_unknown_run_ids_are_404`; 409 `test_a_stale_verdict_is_409_and_not_stored`, `test_a_run_the_library_cannot_replay_is_409`; 413 `test_bodies_over_the_limit_are_413_unread`; 422 `test_bad_verdicts_are_422`, `test_bad_run_requests_are_422`, `test_bad_json_is_422`; 500 without a trace `test_an_internal_error_answers_500_without_a_trace` |
| 2 | `render_page` byte-identical before and after, on the three example bundles | Yes (on 3.13) | `test_render_run.py::test_render_page_is_byte_identical_to_m10` (with and without `api`) |
| 2 | `render_run` is what a card shows | Yes | `test_every_card_shows_exactly_render_run` (every card of all three pages) |
| 3 | The four commands | Yes | `test_cli_service.py` (init, add-user, export-verdicts); `serve` in `test_socket.py` (slow) |
| 4 | The brief's list | Yes | see section 5 and `test_security.py`, `test_end_to_end.py`, `test_socket.py` |
| Done | The end-to-end test | Yes | `test_end_to_end.py::test_end_to_end`: init, add user, list, bundle, two auction card verdicts and one choice, export, `cards import` into temporary copies (2 cards updated, choice confirmed), carbon market with `skeptical-buyers` over seeds 1–3 at `p_audit = 0.5`, one run's story; checks no file under `examples/` changed |

**Outside the tests, a headless browser** (Chrome over the DevTools protocol, a throwaway script; the server was `gao-idl service serve`): signing in at `/login` stored the token and loaded the index (3 entries, Sign out shown); on the auction run page, setting reserve 5, first price, the shading cast and seeds 1, 2 and pressing Run gave "2 runs done." and two run views (tiles, payoff bars, folded diagram); an over-bound reserve sent from the page's own `api()` came back "422 parameter 'reserve' must be at most 15"; on the review page, "Yes" on the first card posted through the token shim and `GET .../verdicts` then held that verdict. The server log had 16 request lines and no token. I looked at one screenshot of the top of the run page. Not seen: the rest of the run page's layout, phones, dark mode, screen readers.

## 5. Safety checklist

| Rule | Covered by |
|---|---|
| Parameterised SQL only | Every statement in `store.py` uses `?`; `test_security.py::test_the_service_imports_no_model_and_builds_sql_from_no_strings` (no `execute(f"...")`, no `.format(` in `service/`); `test_store.py::test_hostile_text_is_stored_as_data` |
| No endpoint returns a stack trace | `test_an_internal_error_answers_500_without_a_trace`, `test_a_run_the_library_cannot_replay_is_409`, the 401 test checks for "Traceback" |
| Every endpoint except the health check requires a token | `test_every_api_route_needs_a_valid_token`; page routes are the documented exception (`docs/service.md`): `test_pages_answer_without_a_token`, `test_page_routes_carry_no_user_data_without_a_token`, `test_a_page_with_a_wrong_token_is_401` |
| Request bodies over 1 MB refused | `test_bodies_over_the_limit_are_413_unread` (a body over the limit, and a `Content-Length` of 2 MB with no body: refused unread) |
| Tokens never logged | `test_tokens_and_bodies_are_never_logged` (valid and wrong token, a note); `test_socket.py` checks the server's output |
| Tokens never stored in clear | `test_store.py::test_tokens_are_stored_only_as_hashes` (the database file and WAL do not contain it); `test_cli_service.py::test_add_user_prints_the_token_once` |
| Untrusted text stored and returned as data, `textContent`, escaped | `test_hostile_names_and_notes_stay_data` (name and notes with quotes, `<script>`, SQL); `test_service_scripts_put_client_text_in_with_text_content_only`; `test_render_run.py::test_hostile_titles_are_escaped` |
| No page contains `http://` or `https://` | `test_no_page_reaches_another_host` (all pages, with and without a token, and a run's HTML) |
| Never record a verdict under `examples/` | `test_end_to_end.py` compares every JSON file under `examples/` before and after; no code under `service/` writes files except the CLI's `--out` |
| No model calls, no import of `gao_idl.llm` | `test_the_service_imports_no_model_and_builds_sql_from_no_strings` (subprocess import check and a source scan) |
| Clock and tokens only through what is passed in | `Store(clock, token_source)`, `make_app(clock)`; tests use fixed ones throughout. Not covered by a scan |
| W6 Standard library only | No dependency added (`pyproject.toml` unchanged); `test_socket.py` runs it on `wsgiref` |
| W7 Environments only from the library | `test_nothing_a_client_names_is_imported` (the sentinel module named as `env_ref`, cast, parameter name and value, slug, card id) |
| W8 Bearer token per user, issued by an admin command | `test_add_user_prints_the_token_once`, the 401 tests |
| W9 Named cast, inline, capped | `test_runs_are_played_stored_and_shown`, `test_runs_are_deterministic`, `test_the_decision_cap`, `test_the_decision_cap_is_exact`, `test_the_time_cap`, `test_the_seed_cap` (each: 422 and nothing stored) |
| W10 Declared parameters only | `test_bad_overrides_are_refused_naming_the_field`, `test_bad_run_requests_are_422` |
| W1/W2 Pages generated, one file, no other host | `test_pages_answer_without_a_token`, `test_no_page_reaches_another_host`; the scripts parse under node (`test_the_page_scripts_parse`) |
| W3 Bundle unchanged | `git diff 34fe6ed -- src/gao_idl/review/bundle.py` is empty; byte-identity test |
| W5 Hash rule | `test_a_stale_verdict_is_409_and_not_stored` |
| Users never see each other's data | `test_one_user_cannot_read_or_change_anothers_runs_or_verdicts` (listing, guessing every id up to the largest, bundle, review page, a verdict on the same card) |

## 6. Not done

- Nothing in the brief was skipped. Not run: `mkdocs build --strict`, `python -m build`, hypothesis tests, Python 3.11 (first line).
- No verdict was recorded in any file under `examples/`; the end-to-end test imports into temporary copies.

## 7. Where the brief underdetermined the design, and what was chosen

1. **The M10 page sends no token.** Its `page.js` posts verdicts with only a `Content-Type` header, yet `/api/...` requires the token and the page must be reused unchanged. The service's review page is `render_page(bundle, api)` with one extra inline script inserted before the page's own; it wraps `fetch` to add `Authorization` from `sessionStorage` to same-origin paths. `page.js` and `render_page` are untouched. Verified in the headless browser.
2. **What a browser's review page shows.** A navigation carries no bearer header, so the browser always gets the library's page (no user data); earlier answers come back from the page's own `localStorage` (M10 behaviour). With a valid token in the request (an API client) the page shows that user's verdicts. The server's copy is `GET .../verdicts`.
3. **Library data in pages.** Titles, summaries, the parameter form and the casts are rendered on the server (administrator configuration, not user data); runs and verdicts come only from `/api/...`.
4. **Agents of a run.** The runner needs adapters for all agents up front, and the PD's entry config names none. A `dict` subclass makes a `ScriptedAgent` on the runner's first `adapters.get(agent)`. Each decision uses the cast's policy for the role kind of that decision's role, so a round-robin PD (players change kinds) works.
5. **Caps.** Decisions are counted per run as policy calls (a retry counts twice); time is the whole request, read from the clock passed to `make_app` at each decision and between seeds. On a cap the runner task is cancelled. Time spent inside one `advance()` is not interrupted.
6. **"1 MB"** is 1,000,000 bytes. **Status codes**: a path used with another method is 404 (405 is not in the brief's list); 403 is a POST whose `Origin` names another host; a stored run the library can no longer replay is 409.
7. **Verdict posts**: anything malformed (unknown card, No or Disagree without a note, wrong choice text, duplicates, other spec title) is 422 and stores nothing; stale cards are left out and the rest stored, with 409 naming them. The stored reviewer is the user's name. The overlay sets status, note, reviewer and confirmed choices, not `expect`.
8. **`export-verdicts` needs a spec title** the pinned command cannot supply. Migration 2 stores the title with each verdict; an optional `--library` takes it from the library (and allows exporting an empty file).
9. **Casts must name exactly the spec's role kinds** (an unknown kind is refused, not only a missing one). A parameter the user does not send keeps the spec's value; `ParamSpec.default` is what the form shows first.
10. **Run page** shows the user's 20 newest runs of that entry; seeds default to `1, 2, 3`; seeds are 0 to 2^32 − 1.

## 8. Deviations from pinned names or signatures

- `Store(path, clock=time.time, token_source=new_token)`: an added third parameter. `init_db(path, clock=time.time)` takes a clock and returns the versions it applied.
- `make_app(library, store, *, clock=time.monotonic)`: an added keyword for the run caps.
- `Library` has private loaded state and methods `entry`, `spec_of`, `cards_of`, `policy`; the pinned fields are unchanged.
- `gao-idl service export-verdicts` has an extra optional `--library FILE`.
- Table `runs` names its column `cast_name` (`cast` is an SQL keyword); verdict tables gain `spec_title` (migration 2).
- Store method names are mine and listed in `docs/api/service.md`.

## 9. Unsure

- **Byte identity on 3.11.** The pinned SHA-256s of the three pages were recorded on 3.13. If any card's payoffs or labels differ in the last float bits on 3.11 (built-in `sum` over floats; only the carbon market was audited), the test fails on CI 3.11 for a reason unrelated to M11. The golden SVG tests of M10 pass on both, which suggests they agree, but I could not check.
- **The decision cap and real time.** The PD gets slower as rounds accumulate: 2,000 decisions take about 5 s here, so on a slower server the 10 s cap may arrive before the decision cap for long PD runs. The shipped library's bounds stay far below both (20 rounds, 80 decisions).
- **Several processes.** The bundle cache is per process and the lock is per process; SQLite WAL handles concurrent writes. I tested one process only (wsgiref is single-threaded).
- **The origin check behind a proxy** relies on the proxy keeping `Host`. Documented; not tested behind a real proxy.
- **The review page in a browser** restores answers from `localStorage`, not from the server; a reviewer on another device sees the page without their earlier answers until a later brief has the page fetch them.
- The headless-browser check was a throwaway script, not a test; nothing in the suite runs the pages' JavaScript beyond `node --check`.
