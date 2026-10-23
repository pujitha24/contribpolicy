"""The Claude Code PreToolUse hook: command matching, output shape, fail-open behaviour.

Everything here is offline: policies come from fixtures through `git config contribpolicy.policy`,
and the GitHub client is replaced by fakes. The git tests use real git in a temporary directory,
with the user's global and system git configuration switched off.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

from conftest import FIXTURES
from contribpolicy import _entry, cli, hook
from contribpolicy.github import GitHubError
from contribpolicy.hook import extract_heredocs, parse_command, run_hook

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def event(command: str, cwd: str = "/work", **extra) -> str:
    data = {
        "session_id": "abc123",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": cwd,
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "x"},
        "tool_use_id": "toolu_01",
    }
    data.update(extra)
    return json.dumps(data)


def no_run(*a, **k):
    raise AssertionError(f"unexpected subprocess call: {a}")


def no_github():
    raise AssertionError("unexpected GitHub access")


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """Isolate git from the user's configuration (signing, hooks, default branch)."""
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\tname = Test User\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("CONTRIBPOLICY_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("CONTRIBPOLICY_HOOK", raising=False)
    return dict(os.environ)


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(cwd, name: str, message: str) -> str:
    (cwd / name).write_text(name)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def fork(tmp_path, git_env):
    """A fork checkout: `upstream` is a local bare repository carrying the k8s-style CONTRIBUTING.md
    (bans Assisted-by and AI co-authors, requires disclosure in the PR description); `origin` is a
    GitHub URL that is never contacted. The policy is read offline from the checkout itself."""
    up = tmp_path / "upstream.git"
    git(tmp_path, "init", "-q", "--bare", str(up))
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q")
    (seed / "CONTRIBUTING.md").write_text((FIXTURES / "k8s_style" / "CONTRIBUTING.md").read_text())
    git(seed, "add", "CONTRIBUTING.md")
    git(seed, "commit", "-q", "-m", "Add CONTRIBUTING")
    git(seed, "push", "-q", str(up), "main")
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", "-o", "upstream", str(up), str(work))
    git(work, "remote", "add", "origin", "https://github.com/me/widgetron.git")
    git(work, "config", "contribpolicy.repo", "acme/widgetron")
    git(work, "config", "contribpolicy.policy", ".")
    git(work, "checkout", "-q", "-b", "fix")
    return work


def decision(res) -> dict:
    assert res.exit_code == 0
    if not res.stdout:
        return {}
    data = json.loads(res.stdout)
    assert set(data) == {"hookSpecificOutput"}
    hso = data["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso.get("permissionDecision") in (None, "deny")  # never "allow": that would skip permission prompts
    return hso


# --------------------------------------------------------------------------
# Command matching
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "src", "cwd"),
    [
        ("git push", "HEAD", "/work"),
        ("git push -u origin feature", "feature", "/work"),
        ("git push origin HEAD:refs/heads/fix", "HEAD", "/work"),
        ("git push --force-with-lease origin +fix", "fix", "/work"),
        ("git push --set-upstream origin fix:fix", "fix", "/work"),
        ("git push -o ci.skip origin fix", "fix", "/work"),
        ("git -C ../other push origin main", "main", "/other"),
        ("git -c core.sshCommand='ssh -v' push origin fix", "fix", "/work"),
        ("cd sub && git push", "HEAD", "/work/sub"),
        ("FOO=1 command git push origin x 2>&1 | tail -5", "x", "/work"),
        ("/usr/bin/git push origin y", "y", "/work"),
        ("git add -A\ngit commit -m 'push fix'\ngit push origin feat", "feat", "/work"),
        ("git push \\\n  origin \\\n  feat", "feat", "/work"),
        ("make test && git push origin feat; echo done", "feat", "/work"),
    ],
)
def test_git_push_variants_match(command, src, cwd):
    acts = parse_command(command, "/work")
    assert [(a.kind, a.src, a.cwd, a.skip) for a in acts] == [("push", src, cwd, "")]


@pytest.mark.parametrize(
    ("command", "skip"),
    [
        ("git push --dry-run origin x", "dry run"),
        ("git push -n origin x", "dry run"),
        ("git push origin --delete old", "deletes remote refs"),
        ("git push origin :old", "deletes a remote branch"),
    ],
)
def test_git_push_that_publishes_nothing_is_skipped(command, skip):
    (act,) = parse_command(command, "/work")
    assert act.skip == skip


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git stash push -m wip",
        "git log --grep push",
        "git commit -m 'push the fix'",
        "git remote set-url --push origin x",
        "echo git push",
        "grep -rn 'git push' docs/",
        "npm run push",
        "gh pr view 12",
        "gh pr list --state open",
        "gh pr checkout 12 && git log",
        "echo 'gh pr create --body x'",
        "gh issue create --title 'gh pr create'",
        "cat push.md",
        "ls",
        "",
    ],
)
def test_unrelated_commands_do_not_match(command):
    assert parse_command(command, "/work") == []


def test_gh_pr_create_body_flag_forms(tmp_path):
    assert parse_command("gh pr create --title t --body 'Fixes #1'", "/w")[0].body == "Fixes #1"
    assert parse_command('gh pr create -t t -b "Short body"', "/w")[0].body == "Short body"
    assert parse_command("gh pr create --body='eq form'", "/w")[0].body == "eq form"
    assert parse_command("gh pr new --body x", "/w")[0].kind == "pr-create"
    (tmp_path / "pr.md").write_text("From a file\n")
    assert parse_command("gh pr create --body-file pr.md", str(tmp_path))[0].body == "From a file\n"
    assert parse_command("gh pr create -F pr.md", str(tmp_path))[0].body == "From a file\n"
    assert parse_command('gh pr create --body "$(cat pr.md)"', str(tmp_path))[0].body == "From a file\n"
    missing = parse_command("gh pr create --body-file nope.md", str(tmp_path))[0]
    assert missing.body is None and "nope.md" in missing.note
    assert parse_command("gh pr create --fill", "/w")[0].body is None


def test_gh_pr_create_heredoc_body_as_claude_code_writes_it():
    command = textwrap.dedent(
        """\
        gh pr create --title "Fix the flaky test" --body "$(cat <<'EOF'
        ## Summary
        Don't retry forever; the test's "timeout" was wrong.

        Parts of this PR were drafted with help from an AI assistant.
        EOF
        )"
        """
    )
    (act,) = parse_command(command, "/w")
    assert act.kind == "pr-create"
    assert act.body.startswith("## Summary\nDon't retry forever;")
    assert act.body.endswith("help from an AI assistant.")


def test_gh_pr_create_body_file_from_stdin_heredoc_and_pipe():
    (a,) = parse_command("gh pr create -t x --body-file - <<'EOF'\nbody via stdin\nEOF", "/w")
    assert a.body == "body via stdin"
    (b,) = parse_command("cat <<EOF | gh pr create -F -\npiped body\nEOF", "/w")
    assert b.body == "piped body"
    (c,) = parse_command("gh pr create -F - <<-EOF\n\tindented\n\tEOF\n", "/w")
    assert c.body == "indented"
    (d,) = parse_command("some-tool | gh pr create -F -", "/w")
    assert d.body is None and "stdin" in d.note


def test_gh_pr_create_repo_base_head():
    (a,) = parse_command("gh pr create -R thanos-io/thanos --base release-0.1 --head me:fix-1 -b x", "/w")
    assert (a.repo, a.base, a.head) == ("thanos-io/thanos", "release-0.1", "fix-1")
    (b,) = parse_command("gh pr create --dry-run -b x", "/w")
    assert b.skip == "dry run"


def test_push_and_pr_in_one_command():
    acts = parse_command("git push -u origin fix && gh pr create --fill", "/w")
    assert [a.kind for a in acts] == ["push", "pr-create"]


def test_unparseable_push_is_reported_not_ignored():
    (a,) = parse_command('git push origin "unterminated', "/w")
    assert a.kind == "unparsed"


def test_extract_heredocs_leaves_non_heredoc_shift_alone():
    text, docs = extract_heredocs('echo "1 << 2" && git push')
    assert docs == [] and text == 'echo "1 << 2" && git push'


# --------------------------------------------------------------------------
# Hook protocol: fast exits
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        event("npm test"),
        event("git status"),
        event("git push", tool_name="Edit"),
        event("git push", hook_event_name="PostToolUse"),
        json.dumps({"tool_name": "Bash", "tool_input": {}}),
        "[]",
        "",
    ],
)
def test_irrelevant_events_exit_0_without_output_or_subprocesses(raw):
    res = run_hook(raw, env={}, run=no_run, github_factory=no_github)
    assert (res.exit_code, res.stdout) == (0, "")


def test_invalid_json_fails_open():
    res = run_hook("{not json", env={}, run=no_run, github_factory=no_github)
    assert res.exit_code == 0 and res.stdout == "" and "not JSON" in res.stderr


def test_entry_point_prefilter_skips_unrelated_commands(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(event("pytest -q")))
    monkeypatch.setattr(hook, "run_hook", lambda *a, **k: pytest.fail("hook should not run"))
    assert _entry.main(["hook", "claude-code"]) == 0
    assert capsys.readouterr().out == ""


def test_entry_prefilter_matches_hook_prefilter():
    assert hook._MAYBE is _entry.MAYBE_PUSH_OR_PR
    assert _entry._irrelevant(event("ls")) is True
    assert _entry._irrelevant(event("git push")) is False
    assert _entry._irrelevant("{broken") is False  # the hook reports it


def test_disabled_by_environment(git_env):
    env = dict(git_env, CONTRIBPOLICY_HOOK="0")
    assert run_hook(event("git push"), env=env, run=no_run).stdout == ""


# --------------------------------------------------------------------------
# Blocking and context, against a real git repository
# --------------------------------------------------------------------------


def test_push_with_banned_trailer_is_denied(fork, git_env):
    sha = commit(fork, "a.txt", "Fix flaky admission test\n\nAssisted-by: Claude Code")
    res = run_hook(event("git push -u origin fix", cwd=str(fork)), env=git_env, github_factory=no_github)
    hso = decision(res)
    assert hso["permissionDecision"] == "deny"
    reason = hso["permissionDecisionReason"]
    assert "1 contribution-rule violation" in reason
    assert "acme/widgetron bans `Assisted-by`" in reason
    assert "(CONTRIBUTING.md:18)" in reason
    assert f"commit {sha[:10]}" in reason
    assert "'Assisted-by: Claude Code'" in reason
    assert "disclose the AI assistance in the PR description instead" in reason
    assert 'Rule text: "Naming an AI tool as a co-author' in reason
    assert "additionalContext" not in hso


def test_clean_push_passes_silently(fork, git_env):
    commit(fork, "a.txt", "Fix flaky admission test")
    res = run_hook(event("git push origin fix", cwd=str(fork)), env=git_env, github_factory=no_github)
    assert decision(res) == {}


def test_push_only_checks_commits_after_the_upstream_base(fork, git_env):
    # A commit already on upstream/main with a banned trailer is not part of this push.
    up_commit = commit(fork, "old.txt", "Old\n\nAssisted-by: someone-else")
    git(fork, "push", "-q", "upstream", f"{up_commit}:refs/heads/main")
    git(fork, "fetch", "-q", "upstream")
    commit(fork, "a.txt", "Clean change")
    res = run_hook(event("git push origin fix", cwd=str(fork)), env=git_env, github_factory=no_github)
    assert decision(res) == {}


def test_pr_create_without_disclosure_is_denied_with_reminders(fork, git_env):
    commit(fork, "a.txt", "Fix flaky admission test")
    command = 'gh pr create --title "Fix" --body "$(cat <<\'EOF\'\nFixes the flaky test.\nEOF\n)"'
    res = run_hook(event(command, cwd=str(fork)), env=git_env, github_factory=no_github)
    hso = decision(res)
    assert hso["permissionDecision"] == "deny"
    reason = hso["permissionDecisionReason"]
    assert "requires AI assistance to be disclosed" in reason
    assert 'for example "Parts of this PR were drafted with help from an AI assistant."' in reason
    # Reminders ride along in the reason when the call is denied.
    assert "reminder: The human author must have signed the CLA" in reason


def test_pr_create_with_disclosure_passes_with_reminders_as_context(fork, git_env):
    commit(fork, "a.txt", "Fix flaky admission test")
    command = "gh pr create --title Fix --body 'Fixes the test. Parts of this PR were drafted with help from an AI assistant.'"
    res = run_hook(event(command, cwd=str(fork)), env=git_env, github_factory=no_github)
    hso = decision(res)
    assert "permissionDecision" not in hso
    assert "reminder: The human author must have signed the CLA" in hso["additionalContext"]
    assert "reminder: Write replies to review comments yourself" in hso["additionalContext"]


def test_warnings_are_context_not_blocks(fork, git_env):
    git(fork, "config", "contribpolicy.aiAssisted", "auto")
    commit(fork, "a.txt", "Fix flaky admission test")
    res = run_hook(
        event("gh pr create -t Fix -b 'Fixes the test.'", cwd=str(fork)), env=git_env, github_factory=no_github
    )
    hso = decision(res)
    assert "permissionDecision" not in hso
    assert "warning: acme/widgetron requires AI assistance to be disclosed" in hso["additionalContext"]


def test_disabled_for_one_repository(fork, git_env):
    git(fork, "config", "contribpolicy.hook", "false")
    commit(fork, "a.txt", "Fix\n\nAssisted-by: Claude Code")
    res = run_hook(event("git push", cwd=str(fork)), env=git_env, github_factory=no_github)
    assert res.stdout == "" and "disabled for this repository" in res.stderr


def test_many_violations_are_capped(fork, git_env):
    for i in range(12):
        commit(fork, f"f{i}.txt", f"Change {i}\n\nAssisted-by: Claude Code")
    reason = decision(run_hook(event("git push", cwd=str(fork)), env=git_env))["permissionDecisionReason"]
    assert "12 contribution-rule violation(s)" in reason
    assert "10. " in reason and "11. " not in reason and "... and 2 more" in reason


def test_policy_json_file_and_cli_override(fork, git_env, tmp_path):
    policy = json.loads(cli.render.render(cli.load_policy(_ns(str(FIXTURES / "k8s_style"))), "json"))
    policy["trailers"]["assisted_by"] = "unspecified"
    (tmp_path / "p.json").write_text(json.dumps(policy))
    commit(fork, "a.txt", "Fix\n\nAssisted-by: Claude Code")
    over = hook._Settings(policy=str(tmp_path / "p.json"), ai_assisted="")
    res = run_hook(event("git push", cwd=str(fork)), env=git_env, overrides=over, github_factory=no_github)
    assert decision(res) == {}


def _ns(target):
    return cli.build_parser().parse_args(["show", target, "--offline"])


# --------------------------------------------------------------------------
# Fail open
# --------------------------------------------------------------------------


def test_not_a_git_repository_is_skipped_quietly(tmp_path, git_env):
    res = run_hook(event("git push", cwd=str(tmp_path)), env=git_env, github_factory=no_github)
    assert res.stdout == "" and "not inside a git repository" in res.stderr


def test_network_failure_fails_open_and_says_so(fork, git_env):
    git(fork, "config", "--unset", "contribpolicy.policy")
    commit(fork, "a.txt", "Fix\n\nAssisted-by: Claude Code")

    class Down:
        def repo_info(self, repo):
            raise GitHubError("network error fetching https://api.github.com/...: offline")

    res = run_hook(event("git push", cwd=str(fork)), env=git_env, github_factory=Down)
    hso = decision(res)
    assert "permissionDecision" not in hso
    assert "could not check this git push" in hso["additionalContext"]
    assert "It was not blocked" in hso["additionalContext"]
    assert "network error" in res.stderr


def test_policy_is_cached_and_a_stale_copy_is_used_when_the_network_fails(fork, git_env, monkeypatch):
    git(fork, "config", "--unset", "contribpolicy.policy")
    commit(fork, "a.txt", "Fix\n\nAssisted-by: Claude Code")
    calls = []

    def fake_fetch(gh, slug, registry, **kw):
        calls.append(slug)
        return hook._policy_from_file(FIXTURES / "k8s_style", slug)

    monkeypatch.setattr(cli, "fetch_policy", fake_fetch)
    monkeypatch.setattr(cli, "resolve_token", lambda: None)
    for _ in range(2):
        hso = decision(run_hook(event("git push", cwd=str(fork)), env=git_env, github_factory=object))
        assert hso["permissionDecision"] == "deny"
    assert calls == ["acme/widgetron"]  # the second run used the cache
    assert (Path(git_env["CONTRIBPOLICY_CACHE_DIR"]) / "policies" / "acme__widgetron.json").is_file()

    def down(*a, **k):
        raise GitHubError("network error")

    monkeypatch.setattr(cli, "fetch_policy", down)
    later = time.time() + 2 * 86400  # the cached copy is now older than the TTL
    hso = decision(run_hook(event("git push", cwd=str(fork)), env=git_env, github_factory=object, now=later))
    assert hso["permissionDecision"] == "deny"
    assert "used the cached copy" in hso["permissionDecisionReason"]


def test_offline_without_policy_fails_open(fork, git_env):
    git(fork, "config", "--unset", "contribpolicy.policy")
    git(fork, "config", "contribpolicy.offline", "true")
    commit(fork, "a.txt", "Fix\n\nAssisted-by: Claude Code")
    hso = decision(run_hook(event("git push", cwd=str(fork)), env=git_env, github_factory=no_github))
    assert "permissionDecision" not in hso and "offline mode" in hso["additionalContext"]


def test_missing_upstream_ref_fails_open(fork, git_env):
    git(fork, "remote", "remove", "upstream")
    commit(fork, "a.txt", "Fix\n\nAssisted-by: Claude Code")
    hso = decision(run_hook(event("git push", cwd=str(fork)), env=git_env, github_factory=no_github))
    assert "permissionDecision" not in hso
    assert "git fetch origin" in hso["additionalContext"]


def test_too_many_commits_fails_open(fork, git_env, monkeypatch):
    monkeypatch.setattr(hook, "MAX_COMMITS", 1)
    commit(fork, "a.txt", "One")
    commit(fork, "b.txt", "Two\n\nAssisted-by: Claude Code")
    hso = decision(run_hook(event("git push", cwd=str(fork)), env=git_env, github_factory=no_github))
    assert "permissionDecision" not in hso and "stale" in hso["additionalContext"]


def test_internal_error_fails_open(fork, git_env, monkeypatch):
    commit(fork, "a.txt", "Fix\n\nAssisted-by: Claude Code")

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr("contribpolicy.check.run_check", boom)
    res = run_hook(event("git push", cwd=str(fork)), env=git_env, github_factory=no_github)
    hso = decision(res)
    assert "permissionDecision" not in hso and "internal error" in hso["additionalContext"]
    assert "RuntimeError('bug')" in res.stderr


def test_unparseable_command_fails_open(git_env):
    hso = decision(run_hook(event('git push origin "oops'), env=git_env, run=no_run))
    assert "permissionDecision" not in hso and "could not be parsed" in hso["additionalContext"]


# --------------------------------------------------------------------------
# Target repository resolution
# --------------------------------------------------------------------------


def _gh_runner(parent_json: str | None):
    def run(cmd, **kw):
        if cmd[0] == "gh":
            if parent_json is None:
                raise FileNotFoundError("gh")
            return subprocess.CompletedProcess(cmd, 0, parent_json, "")
        raise AssertionError(cmd)

    return run


@pytest.mark.parametrize(
    ("remotes", "act_repo", "gh_json", "expected"),
    [
        ({"origin": ("u", "me/x"), "upstream": ("u", "org/x")}, "", "{}", "org/x"),
        ({"origin": ("u", "me/x")}, "org/y", None, "org/y"),
        ({"origin": ("u", "me/x")}, "", '{"parent": {"name": "x", "owner": {"login": "org"}}}', "org/x"),
        ({"origin": ("u", "me/x")}, "", '{"parent": null}', "me/x"),
        ({"origin": ("u", "me/x")}, "", None, "me/x"),
        ({"origin": ("/local/path", "")}, "", None, ""),
    ],
)
def test_target_repo_resolution(remotes, act_repo, gh_json, expected):
    act = hook.Action("push", "/w", repo=act_repo)
    cfg = hook._Settings()
    assert hook._target_repo(act, cfg, remotes, _gh_runner(gh_json), {}) == expected


def test_git_config_repo_wins():
    act = hook.Action("pr-create", "/w", repo="org/y")
    assert hook._target_repo(act, hook._Settings(repo="cfg/z"), {}, no_run, {}) == "cfg/z"


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------


def test_cli_hook_subcommand(fork, git_env, monkeypatch, capsys):
    commit(fork, "a.txt", "Fix\n\nAssisted-by: Claude Code")
    monkeypatch.setattr("sys.stdin", io.StringIO(event("git push", cwd=str(fork))))
    assert cli.main(["hook", "claude-code"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_cli_hook_needs_an_agent(capsys):
    assert cli.main(["hook"]) == 2
    assert "hook claude-code" in capsys.readouterr().err
