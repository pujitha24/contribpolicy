import json
import subprocess
import sys

import pytest

from conftest import FIXTURES
from contribpolicy import cli
from contribpolicy.github import GitHubError, RateLimitError


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


class FakeGitHub:
    """Serves FIXTURES/<name> as GitHub repo acme/<name>; acme/community holds a registry doc."""

    repos = {
        "acme/k8s_style": FIXTURES / "k8s_style",
        "acme/jaeger_style": FIXTURES / "jaeger_style",
        "acme/radius_style": FIXTURES / "radius_style",
    }
    issues: dict = {}

    def __init__(self, token=None, **kw):
        self.token = token

    def _root(self, repo):
        return self.repos.get(repo)

    def repo_info(self, repo):
        if repo == "acme/ratelimited":
            raise RateLimitError("GitHub API rate limit exceeded.")
        return {"full_name": repo} if self._root(repo) else None

    def list_dir(self, repo, path=""):
        root = self._root(repo)
        if not root or not (root / path).is_dir():
            return None
        return [(p.name, "dir" if p.is_dir() else "file") for p in sorted((root / path).iterdir())]

    def raw(self, repo, path, ref="HEAD"):
        root = self._root(repo)
        f = root / path if root else None
        return f.read_text() if f and f.is_file() else None

    def issue(self, repo, num):
        return self.issues.get((repo, num))


@pytest.fixture
def fake_gh(monkeypatch):
    monkeypatch.setattr(cli, "GitHub", FakeGitHub)
    monkeypatch.setattr(cli, "resolve_token", lambda: None)
    return FakeGitHub


def test_no_command_prints_help(capsys):
    code, out, _ = run(capsys)
    assert code == 2 and "usage:" in out


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "contribpolicy 0.1.0" in capsys.readouterr().out


@pytest.mark.parametrize("fmt", ["table", "json", "yaml", "markdown"])
def test_show_local_formats(capsys, fmt):
    code, out, _ = run(capsys, "show", str(FIXTURES / "k8s_style"), "--offline", "--format", fmt)
    assert code == 0
    assert "disclose" in out.lower()


def test_show_local_json_and_output_file(capsys, tmp_path):
    target = tmp_path / "p.json"
    code, out, _ = run(capsys, "show", str(FIXTURES / "flux_style"), "--offline", "-f", "json", "-o", str(target))
    assert code == 0 and out == ""
    d = json.loads(target.read_text())
    assert d["trailers"]["assisted_by"] == "required"
    assert d["sources"][0]["kind"] == "repo"


def test_show_local_with_repo_flag_uses_org_sources(capsys, fake_gh):
    code, out, err = run(capsys, "show", str(FIXTURES / "k8s_style"), "--repo", "kubernetes-sigs/demo", "-f", "json")
    assert code == 0
    d = json.loads(out)
    assert d["repo"] == "kubernetes-sigs/demo"
    # The fake has no kubernetes/community repo, so the registry entry warns instead of failing.
    assert "org registry entry kubernetes-ai-guidance" in err


def test_show_remote(capsys, fake_gh):
    code, out, _ = run(capsys, "show", "acme/jaeger_style", "-f", "json")
    assert code == 0
    d = json.loads(out)
    assert d["repo"] == "acme/jaeger_style"
    assert d["sources"][0]["url"] == "https://github.com/acme/jaeger_style/blob/HEAD/AI_POLICY.md"
    assert d["disclosure"]["where"] == ["checkbox"]


def test_show_remote_github_url(capsys, fake_gh):
    code, out, _ = run(capsys, "show", "https://github.com/acme/k8s_style", "-f", "json", "--no-org", "--no-links")
    assert code == 0 and json.loads(out)["ai_use"] == "disclose"


def test_show_errors(capsys, fake_gh, tmp_path):
    assert run(capsys, "show", "acme/missing")[0] == 2
    assert run(capsys, "show", "not a repo")[0] == 2
    assert run(capsys, "show", "acme/k8s_style", "--offline")[0] == 2
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert run(capsys, "show", str(f))[0] == 2
    code, _, err = run(capsys, "show", str(FIXTURES / "k8s_style"), "--repo", "bad slug!")
    assert code == 2 and "owner/name" in err
    code, _, err = run(capsys, "show", "acme/ratelimited")
    assert code == 2 and "rate limit" in err
    bad = tmp_path / "reg.json"
    bad.write_text("{")
    assert run(capsys, "show", "acme/k8s_style", "--registry", str(bad))[0] == 2


def test_agents_md(capsys):
    code, out, _ = run(capsys, "agents-md", str(FIXTURES / "k8s_style"), "--offline")
    assert code == 0 and "## Contribution rules for" in out and "Do NOT add `Assisted-by`" in out


def test_registry_command(capsys, tmp_path):
    code, out, _ = run(capsys, "registry")
    assert code == 0 and "kubernetes-ai-guidance" in out and "tektoncd" in out
    code, out, _ = run(capsys, "registry", "-f", "json")
    assert code == 0 and {e["id"] for e in json.loads(out)} >= {"flux-contributing", "tekton-ai-contribution-policy"}
    bad = tmp_path / "r.json"
    bad.write_text("[]")
    assert run(capsys, "registry", "--registry", str(bad))[0] == 2


# --- check ----------------------------------------------------------------------------


@pytest.fixture
def k8s_policy(tmp_path, capsys):
    path = tmp_path / "policy.json"
    run(capsys, "show", str(FIXTURES / "k8s_style"), "--offline", "-f", "json", "-o", str(path))
    return path


def test_check_with_policy_file_violation(capsys, tmp_path, k8s_policy):
    body = tmp_path / "body.md"
    body.write_text("Refactors the parser. Written with help from Claude.")
    commits = tmp_path / "commits.txt"
    commits.write_text("Fix parser\n\nAssisted-by: Claude\n---\nSecond\n")
    code, out, _ = run(capsys, "check", "--policy", str(k8s_policy), "--pr-body", str(body), "--commits", str(commits))
    assert code == 1
    assert "ERROR trailers.assisted_by" in out and "1 violation(s)" in out
    assert "rule source CONTRIBUTING.md:18" in out


def test_check_ok_json(capsys, tmp_path, k8s_policy):
    body = tmp_path / "body.md"
    body.write_text("Parts of this PR were drafted with help from an AI assistant.")
    code, out, _ = run(capsys, "check", "--policy", str(k8s_policy), "--pr-body", str(body), "-f", "json")
    assert code == 0
    d = json.loads(out)
    assert d["ok"] is True and d["violations"] == []


def test_check_reads_stdin(capsys, monkeypatch, k8s_policy):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO("Refactor."))
    code, out, _ = run(capsys, "check", "--policy", str(k8s_policy), "--pr-body", "-", "--ai-assisted", "yes")
    assert code == 1 and "disclosure" in out


def test_check_local_target_and_git_range(capsys, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CONTRIBUTING.md").write_text((FIXTURES / "flux_style" / "CONTRIBUTING.md").read_text())

    def git(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    git("init", "-q")
    for msg in ("base", "change without sign-off"):
        git(
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@x",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            msg,
        )
    code, out, _ = run(capsys, "check", str(repo), "--offline", "--git-range", "HEAD~1..HEAD", "--ai-assisted", "yes")
    assert code == 1
    assert "ERROR dco" in out and "ERROR trailers.assisted_by" in out
    code, _, err = run(capsys, "check", str(repo), "--offline", "--git-range", "nope..HEAD")
    assert code == 2 and "git log" in err


def test_check_issue_lookup(capsys, fake_gh, tmp_path):
    FakeGitHub.issues = {("acme/radius_style", 9): {"labels": [{"name": "triaged"}], "assignees": [], "state": "open"}}
    body = tmp_path / "b.md"
    body.write_text("Fixes #9")
    code, out, _ = run(capsys, "check", "acme/radius_style", "--pr-body", str(body))
    assert code == 1 and "lacks a required label: help wanted or good first issue" in out
    code, out, _ = run(capsys, "check", "acme/radius_style", "--pr-body", str(body), "--no-issue-lookup")
    assert "lacks a required label" not in out


def test_check_usage_errors(capsys, tmp_path, k8s_policy):
    assert run(capsys, "check", "--pr-body", "x")[0] == 2
    assert run(capsys, "check", "--policy", str(k8s_policy))[0] == 2
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 9}')
    code, _, err = run(capsys, "check", "--policy", str(bad), "--pr-body", str(bad))
    assert code == 2 and "unsupported policy schema version" in err


def test_github_error_exit_code(capsys, monkeypatch):
    def boom(args):
        raise GitHubError("network error fetching x: dns")

    monkeypatch.setattr(cli, "load_policy", boom)
    code, _, err = run(capsys, "show", "acme/x")
    assert code == 2 and "network error" in err


def test_module_entry_point():
    out = subprocess.run([sys.executable, "-m", "contribpolicy", "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and "contribpolicy" in out.stdout
