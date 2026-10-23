---
name: contribpolicy
description: Look up an open-source repository's rules for AI-assisted contributions (AI disclosure, banned or required Assisted-by / Co-authored-by trailers, parts a human must write, DCO, CLA, signed commits, required issue labels) with the contribpolicy CLI, and check commits and a PR description against them. Use before starting work in an unfamiliar repository you do not own, before committing or pushing there, and before opening a pull request or issue.
allowed-tools: Bash(contribpolicy *)
---

# Follow a repository's AI-contribution rules

Open-source projects increasingly write down rules for AI-assisted work, and they disagree:
one project requires an `Assisted-by:` trailer, the next one forbids it; some want the PR
description written by a human. Breaking a written rule gets a PR closed, often unreviewed.
`contribpolicy` finds those rules (CONTRIBUTING, AGENTS.md, AI policy files, PR templates,
org-level policies) and prints each one with the sentence and line it came from.

## 1. Before you start work

Find the upstream repository (the one the PR will go to, not the user's fork):

```sh
git remote -v                            # an `upstream` remote, if there is one
gh repo view --json parent -q .parent    # the fork's parent, if gh is installed
```

Then read its rules. `agents-md` is the short form meant for you; `show` has the evidence:

```sh
contribpolicy agents-md OWNER/NAME
contribpolicy show OWNER/NAME            # table with sources and evidence quotes
contribpolicy show .                     # a local checkout, when there is no GitHub remote
```

Network access to GitHub is needed for `OWNER/NAME` (no token required). If the command is
missing, tell the user it can be installed with `pipx install contribpolicy`, then continue
by reading CONTRIBUTING.md and any AI policy file yourself.

## 2. How to read the output

| Field | What to do |
| --- | --- |
| `AI use: BANNED` | Stop. Tell the user the project does not accept AI-generated contributions. Do not write the change. |
| `AI use: RESTRICTED` | AI help is accepted only under the conditions listed (labels, assignment, parts a human must write). Check each one with the user before writing code. |
| `Disclosure required in: description / checkbox / commit-trailer` | Say in the PR how AI was used, in the place named. Use the `example` sentence when there is one. |
| `Assisted-by trailer: required` | Add `Assisted-by: <agent>/<model>` to every commit. |
| `Assisted-by trailer: banned`, `AI Co-authored-by: banned` | Never add `Assisted-by`, `Co-developed-by` or `Co-authored-by: <AI>` lines, and do not add "Generated with" footers to commits. |
| `Human-only: ...` | The human must write these: `review-replies`, `commit-messages`, `pr-description`, `issues`. Do not draft them; hand that part back to the user. |
| `Required labels`, `Assignment required`, `Issue first` | Only work on an issue that meets these conditions, and link it in the PR (`Fixes #123`). |
| `DCO yes` | Every commit needs `Signed-off-by:` with the human's name and email (`git commit -s`), never the agent's. |
| `CLA yes` | Remind the user that they must have signed the CLA. You cannot sign it. |
| `signed+verified commits yes` | Commits must be signed with the user's key. Do not set up keys yourself; ask the user. |

Every rule is a heuristic reading of the source. When a rule matters to what you are about to do,
open the quoted file and line (`Evidence` in `show`, links in `agents-md`) and confirm it. If the
quote does not say what the field claims, follow the source and tell the user.

## 3. Before you push or open the PR

Check the commits and the PR description:

```sh
contribpolicy check OWNER/NAME --pr-body pr.md --git-range upstream/main..HEAD --ai-assisted yes
```

`ERROR` lines are violations: fix them. `WARN` lines need a look. `NOTE` lines are rules text cannot
verify (for example "write review replies yourself"); pass them on to the user.

If the user installed the contribpolicy Claude Code hook, the same check runs automatically before
`git push` and `gh pr create`, and a violation blocks the command with a reason that names the
rule, the source line and the fix. Fix the cause and run the command again. Do not work around the
hook (by disabling it, splitting the command, or pushing another way) unless the user tells you to;
if you think a rule was misread, show the user the quoted rule text.
