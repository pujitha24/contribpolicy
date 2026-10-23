# Using contribpolicy with Claude Code

`contribpolicy` ships two pieces for [Claude Code](https://code.claude.com/docs):

- a **`PreToolUse` hook** (`contribpolicy hook claude-code`) that checks every `git push` and
  `gh pr create` Claude is about to run against the upstream repository's written AI-contribution
  rules, and blocks the command with a reason Claude can act on when a rule is broken;
- a **skill** (`integrations/claude-code/SKILL.md`) that tells Claude to read a repository's rules
  with `contribpolicy agents-md` / `show` before it starts work there, and how to read the output.

The hook catches what slips through at the last moment (a trailer the project forbids, a missing
sign-off, an undisclosed AI-assisted PR). The skill avoids most of those mistakes in the first place.

## Install

1. Install the CLI (Python 3.10+, no dependencies), so that `contribpolicy` is on your `PATH`:

   ```sh
   pipx install contribpolicy        # or: pipx install git+https://github.com/pujitha24/contribpolicy@v0.1.2
   ```

2. Add the hook to your Claude Code settings: `~/.claude/settings.json` for every project, or
   `.claude/settings.json` in one project. The snippet is also in
   [`integrations/claude-code/settings.json`](../integrations/claude-code/settings.json):

   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Bash",
           "hooks": [
             {
               "type": "command",
               "command": "contribpolicy hook claude-code",
               "timeout": 60
             }
           ]
         }
       ]
     }
   }
   ```

   If the file already has a `hooks.PreToolUse` list, add the object to it. Run `/hooks` in Claude
   Code to confirm the hook is registered.

3. Optional: install the skill.

   ```sh
   mkdir -p ~/.claude/skills/contribpolicy
   curl -fsSL https://raw.githubusercontent.com/pujitha24/contribpolicy/v0.1.2/integrations/claude-code/SKILL.md \
     -o ~/.claude/skills/contribpolicy/SKILL.md
   ```

The hook runs before every Bash command, so it is built to be cheap: a command that is not a
`git push` or `gh pr ...` call is recognised before the extractor is even imported, and the hook
exits 0 in about the time it takes to start Python (30 to 50 ms on a laptop).

## What the hook checks

**Which commands.** `git push` (including `git -C dir push`, `cd dir && git push`, `FOO=1 git push`,
multi-line commands and pipelines) and `gh pr create` / `gh pr new`. Pushes that publish nothing
(`--dry-run`, `-n`, `--delete`, `origin :branch`) and `gh pr create --dry-run` are skipped. Everything
else exits 0 at once.

**Which repository's rules.** The first of:

1. `git config contribpolicy.repo OWNER/NAME`;
2. `gh pr create --repo OWNER/NAME`;
3. the `upstream` remote, if it points at GitHub;
4. the parent of `origin`, when `origin` is a fork (`gh repo view --json parent`, if the GitHub CLI is installed);
5. `origin` itself.

The policy is fetched from GitHub the same way `contribpolicy show OWNER/NAME` does it, and cached for
24 hours in `~/.cache/contribpolicy/policies/` (`$XDG_CACHE_HOME` and `$CONTRIBPOLICY_CACHE_DIR` are
honoured). If GitHub cannot be reached, an older cached copy is used and Claude is told so.

**Which commits.** The non-merge commits between the upstream default branch (or `gh pr create --base`)
and the ref being pushed (or `--head`): `git log $(git merge-base upstream/main HEAD)..HEAD`. The
upstream branch is the remote-tracking ref of the remote that points at the policy repository, else
`upstream`, else `origin`; keep it fetched.

**Which PR text.** For `gh pr create`: `--body`/`-b` (including the
`--body "$(cat <<'EOF' ... EOF)"` form Claude Code uses), `--body "$(cat file)"`, `--body-file`/`-F` with
a file, or `-F -` fed by a here-document or `cat <<EOF |` / `echo ... |`. With `--fill` or `--web` the
body is not known; the commits are still checked.

**The checks** are the ones `contribpolicy check --ai-assisted yes --git-range ...` runs: banned or
required `Assisted-by` trailers, AI `Co-authored-by` lines, AI disclosure in the PR description or
template checkbox, "Generated with" footers where commit messages or PR descriptions must be written by a
human, DCO sign-offs (and sign-offs that name an AI), signed commits, and an issue reference when the
project requires labelled or assigned issues (the issue is looked up on GitHub, read-only).

**What happens:**

| Result | Hook output | Effect in Claude Code |
| --- | --- | --- |
| A violation | `permissionDecision: "deny"` with a `permissionDecisionReason` | The command does not run. Claude sees the reason: each rule, its source line, the offending commit and the fix. |
| Only warnings or reminders | `additionalContext`, no decision | The command goes through the normal permission flow; Claude sees the notes next to the result. Reminders (CLA, "write review replies yourself") are added for `gh pr create` only. |
| Nothing to report | no output | Nothing changes. |
| The hook could not check (no network and no cache, no upstream ref, unparseable command, a bug) | `additionalContext` saying so, plus a line on stderr | The command is **not** blocked. Claude is told the check did not run, so "not checked" is never mistaken for "clean". |
| Not applicable (not a git repository, no GitHub remote, disabled) | a line on stderr only | Nothing changes. |

The hook never answers `"allow"`, so it can never skip one of your permission prompts. It always exits 0.

## Example: a blocked push

A fork of a project whose CONTRIBUTING.md says that "adding an `assisted-by`, `co-developed` or similar
commit trailer is not allowed". Claude committed with an `Assisted-by: Claude Code` trailer and runs
`git push -u origin fix-flaky-test`. Claude Code shows the call as denied, and Claude receives:

```text
contribpolicy blocked this command: 1 contribution-rule violation(s).
1. acme/widgetron bans `Assisted-by`, `Co-developed-by` and similar AI commit trailers (CONTRIBUTING.md:18): AI attribution trailer is not allowed: 'Assisted-by: Claude Code' [commit a206a722eb]. Fix: remove the trailer from that commit: `git rebase -i a895d6d3d946` and `reword` it (or `git commit --amend` if it is the last commit) and disclose the AI assistance in the PR description instead.
   Rule text: "Naming an AI tool as a co-author, co-signing commits with an AI tool, or adding an `assisted-by`, `co-developed` or similar commit trailer is not allowed."
Fix these, then run the command again. If a rule looks misread, show the quoted rule text to the user instead of working around this check.
```

Claude amends the commit and pushes again; this time the hook prints nothing and the push goes ahead.
When it opens the PR, a `gh pr create` whose body does not disclose the AI assistance is blocked in the
same way, with the project's own example sentence in the fix.

You can reproduce this without Claude Code: pipe the hook input into the hook yourself.

```sh
printf '%s' '{"hook_event_name":"PreToolUse","tool_name":"Bash","cwd":"'"$PWD"'","tool_input":{"command":"git push"}}' \
  | contribpolicy hook claude-code
```

## Configuration

All settings are per repository, in git config (`git config <key> <value>` inside the checkout), so they
also apply when you use the CLI by hand. The hook also accepts `--repo`, `--policy`, `--offline` and
`--ai-assisted` on its command line, for a settings file that should override them.

| Key | Meaning |
| --- | --- |
| `contribpolicy.hook false` | Turn the hook off for this repository. |
| `contribpolicy.repo OWNER/NAME` | The repository whose rules apply (when the remotes do not say). |
| `contribpolicy.policy PATH` | Use a saved policy (`contribpolicy show OWNER/NAME -f json -o PATH`) or a local checkout directory instead of fetching. Relative paths are relative to the repository root. |
| `contribpolicy.offline true` | Never use the network (needs `contribpolicy.policy`). |
| `contribpolicy.aiAssisted auto\|yes\|no` | Whether the commits count as AI-assisted. Default `yes`: Claude is the one pushing. |

**Turning it off for one repository:** `git config contribpolicy.hook false`. To turn it off for a
whole session, start Claude Code with `CONTRIBPOLICY_HOOK=0` in the environment. To turn it off
everywhere, remove the entry from your settings file.

## Limitations

- The rules come from the same heuristic extractor as `contribpolicy show`: regular expressions over
  English Markdown. It can miss a rule or over-read a sentence. The reason always quotes the source
  sentence and line, so Claude (and you) can check it; a misreading should be reported, not worked around.
- Only text is checked. The hook cannot tell whether a human wrote a review reply or a PR description;
  for projects that require that, it adds a reminder to Claude's context instead of blocking.
- The commit range depends on a fetched upstream branch. If your fork's default branch lags the upstream
  and there is no `upstream` remote, older upstream commits can appear in the range; above 100 commits the
  hook stops and says the upstream ref looks stale.
- Shell parsing covers the forms coding agents write (chains, pipelines, here-documents, `cd`, `-C`),
  not every shell construct. Pushes hidden inside scripts, aliases or `bash -c '...'` are not seen.
- Only `git push` and `gh pr create` are checked; `gh pr edit`, `gh issue create` and pushes made by
  other tools are not.
- The first push to a repository fetches its policy from GitHub (about 4 to 10 API requests; set
  `GITHUB_TOKEN` or log in with `gh` to raise the 60 requests per hour limit). Later pushes use the cache.
