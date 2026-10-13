"""Minimal, read-only GitHub access over urllib.

* Directory listings and issue lookups use the REST API (counts against the
  rate limit: 60/hour unauthenticated, 5000/hour with a token).
* File contents come from raw.githubusercontent.com, which does not count
  against the REST rate limit.

A token is optional. It is taken from ``GITHUB_TOKEN`` / ``GH_TOKEN`` or, if
the GitHub CLI is installed and logged in, from ``gh auth token``. It is only
ever sent to api.github.com and raw.githubusercontent.com.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from ._meta import TOOL_NAME, VERSION

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
_TOKEN_HOSTS = ("api.github.com", "raw.githubusercontent.com")


class GitHubError(RuntimeError):
    pass


class RateLimitError(GitHubError):
    pass


def resolve_token(
    env: dict[str, str] | None = None,
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    env = dict(os.environ) if env is None else env
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        tok = env.get(var, "").strip()
        if tok:
            return tok
    if which("gh"):
        try:
            out = run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        tok = (out.stdout or "").strip()
        if out.returncode == 0 and tok and " " not in tok:
            return tok
    return None


class GitHub:
    def __init__(
        self,
        token: str | None = None,
        opener: Callable[..., Any] | None = None,
        timeout: float = 20.0,
    ):
        self.token = token
        self._open = opener or urllib.request.urlopen
        self.timeout = timeout
        self._cache: dict[str, tuple[int, bytes]] = {}
        self.requests = 0

    def _get(self, url: str, accept: str = "application/vnd.github+json") -> tuple[int, bytes]:
        if url in self._cache:
            return self._cache[url]
        headers = {"User-Agent": f"{TOOL_NAME}/{VERSION}", "Accept": accept}
        host = urllib.parse.urlsplit(url).hostname or ""
        if self.token and host in _TOKEN_HOSTS:
            headers["Authorization"] = f"Bearer {self.token}"
        if host == "api.github.com":
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        req = urllib.request.Request(url, headers=headers)
        self.requests += 1
        try:
            with self._open(req, timeout=self.timeout) as resp:
                result = (resp.status, resp.read())
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            if e.code in (403, 429) and (
                b"rate limit" in body.lower() or (e.headers and e.headers.get("X-RateLimit-Remaining") == "0")
            ):
                hint = "" if self.token else " Set GITHUB_TOKEN (or log in with `gh auth login`) to raise the limit."
                raise RateLimitError(f"GitHub API rate limit exceeded.{hint}") from None
            if e.code in (401,):
                raise GitHubError("GitHub rejected the token (401). Check GITHUB_TOKEN.") from None
            result = (e.code, body)
        except urllib.error.URLError as e:
            raise GitHubError(f"network error fetching {url}: {e.reason}") from None
        self._cache[url] = result
        return result

    def api(self, path: str) -> Any | None:
        status, body = self._get(f"{API}/{path.lstrip('/')}")
        if status == 404:
            return None
        if status >= 400:
            raise GitHubError(f"GitHub API {path} returned HTTP {status}")
        return json.loads(body.decode("utf-8"))

    def raw(self, repo: str, path: str, ref: str = "HEAD") -> str | None:
        quoted = urllib.parse.quote(path)
        status, body = self._get(f"{RAW}/{repo}/{ref}/{quoted}", accept="text/plain")
        if status == 404:
            return None
        if status >= 400:
            raise GitHubError(f"raw fetch {repo}/{path} returned HTTP {status}")
        return body.decode("utf-8", errors="replace")

    def list_dir(self, repo: str, path: str = "") -> list[tuple[str, str]] | None:
        data = self.api(f"repos/{repo}/contents/{urllib.parse.quote(path)}" if path else f"repos/{repo}/contents")
        if not isinstance(data, list):
            return None
        return [(d.get("name", ""), d.get("type", "file")) for d in data]

    def repo_info(self, repo: str) -> dict[str, Any] | None:
        data = self.api(f"repos/{repo}")
        return data if isinstance(data, dict) else None

    def issue(self, repo: str, number: int) -> dict[str, Any] | None:
        data = self.api(f"repos/{repo}/issues/{number}")
        return data if isinstance(data, dict) else None
