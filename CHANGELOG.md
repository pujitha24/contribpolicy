# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.2] - 2026-10-23

The JSON/YAML schema is unchanged (`version: 1`).

### Added

- Claude Code integration. `contribpolicy hook claude-code` is a `PreToolUse` hook: before Claude runs
  `git push` or `gh pr create`, it finds the upstream repository (`git config contribpolicy.repo`, `gh pr
  create --repo`, the `upstream` remote, the fork parent via `gh repo view --json parent`, or `origin`),
  collects the commits since the merge base with the upstream default branch and, for `gh pr create`, the
  `--body` / `--body-file` text (including here-documents), and runs the same checks as `check`. A
  violation denies the call with a reason that names the rule, its source line, the commit and the fix;
  warnings and reminders are passed to Claude as context; any internal error fails open, with a note.
  Every other command exits at once. Policies are cached for 24 hours. Per-repository settings live in
  git config (`contribpolicy.hook`, `.repo`, `.policy`, `.offline`, `.aiAssisted`). See
  `docs/claude-code.md`.
- A Claude Code skill (`integrations/claude-code/SKILL.md`) that has Claude read a repository's rules with
  `agents-md` / `show` before working there, and a ready-to-copy settings snippet
  (`integrations/claude-code/settings.json`).

### Fixed

- "Resource recommendations must be created by human beings" (awesome-claude-code) was missed. A rule that
  some text must be created, made, written, submitted or opened "by human beings" / "by humans" / "by a
  person" now sets `human_only` (`issues` for issues, bug reports, recommendations and feature requests;
  `pr-description`, `commit-messages`, `review-replies` as named), and `ai_use: restricted` when it covers a
  whole contribution (recommendations, submissions, pull requests). It needs normative wording ("must") in
  an AI context; a leading concession ("Although resources may be written by a coding agent, ...") is not
  the subject, and "human" in other senses ("treat maintainers as human beings", "issues are triaged by
  humans", "human-readable") is ignored. "Do not have to be written by humans" no longer reads as a ban.
- More CLA misreads from discovery round 2 no longer set `cla: true`: "We use the DCO, not a CLA", "we
  don't explicitly require a CLA" (an adverb between the negation and the verb), "no extra CLA needed"
  ("no separate / additional CLA"), and the Chinese "无需额外 CLA" / "不需要 CLA" / "不需要签署 CLA".
- DCO and CLA negations are now read per clause, so "We don't require a CLA, but sign off every commit"
  is still a DCO rule.

### Changed

- The console script entry point is `contribpolicy._entry:main`, which sends `hook claude-code` past the
  extractor imports so the hook stays fast; every other command behaves as before.

## [0.1.1] - 2026-10-19

Prepared for PyPI (not published; 0.1.2 is the first PyPI release) (`pipx install contribpolicy`). The JSON/YAML schema is unchanged (`version: 1`),
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

[0.1.2]: https://github.com/pujitha24/contribpolicy/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/pujitha24/contribpolicy/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/pujitha24/contribpolicy/releases/tag/v0.1.0
