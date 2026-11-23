PyPI was unreachable in the build sandbox (network off), so these gate commands did not run as written: `pip install -e ".[dev,llm,docs]"` (tests ran with `PYTHONPATH=src` on Python 3.13 only), the two hypothesis test files and `tests/test_packaging.py` (ignored), `pytest-timeout` (the 30 s limit was not enforced), `mkdocs build --strict` with the real config (aborts: mkdocstrings missing; the reduced build in `tests/test_docs.py` passes), and `python -m build` (`build` missing). Nobody has looked at the three pages rendered.

# REPORT — M10: the review page

## 1. Could not run

See the first line. In addition: Python 3.11 is present but has no pydantic, so the pinned carbon-market `outcome_hash` has been checked on 3.13 only. The new files compile under 3.11 (`py_compile`). CI (`test (3.11)`, `test (3.13)`, `docs`, `package`) is the full gate and has not run on this branch.

## 2. Gate status

| Command | Result |
|---|---|
| `ruff check .` | **Pass** |
| `ruff format --check .` | **Pass** (227 files) |
| `mypy src/gao_idl` | **Pass** (97 source files, 5.6 s uncached) |
| `pytest -q -m "not slow"` (three files ignored, see first line) | **Pass**: 933 passed, 4 skipped, 12 deselected, 91 s. Baseline on `main`: 880 passed. |
| `pytest -q -m slow` (same ignores) | **Pass**: 12 passed, 157 s. No new slow tests. |
| `mkdocs build --strict` | **Not run as written** (mkdocstrings missing). Reduced config inside `tests/test_docs.py` passes; `docs/api/review.md` has never rendered with mkdocstrings. |
| `python -m build` | **Not run.** Whether the wheel contains `page.html/css/js` is unverified (see Unsure). |
| `python scripts/make_example_pages.py` | **Pass**: three pages, 213 / 211 / 213 runs (200 fuzzed each), 0 mismatches, 91 s. |

## 3. What was built

| Commit | Content |
|---|---|
| `M10 task 0` | Baseline recorded; `0001-cards-review.patch` applied with `git am` (`gao-idl cards review`), message amended to the task convention, author kept. |
| `M10 task 1` | `Move`, `Told`, `StepView`, `Story`, `tell_session`, `tell_card` in `authoring/cards.py`; `render_card` rebuilt on them. |
| `M10 task 2` | `review/draw.py`: `flow_svg`, `timeline_svg`; six golden SVGs. |
| `M10 task 3` | `review/bundle.py`: `CheckStatus`, `CardView`, `Bundle`, `outcome_hash`, `make_bundle`, `dump/save/load_bundle`; `gao-idl review bundle`. |
| `M10 task 4` | `review/page.py` with `page.html`, `page.css`, `page.js`; `render_page`; `gao-idl review page`; wheel `artifacts` in `pyproject.toml`. |
| `M10 task 5` | `review/verdicts.py`: `CardVerdict`, `AssumptionVerdict`, `Verdicts`, `load_verdicts`, `apply_verdicts` (+ `apply_card_verdicts`, `disputed`, `save_spec`); `Card.reviewer`; `gao-idl cards import`. |
| `M10 task 6` | `docs/verifying.md`, `docs/api/review.md`, nav, `llms.md`, CHANGELOG, README, skill item 8, `scripts/make_example_pages.py`, `.gitignore`, isolation test, this report. |

Files added: `src/gao_idl/review/{__init__,draw,bundle,page,verdicts}.py`, `src/gao_idl/review/page.{html,css,js}`, `tests/review/{test_draw,test_bundle,test_page,test_page_js,test_verdicts,test_isolation}.py` (49 tests), `tests/review/golden/*.svg` (6), `scripts/make_example_pages.py`, `docs/api/review.md`, this report. `gao_idl.__all__` is still 23 names; no dependency added; nothing under `gao_idl.review` imports `gao_idl.llm` (subprocess test). `core`, `composition`, `runtime` are unchanged. No file under `examples/` changed.

## 4. Exit tests

| Task | Criterion | Met | Test |
|---|---|---|---|
| 0 | Gate before and after the patch | Yes | NOTES.md entry; `test_cli.py::test_cards_review_goes_one_card_at_a_time_and_saves_each_answer` |
| 1 | Every existing test and golden file passes unchanged | Yes | `tests/authoring/` unchanged and green; also all 37 shipped cards render byte-identically with the old and new `render_card`, with and without spec (74 comparisons, not committed) |
| 2 | Golden flow + two timelines each for auction and PD | Yes | `test_draw.py::test_flow_golden`, `test_timeline_golden` |
| 2 | One step box per primitive, one lane per participant | Yes | `test_one_step_box_per_primitive_and_frames_for_loops_and_branches`, `test_one_lane_per_participant_and_one_for_the_rules` |
| 2 | `reputation-shown` timeline under 200 KB | Yes (36 KB) | `test_the_two_round_carbon_market_timeline_stays_small` |
| 3 | Three bundles build, round-trip, byte-identical twice | Yes | `test_bundle.py::test_shipped_bundles_build_round_trip_and_are_byte_identical` |
| 3 | One hash pinned for a carbon-market card | Yes, on 3.13 only | `test_outcome_hash_is_pinned_for_a_carbon_market_card` |
| 4 | Auction page: no `http://`/`https://`, no `src=`, no external `href=` | Yes (no `href=` at all) | `test_page.py::test_the_page_makes_no_network_request` |
| 4 | Escaping (`<script>` and `"` in a title and a note) | Yes | `test_untrusted_text_never_appears_unescaped` |
| 4 | Carbon-market page under 2 MB | Yes (978 KB; 1.0 MB with 200 fuzzed runs) | `test_the_carbon_market_page_is_under_two_megabytes` |
| 4 | Assets load through `importlib.resources` | Yes | `test_the_assets_ship_with_the_package` |
| 5 | Confirm two auction cards, reject one: clean import; `cards verify` fails on the rejected card only | Yes | `test_verdicts.py::test_two_confirmed_and_one_rejected_import_cleanly_then_verify_fails_on_the_rejection` |
| 5 | Changed tie-break → stale-outcome problem | Yes | `test_a_changed_tie_break_makes_a_matching_file_import_with_a_stale_outcome_problem` |
| 5 | The page's export validates as `Verdicts` | Yes, for the pure export function under node | `test_page_js.py::test_page_js_state_functions_under_node` |
| 6 | Three pages build | Yes | `test_page.py::test_the_example_page_script_writes_three_pages` |

## 5. For a person to open

| Page | Size | Content |
|---|---|---|
| `build/review/auction.html` | 245 KB | 13 cards, 7 open choices, 213 runs (200 fuzzed), 0 mismatches |
| `build/review/pd.html` | 261 KB | 11 cards, 4 open choices, 211 runs (200 fuzzed), 0 mismatches |
| `build/review/vcm.html` | 1.0 MB | 13 cards, 6 open choices, 213 runs (200 fuzzed), 0 mismatches |

What was checked, and how:

- **The HTML and SVG as text**, by the tests: structure, escaping, absence of URLs, sizes, the embedded bundle.
- **The SVG pictures on their own**: I rasterized them with `rsvg-convert` during development to look for overlaps. That shows the pictures, not the page.
- **`page.js`, pure half**: executed under node 22 by `tests/review/test_page_js.py` (moving, deciding, stale refusal, note requirement, export shape, key map, restoring saved progress).
- **`page.js`, DOM half**: executed once per page in headless Chrome (`--dump-dom`, outside the test suite, no screenshot). Each page booted under its Content-Security-Policy with no script error: `<html class="js">`, one screen and one card visible, the summary line filled in. **No click, key press, download or `localStorage` round trip was ever executed.**
- **Not checked:** what the pages look like, on a laptop or a phone, in light or dark mode; whether the blob download works under the CSP in every viewer; keyboard-only use; screen readers. That is the human step before M11.

## 6. Not done

- The full gate, as in the first line.
- No verdict was recorded anywhere: every card under `examples/` is still `proposed` and every assumption unconfirmed (tests assert it). `cards import` ran only on temporary copies.
- No user trial, no server (M11), no review screen for `diverge` choice cards (out of scope).

## 7. Where the brief underdetermined the design, and what was chosen

1. **Who renders the content.** All text and pictures are rendered and escaped in Python; `page.js` only navigates, records, saves and exports, and builds no markup from strings. The page reads fully without scripts. Cost: the SVGs appear twice (markup and bundle).
2. **Escaping the embedded JSON:** every `<`, `>`, `&` becomes `<` etc. (a superset of "`</` escaped"; with only `</` escaped the escaping test cannot pass).
3. **A CSP `<meta>`** (`default-src 'none'`, `connect-src 'none'` unless `api`) so the browser itself enforces "no network request". `api` must be a plain path or an http(s) URL without query.
4. **`CheckStatus.runs`** counts all runs (cards + fuzzed); the bundle's who-knows-what table uses the same runs.
5. **`StepView.place`** excludes the step name; events outside any layer instance (a loop cut off at its limit) get a view with `layer == ""`; places where only store access happened are dropped.
6. **Timeline:** "the rules" lane leftmost; one row per arrow; defaults dashed, drops a dashed stub; a person who stays silent in a step that stops the run shows only as the band's "stops: ..." text (the pinned `Move` has no such kind).
7. **Verdicts:** a file for a differently titled spec is refused whole; duplicate verdicts for one card are refused; a stale card can be rejected (its `expect` becomes the rejected behaviour, otherwise the rejection would be vacuous) but not confirmed; disputed choices are printed for the author and do not change the exit status; without `--spec`, choice answers are problems.
8. **Page behaviour:** after the last card the page moves to Finish; "Remove my answer" undoes a card verdict; the download is `verdicts.json`; progress in `localStorage` is keyed by the SHA-256 of the bundle and re-validated on restore.
9. **`make_example_pages.py`** defaults to `--fuzz 200 --seed 1` (the M9 settings).

## 8. Deviations from pinned names or signatures

- None of the pinned signatures changed. Additions: `dump_bundle`, `save_bundle`, `load_bundle`, `apply_card_verdicts`, `disputed`, `save_spec`, `render_story` (in `authoring.cards`), and `gao-idl review page --api` (hidden from help until M11).
- Task 0's commit message is `M10 task 0: ...` rather than the patch's own subject (content and author unchanged).
- `.gitignore` already ignored `build/`; `build/review/` was added anyway, as the brief asks.

## 9. Unsure

- **Everything visual.** Text widths in the SVGs are estimated from character counts. Long action labels are cut at 28 characters (full text in a `<title>` tooltip, which phones do not show; the full text is in "the steps in words" below each picture).
- **The carbon-market cards are long.** Each card screen has a 9-lane timeline (one participant not drawn) and a long list of reports with nested order books. I expect this is still too much for a non-technical reviewer; the user trial will say.
- **The wheel.** Hatchling should include the three files (they sit under `packages`, as `py.typed` does) and they are listed as `artifacts`; neither has been checked by a real build.
- **`outcome_hash` across Pythons** rests on the 9-place rounding and the carbon market's `fsum` fix; the other environments were never audited for float `sum` (M9). The pinned test will tell on CI's 3.11 job.
- **The blob download under `default-src 'none'`.** I believe downloads are not governed by CSP, but have not seen it work; the text box is the fallback.
- **Keyboard `n`** with an empty note only moves focus to the note field. A reviewer must then click No or Tab to it, because typed letters in the field are text.
