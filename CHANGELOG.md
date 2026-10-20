# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-10-19

First release on PyPI (`pipx install contribpolicy`). The JSON/YAML schema is unchanged (`version: 1`),
so policy files stay compatible with the AI Contribution Check GitHub Action.

### Fixed

- The Apache license header ("Licensed to the Apache Software Foundation (ASF) under one or more contributor
  license agreements") no longer counts as a CLA requirement.
- "We use the DCO instead of a CLA" (and "rather than", "in place of", "replaces", "switched from") no
  longer counts as a CLA requirement. The same applies the other way round: "a CLA instead of the DCO" no
  longer counts as a DCO requirement.
- A maintainer's "sign-off" on a change ("only with maintainer sign-off") no longer counts as a DCO requirement.
- A general ban on AI trailers ("No AI attribution/authorship trailers", "any AI-attribution trailers") now
  reports `trailers.assisted_by: banned` as well as `trailers.co_authored_by_ai: banned`. An explicitly
  allowed `Co-authored-by` in the same sentence is kept.

### Changed

- A CLA that applies only to some contributions ("a CLA is only required for larger changes", "trivial fixes
  do not need a CLA") is reported as conditional: `cla` stays `true` (the field is a boolean in the shared
  schema), the evidence entry has the rule `cla.conditional`, the table shows "CLA only for some changes",
  `agents-md` says the CLA may be needed, and `check` prints a softer reminder.
- Package metadata for PyPI: `Source` and `Changelog` project URLs; the version is read from
  `src/contribpolicy/_meta.py`; README links are absolute so they work on pypi.org.

### Added

- Release workflow (`.github/workflows/release.yml`) that tests, builds and publishes to PyPI with Trusted
  Publishing when a GitHub release is published. See `docs/RELEASING.md`.

## [0.1.0] - 2026-10-13

First public release (GitHub only, installable from the `v0.1.0` tag).

### Added

- `contribpolicy show`: find a repository's AI-contribution rules (local checkout or GitHub) and print them
  with evidence quotes and line numbers as a table, JSON, YAML or Markdown.
- `contribpolicy check`: lint a draft PR description and commit messages against those rules.
- `contribpolicy agents-md`: print the rules as an instruction block for a coding agent.
- `contribpolicy registry`: list the bundled org registry (Kubernetes, Tekton, Flux, Jaeger, Cortex).

[0.1.1]: https://github.com/pujitha24/contribpolicy/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/pujitha24/contribpolicy/releases/tag/v0.1.0
