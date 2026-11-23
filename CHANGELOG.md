# Changelog

All notable changes are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semantic versioning.

## [0.1.0] - Unreleased

First release. Built in seven milestones:

- **M0** Skeleton and core: ids, paths, RNG streams, schemas and contracts, ledger, store, events, the
  `Primitive` layer and the event-sourced `Session`.
- **M1** Composition: `Seq`, `Par` and `Loop`, connectors, joiners and splitters, checked when built.
- **M2** Prisoner's dilemma and sealed-bid auction, the async runner, and the scripted, random and
  human-transport adapters.
- **M3** Tabular learning: `TableAgent`, observation codecs, Q-learning, `train` and `evaluate`.
- **M4** Optional LLM agents (`gao_idl.llm`): backends, structured-output validation, replayable
  external responses.
- **M5** Participatory budgeting: nomination, eligibility, voting, and the greedy, knapsack, Method of
  Equal Shares and sequential Phragmen funding rules.
- **M6** Release: packaging, public API of 23 names, documentation and six tutorials, the `gao-idl`
  command line, demo configs, `selftest`, and the acceptance script.

Fix-up after M6: single `gao_idl.cli` module and `gao-idl` script, `knapsack` rounds costs to cents,
`clean_install.sh` selects the wheel for the current version, decision log rebuilt from the plan,
Apache-2.0 license, author and URL metadata, `py.typed`, `CITATION.cff`, `mkdocs<2` pin, and release
notes in `docs/releasing.md`.

After M7 (unreleased): **M8, verified authoring.** New supporting package `gao_idl.authoring`: the
`EnvSpec` model, runs from data (`play`, `script_of`), deterministic rendering (`knowledge_table`,
`to_mermaid`, `to_outline`, `render_design`), `check_spec`, and scenario cards; `assert_cards` in
`gao_idl.testing`; `gao-idl spec render|check` and `gao-idl cards show|confirm|reject|verify`; specs
and proposed cards for the auction, the prisoner's dilemma and the carbon market; `docs/verifying.md`.

**M9, more evidence.** `gao_idl.authoring.fuzz` (`edge_scripts`, `sample_action`), `invariants`
(`check_invariants`), `predicates`, and `diverge` (`diverge`, choice cards); `InvariantSpec.args`;
`gao-idl spec check --fuzz N` and `gao-idl diverge`; skills `second-implementation` and
`write-spec-tests`; `examples/candidates/auction_second.py`.

**M10, the review page.** New supporting package `gao_idl.review`: `make_bundle` (the JSON bundle,
`bundle_version: 1`, the only contract between the engine and a front end), `flow_svg` and
`timeline_svg` (SVG drawn in Python), `render_page` (one self-contained HTML file with no network
request, in which a designer reads the design and gives a verdict on each card), and verdicts
(`Verdicts`, `apply_verdicts`; a verdict is refused if the card's outcome changed since the page
was made). `gao_idl.authoring` gains `Story`, `tell_card` and `tell_session` (`render_card` is
built on them), and `Card.reviewer`. Command line: `gao-idl review bundle|page`,
`gao-idl cards import`, and `gao-idl cards review` (one card at a time in a terminal).
`scripts/make_example_pages.py` writes the three example pages to `build/review/`.

**M10 fix-up.** The review page after a first look at it rendered: `gao_idl.review.tell`
(`split_told`, `summarize`) shows what a step told everyone alike once instead of once per
recipient; the timeline is at most 920 wide, puts its text in a column on the left (what a phone shows first), draws up to twelve participants, writes small nested values out, and repeats the
lane names in every band; cards show payoffs before two folded lists and keep the answer bar in
view; disagreeing with a choice needs a note (`cards import` refuses one without); screens are named Checks, Design,
Choices, Cards, Finish; each card shows what happened by participant (`run_steps`: a tile per
participant per step, payoffs as bars on one scale), with the timeline as a folded "Detailed
diagram" and the exact record folded below; "No…" and
"Disagree…" open a note box with Submit and Cancel buttons (Enter submits, Shift+Enter is a new line); one-row navigation on phones; `clean_install.sh` and
`tests/test_packaging.py` check that the wheel carries the page's files.

Replace "Unreleased" with the date when the tag is cut.

[0.1.0]: https://github.com/tolkienbagchi/GAO-Institutional-Design/releases/tag/v0.1.0
