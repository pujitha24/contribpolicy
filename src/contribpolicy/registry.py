"""The bundled registry of organisation-level policies.

Some organisations publish one AI policy for all of their repositories and
do not copy it into each repository (Kubernetes, Tekton, Flux, ...). The
registry maps a GitHub organisation to the document that holds that policy.
The document is fetched at run time and goes through the same extractor as
repository files, so the registry stores *where* the rules are, never a
hand-written copy of them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

__all__ = ["RegistryEntry", "RegistryError", "entries_for", "load_registry"]

_REQUIRED = ("id", "title", "orgs", "url", "repo", "path")


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class RegistryEntry:
    id: str
    title: str
    orgs: tuple[str, ...]
    url: str
    repo: str
    path: str
    section: str = ""
    ai_policy: bool = False
    scope: str = ""
    verified: str = ""
    exclude: tuple[str, ...] = ()

    @property
    def source_path(self) -> str:
        """How this entry appears as a source path in output."""
        return f"registry:{self.repo}:{self.path}"


def _parse(data: object, origin: str) -> list[RegistryEntry]:
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise RegistryError(f"{origin}: expected an object with an 'entries' list")
    out: list[RegistryEntry] = []
    seen: set[str] = set()
    for i, e in enumerate(data["entries"]):
        if not isinstance(e, dict):
            raise RegistryError(f"{origin}: entry {i} is not an object")
        missing = [k for k in _REQUIRED if not e.get(k)]
        if missing:
            raise RegistryError(f"{origin}: entry {e.get('id', i)!r} is missing {', '.join(missing)}")
        if e["id"] in seen:
            raise RegistryError(f"{origin}: duplicate entry id {e['id']!r}")
        seen.add(e["id"])
        if not isinstance(e["orgs"], list) or not all(isinstance(o, str) and o for o in e["orgs"]):
            raise RegistryError(f"{origin}: entry {e['id']!r}: 'orgs' must be a list of organisation names")
        if e["repo"].count("/") != 1:
            raise RegistryError(f"{origin}: entry {e['id']!r}: 'repo' must look like owner/name")
        if not str(e["url"]).startswith("https://"):
            raise RegistryError(f"{origin}: entry {e['id']!r}: 'url' must be an https URL")
        out.append(
            RegistryEntry(
                id=e["id"],
                title=e["title"],
                orgs=tuple(o.lower() for o in e["orgs"]),
                url=e["url"],
                repo=e["repo"],
                path=e["path"],
                section=e.get("section", "") or "",
                ai_policy=bool(e.get("ai_policy", False)),
                scope=e.get("scope", "") or "",
                verified=e.get("verified", "") or "",
                exclude=tuple(r.lower() for r in e.get("exclude", []) or []),
            )
        )
    return out


def load_registry(extra: str | Path | None = None) -> list[RegistryEntry]:
    """Load the bundled registry, plus an optional user file whose entries win on id clashes."""
    bundled = resources.files(__package__ or "contribpolicy").joinpath("data/org_registry.json")
    text = bundled.read_text(encoding="utf-8")
    entries = _parse(json.loads(text), "bundled registry")
    if extra:
        p = Path(extra)
        try:
            user = _parse(json.loads(p.read_text(encoding="utf-8")), str(p))
        except json.JSONDecodeError as e:
            raise RegistryError(f"{p}: invalid JSON: {e}") from None
        ids = {e.id for e in user}
        entries = [e for e in entries if e.id not in ids] + user
    return entries


def entries_for(repo: str, entries: list[RegistryEntry]) -> list[RegistryEntry]:
    owner = repo.split("/", 1)[0].lower()
    return [e for e in entries if owner in e.orgs and repo.lower() not in e.exclude]
