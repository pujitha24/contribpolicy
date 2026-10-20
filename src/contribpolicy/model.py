"""The policy data model and its JSON form.

The JSON layout (field names and value vocabularies) is shared with a sibling
GitHub Action, so ``Policy.to_dict`` must keep exactly these keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._meta import SCHEMA_VERSION

AI_USE_VALUES = ("allowed", "disclose", "restricted", "banned", "unspecified")
ASSISTED_BY_VALUES = ("required", "allowed", "banned", "unspecified")
CO_AUTHORED_VALUES = ("allowed", "banned", "unspecified")
DISCLOSURE_WHERE = ("description", "checkbox", "commit-trailer")
HUMAN_ONLY = ("review-replies", "commit-messages", "pr-description", "issues")
SOURCE_KINDS = ("repo", "org", "linked")


@dataclass
class Source:
    path: str
    url: str
    kind: str  # repo | org | linked
    # Not serialised; used for human-readable output.
    title: str = ""
    registry: str = ""  # registry entry id when the source comes from the org registry

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "url": self.url, "kind": self.kind}


@dataclass
class Evidence:
    rule: str
    quote: str
    path: str
    line: int
    # Not serialised: the value this sentence supports (e.g. "banned").
    value: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "quote": self.quote, "path": self.path, "line": self.line}


@dataclass
class Policy:
    repo: str
    sources: list[Source] = field(default_factory=list)
    ai_use: str = "unspecified"
    disclosure_required: bool = False
    disclosure_where: list[str] = field(default_factory=list)
    disclosure_example: str = ""
    assisted_by: str = "unspecified"
    co_authored_by_ai: str = "unspecified"
    human_only: list[str] = field(default_factory=list)
    assignment_required: bool = False
    issue_first: bool = False
    required_labels: list[list[str]] = field(default_factory=list)
    dco: bool = False
    cla: bool = False
    signed_commits: bool = False
    ui_screenshots_required: bool = False
    evidence: list[Evidence] = field(default_factory=list)
    # Not serialised.
    warnings: list[str] = field(default_factory=list)
    checkbox_options: list[str] = field(default_factory=list)
    # Not serialised (``cla`` stays a boolean in the shared schema): every CLA statement found
    # applies only to some contributions, e.g. "a CLA is only required for larger changes".
    # Recorded in the JSON as a ``cla.conditional`` evidence entry.
    cla_conditional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "repo": self.repo,
            "sources": [s.to_dict() for s in self.sources],
            "ai_use": self.ai_use,
            "disclosure": {
                "required": self.disclosure_required,
                "where": list(self.disclosure_where),
                "example": self.disclosure_example,
            },
            "trailers": {
                "assisted_by": self.assisted_by,
                "co_authored_by_ai": self.co_authored_by_ai,
            },
            "human_only": list(self.human_only),
            "assignment_required": self.assignment_required,
            "issue_first": self.issue_first,
            "required_labels": [list(g) for g in self.required_labels],
            "dco": self.dco,
            "cla": self.cla,
            "signed_commits": self.signed_commits,
            "ui_screenshots_required": self.ui_screenshots_required,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Policy:
        """Load a policy previously written with ``show --format json``."""
        if d.get("version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported policy schema version: {d.get('version')!r}")
        disc = d.get("disclosure") or {}
        tr = d.get("trailers") or {}
        rules = {e.get("rule") for e in d.get("evidence", [])}
        p = cls(
            repo=d.get("repo", ""),
            sources=[Source(s["path"], s.get("url", ""), s.get("kind", "repo")) for s in d.get("sources", [])],
            ai_use=d.get("ai_use", "unspecified"),
            disclosure_required=bool(disc.get("required", False)),
            disclosure_where=list(disc.get("where", [])),
            disclosure_example=disc.get("example") or "",
            assisted_by=tr.get("assisted_by", "unspecified"),
            co_authored_by_ai=tr.get("co_authored_by_ai", "unspecified"),
            human_only=list(d.get("human_only", [])),
            assignment_required=bool(d.get("assignment_required", False)),
            issue_first=bool(d.get("issue_first", False)),
            required_labels=[list(g) for g in d.get("required_labels", [])],
            dco=bool(d.get("dco", False)),
            cla=bool(d.get("cla", False)),
            signed_commits=bool(d.get("signed_commits", False)),
            ui_screenshots_required=bool(d.get("ui_screenshots_required", False)),
            evidence=[
                Evidence(e["rule"], e.get("quote", ""), e.get("path", ""), int(e.get("line", 0)))
                for e in d.get("evidence", [])
            ],
        )
        p.cla_conditional = p.cla and "cla.conditional" in rules and "cla" not in rules
        return p

    def evidence_for(self, prefix: str) -> list[Evidence]:
        return [e for e in self.evidence if e.rule == prefix or e.rule.startswith(prefix + ".")]
