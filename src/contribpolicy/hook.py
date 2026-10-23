"""Claude Code integration: a ``PreToolUse`` hook for ``git push`` and ``gh pr create``.

Claude Code runs the hook before every Bash tool call and passes the call as JSON on stdin
(https://code.claude.com/docs/en/hooks). The hook ignores every command except ``git push`` and
``gh pr create``. For those it works out the upstream repository, collects the commits that would be
pushed (and, for ``gh pr create``, the PR body), and runs the same checks as ``contribpolicy check``:

* violations deny the tool call (``permissionDecision: "deny"``) with a reason Claude can act on;
* warnings and reminders are passed to Claude as ``additionalContext``, without a decision, so the
  normal permission flow is unchanged;
* any internal error fails open: the call goes ahead, with a short note on stderr and, when the
  command was a push or PR, in Claude's context, so that "not checked" never reads as "clean".

The hook never returns ``"allow"``: it can block a call or add context, never skip a permission prompt.

Only the standard library is imported at module level, so unrelated commands exit fast; the extractor
and the GitHub client are imported when a push or PR is actually checked.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._entry import MAYBE_PUSH_OR_PR as _MAYBE
from ._meta import TOOL_NAME

__all__ = [
    "Action",
    "HookResult",
    "add_arguments",
    "extract_heredocs",
    "main",
    "parse_command",
    "run_hook",
]

DESCRIPTION = (
    "Claude Code PreToolUse hook. Reads the tool call as JSON on stdin. For `git push` and `gh pr create` it "
    "checks the commits (and the PR body) against the upstream repository's AI-contribution rules and denies "
    "the call on a violation; every other command exits 0 at once. Internal errors never block a command."
)
MAX_COMMITS = 100
CACHE_TTL = 24 * 3600
_MAX_BODY_BYTES = 1024 * 1024
_MAX_ITEMS = 10

Runner = Callable[..., subprocess.CompletedProcess]


# --------------------------------------------------------------------------
# Command parsing
# --------------------------------------------------------------------------


@dataclass
class Action:
    kind: str  # push | pr-create | unparsed
    cwd: str
    src: str = "HEAD"  # push: the local ref being pushed
    repo: str = ""  # gh pr create --repo
    base: str = ""  # gh pr create --base
    head: str = ""  # gh pr create --head
    body: str | None = None  # gh pr create: the PR description, when it can be read
    skip: str = ""  # why this call needs no check (dry run, delete, ...)
    note: str = ""  # something the hook could not read (e.g. a --body-file that does not exist)

    @property
    def label(self) -> str:
        return {"push": "git push", "pr-create": "gh pr create"}.get(self.kind, "command")


_HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([A-Za-z_][\w.-]*))")
_PH = "__contribpolicy_heredoc_{}__"
_PH_RE = re.compile(r"__contribpolicy_heredoc_(\d+)__")
_CAT_PH = re.compile(r"^\s*\$\(\s*cat\s*(?:<\s*)?__contribpolicy_heredoc_(\d+)__\s*\)\s*$")
_CAT_FILE = re.compile(r"^\s*\$\(\s*cat\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s'\")]+))\s*\)\s*$")


def extract_heredocs(cmd: str) -> tuple[str, list[str]]:
    """Replace each here-document (``<<EOF ... EOF``) with a placeholder word and return the bodies.

    The bodies are cut out before tokenising, because a PR description is free text: an apostrophe
    in it would otherwise be read as an unterminated quote.
    """
    docs: list[str] = []
    out: list[str] = []
    pos = 0
    while True:
        m = _HEREDOC.search(cmd, pos)
        if not m:
            break
        delim = m.group(2) or m.group(3) or m.group(4)
        nl = cmd.find("\n", m.end())
        if nl < 0:
            break
        lines = cmd[nl + 1 :].split("\n")
        body: list[str] = []
        end = None
        for i, line in enumerate(lines):
            cand = line.lstrip("\t") if m.group(1) else line
            if cand.rstrip() == delim:
                end = i
                break
            body.append(cand)
        if end is None:  # not a here-document after all (e.g. "a << b" inside a quoted string)
            out.append(cmd[pos : m.end()])
            pos = m.end()
            continue
        out += [cmd[pos : m.start()], _PH.format(len(docs)), cmd[m.end() : nl], "\n"]
        docs.append("\n".join(body))
        pos = min(len(cmd), nl + 1 + sum(len(x) + 1 for x in lines[: end + 1]))
    out.append(cmd[pos:])
    return "".join(out), docs


_SEP_CHARS = frozenset(";&|()\n")
_WRAPPERS = {"command", "builtin", "exec", "nohup", "time", "env", "sudo", "nice"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GIT_OPTS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
_PUSH_OPTS_WITH_ARG = {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}
_GH_OPTS_WITH_ARG = {
    "-t": "title",
    "--title": "title",
    "-b": "body",
    "--body": "body",
    "-F": "body-file",
    "--body-file": "body-file",
    "-B": "base",
    "--base": "base",
    "-H": "head",
    "--head": "head",
    "-R": "repo",
    "--repo": "repo",
    "-a": "",
    "--assignee": "",
    "-l": "",
    "--label": "",
    "-m": "",
    "--milestone": "",
    "-p": "",
    "--project": "",
    "-r": "",
    "--reviewer": "",
    "-T": "",
    "--template": "",
    "--recover": "",
}


def _segments(cmd: str) -> list[tuple[list[str], str]]:
    """Split a shell command into simple commands: (words, the operator before it)."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    segs: list[tuple[list[str], str]] = []
    cur: list[str] = []
    sep = ""
    for tok in lex:
        if tok and set(tok) <= _SEP_CHARS:
            if cur:
                segs.append((cur, sep))
                cur = []
            sep = tok.strip("\n") or "\n"
            continue
        cur.append(tok)
    if cur:
        segs.append((cur, sep))
    return segs


def _drop_redirects(words: list[str]) -> list[str]:
    out: list[str] = []
    skip = False
    for i, w in enumerate(words):
        if skip:
            skip = False
            continue
        if w and w[0] in "<>":
            skip = not w.endswith("-") and i + 1 < len(words)
            continue
        if w.isdigit() and i + 1 < len(words) and words[i + 1][:1] in "<>":
            continue
        out.append(w)
    return out


def _strip_prefix(words: list[str]) -> list[str]:
    i = 0
    while i < len(words):
        w = words[i]
        if _ASSIGNMENT.match(w):
            i += 1
        elif w in _WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith("-") or _ASSIGNMENT.match(words[i])):
                i += 1
        else:
            break
    return words[i:]


def _join(cwd: str, path: str) -> str:
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))


def _parse_git(words: list[str], cwd: str) -> Action | None:
    i = 1
    while i < len(words) and words[i].startswith("-"):
        w = words[i]
        if w in _GIT_OPTS_WITH_ARG:
            if w == "-C" and i + 1 < len(words):
                cwd = _join(cwd, words[i + 1])
            i += 2
            continue
        i += 1
    if i >= len(words) or words[i] != "push":
        return None
    act = Action("push", cwd)
    positional: list[str] = []
    args = words[i + 1 :]
    j = 0
    while j < len(args):
        a = args[j]
        if a == "--":
            positional += args[j + 1 :]
            break
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            if name == "--dry-run":
                act.skip = "dry run"
            elif name in ("--delete", "--prune"):
                act.skip = "deletes remote refs"
            if name in _PUSH_OPTS_WITH_ARG and "=" not in a:
                j += 1
        elif a.startswith("-") and len(a) > 1:
            flags = a[1:]
            if "n" in flags:
                act.skip = "dry run"
            if "d" in flags:
                act.skip = "deletes remote refs"
            if flags.endswith("o"):
                j += 1
        elif not _PH_RE.fullmatch(a):
            positional.append(a)
        j += 1
    refspecs = positional[1:]
    if refspecs:
        src = refspecs[0].lstrip("+").split(":", 1)[0]
        if not src:
            act.skip = "deletes a remote branch"
        act.src = src or "HEAD"
    return act


def _read_body_file(path: str, cwd: str, act: Action) -> str | None:
    full = _join(cwd, path)
    try:
        with open(full, "rb") as fh:
            return fh.read(_MAX_BODY_BYTES).decode("utf-8", errors="replace")
    except OSError as e:
        act.note = f"could not read the PR body file {path}: {e.strerror or e}"
        return None


def _resolve_body(value: str, docs: list[str], cwd: str, act: Action) -> str | None:
    m = _CAT_PH.match(value)
    if m:
        return docs[int(m.group(1))]
    m = _CAT_FILE.match(value)
    if m:
        return _read_body_file(next(g for g in m.groups() if g), cwd, act)
    return _PH_RE.sub(lambda mm: docs[int(mm.group(1))], value)


def _parse_gh(words: list[str], cwd: str, docs: list[str], stdin: str | None) -> Action | None:
    if len(words) < 3 or words[1] != "pr" or words[2] not in ("create", "new"):
        return None
    act = Action("pr-create", cwd)
    vals: dict[str, str] = {}
    own_stdin = None
    args = words[3:]
    j = 0
    while j < len(args):
        a = args[j]
        if a.startswith("--") and "=" in a:
            name, v = a.split("=", 1)
            if _GH_OPTS_WITH_ARG.get(name):
                vals[_GH_OPTS_WITH_ARG[name]] = v
        elif a in _GH_OPTS_WITH_ARG:
            if j + 1 < len(args):
                if _GH_OPTS_WITH_ARG[a]:
                    vals[_GH_OPTS_WITH_ARG[a]] = args[j + 1]
                j += 1
        elif a == "--dry-run":
            act.skip = "dry run"
        elif _PH_RE.fullmatch(a):
            own_stdin = docs[int(_PH_RE.fullmatch(a).group(1))]  # type: ignore[union-attr]
        j += 1
    act.repo = vals.get("repo", "")
    act.base = vals.get("base", "")
    act.head = vals.get("head", "").split(":", 1)[-1]
    if "body" in vals:
        act.body = _resolve_body(vals["body"], docs, cwd, act)
    elif "body-file" in vals:
        if vals["body-file"] == "-":
            act.body = own_stdin if own_stdin is not None else stdin
            if act.body is None:
                act.note = "the PR body comes from stdin and could not be read"
        else:
            act.body = _read_body_file(vals["body-file"], cwd, act)
    return act


def _stdin_of(prev: list[str], docs: list[str]) -> str | None:
    """What the previous command in a pipeline writes, when it is a here-document or an echo."""
    if not prev:
        return None
    for w in prev:
        m = _PH_RE.fullmatch(w)
        if m and prev[0] == "cat":
            return docs[int(m.group(1))]
    if prev[0] == "echo":
        return " ".join(w for w in prev[1:] if not w.startswith("-")) + "\n"
    if prev[0] == "printf" and len(prev) >= 3 and prev[1] == "%s":
        return prev[2]
    return None


def parse_command(command: str, cwd: str) -> list[Action]:
    """The ``git push`` / ``gh pr create`` calls in a Bash command, in order."""
    if not _MAYBE.search(command):
        return []
    text, docs = extract_heredocs(command)
    text = text.replace("\\\n", " ")
    try:
        segs = _segments(text)
    except ValueError:
        if re.search(r"\bgit\s+(?:-\S+\s+)*push\b|\bgh\s+pr\s+(?:create|new)\b", text):
            return [Action("unparsed", cwd, note="the command could not be parsed (unbalanced quotes?)")]
        return []
    actions: list[Action] = []
    prev: list[str] = []
    for raw, sep in segs:
        words = _strip_prefix(_drop_redirects(raw))
        if not words:
            prev = words
            continue
        prog = posixpath.basename(words[0])
        if prog == "cd":
            cwd = _join(cwd, words[1]) if len(words) > 1 and words[1] != "-" else os.path.expanduser("~")
        elif prog == "git":
            act = _parse_git(words, cwd)
            if act:
                actions.append(act)
        elif prog == "gh":
            act = _parse_gh(words, cwd, docs, _stdin_of(prev, docs) if sep in ("|", "|&") else None)
            if act:
                actions.append(act)
        prev = words
    return actions


# --------------------------------------------------------------------------
# Repository context
# --------------------------------------------------------------------------


class _Skip(Exception):
    """The hook does not apply here (not a git repository, disabled, ...). Exit quietly."""


class _FailOpen(Exception):
    """The check could not run. Let the command go ahead, and say so."""


_GH_REMOTE = re.compile(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
_SLUG = re.compile(r"^[\w.-]+/[\w.-]+$")


class _Git:
    def __init__(self, cwd: str, run: Runner, env: dict[str, str]):
        self.cwd = cwd
        self.run = run
        self.env = env

    def __call__(self, *args: str) -> str | None:
        try:
            out = self.run(
                ["git", *args], cwd=self.cwd, capture_output=True, text=True, timeout=15, check=False, env=self.env
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    def config(self, key: str, bool_: bool = False) -> str:
        args = ["config", "--type=bool", "--get", key] if bool_ else ["config", "--get", key]
        return self(*args) or ""

    def remotes(self) -> dict[str, tuple[str, str]]:
        """name -> (url, owner/name or "")"""
        out: dict[str, tuple[str, str]] = {}
        for line in (self("remote", "-v") or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] not in out:
                m = _GH_REMOTE.search(parts[1])
                out[parts[0]] = (parts[1], f"{m.group(1)}/{m.group(2)}" if m else "")
        return out


def _gh_parent(slug: str, run: Runner, env: dict[str, str]) -> str | None:
    """The parent of a fork via the GitHub CLI: "owner/name", "" (not a fork) or None (unknown)."""
    try:
        out = run(
            ["gh", "repo", "view", slug, "--json", "parent"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if out.returncode != 0:
        return None
    try:
        parent = json.loads(out.stdout or "{}").get("parent") or {}
    except ValueError:
        return None
    login = (parent.get("owner") or {}).get("login")
    name = parent.get("name")
    return f"{login}/{name}" if login and name else ""


@dataclass
class _Settings:
    repo: str = ""
    policy: str = ""
    offline: bool = False
    ai_assisted: str = "yes"


@dataclass
class HookResult:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class _Outcome:
    action: Action
    repo: str = ""
    errors: list[tuple[str, str]] = field(default_factory=list)  # (sentence, quoted rule)
    context: list[str] = field(default_factory=list)


def _cache_dir(env: dict[str, str]) -> Path:
    if env.get("CONTRIBPOLICY_CACHE_DIR"):
        return Path(env["CONTRIBPOLICY_CACHE_DIR"])
    base = env.get("XDG_CACHE_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".cache")
    return Path(base) / TOOL_NAME


def _policy_from_file(path: Path, repo: str):
    from .model import Policy

    if path.is_dir():
        from .discover import LocalReader, discover
        from .extract import extract

        found = discover(LocalReader(path, name=repo or path.name), repo, org_reader=None, registry=None)
        policy = extract(repo or str(path), found.docs, found.sources)
        policy.warnings = found.warnings + policy.warnings
        return policy
    data = json.loads(path.read_text(encoding="utf-8"))
    policy = Policy.from_dict(data)
    policy.checkbox_options = list(data.get("checkbox_options") or [])
    if repo and not policy.repo:
        policy.repo = repo
    return policy


def _cached_policy(
    slug: str, env: dict[str, str], github_factory: Callable[[], Any] | None, notes: list[str], now: float
):
    from .github import GitHubError

    path = _cache_dir(env) / "policies" / (slug.lower().replace("/", "__") + ".json")
    fresh = path.is_file() and now - path.stat().st_mtime < CACHE_TTL
    if fresh:
        try:
            return _policy_from_file(path, slug)
        except (OSError, ValueError, KeyError):
            pass
    try:
        from .cli import UsageError, fetch_policy
        from .registry import load_registry

        if github_factory is None:
            from .github import GitHub, resolve_token

            gh = GitHub(token=resolve_token(), timeout=15)
        else:
            gh = github_factory()
        policy = fetch_policy(gh, slug, load_registry(None))
    except (GitHubError, UsageError, OSError, ValueError) as e:
        if path.is_file():
            try:
                stale = _policy_from_file(path, slug)
            except (OSError, ValueError, KeyError):
                stale = None
            if stale is not None:
                when = time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))
                notes.append(f"could not refresh the policy ({e}); used the cached copy from {when}")
                return stale
        raise _FailOpen(f"could not fetch {slug}'s policy: {e}") from None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = policy.to_dict()
        data["checkbox_options"] = policy.checkbox_options
        tmp = path.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a read-only cache only costs speed
    return policy


def _target_repo(act: Action, cfg: _Settings, remotes: dict[str, tuple[str, str]], run, env) -> str:
    if cfg.repo:
        return cfg.repo
    if act.repo and _SLUG.match(act.repo):
        return act.repo
    if remotes.get("upstream", ("", ""))[1]:
        return remotes["upstream"][1]
    origin = remotes.get("origin", ("", ""))[1]
    if origin and not cfg.offline:
        parent = _gh_parent(origin, run, env)
        if parent:
            return parent
    if origin:
        return origin
    return next((s for _, s in remotes.values() if s), "")


def _base_ref(git: _Git, remotes: dict[str, tuple[str, str]], repo: str, branch: str) -> str:
    remote = next((n for n, (_, s) in remotes.items() if s and s.lower() == repo.lower()), None)
    if remote is None:
        remote = "upstream" if "upstream" in remotes else ("origin" if "origin" in remotes else None)
    if remote is None:
        raise _FailOpen("the repository has no remote to compare against")
    if not branch:
        head = git("symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD")
        if head and head.startswith(f"refs/remotes/{remote}/"):
            branch = head[len(f"refs/remotes/{remote}/") :]
    for b in [branch] if branch else ["main", "master"]:
        ref = f"refs/remotes/{remote}/{b}"
        if git("rev-parse", "--verify", "--quiet", ref + "^{commit}"):
            return ref
    raise _FailOpen(f"cannot find {remote}/{branch or 'main'} locally; run `git fetch {remote}`")


def _settings(git: _Git, overrides: _Settings) -> _Settings:
    ai = overrides.ai_assisted or git.config("contribpolicy.aiAssisted") or "yes"
    return _Settings(
        repo=overrides.repo or git.config("contribpolicy.repo"),
        policy=overrides.policy or git.config("contribpolicy.policy"),
        offline=overrides.offline or git.config("contribpolicy.offline", bool_=True) == "true",
        ai_assisted=ai if ai in ("auto", "yes", "no") else "yes",
    )


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------


def _rule_phrase(rule: str, policy) -> str:
    if rule == "trailers.assisted_by":
        if policy.assisted_by == "required":
            return "requires an `Assisted-by: <agent>/<model>` trailer on every commit"
        return "bans `Assisted-by`, `Co-developed-by` and similar AI commit trailers"
    if rule == "trailers.co_authored_by_ai":
        return "does not allow an AI tool as co-author"
    phrases = {
        "disclosure": "requires AI assistance to be disclosed",
        "disclosure.where.checkbox": "requires the AI-usage checkbox in the PR template",
        "dco": "requires a DCO sign-off by the human author on every commit",
        "signed_commits": "requires signed and verified commits",
        "human_only.commit-messages": "requires commit messages written by a human",
        "human_only.pr-description": "requires a PR description written by a human",
        "required_labels": "only accepts these PRs for issues with the required labels",
        "assignment_required": "requires the issue to be assigned to the contributor",
        "issue_first": "asks for an issue before a PR",
        "ui_screenshots_required": "requires screenshots for UI changes",
    }
    return phrases.get(rule, f"has a rule `{rule}`")


def _remedy(f, policy, base: str) -> str:
    rule = f.rule
    rb = f"`git rebase -i {base[:12]}` and `reword` it (or `git commit --amend` if it is the last commit)"
    disclose_instead = (
        " and disclose the AI assistance in the PR description instead"
        if policy.disclosure_required or "description" in policy.disclosure_where
        else ""
    )
    if rule == "trailers.assisted_by":
        if policy.assisted_by == "required":
            return f"add an `Assisted-by: <agent>/<model>` trailer to that commit: {rb}"
        return f"remove the trailer from that commit: {rb}{disclose_instead}"
    if rule == "trailers.co_authored_by_ai":
        if f.where == "PR description":
            return "remove the AI authorship footer from the PR description"
        return f"remove the `Co-authored-by` line that names an AI tool: {rb}{disclose_instead}"
    if rule == "dco":
        if "names an AI" in f.message:
            return "the sign-off must name the human contributor (user.name / user.email), never the agent"
        return f"sign off the commits as the human contributor: `git rebase --signoff {base[:12]}`"
    if rule == "signed_commits":
        return (
            "the commits must be signed with the human contributor's key; ask the user to sign them "
            f"(for example `git rebase --force-rebase --gpg-sign {base[:12]}` where their key is set up)"
        )
    if rule == "human_only.commit-messages":
        return "remove the AI 'Generated with' footer, and ask the user to write or approve the commit message"
    if rule == "human_only.pr-description":
        return "remove the AI footer, and ask the user to write the PR description"
    if rule.startswith("disclosure.where.checkbox"):
        return "copy the PR template's AI-usage section into the body and tick exactly one option"
    if rule == "disclosure":
        ex = policy.disclosure_example
        tail = f', for example "{ex}"' if ex and not ex.startswith(("[x]", "Assisted-by")) else ""
        return f"add a sentence to the PR description that says how AI tools were used{tail}"
    if rule in ("required_labels", "assignment_required", "issue_first"):
        return "link an issue that meets this rule in the PR description (`Fixes #123`), or ask the user"
    if rule == "ui_screenshots_required":
        return "ask the user for screenshots of the UI change and add them to the PR description"
    return "fix this before running the command again"


def _cite(f) -> tuple[str, str]:
    if not f.evidence:
        return "", ""
    e = f.evidence[0]
    quote = e.quote if len(e.quote) <= 200 else e.quote[:199].rstrip() + "…"
    return f" ({e.path}:{e.line})", quote


def _describe(f, policy, repo: str, base: str) -> tuple[str, str]:
    """One finding as (sentence with the fix, quoted rule text or "")."""
    where, quote = _cite(f)
    rule = _rule_phrase(f.rule, policy)
    return f"{repo} {rule}{where}: {f.message} [{f.where}]. Fix: {_remedy(f, policy, base)}.", quote


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------


def _check_action(
    act: Action,
    overrides: _Settings,
    env: dict[str, str],
    run: Runner,
    github_factory: Callable[[], Any] | None,
    now: float,
) -> _Outcome:
    out = _Outcome(act)
    if act.kind == "unparsed":
        raise _FailOpen(act.note)
    if act.skip:
        raise _Skip(act.skip)
    git = _Git(act.cwd, run, env)
    top = git("rev-parse", "--show-toplevel")
    if not top:
        raise _Skip("not inside a git repository")
    git.cwd = top
    if git.config("contribpolicy.hook", bool_=True) == "false":
        raise _Skip("disabled for this repository (git config contribpolicy.hook false)")
    cfg = _settings(git, overrides)
    remotes = git.remotes()
    repo = _target_repo(act, cfg, remotes, run, env)
    if not repo and not cfg.policy:
        raise _Skip("no GitHub remote and no contribpolicy.repo / contribpolicy.policy configured")
    out.repo = repo or "(local policy)"
    notes: list[str] = []
    if cfg.policy:
        path = Path(os.path.expanduser(cfg.policy))
        if not path.is_absolute():
            path = Path(top) / path
        try:
            policy = _policy_from_file(path, repo)
        except (OSError, ValueError, KeyError) as e:
            raise _FailOpen(f"cannot read contribpolicy.policy {cfg.policy}: {e}") from None
    elif cfg.offline:
        raise _FailOpen("offline mode is on and no contribpolicy.policy file is configured")
    else:
        policy = _cached_policy(repo, env, github_factory, notes, now)
    out.repo = repo or policy.repo or "(local policy)"

    base_ref = _base_ref(git, remotes, repo, act.base)
    src = act.head if act.kind == "pr-create" and act.head else act.src
    if src.startswith("-") or not git("rev-parse", "--verify", "--quiet", src + "^{commit}"):
        raise _FailOpen(f"cannot resolve {src!r} to a commit")
    base = git("merge-base", base_ref, src)
    if not base:
        raise _FailOpen(f"no common ancestor between {src} and {base_ref}")

    from .check import git_commits, run_check

    commits = git_commits(f"{base}..{src}", cwd=top, run=lambda *a, **k: run(*a, env=env, **k))
    if len(commits) > MAX_COMMITS:
        raise _FailOpen(
            f"{len(commits)} commits between {base_ref} and {src}; is the upstream ref stale? (`git fetch`)"
        )
    lookup = None
    if act.kind == "pr-create" and act.body is not None and not cfg.offline and "/" in (policy.repo or ""):

        def lookup(repo: str, number: int):  # only called when the policy needs labels or assignment
            if github_factory is None:
                from .github import GitHub, resolve_token

                return GitHub(token=resolve_token(), timeout=15).issue(repo, number)
            return github_factory().issue(repo, number)

    result = run_check(
        policy,
        act.body,
        commits,
        ai_assisted=cfg.ai_assisted,
        issue_lookup=lookup,
        signatures_known=True,
    )
    seen: set[tuple[str, str, str]] = set()
    for f in result.findings:
        if (f.rule, f.message, f.where) in seen:
            continue
        seen.add((f.rule, f.message, f.where))
        if f.severity == "error":
            out.errors.append(_describe(f, policy, out.repo, base))
        elif f.severity == "warning":
            out.context.append(f"warning: {_describe(f, policy, out.repo, base)[0]}")
        elif act.kind == "pr-create":
            where, _ = _cite(f)
            out.context.append(f"reminder: {f.message}{where}")
    if act.kind == "pr-create" and "pr-description" in policy.human_only:
        out.context.append(
            f"reminder: {out.repo} wants the PR description written by a human. If you drafted it, "
            "stop and ask the user to write it."
        )
    if act.note:
        out.context.append(f"note: {act.note}; the PR body was not checked")
    out.context += [f"note: {n}" for n in notes]
    return out


def run_hook(
    raw: str,
    *,
    env: dict[str, str] | None = None,
    run: Runner = subprocess.run,
    github_factory: Callable[[], Any] | None = None,
    overrides: _Settings | None = None,
    now: float | None = None,
) -> HookResult:
    """Process one PreToolUse event (the JSON text Claude Code writes to stdin)."""
    env = dict(os.environ) if env is None else env
    if (env.get("CONTRIBPOLICY_HOOK") or "").strip().lower() in ("0", "false", "off", "no"):
        return HookResult()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return HookResult(stderr=f"{TOOL_NAME} hook: ignored input that is not JSON\n")
    if not isinstance(event, dict):
        return HookResult()
    if event.get("hook_event_name", "PreToolUse") != "PreToolUse" or event.get("tool_name") != "Bash":
        return HookResult()
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not _MAYBE.search(command):
        return HookResult()
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else os.getcwd()
    overrides = overrides or _Settings(ai_assisted="")
    now = time.time() if now is None else now

    blocks: list[tuple[str, list[tuple[str, str]]]] = []  # (header, violations)
    context: list[str] = []
    stderr: list[str] = []
    try:
        actions = parse_command(command, cwd)
    except Exception as e:  # never block a command because the parser broke
        actions = [Action("unparsed", cwd, note=f"internal error while parsing the command: {e!r}")]
    for act in actions:
        try:
            o = _check_action(act, overrides, env, run, github_factory, now)
        except _Skip as e:
            stderr.append(f"{TOOL_NAME} hook: {act.label} not checked: {e}")
            continue
        except _FailOpen as e:
            stderr.append(f"{TOOL_NAME} hook: {act.label} not checked: {e}")
            context.append(
                f"{TOOL_NAME} could not check this {act.label} against the upstream contribution rules ({e}). "
                "It was not blocked. If the rules matter here, run `contribpolicy check` yourself."
            )
            continue
        except Exception as e:  # fail open on any bug
            stderr.append(f"{TOOL_NAME} hook: {act.label} not checked (internal error: {e!r})")
            context.append(
                f"{TOOL_NAME} could not check this {act.label} (internal error). It was not blocked. "
                "Run `contribpolicy check` yourself if the upstream rules matter."
            )
            continue
        if o.errors:
            blocks.append((f"{act.label} -> {o.repo}", o.errors))
        if o.context:
            context.append(f"{TOOL_NAME} ({act.label} -> {o.repo}):")
            context += [f"- {c}" for c in o.context]

    err_text = "".join(s + "\n" for s in stderr)
    hso: dict[str, Any] = {"hookEventName": "PreToolUse"}
    if blocks:
        total = sum(len(v) for _, v in blocks)
        reason = [f"{TOOL_NAME} blocked this command: {total} contribution-rule violation(s)."]
        n = 0
        for header, items in blocks:
            if len(blocks) > 1:
                reason.append(f"[{header}]")
            for sentence, quote in items:
                n += 1
                if n > _MAX_ITEMS:
                    continue
                reason.append(f"{n}. {sentence}")
                if quote:
                    reason.append(f'   Rule text: "{quote}"')
        if n > _MAX_ITEMS:
            reason.append(f"... and {n - _MAX_ITEMS} more (run `contribpolicy check` to see all).")
        reason.append(
            "Fix these, then run the command again. If a rule looks misread, show the quoted rule text "
            "to the user instead of working around this check."
        )
        if context:
            reason += ["", *context]
        hso["permissionDecision"] = "deny"
        hso["permissionDecisionReason"] = "\n".join(reason)
    elif context:
        hso["additionalContext"] = "\n".join(context)
    else:
        return HookResult(stderr=err_text)
    return HookResult(stdout=json.dumps({"hookSpecificOutput": hso}, ensure_ascii=False) + "\n", stderr=err_text)


# --------------------------------------------------------------------------
# CLI glue
# --------------------------------------------------------------------------


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--repo",
        metavar="OWNER/NAME",
        help="upstream repository (default: git config contribpolicy.repo, "
        "the upstream remote, the fork parent via `gh`, then origin)",
    )
    p.add_argument(
        "--policy",
        metavar="FILE",
        help="policy JSON from `show -f json` (or a local checkout) instead of "
        "fetching; default: git config contribpolicy.policy",
    )
    p.add_argument("--offline", action="store_true", help="never touch the network (needs --policy)")
    p.add_argument(
        "--ai-assisted",
        choices=("auto", "yes", "no"),
        default="",
        help="whether the commits are AI-assisted (default: git config contribpolicy.aiAssisted, else yes)",
    )


def run_from_args(args: argparse.Namespace, stdin_text: str | None = None) -> int:
    """Run the hook for parsed CLI arguments; always returns 0 (the hook fails open)."""
    if stdin_text is None:
        try:
            stdin_text = sys.stdin.read()
        except (OSError, ValueError, UnicodeDecodeError) as e:
            sys.stderr.write(f"{TOOL_NAME} hook: could not read stdin: {e}\n")
            return 0
    raw = stdin_text
    overrides = _Settings(
        repo=args.repo or "", policy=args.policy or "", offline=args.offline, ai_assisted=args.ai_assisted or ""
    )
    try:
        res = run_hook(raw, overrides=overrides)
    except Exception as e:  # pragma: no cover - run_hook already fails open
        sys.stderr.write(f"{TOOL_NAME} hook: internal error: {e!r}\n")
        return 0
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    return res.exit_code


def main(argv: Sequence[str] | None = None, stdin_text: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog=f"{TOOL_NAME} hook claude-code", description=DESCRIPTION)
    add_arguments(ap)
    return run_from_args(ap.parse_args(list(argv or [])), stdin_text=stdin_text)
