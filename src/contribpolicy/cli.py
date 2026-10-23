"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import hook as hook_mod
from . import render
from ._meta import TOOL_NAME, VERSION
from .check import git_commits, parse_commits, run_check
from .discover import GitHubReader, LocalReader, discover, git_remote_slug, parse_repo_slug
from .extract import extract
from .github import GitHub, GitHubError, resolve_token
from .model import Policy
from .registry import RegistryError, load_registry

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2


class UsageError(Exception):
    pass


def _add_discovery_args(p: argparse.ArgumentParser, target_required: bool = True) -> None:
    p.add_argument(
        "target",
        nargs=None if target_required else "?",
        help="owner/name (or a github.com URL) to read via the GitHub API, or a path to a local checkout",
    )
    p.add_argument(
        "--repo",
        metavar="OWNER/NAME",
        help="for a local path: the GitHub repository it belongs to "
        "(default: taken from the 'origin' remote); enables org-level policies",
    )
    p.add_argument("--offline", action="store_true", help="never touch the network (local paths only; no org policies)")
    p.add_argument("--no-org", action="store_true", help="skip org-level sources (<owner>/.github and the registry)")
    p.add_argument("--no-links", action="store_true", help="do not follow links to other in-repo documents")
    p.add_argument(
        "--registry", metavar="FILE", help="extra org registry JSON; its entries override bundled ones by id"
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Find a repository's rules for AI-assisted contributions and lint a draft PR against them. "
        "Deterministic (regex/heuristics), no API key needed.",
    )
    ap.add_argument("--version", action="version", version=f"{TOOL_NAME} {VERSION}")
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")

    show = sub.add_parser("show", help="print the extracted policy with evidence")
    _add_discovery_args(show)
    show.add_argument("--format", "-f", choices=render.FORMATS, default="table")
    show.add_argument("--output", "-o", metavar="FILE", help="write to FILE instead of stdout")

    check = sub.add_parser("check", help="lint a PR description and commit messages; exit 1 on violations")
    _add_discovery_args(check, target_required=False)
    check.add_argument(
        "--policy", metavar="FILE", help="use a policy JSON from 'show --format json' instead of fetching"
    )
    check.add_argument("--pr-body", metavar="FILE", help="PR description (Markdown); '-' reads stdin")
    src = check.add_mutually_exclusive_group()
    src.add_argument("--commits", metavar="FILE", help="commit messages (git log output, NUL- or '---'-separated)")
    src.add_argument("--git-range", metavar="A..B", help="read commits from git (also checks signatures)")
    check.add_argument(
        "--git-dir", metavar="PATH", default=None, help="repository for --git-range (default: target path or cwd)"
    )
    check.add_argument(
        "--ai-assisted",
        choices=("auto", "yes", "no"),
        default="auto",
        help="whether AI tools helped with this PR (auto: infer from trailers/description)",
    )
    check.add_argument("--author", metavar="LOGIN", help="your GitHub login, to verify issue assignment")
    check.add_argument(
        "--ui-change", action="store_true", help="the PR changes UI (screenshots become mandatory where required)"
    )
    check.add_argument("--no-issue-lookup", action="store_true", help="do not fetch the linked issue from GitHub")
    check.add_argument("--format", "-f", choices=("text", "json"), default="text")

    am = sub.add_parser("agents-md", help="print a short rules block to paste into an agent's context")
    _add_discovery_args(am)

    hook = sub.add_parser("hook", help="run as a coding-agent hook (reads the tool call as JSON on stdin)")
    agents = hook.add_subparsers(dest="agent", metavar="AGENT")
    cc = agents.add_parser(
        "claude-code",
        help="Claude Code PreToolUse hook: check `git push` / `gh pr create` against the upstream's rules",
        description=hook_mod.DESCRIPTION,
    )
    hook_mod.add_arguments(cc)

    reg = sub.add_parser("registry", help="list the org-level policy registry")
    reg.add_argument("--registry", metavar="FILE", help="extra registry JSON to merge")
    reg.add_argument("--format", "-f", choices=("table", "json"), default="table")
    return ap


def _github(offline: bool) -> GitHub | None:
    if offline:
        return None
    return GitHub(token=resolve_token())


def load_policy(args: argparse.Namespace) -> Policy:
    target: str = args.target
    try:
        registry = None if args.no_org else load_registry(args.registry)
    except RegistryError as e:
        raise UsageError(str(e)) from None
    local = Path(os.path.expanduser(target))
    if local.exists():
        if not local.is_dir():
            raise UsageError(f"{target} is not a directory")
        repo = args.repo or ("" if args.offline else git_remote_slug(local)) or ""
        if args.repo and not parse_repo_slug(args.repo):
            raise UsageError(f"--repo must look like owner/name, got {args.repo!r}")
        gh = _github(args.offline)
        reader = LocalReader(local, name=repo or local.resolve().name)
        org_reader = (lambda r: GitHubReader(gh, r)) if gh and not args.no_org else None
        found = discover(reader, repo, org_reader=org_reader, registry=registry, follow_links=not args.no_links)
        policy = extract(repo or str(local), found.docs, found.sources)
        policy.warnings = found.warnings + policy.warnings
        return policy
    slug = parse_repo_slug(target)
    if not slug:
        raise UsageError(f"{target!r} is neither a local directory nor owner/name")
    if args.offline:
        raise UsageError("--offline needs a local path")
    gh = _github(False)
    assert gh is not None
    return fetch_policy(gh, slug, registry, no_org=args.no_org, follow_links=not args.no_links)


def fetch_policy(
    gh: GitHub,
    slug: str,
    registry: list | None,
    *,
    no_org: bool = False,
    follow_links: bool = True,
) -> Policy:
    """Discover and extract the policy of the GitHub repository ``slug`` (``owner/name``)."""
    info = gh.repo_info(slug)
    if info is None:
        raise UsageError(f"repository {slug} not found (or not visible without a token)")
    slug = info.get("full_name", slug)
    reader = GitHubReader(gh, slug)
    org_reader = None if no_org else (lambda r: GitHubReader(gh, r))
    found = discover(reader, slug, org_reader=org_reader, registry=registry, follow_links=follow_links)
    policy = extract(slug, found.docs, found.sources)
    policy.warnings = found.warnings + policy.warnings
    return policy


def _write(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def cmd_show(args: argparse.Namespace) -> int:
    policy = load_policy(args)
    _write(render.render(policy, args.format), args.output)
    if args.format != "table":
        for w in policy.warnings:
            print(f"{TOOL_NAME}: warning: {w}", file=sys.stderr)
    return EXIT_OK


def cmd_agents_md(args: argparse.Namespace) -> int:
    policy = load_policy(args)
    sys.stdout.write(render.render_agents_md(policy))
    for w in policy.warnings:
        print(f"{TOOL_NAME}: warning: {w}", file=sys.stderr)
    return EXIT_OK


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def cmd_check(args: argparse.Namespace) -> int:
    if not args.policy and not args.target:
        raise UsageError("check needs a target (owner/name or path) or --policy FILE")
    if not args.pr_body and not args.commits and not args.git_range:
        raise UsageError("nothing to check: pass --pr-body and/or --commits/--git-range")
    if args.policy:
        try:
            policy = Policy.from_dict(json.loads(Path(args.policy).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError) as e:
            raise UsageError(f"cannot read policy {args.policy}: {e}") from None
    else:
        policy = load_policy(args)
    body = _read_text(args.pr_body) if args.pr_body else None
    signatures_known = False
    if args.git_range:
        git_dir = args.git_dir
        if git_dir is None and args.target and Path(args.target).is_dir():
            git_dir = args.target
        try:
            commits = git_commits(args.git_range, cwd=git_dir)
        except (RuntimeError, ValueError, OSError) as e:
            raise UsageError(str(e)) from None
        signatures_known = True
    elif args.commits:
        commits = parse_commits(_read_text(args.commits))
    else:
        commits = []
    lookup = None
    if not args.no_issue_lookup and not args.offline and policy.repo.count("/") == 1:
        gh = GitHub(token=resolve_token())
        lookup = gh.issue
    result = run_check(
        policy,
        body,
        commits,
        ai_assisted=args.ai_assisted,
        issue_lookup=lookup,
        author=args.author,
        ui_change=args.ui_change,
        signatures_known=signatures_known,
    )
    if args.format == "json":
        sys.stdout.write(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(_format_check(policy, result))
    return EXIT_VIOLATIONS if result.violations else EXIT_OK


def _format_check(policy: Policy, result) -> str:
    out = [f"{TOOL_NAME} check · {policy.repo or '(local)'}", ""]
    tags = {"error": "ERROR", "warning": "WARN ", "reminder": "NOTE "}
    order = {"error": 0, "warning": 1, "reminder": 2}
    for f in sorted(result.findings, key=lambda f: order[f.severity]):
        out.append(f"{tags[f.severity]} {f.rule}: {f.message} [{f.where}]")
        for e in f.evidence[:1]:
            out.append(f'        rule source {e.path}:{e.line}: "{e.quote}"')
    n_err = len(result.violations)
    n_warn = sum(1 for f in result.findings if f.severity == "warning")
    if out[-1] != "":
        out.append("")
    out.append(f"{n_err} violation(s), {n_warn} warning(s)" + ("" if n_err else " - OK"))
    return "\n".join(out) + "\n"


def cmd_registry(args: argparse.Namespace) -> int:
    try:
        entries = load_registry(args.registry)
    except RegistryError as e:
        raise UsageError(str(e)) from None
    if args.format == "json":
        data = [
            {
                "id": e.id,
                "title": e.title,
                "orgs": list(e.orgs),
                "url": e.url,
                "repo": e.repo,
                "path": e.path,
                "section": e.section,
                "scope": e.scope,
                "verified": e.verified,
            }
            for e in entries
        ]
        sys.stdout.write(json.dumps(data, indent=2) + "\n")
        return EXIT_OK
    for e in entries:
        sec = f" § {e.section}" if e.section else ""
        print(f"{e.id}\n  orgs:   {', '.join(e.orgs)}\n  source: {e.repo}/{e.path}{sec}")
        print(f"  url:    {e.url}\n  scope:  {e.scope}\n")
    return EXIT_OK


def cmd_hook(args: argparse.Namespace) -> int:
    if args.agent != "claude-code":
        raise UsageError("hook needs an agent: contribpolicy hook claude-code")
    return hook_mod.run_from_args(args)


def main(argv: Sequence[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.command:
        ap.print_help()
        return EXIT_ERROR
    handlers = {
        "show": cmd_show,
        "check": cmd_check,
        "agents-md": cmd_agents_md,
        "registry": cmd_registry,
        "hook": cmd_hook,
    }
    try:
        return handlers[args.command](args)
    except UsageError as e:
        print(f"{TOOL_NAME}: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except GitHubError as e:
        print(f"{TOOL_NAME}: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
