import io
import json
import subprocess
import urllib.error

import pytest

from contribpolicy.github import GitHub, GitHubError, RateLimitError, resolve_token


class Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(routes):
    seen = []

    def opener(req, timeout=None):
        seen.append(req)
        url = req.full_url
        for prefix, result in routes.items():
            if url.startswith(prefix):
                if isinstance(result, Exception):
                    raise result
                status, body = result
                if status >= 400:
                    raise urllib.error.HTTPError(url, status, "err", hdrs=None, fp=io.BytesIO(body))
                return Resp(status, body)
        raise AssertionError(f"unexpected URL {url}")

    opener.seen = seen
    return opener


def test_api_raw_and_listing():
    op = opener_for(
        {
            "https://api.github.com/repos/a/b/contents": (200, json.dumps([{"name": "X.md", "type": "file"}]).encode()),
            "https://api.github.com/repos/a/b": (200, b'{"full_name": "a/b"}'),
            "https://raw.githubusercontent.com/a/b/HEAD/docs/A%20B.md": (200, b"hello"),
        }
    )
    gh = GitHub(token="t0k", opener=op)
    assert gh.list_dir("a/b") == [("X.md", "file")]
    assert gh.repo_info("a/b") == {"full_name": "a/b"}
    assert gh.raw("a/b", "docs/A B.md") == "hello"
    assert gh.raw("a/b", "docs/A B.md") == "hello"  # cached
    assert gh.requests == 3
    req = op.seen[0]
    assert req.get_header("Authorization") == "Bearer t0k"
    assert req.get_header("User-agent").startswith("contribpolicy/")


def test_token_not_sent_to_other_hosts():
    op = opener_for({"https://example.org/": (200, b"{}")})
    gh = GitHub(token="secret", opener=op)
    gh._get("https://example.org/x")
    assert op.seen[0].get_header("Authorization") is None


def test_404_returns_none():
    op = opener_for(
        {
            "https://api.github.com/repos/a/b/contents/docs": (404, b"{}"),
            "https://raw.githubusercontent.com/a/b/HEAD/NOPE.md": (404, b"404"),
            "https://api.github.com/repos/a/b/issues/3": (404, b"{}"),
        }
    )
    gh = GitHub(opener=op)
    assert gh.list_dir("a/b", "docs") is None
    assert gh.raw("a/b", "NOPE.md") is None
    assert gh.issue("a/b", 3) is None


def test_rate_limit_and_errors():
    gh = GitHub(opener=opener_for({"https://api.github.com/": (403, b'{"message": "API rate limit exceeded"}')}))
    with pytest.raises(RateLimitError, match="GITHUB_TOKEN"):
        gh.api("repos/a/b")
    gh = GitHub(opener=opener_for({"https://api.github.com/": (401, b"bad creds")}))
    with pytest.raises(GitHubError, match="401"):
        gh.api("repos/a/b")
    gh = GitHub(opener=opener_for({"https://api.github.com/": (500, b"oops")}))
    with pytest.raises(GitHubError, match="HTTP 500"):
        gh.api("repos/a/b")
    gh = GitHub(opener=opener_for({"https://raw.githubusercontent.com/": (500, b"oops")}))
    with pytest.raises(GitHubError, match="HTTP 500"):
        gh.raw("a/b", "x.md")
    gh = GitHub(opener=opener_for({"https://api.github.com/": urllib.error.URLError("dns")}))
    with pytest.raises(GitHubError, match="network error"):
        gh.api("repos/a/b")


def test_list_dir_non_list_is_none():
    gh = GitHub(opener=opener_for({"https://api.github.com/repos/a/b/contents/f": (200, b'{"type": "file"}')}))
    assert gh.list_dir("a/b", "f") is None


def test_resolve_token_env_first():
    assert resolve_token(env={"GITHUB_TOKEN": " abc "}, which=lambda _: None) == "abc"
    assert resolve_token(env={"GH_TOKEN": "def"}, which=lambda _: None) == "def"
    assert resolve_token(env={}, which=lambda _: None) is None


def test_resolve_token_gh_cli():
    def run(cmd, **kw):
        assert cmd == ["gh", "auth", "token"]
        return subprocess.CompletedProcess(cmd, 0, "gho_x\n", "")

    assert resolve_token(env={}, run=run, which=lambda _: "/usr/bin/gh") == "gho_x"

    def not_logged_in(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "not logged in")

    assert resolve_token(env={}, run=not_logged_in, which=lambda _: "/usr/bin/gh") is None

    def broken(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)

    assert resolve_token(env={}, run=broken, which=lambda _: "/usr/bin/gh") is None
