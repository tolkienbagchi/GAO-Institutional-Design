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

Replace "Unreleased" with the date when the tag is cut.

[0.1.0]: https://github.com/tolkienbagchi/GAO-Institutional-Design/releases/tag/v0.1.0
