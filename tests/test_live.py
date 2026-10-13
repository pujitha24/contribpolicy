"""Read-only checks against real repositories. Run with CONTRIBPOLICY_LIVE=1.

Upstream documents change; when one of these fails, read the source and
update either the expectation or the extractor.
"""

import os

import pytest

from contribpolicy.discover import GitHubReader, discover
from contribpolicy.extract import extract
from contribpolicy.github import GitHub, resolve_token
from contribpolicy.registry import load_registry

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("CONTRIBPOLICY_LIVE") != "1", reason="set CONTRIBPOLICY_LIVE=1"),
]

EXPECT = {
    "kubernetes-sigs/kueue": {
        "ai_use": "disclose",
        "assisted_by": "banned",
        "co_authored_by_ai": "banned",
        "human_only": {"review-replies", "commit-messages"},
        "cla": True,
    },
    "thanos-io/thanos": {
        "ai_use": "disclose",
        "assisted_by": "banned",
        "co_authored_by_ai": "banned",
        "human_only": {"review-replies", "commit-messages"},
        "dco": True,
    },
    "jaegertracing/jaeger": {"ai_use": "disclose", "where": {"checkbox"}, "co_authored_by_ai": "allowed", "dco": True},
    "cortexproject/cortex": {"ai_use": "disclose", "where": {"description"}, "human_only": {"issues"}, "dco": True},
    "radius-project/radius": {
        "ai_use": "restricted",
        "required_labels": [["triaged"], ["help wanted", "good first issue"]],
        "signed_commits": True,
        "dco": True,
    },
    "microsoft/agent-framework": {"ai_use": "disclose", "where": {"description", "checkbox"}},
    "grafana/mimir": {"signed_commits": True},
    "fluxcd/flux2": {"ai_use": "disclose", "assisted_by": "required", "co_authored_by_ai": "banned", "dco": True},
    "tektoncd/pipeline": {"ai_use": "disclose", "assisted_by": "allowed"},
    "tj/commander.js": {"ai_use": "unspecified", "assisted_by": "unspecified", "human_only": set()},
}


@pytest.fixture(scope="module")
def gh():
    return GitHub(token=resolve_token())


@pytest.mark.parametrize("repo", sorted(EXPECT))
def test_live(repo, gh):
    found = discover(GitHubReader(gh, repo), repo, org_reader=lambda r: GitHubReader(gh, r), registry=load_registry())
    p = extract(repo, found.docs, found.sources)
    exp = EXPECT[repo]
    for key, want in exp.items():
        if key == "where":
            assert want <= set(p.disclosure_where), key
        elif key == "human_only":
            assert want <= set(p.human_only) if want else p.human_only == [], key
        else:
            assert getattr(p, key) == want, key
    for e in p.evidence:
        assert e.line > 0 and e.quote
