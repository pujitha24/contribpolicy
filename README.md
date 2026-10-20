# contribpolicy

**Find a repository's rules for AI-assisted contributions, with the sentence and line each rule came from. Then lint your PR against them.**

Coding agents (Claude Code, Copilot, Codex, Cursor, ...) open a lot of pull requests now, and open-source projects have answered with written rules for AI-assisted work. The rules are reasonable. They are also scattered:

- in `CONTRIBUTING.md`, `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` or the PR template;
- in a separate file the CONTRIBUTING guide links to (`AI_POLICY.md`, `GENAI_POLICY.md`, `AI_CONTRIBUTIONS.md`, `docs/contributing/...`);
- or outside the repository altogether. Every Kubernetes repository follows the AI guidance on kubernetes.dev, every Tekton repository follows `tektoncd/community/ai-contribution-policy.md`, and Jaeger's PR template (with its AI-usage checkbox) lives in `jaegertracing/.github`.

The rules also differ from project to project. Flux *requires* an `Assisted-by:` commit trailer; Kubernetes and Thanos *forbid* it. Jaeger lets you credit your tool with `Co-authored-by`; Flux and Kubernetes forbid it. Thanos does not want AI-written commit messages. Radius accepts automated contributions only on issues labelled `triaged` and (`help wanted` or `good first issue`). Grafana requires signed *and verified* commits in every repository. Get one of these wrong and the PR is closed, sometimes without a review.

`contribpolicy` finds all of a repository's policy sources, extracts the rules **deterministically** (regular expressions and a few structural heuristics, no LLM, no API key), and prints them with evidence quotes and line numbers. It can also lint a draft PR (description plus commit messages) against those rules and exit non-zero on a violation, so it fits in a pre-push hook, a CI job or an agent harness.

## 30-second quick start

```console
$ pipx install contribpolicy                   # from PyPI (or: pip install contribpolicy)

$ contribpolicy show thanos-io/thanos          # any public repo, via the GitHub API
$ contribpolicy show .                         # a local checkout
$ contribpolicy show fluxcd/flux2 -f json      # json | yaml | markdown | table (default)

$ contribpolicy agents-md kubernetes-sigs/kueue      # a rules block to paste into an agent's context

$ contribpolicy check kubernetes-sigs/kueue --pr-body body.md --git-range origin/main..HEAD
```

Until the PyPI release is live, install from the Git tag instead: `pipx install git+https://github.com/pujitha24/contribpolicy@v0.1.1`.

No token is needed. Without one, GitHub allows 60 API requests an hour, and one `show` uses about 4 to 10 of them (file contents come from `raw.githubusercontent.com`, which does not count). Set `GITHUB_TOKEN`, or log in with `gh auth login`, to raise the limit. Python 3.10+ and no runtime dependencies.

## Sample output

```console
$ contribpolicy show thanos-io/thanos
contribpolicy · thanos-io/thanos

AI use               DISCLOSE  (AI assistance is allowed if you disclose it)
Disclosure           required in: description
  example            "This PR was written in part with the assistance of generative AI"
Assisted-by trailer  banned
AI Co-authored-by    banned
Human-only           review-replies, commit-messages
Issue first          yes
Assignment required  no
Required labels      -
DCO / CLA / signed   DCO yes  ·  CLA no  ·  signed+verified commits no
UI screenshots       -

Sources
  [repo]         CONTRIBUTING.md
                 https://github.com/thanos-io/thanos/blob/HEAD/CONTRIBUTING.md
  ...

Evidence
  disclosure.required  (CONTRIBUTING.md:69)
      "If you used AI tools in preparing your PR, you must disclose this in the description of your PR."
  trailers.assisted_by  (CONTRIBUTING.md:71)
      "Listing AI tooling as a co-author, co-signing commits using an AI tool, or using the
      assisted-by, co-developed or similar commit trailer is not allowed."
  human_only.commit-messages  (CONTRIBUTING.md:75)
      "Please do not use AI to generate the commit message, as it can often turn into ..."
  human_only.review-replies  (CONTRIBUTING.md:79)
      "When responding to review comments, you must do so without relying on AI tools."
  ...

Heuristic extraction: always read the sources before relying on this. Not legal advice.
```

Linting a draft PR for a Kubernetes SIG repository, where the AI rules come from the org-level guide:

```console
$ contribpolicy check kubernetes-sigs/kueue --pr-body body.md --commits commits.txt
contribpolicy check · kubernetes-sigs/kueue

ERROR trailers.assisted_by: AI attribution trailer is not allowed: 'Assisted-by: Claude Code' [commit 'Fix flaky admission test']
        rule source AGENTS.md:42: "Do not add AI co-author lines, `assisted-by`, `co-developed`, or similar commit trailers."
ERROR trailers.co_authored_by_ai: AI co-author trailer is not allowed: 'Co-authored-by: Claude <noreply@anthropic.com>' [...]
ERROR disclosure: AI assistance is not disclosed (expected in: description) [PR description]
        rule source AGENTS.md:40: "Disclose AI usage in the PR description."
NOTE  human_only.review-replies: Write replies to review comments yourself, without AI tools [review]
        rule source registry:kubernetes/community:contributors/guide/pull-requests.md:644: "When responding to review ..."
NOTE  cla: The human author must have signed the CLA [author]

3 violation(s), 0 warning(s)
```

[`docs/examples/`](https://github.com/pujitha24/contribpolicy/tree/main/docs/examples) has full `show` output (table, JSON and `agents-md`) for Kueue, Thanos, Jaeger, Cortex, Radius, Microsoft Agent Framework, Grafana Mimir, Flux, Tekton Pipelines and a repository without an AI policy.

## What it detects

| Field | Values | Recognises, for example |
| --- | --- | --- |
| `ai_use` | `allowed` `disclose` `restricted` `banned` `unspecified` | "Using AI tools ... is acceptable"; "must disclose"; "automated contributors must restrict their contributions to issues labeled ..."; "we do not accept AI-generated contributions" |
| `disclosure.required` / `where` | `description`, `checkbox`, `commit-trailer` | "disclose this in the description of your PR"; an AI-usage checkbox group in the PR template (including the org `.github` template); "Preferred: Git commit message trailer" |
| `disclosure.example` | text | the quoted example sentence, the `Assisted-by:` example, or the template option to tick |
| `trailers.assisted_by` | `required` `allowed` `banned` `unspecified` | Flux's required `Assisted-by: AGENT/MODEL`; Tekton's *preferred* trailer (`allowed`); Kubernetes' ban on `assisted-by`/`co-developed` |
| `trailers.co_authored_by_ai` | `allowed` `banned` `unspecified` | "Listing AI tooling as a co-author ... is not allowed"; "must not add Co-authored-by"; "an AI account as co-author breaks EasyCLA"; Jaeger's "up to you" |
| `human_only` | `review-replies` `commit-messages` `pr-description` `issues` | "respond to review comments ... without relying on AI tools"; "do not use AI to generate the commit message"; "issues, pull request reviews, and discussions must be substantively human-authored"; an agent answering review feedback listed under a "Disallowed" heading |
| `required_labels` | AND of OR-groups | `` labeled `triaged` and either `help wanted` or `good first issue` `` → `[["triaged"], ["help wanted", "good first issue"]]` |
| `assignment_required` | bool | "PRs from contributors who are not assigned to the issue will be closed" (but not "we do not assign issues" or "must not be assigned to someone else") |
| `issue_first` | bool | "Before making any significant changes, please open an issue" |
| `dco` / `cla` / `signed_commits` | bool | `Signed-off-by` / DCO; Contributor License Agreement / EasyCLA; "signed **and verified**", GPG/SSH, Verified badge (a DCO sign-off is *not* a signed commit). A CLA asked for only for some changes ("only for larger changes") is `cla: true` with a `cla.conditional` evidence entry |
| `ui_screenshots_required` | bool | "attach screenshots or a recording for UI changes" |
| evidence-only `guidance.*` | | run tests locally, be able to explain the change, keep AI-assisted PRs small, review AI output yourself |

Every value comes with `evidence`: the rule id, a quote of at most 240 characters, the source path and the line number.

Things it deliberately does **not** count as a policy: a project that merely mentions an AI product ("Copilot code review runs on every PR"), "AI" inside other words (`MAIL`, `AIX`, `OpenAI`), `Co-authored-by` used to credit human pair programmers, prose such as "assisted by AI" (only the hyphenated trailer counts), rules under a "Good use of AI" heading, qualified rules ("do not post *bulk* AI-generated comments"), "label X *or* explicit maintainer agreement" (not a hard label requirement), the Apache license header's "under one or more contributor license agreements" (not a CLA rule), the agreement a project does *not* use ("we use the DCO instead of a CLA"), and a maintainer's "sign-off" on a change (not the DCO).

### Where it looks

1. Well-known files in the repository root, `.github/` and `docs/`: `CONTRIBUTING*`, `AI_POLICY.md`, `GENAI_POLICY.md`, `AI_CONTRIBUTIONS.md`, `ai-contribution-policy.md` and similar names, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, and the pull request template(s).
2. One hop of in-repository links from those files, when the link target or text looks like a policy (relative links, `/absolute` links, same-repository `github.com/.../blob/...` URLs, directory links to their README, and `@path` imports in `CLAUDE.md`/`AGENTS.md`). At most 10 linked documents.
3. GitHub's organisation fallback: if the repository has no CONTRIBUTING or no PR template, the ones in `<owner>/.github` (kind `org`).
4. The bundled **org registry** (kind `org`, path prefixed with `registry:`).

Identical documents (for example a `CLAUDE.md` that duplicates `AGENTS.md`) are read once.

## Output schema

`--format json` (and `yaml`) prints exactly this structure, which is shared with a companion GitHub Action:

```json
{
  "version": 1,
  "repo": "owner/name",
  "sources": [{"path": "CONTRIBUTING.md", "url": "https://...", "kind": "repo|org|linked"}],
  "ai_use": "allowed|disclose|restricted|banned|unspecified",
  "disclosure": {"required": true, "where": ["description", "checkbox", "commit-trailer"], "example": "..."},
  "trailers": {"assisted_by": "required|allowed|banned|unspecified", "co_authored_by_ai": "allowed|banned|unspecified"},
  "human_only": ["review-replies", "commit-messages", "pr-description", "issues"],
  "assignment_required": false,
  "issue_first": false,
  "required_labels": [["triaged"], ["help wanted", "good first issue"]],
  "dco": true, "cla": false, "signed_commits": false,
  "ui_screenshots_required": false,
  "evidence": [{"rule": "trailers.assisted_by", "quote": "...", "path": "CONTRIBUTING.md", "line": 42}]
}
```

`disclosure.example` is `""` when no example was found. `cla` stays a boolean so that the companion Action can read the file; when every CLA statement is conditional, the evidence entry has the rule `cla.conditional` instead of `cla`, and the table shows "CLA only for some changes". `where` lists every channel the sources mention; it does not say whether they are alternatives (Tekton) or all required (Flux), so read the evidence. Sources from the org registry have paths of the form `registry:<repo>:<path>` and their `url` is the registry's canonical link; sources from the `<owner>/.github` fallback look like `<owner>/.github:<path>`.

## `check`: lint a draft PR

```console
contribpolicy check owner/repo --pr-body body.md --commits commits.txt
contribpolicy check .          --pr-body body.md --git-range origin/main..HEAD
contribpolicy check --policy policy.json --pr-body - < body.md     # offline, from saved `show -f json`
```

- `--commits` accepts plain `git log` output, NUL-separated messages (`git log --format=%H%n%B%x00`) or messages separated by a `---` line. `--git-range` reads the commits itself (merge commits are skipped) and also checks signatures with `git log %G?`: an unsigned commit is an error, a signature git cannot verify locally (no public key) is a warning.
- `--ai-assisted auto|yes|no` (default `auto`): in `auto` mode, AI use is inferred from AI trailers, an AI co-author, a ticked AI checkbox or a description that says AI was used. If none of these are present, disclosure rules produce warnings rather than errors.
- When the policy needs a linked issue (labels, assignment), `check` looks the issue up on GitHub (read-only) and checks its labels, state and assignees. Pass `--author LOGIN` to verify that the issue is assigned to you, and `--no-issue-lookup` to skip the lookup.
- Rules that text cannot verify, such as "write review replies yourself" or "sign the CLA", are printed as `NOTE` reminders and never fail the run.
- Exit codes: `0` no violations, `1` violations, `2` usage, network or rate-limit error. `--format json` gives machine-readable results.

## Using it in CI, hooks and agent harnesses

**GitHub Actions**, checking the PR that triggered the workflow:

```yaml
on: pull_request
permissions:
  contents: read
  pull-requests: read
jobs:
  contribpolicy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<full-commit-sha>   # pin to a SHA
        with:
          fetch-depth: 0
      - run: pipx install contribpolicy==0.1.1
      - env:
          GITHUB_TOKEN: ${{ github.token }}
          PR_BODY: ${{ github.event.pull_request.body }}
        run: |
          printf '%s' "$PR_BODY" > "$RUNNER_TEMP/body.md"
          contribpolicy check "${{ github.repository }}" \
            --pr-body "$RUNNER_TEMP/body.md" \
            --git-range "origin/${{ github.base_ref }}..${{ github.event.pull_request.head.sha }}"
```

**Pre-push hook** (`.git/hooks/pre-push`) for your fork. Save the upstream policy once, so the hook works offline:

```sh
contribpolicy show upstream-owner/repo -f json -o .git/contribpolicy.json   # once
```

```sh
#!/bin/sh
exec contribpolicy check --policy .git/contribpolicy.json --git-range origin/main..HEAD --ai-assisted yes
```

**Agent harness.** Put the rules in front of the agent before it starts, and gate the push on `check`:

```sh
contribpolicy agents-md owner/repo > /tmp/rules.md      # prepend to the agent's prompt or AGENTS.md
contribpolicy check owner/repo --pr-body pr.md --git-range origin/main..HEAD --ai-assisted yes || exit 1
```

`agents-md` prints short imperative rules ("Do NOT add `Assisted-by` ... trailers", "A human must write replies to review comments") with a link to the source line of each one.

## The org registry

Some organisations publish one policy for all of their repositories and do not copy it into each one. The registry in [`src/contribpolicy/data/org_registry.json`](https://github.com/pujitha24/contribpolicy/blob/main/src/contribpolicy/data/org_registry.json) maps a GitHub organisation to the **document** that holds its policy. The document is fetched at run time and goes through the same extractor as repository files, so the registry records *where* the rules are, never a hand-written copy of them, and evidence lines point into the real document.

| Entry | Organisations | Source |
| --- | --- | --- |
| `kubernetes-ai-guidance` | kubernetes, kubernetes-sigs, kubernetes-csi, kubernetes-client | `kubernetes/community` `contributors/guide/pull-requests.md`, section "AI Guidance" ([kubernetes.dev](https://www.kubernetes.dev/docs/guide/pull-requests/#ai-guidance)) |
| `tekton-ai-contribution-policy` | tektoncd | `tektoncd/community` `ai-contribution-policy.md` |
| `flux-contributing` | fluxcd | `fluxcd/.github` `CONTRIBUTING.md` |
| `jaeger-ai-policy` | jaegertracing | `jaegertracing/jaeger` `AI_POLICY.md` (the policy says it covers every repository in the org) |
| `cortex-genai-policy` | cortexproject | `cortexproject/cortex` `GENAI_POLICY.md` (same) |

Registry sources are always marked: `[org registry]` in the table, `registry:` in JSON paths. List the entries with `contribpolicy registry`. Skip org sources with `--no-org`.

### Contributing an entry

1. Find the organisation's own statement that the policy applies across its repositories. A policy that only happens to be copied into several repositories does not qualify; those repositories are already read directly.
2. Add an entry to `org_registry.json`:

   ```json
   {
     "id": "example-ai-policy",
     "title": "Example AI contribution policy",
     "orgs": ["example-org"],
     "url": "https://example.dev/contributing/ai",
     "repo": "example-org/community",
     "path": "AI_POLICY.md",
     "section": "",
     "ai_policy": true,
     "scope": "Where the org says this applies to all of its repositories.",
     "verified": "2026-10-12"
   }
   ```

   `repo` + `path` must be a Markdown file on GitHub (that is what gets fetched). Set `section` to a heading title to read only that section. Set `ai_policy: true` when the whole document (or section) is about AI, so that every sentence in it is read in AI context. `exclude` (optional) lists `owner/name` repositories the entry does not cover.
3. Try it before opening the PR: `contribpolicy show example-org/some-repo --registry my-entry.json`. Entries in `--registry` override bundled entries with the same `id`.
4. Add a line to the `EXPECT` table in `tests/test_live.py` and run `CONTRIBPOLICY_LIVE=1 pytest tests/test_live.py`.

## Limitations

- **It is a heuristic.** It reads English Markdown with regular expressions. It will miss rules phrased in ways it does not know, and it can over-read a sentence. The evidence quote and line number are there so that you can check: **always read the source** before relying on the result. This is not legal advice about DCOs, CLAs or licensing.
- Conditions are collapsed. "Disclose *significant* AI use" (Cortex) and "disclose *material* AI assistance" (Agent Framework) both become `disclosure.required: true`. `human_only` can be stricter than the source: Jaeger lets AI help you *word* a review reply as long as you read and stand behind it, and only forbids letting an agent answer autonomously. The schema reports that as `review-replies`.
- `where` does not distinguish alternatives from requirements (see the schema notes above).
- `restricted` is detected for label-gated and maintainer-only automation. Conditions such as "the issue must not be assigned to someone else or already have an open PR" appear in the evidence, not in a field.
- Policies that live only on a website outside GitHub are invisible unless they have a registry entry pointing at their GitHub source. Links are followed one hop, at most 10 documents, and only inside the same repository.
- `check` verifies text: trailers, sign-offs, ticked boxes, issue references, AI "Generated with" footers. It cannot tell whether a human actually wrote a review reply.
- Unauthenticated GitHub API use is limited to 60 requests an hour.

## Development

```console
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -e .
pytest                                     # offline unit tests with synthetic fixtures
CONTRIBPOLICY_LIVE=1 pytest tests/test_live.py   # read-only checks against real repositories
python scripts/smoke.py                    # regenerate docs/examples/
ruff check . && ruff format --check .
```

Releases are published to PyPI from GitHub Actions with Trusted Publishing; see [`docs/RELEASING.md`](https://github.com/pujitha24/contribpolicy/blob/main/docs/RELEASING.md) and the [changelog](https://github.com/pujitha24/contribpolicy/blob/main/CHANGELOG.md).

The test fixtures in `tests/fixtures/` are synthetic documents written for this project, modelled on the shapes of real 2026 policies, not copies of them.

## Why this exists

I built this while running AI-assisted contributions to open-source projects and learning these rules the hard way. Pull requests were closed for breaking a rule that was written down, just not in the file I had read: a trailer that one project asks for and the next one forbids, a policy kept at the organisation level, a checkbox in a template that lives in another repository. Maintainers are right to expect contributors to know their rules. This tool makes those rules quick to find and hard to miss, for people and for the agents they run.

In the spirit of the subject: this project was developed with AI coding assistance.

## License

[MIT](https://github.com/pujitha24/contribpolicy/blob/main/LICENSE) © 2026 Pujitha Paladugu
