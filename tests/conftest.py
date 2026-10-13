from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from contribpolicy.discover import LocalReader, discover
from contribpolicy.extract import Doc, extract, role_for_path
from contribpolicy.model import Policy, Source

FIXTURES = Path(__file__).parent / "fixtures"


def policy_for(name: str, repo: str = "", **kw) -> Policy:
    reader = LocalReader(FIXTURES / name, name=repo or name)
    found = discover(reader, repo, **kw)
    p = extract(repo or name, found.docs, found.sources)
    p.warnings = found.warnings + p.warnings
    return p


def policy_from_text(text: str, path: str = "CONTRIBUTING.md", role: str | None = None, **doc_kw) -> Policy:
    text = textwrap.dedent(text).lstrip("\n")
    r = role or role_for_path(path)
    doc = Doc(Source(path, "", "repo"), text, role=r, ai_scope=doc_kw.pop("ai_scope", r == "ai_policy"), **doc_kw)
    return extract("acme/widget", [doc])


def ev(p: Policy, rule: str):
    return [e for e in p.evidence if e.rule == rule]


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
