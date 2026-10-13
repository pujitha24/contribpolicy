"""Find a repository's policy documents.

Sources, in the order they are read:

1. Well-known files in the repository root, ``.github/`` and ``docs/``:
   CONTRIBUTING, AI policy files (AI_POLICY.md, GENAI_POLICY.md,
   AI_CONTRIBUTIONS.md, ...), agent instruction files (AGENTS.md, CLAUDE.md,
   GEMINI.md, .github/copilot-instructions.md) and pull request templates.
2. In-repository documents linked from those files (one hop), when the link
   target or link text looks like a policy (``kind: linked``).
3. GitHub's organisation fallback: if the repository has no CONTRIBUTING or
   no pull request template, the ones in ``<owner>/.github`` (``kind: org``).
4. Organisation policies from the bundled registry (``kind: org``,
   path prefixed with ``registry:``).
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .extract import Doc, role_for_path
from .github import GitHub, GitHubError, RateLimitError
from .markdown import parse
from .model import Source
from .registry import RegistryEntry, entries_for

__all__ = [
    "Discovery",
    "GitHubReader",
    "LocalReader",
    "Reader",
    "discover",
    "git_remote_slug",
    "parse_repo_slug",
]

_SEARCH_DIRS = ("", ".github", "docs")
_DOC_EXT = (".md", ".markdown", ".rst", ".txt")
_MAX_LINKS = 10
_MAX_BYTES = 512 * 1024

_LINK = re.compile(r"\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_REFDEF = re.compile(r"(?m)^\s{0,3}\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s+[\"'(].*)?$")
_IMPORT = re.compile(r"(?m)(?:^|\s)@((?:\.{1,2}/)?[\w./-]+\.md)\b")
_GH_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/(?:blob|tree)/[^/]+/(.+)$|"
    r"^https?://raw\.githubusercontent\.com/([\w.-]+)/([\w.-]+)/[^/]+/(.+)$",
    re.I,
)
_POLICYISH = re.compile(
    r"(?i)(?:^|[^a-z])(?:ai|genai|llm|agents?|polic(?:y|ies)|contribut\w*|guidelines?|pull[-_ ]?requests?|"
    r"commit\w*|code[-_ ]?review|dco|cla|sign\w*|first[-_ ]commit|creating[-_ ]a[-_ ]pr)(?:[^a-z]|$)"
)
_AIISH = re.compile(r"(?i)(?:^|[^a-z])(?:ai|genai|llm|generative|agents?)(?:[^a-z]|$)")
_SKIP_NAME = re.compile(
    r"(?i)^(?:code[-_]of[-_]conduct|security|license|licence|changelog|notice|authors|maintainers|owners)(?:\.\w+)?$"
)
_REPO_SLUG = re.compile(r"^(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


class Reader(Protocol):
    name: str

    def list_dir(self, path: str) -> list[tuple[str, str]] | None: ...

    def read(self, path: str) -> str | None: ...

    def url(self, path: str) -> str: ...


class GitHubReader:
    def __init__(self, gh: GitHub, repo: str):
        self.gh = gh
        self.name = repo

    def list_dir(self, path: str) -> list[tuple[str, str]] | None:
        return self.gh.list_dir(self.name, path)

    def read(self, path: str) -> str | None:
        return self.gh.raw(self.name, path)

    def url(self, path: str) -> str:
        return f"https://github.com/{self.name}/blob/HEAD/{path}"


class LocalReader:
    def __init__(self, root: str | Path, name: str = ""):
        self.root = Path(root).resolve()
        self.name = name or self.root.name

    def _resolve(self, path: str) -> Path | None:
        p = (self.root / path).resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            return None
        return p

    def list_dir(self, path: str) -> list[tuple[str, str]] | None:
        p = self._resolve(path)
        if p is None or not p.is_dir():
            return None
        out = []
        for e in sorted(os.scandir(p), key=lambda e: e.name):
            out.append((e.name, "dir" if e.is_dir() else "file"))
        return out

    def read(self, path: str) -> str | None:
        p = self._resolve(path)
        if p is None or not p.is_file():
            return None
        data = p.read_bytes()[:_MAX_BYTES]
        return data.decode("utf-8", errors="replace")

    def url(self, path: str) -> str:
        return path


@dataclass
class Discovery:
    docs: list[Doc] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_repo_slug(s: str) -> str | None:
    m = _REPO_SLUG.match(s.strip())
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


def git_remote_slug(path: str | Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str | None:
    """owner/name of the 'origin' (or first) GitHub remote of a local checkout."""
    try:
        out = run(["git", "-C", str(path), "remote", "-v"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    best = None
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?$", parts[1])
        if m:
            slug = f"{m.group(1)}/{m.group(2)}"
            if parts[0] == "origin":
                return slug
            best = best or slug
    return best


def _norm_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()


def _classify_file(dirpath: str, name: str) -> str | None:
    """Which well-known file is this (or None)."""
    full = posixpath.join(dirpath, name) if dirpath else name
    role = role_for_path(full)
    lower = name.lower()
    if role == "agent":
        if lower == "copilot-instructions.md":
            return role if dirpath == ".github" else None
        return role if dirpath == "" else None
    if role in ("contributing", "ai_policy", "pr_template"):
        return role
    return None


_ROLE_ORDER = {"contributing": 0, "ai_policy": 1, "agent": 2, "pr_template": 3, "other": 4}


def _well_known(reader: Reader) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for d in _SEARCH_DIRS:
        items = reader.list_dir(d)
        if not items:
            continue
        for name, typ in items:
            full = posixpath.join(d, name) if d else name
            if typ in ("file", "symlink"):
                role = _classify_file(d, name)
                if role:
                    found.append((full, role))
            elif typ == "dir" and name.lower() == "pull_request_template":
                sub = reader.list_dir(full) or []
                for sname, styp in sub[:3]:
                    if styp in ("file", "symlink") and sname.lower().endswith(".md"):
                        found.append((posixpath.join(full, sname), "pr_template"))
    found.sort(key=lambda t: (_ROLE_ORDER.get(t[1], 9), t[0].count("/"), t[0].lower()))
    return found


def _links(text: str, src_path: str, repo: str) -> list[tuple[str, str]]:
    """(target path, link text) for in-repo links in a document."""
    out: list[tuple[str, str]] = []
    cands: list[tuple[str, str]] = []
    cands += [(m.group(2), m.group(1)) for m in _LINK.finditer(text)]
    cands += [(m.group(2), m.group(1)) for m in _REFDEF.finditer(text)]
    cands += [(m.group(1), "") for m in _IMPORT.finditer(text)]
    owner, name = [*repo.split("/", 1), ""][:2]
    for target, label in cands:
        target = target.strip()
        if not target or target.startswith(("#", "mailto:")):
            continue
        m = _GH_URL.match(target)
        if m:
            o, r, p = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
            if o.lower() != owner.lower() or r.lower() != name.lower():
                continue
            path = p
        elif re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            continue
        else:
            target = target.split("#", 1)[0].split("?", 1)[0]
            if not target:
                continue
            if target.startswith("/"):
                path = target.lstrip("/")
            else:
                path = posixpath.join(posixpath.dirname(src_path), target)
        path = posixpath.normpath(path.split("#", 1)[0].split("?", 1)[0]) if path else ""
        if not path or path.startswith("..") or path == ".":
            continue
        out.append((path, label))
    return out


def _link_score(path: str, label: str) -> int:
    base = posixpath.basename(path)
    if _SKIP_NAME.match(base):
        return 0
    ext = posixpath.splitext(base)[1].lower()
    if ext and ext not in _DOC_EXT:
        return 0
    if path.lower() == "readme.md":
        return 0
    score = 0
    if _AIISH.search(path) or _AIISH.search(label):
        score += 10
    if _POLICYISH.search(path):
        score += 3
    if _POLICYISH.search(label):
        score += 2
    return score


def _read_dir_index(reader: Reader, path: str) -> tuple[str, str] | None:
    items = reader.list_dir(path)
    if not items:
        return None
    names = {n.lower(): n for n, t in items if t in ("file", "symlink")}
    for cand in ("readme.md", "_index.md", "index.md", "contributing.md"):
        if cand in names:
            full = posixpath.join(path, names[cand])
            text = reader.read(full)
            if text is not None:
                return full, text
    return None


def _mk_doc(source: Source, text: str, role: str | None = None) -> Doc:
    r = role or role_for_path(source.path.rsplit(":", 1)[-1])
    return Doc(source=source, text=text, role=r, ai_scope=(r == "ai_policy"))


def discover(
    reader: Reader,
    repo: str,
    *,
    org_reader: Callable[[str], Reader] | None = None,
    registry: list[RegistryEntry] | None = None,
    follow_links: bool = True,
    max_links: int = _MAX_LINKS,
) -> Discovery:
    """Collect policy documents for ``repo`` (``owner/name``; may be empty for a local path)."""
    res = Discovery()
    hashes: set[str] = set()
    seen_paths: set[str] = set()

    def add(doc: Doc) -> bool:
        h = _norm_hash(doc.text)
        if h in hashes or not doc.text.strip():
            return False
        hashes.add(h)
        seen_paths.add(doc.source.path)
        res.docs.append(doc)
        res.sources.append(doc.source)
        return True

    roots: list[Doc] = []
    for path, role in _well_known(reader):
        text = reader.read(path)
        if text is None:
            continue
        doc = Doc(Source(path, reader.url(path), "repo"), text, role=role, ai_scope=role == "ai_policy")
        if add(doc):
            roots.append(doc)
        else:
            seen_paths.add(path)

    # One hop of links from the root documents.
    if follow_links:
        cands: dict[str, tuple[int, int, str]] = {}
        order = 0
        for doc in roots:
            for path, label in _links(doc.text, doc.source.path, repo or reader.name):
                if path in seen_paths:
                    continue
                s = _link_score(path, label)
                order += 1
                if s and (path not in cands or cands[path][0] < s):
                    cands[path] = (s, order, label)
        ranked = sorted(cands.items(), key=lambda kv: (-kv[1][0], kv[1][1]))[:max_links]
        for path, _ in ranked:
            real = path
            if posixpath.splitext(path)[1]:
                text = reader.read(path)
            else:
                # Extensionless targets are only followed when they are directories
                # (to their README); files like DCO or LICENSE are not policies.
                hit = _read_dir_index(reader, path)
                text = None
                if hit:
                    real, text = hit
            if text is None or real in seen_paths:
                continue
            add(_mk_doc(Source(real, reader.url(real), "linked"), text))

    # GitHub's organisation-wide fallback for community health files.
    roles = {d.role for d in res.docs if d.source.kind == "repo"}
    owner = repo.split("/", 1)[0] if repo else ""
    if org_reader and owner and repo.split("/", 1)[1].lower() != ".github":
        need = {r for r in ("contributing", "pr_template") if r not in roles}
        if need:
            try:
                org = org_reader(f"{owner}/.github")
                for path, role in _well_known(org):
                    if role in need or role == "ai_policy":
                        text = org.read(path)
                        if text is None:
                            continue
                        src = Source(f"{owner}/.github:{path}", org.url(path), "org", title=f"{owner}/.github default")
                        add(Doc(src, text, role=role, ai_scope=role == "ai_policy"))
                        need.discard(role)
            except RateLimitError:
                raise
            except GitHubError as e:
                res.warnings.append(f"could not read {owner}/.github: {e}")

    # Organisation policies from the registry.
    if registry and repo and org_reader:
        for entry in entries_for(repo, registry):
            try:
                text = org_reader(entry.repo).read(entry.path)
            except RateLimitError:
                raise
            except GitHubError as e:
                res.warnings.append(f"org registry entry {entry.id}: could not fetch {entry.repo}/{entry.path}: {e}")
                continue
            if text is None:
                res.warnings.append(f"org registry entry {entry.id}: {entry.repo}/{entry.path} not found")
                continue
            if entry.section and not any(
                u.kind == "heading" and u.text.strip().lower() == entry.section.lower() for u in parse(text)
            ):
                res.warnings.append(
                    f"org registry entry {entry.id}: section '{entry.section}' not found in {entry.repo}/{entry.path}"
                )
                continue
            src = Source(entry.source_path, entry.url, "org", title=entry.title, registry=entry.id)
            doc = Doc(
                src,
                text,
                role="ai_policy" if entry.ai_policy else role_for_path(entry.path),
                ai_scope=entry.ai_policy,
                section=entry.section,
            )
            if not entry.section and _norm_hash(text) in hashes:
                continue  # the repository already carries this exact document
            hashes.add(_norm_hash(text) + entry.id)
            res.docs.append(doc)
            res.sources.append(src)
    return res
