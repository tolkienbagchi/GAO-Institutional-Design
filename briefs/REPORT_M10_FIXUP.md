A headless browser (`google-chrome`) was on the path; I looked at screenshots of all three pages at 1280 and 390 pixels wide. PyPI was unreachable, so these gate commands did not run as written: the real install (tests ran with `PYTHONPATH=src` on Python 3.13), the two hypothesis test files (ignored), `pytest-timeout`, `mkdocs build --strict` with mkdocstrings, `python -m build`, and therefore the new wheel test against a real wheel (it skipped).

# REPORT — M10 fix-up

## 1. Could not run

See the first line. `tests/test_packaging.py` was ignored in M8–M10 out of habit; it needs no `build` and is back in the gate (4 passed, 1 skipped: the new wheel test, for lack of a wheel). The shared context with "Amendments after M10" named in the brief was not provided; I worked from the earlier one (NOTES.md).

## 2. Gate status

| Command | Result |
|---|---|
| `ruff check .`, `ruff format --check .` | **Pass** (230 files) |
| `mypy src/gao_idl` | **Pass** (98 source files, 5.3 s uncached) |
| `pytest -q -m "not slow"` (hypothesis files ignored) | **Pass**: 953 passed, 5 skipped, 12 deselected, 102 s (M10: 933 passed with `test_packaging.py` also ignored) |
| `pytest -q -m slow` | **Pass**: 12 passed, 145 s |
| `tests/authoring/` with no golden file changed | **Pass**; `git diff m10-review-page -- tests/authoring examples` is empty |
| The three pages build; no `http://` / `https://` | **Pass** (0 occurrences in each) |
| Every card under `examples/` still `proposed`, every assumption unconfirmed | **Pass** |
| `mkdocs build --strict`, `python -m build` | **Not run** (first line); the reduced docs build in `tests/test_docs.py` passes |

## 3. What was built

| Commit | Content |
|---|---|
| `M10 fix-up task 1` | `review/tell.py`: `ToldRow`, `split_told`, `summarize` (+ tests) |
| `M10 fix-up task 2` | `timeline_svg` relaid: ≤ 920 wide, "what" column, split report rows, lane names in every band, scales with `viewBox`/`width="100%"`/`max-width`/`min-width` |
| `M10 fix-up task 3` | Cards: split "told" list, two `<details>` lists, payoffs before them, sticky answer bar, one-line note; `tell.addressed`, `tell.participants` |
| `M10 fix-up task 4` | A note is needed to disagree (page and `apply_verdicts`); Ctrl/Cmd+Enter in note fields |
| `M10 fix-up task 5` | `clean_install.sh` checks and uses the page files from the installed wheel; wheel test; one-row navigation under 30 rem; label spacing |
| `M10 fix-up task 6` | `docs/verifying.md`, CHANGELOG, `llms.md`, NOTES, this report |

New files: `src/gao_idl/review/tell.py`, `tests/review/test_tell.py`, `briefs/REPORT_M10_FIXUP.md`. The bundle's shape, the `Verdicts` models, `outcome_hash`, `Story` and its parts, and `render_card`'s markdown are unchanged.

**Regenerated golden files** (each diff looked at as a rasterized picture): `tests/review/golden/auction.tie-top.svg`, `auction.no-bid.svg`, `pd.two-pairs.svg`, `pd.no-move-default.svg`. Not changed: `auction.flow.svg`, `pd.flow.svg`, everything in `tests/authoring/golden/`.

## 4. Exit tests

| Task | Criterion | Met | Test |
|---|---|---|---|
| 1 | `tie-top` settlement: everyone-row with `winner = alice`, `winning_payment = 8`; three own rows with `own_payment` | Yes | `test_tell.py::test_auction_settlement_has_one_everyone_row_and_three_own_rows` |
| 1 | `audited`: `revealed` in the everyone-row only | Yes | `test_carbon_market_audit_reveals_once` |
| 1 | Single recipient, identical remainders, empty remainder, each `summarize` rule | Yes | the other six tests in `test_tell.py` |
| 2 | `reputation-shown`: `viewBox` width ≤ 920, eight lanes | Yes (920; 8) | `test_draw.py::test_the_two_round_carbon_market_timeline_stays_small` |
| 2 | …rows fewer than half of M10's | **No**: 39 vs 54 (see Not done) | same test pins 39 |
| 2 | …every band contains each drawn lane's name | Yes | same test |
| 2 | `tie-top` text has `winner = alice` and `own_payment = 8` | Yes | `test_the_auction_settlement_names_the_winner_and_each_payment` |
| 2 | Golden files regenerated and reviewed | Yes | listed above |
| 3 | `audited` card shows the `revealed` payload once, not per recipient | Yes: once in the text, once in the timeline row's `<title>` (was 9 + 9) | `test_page.py::test_a_public_report_is_shown_once_not_once_per_recipient` |
| 3 | Carbon-market page smaller than 978 KB | Yes: 886 KB (no fuzz), 890 KB (200 fuzzed) | `test_the_carbon_market_page_is_smaller_than_in_m10` |
| 3 | Auction cards have both lists `open` | Yes | `test_short_cards_have_their_lists_open_and_long_ones_folded` |
| 3 | Sticky rule in CSS; answer block inside each card | Yes | `test_the_answer_bar_is_sticky_and_inside_every_card` |
| 4 | Dispute needs a note (page, restore, import); Ctrl/Cmd+Enter | Yes | `test_page_js.py` (node), `test_verdicts.py::test_a_dispute_without_a_note_is_a_problem` |
| 5 | Wheel contains the three files | Checked by `clean_install.sh` and `test_packaging.py::test_the_wheel_contains_the_review_page_files`; **neither ran against a real wheel** (Unsure) | |
| 5 | Narrow navigation, label spacing | Yes | `test_narrow_navigation_keeps_full_names_for_screen_readers`; screenshots |

## 5. For a person to open

| Page | M10 (200 fuzzed) | Now (200 fuzzed) |
|---|---|---|
| `review-auction.html` | 245,184 bytes | 308,784 bytes |
| `review-pd.html` | 260,607 bytes | 349,995 bytes |
| `review-vcm.html` | 1,001,768 bytes | 911,393 bytes |

The auction and PD pages grew: the timelines now span the full 920 width and repeat the lane names in every band. Only the carbon market had to shrink.

Carbon-market card `reputation-shown`:

| | M10 | Now |
|---|---|---|
| Timeline width × height (SVG units) | 1680 × 1700 | 920 × 1156 |
| Timeline rows | 54 (16 moves, 32 reports, 6 notes) | 39 (16 moves, 23 report rows) |
| Rendered card height, 1280 wide | about 6,500 px (reviewer) | 2,188 px |
| Rendered card height, 390 wide | not measured | 2,207 px |

Other cards at 390: auction `tie-top` 1,799 px, PD `two-pairs` 3,179 px (eight bands of a three-round, two-pair run; its lists are folded). At 1280, `tie-top` is 1,546 px.

**I looked at screenshots**, not only text: for each page at 1280 × 900 and 390 × 844, screens 1–3 and one card (`tie-top`, `two-pairs`, `reputation-shown`), plus full-length shots of `tie-top` and `reputation-shown` at 1280 (26 images, returned). Method: headless Chrome with a short script injected into a temporary copy of each page to open the screen and card. Headless Chrome will not make a window narrower than 500 px, so the 390 shots render the page in a 390-px iframe; there the page's own `innerWidth` is 390 and nothing overflows horizontally. (My first narrow shots, without the iframe, were actually a 485-px layout cropped to 390; they were redone.)

Seen in the screenshots: no lane is off to the right at 1280, lane names are in every band, the winner and prices are readable, and public reports appear once. The answer bar stays at the bottom of the window while a card scrolls. At 390 the navigation is one row, the timeline scrolls sideways at legible size, and the answer bar wraps onto two rows.

I also drove one review end to end in headless Chrome (synthetic clicks and key events): note-less No and Disagree were refused with their messages, Ctrl+Enter and Cmd+Enter recorded No and Disagree, `s` skipped, and the exported verdicts imported with `gao-idl cards import` (exit 0, the dispute printed). Not seen: dark mode, a real phone, a screen reader, a real download (the export box was read instead).

## 6. Not done

- **The row count criterion.** `reputation-shown` has 39 rows; under half of M10's 54 would be 26 or fewer. Splitting reports saved 9 rows and moving the "+k not drawn" notes into band headers saved 6. The remaining 16 rows are one per move, which the brief specifies (each row has its own arrow and `summarize(...)` of the action). Getting under 27 needs the simultaneous moves of a step to share a row, dropping per-move text from the picture. I did not improvise that; it is a decision for the reviewer.
- No real wheel was built or inspected (first line).
- No verdict was recorded in any file under `examples/`.

## 7. Where the brief underdetermined the design, and what was chosen

1. **"Everyone" in a parallel step.** `to == []` means everyone told in this step. In the PD's `pair_0` that is `a` and `b`, so the row has heads on their lanes only and reads `a, b: ...`. The word "everyone" is used only when every participant of the run was told. The brief's "one arrow across all lanes" would have shown `c` and `d` as told. Shared helper `tell.addressed`.
2. **`summarize` when the first pair fits but `, …` does not:** the first pair is kept whole with `, …`, so the label can run up to 3 characters over the limit.
3. **Column width:** label limits are computed from the "what" column's width (about 6.1 units per character at 12 units); lane spacing is at most 90.
4. **The answer bar on phones:** the note's label is visually hidden but still the field's label (the same text is a placeholder), and the keys line is hidden.
5. **`data-rows`** on the timeline `svg`, for tests and this report.
6. **The wheel test** uses `dist/` or a no-network build when `build` and `hatchling` exist, else skips. In CI's `test` job it will skip (no `hatchling`, no `dist/`); the enforcing check is `clean_install.sh` in the `package` job.

## 8. Deviations from pinned names or signatures

- `ToldRow`, `split_told`, `summarize` as pinned. Added `tell.addressed` and `tell.participants`, and `submitsNote` in `page.js`.
- The everyone row's arrow reaches the step's recipients, not all lanes (item 1 above).
- `draw.cut(text, n)` lost its default (`LABEL_CUT` is gone).

## 9. Unsure

- **Wheel contents.** `clean_install.sh` and the new test both work against a hand-zipped stand-in wheel (pass with all files; fail naming `page.js` when it is left out). Whether hatchling ships the three files is first shown by CI's `package` job.
- **Timeline text size at 1280.** The 920-unit picture is drawn about 4% smaller in an 880-px card, so its 12-unit text is about 11.5 px. It is legible in the screenshots, but it is the smallest text on the page.
- **Buyers' orders in the carbon-market timeline** summarize as `s0: 2 entries, s1: 2 entries, …` (nested values by size, as specified). The prices are only in the tooltip and in the folded "steps in words". That is honest but not informative.
- **The PD `two-pairs` card at 390 is 3,179 px tall**, the longest card now.

## 10. Reviewer's changes after delivery

The reviewer opened the delivered pages in headless Chrome (1280 and 390 wide, and dark mode) and made four presentation changes on this branch; see the last `NOTES.md` entry. In short: the timeline draws up to 12 participants (the two-round carbon-market card had left out `v1`, the verifier it is about); its text column moved to the left of the lanes, so a phone shows the words first; labels write small nested values out (`s0: (price = 8.84, qty = 3)` instead of `s0: 2 entries`); the answer bar is slimmer. Numbers in sections 4 and 5 above describe the fix-up as delivered: `reputation-shown` now has 9 lanes and 43 rows, and the pages with 200 fuzzed runs are 309 KB, 351 KB and 962 KB. Fast suite after the changes: 955 passed, 5 skipped.
