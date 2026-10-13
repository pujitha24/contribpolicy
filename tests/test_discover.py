import subprocess

import pytest

from conftest import FIXTURES
from contribpolicy.discover import (
    GitHubReader,
    LocalReader,
    _link_score,
    _links,
    discover,
    git_remote_slug,
    parse_repo_slug,
)
from contribpolicy.extract import extract
from contribpolicy.github import GitHubError, RateLimitError
from contribpolicy.registry import RegistryEntry


class DictReader:
    """In-memory repository for tests."""

    def __init__(self, name: str, files: dict[str, str], fail: Exception | None = None):
        self.name = name
        self.files = files
        self.fail = fail
        self.reads: list[str] = []

    def list_dir(self, path):
        if self.fail:
            raise self.fail
        prefix = f"{path}/" if path else ""
        out = {}
        for f in self.files:
            if f.startswith(prefix):
                rest = f[len(prefix) :]
                head = rest.split("/", 1)[0]
                out[head] = "dir" if "/" in rest else "file"
        return sorted(out.items()) or None

    def read(self, path):
        if self.fail:
            raise self.fail
        self.reads.append(path)
        return self.files.get(path)

    def url(self, path):
        return f"https://github.com/{self.name}/blob/HEAD/{path}"


def test_parse_repo_slug():
    assert parse_repo_slug("kubernetes-sigs/kueue") == "kubernetes-sigs/kueue"
    assert parse_repo_slug("https://github.com/fluxcd/flux2.git") == "fluxcd/flux2"
    assert parse_repo_slug("https://github.com/fluxcd/flux2/") == "fluxcd/flux2"
    assert parse_repo_slug("not a slug") is None
    assert parse_repo_slug("a/b/c") is None


def test_git_remote_slug_prefers_origin():
    def run(cmd, **kw):
        out = "upstream\thttps://github.com/up/stream.git (fetch)\norigin\tgit@github.com:me/fork.git (fetch)\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    assert git_remote_slug(".", run=run) == "me/fork"


def test_git_remote_slug_fallbacks():
    def other(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "mirror\thttps://github.com/a/b (fetch)\nx\n", "")

    assert git_remote_slug(".", run=other) == "a/b"

    def fails(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 128, "", "not a repo")

    assert git_remote_slug(".", run=fails) is None

    def boom(cmd, **kw):
        raise OSError("no git")

    assert git_remote_slug(".", run=boom) is None


def test_local_reader_stays_inside_root(tmp_path):
    (tmp_path / "a.md").write_text("x")
    r = LocalReader(tmp_path)
    assert r.read("a.md") == "x"
    assert r.read("../etc/passwd") is None
    assert r.list_dir("..") is None
    assert r.list_dir("missing") is None
    assert r.url("a.md") == "a.md"


def test_links_resolution():
    text = (
        "[a](docs/ai.md) [b](/top/policy.md#sec) [c](../up.md) [d](https://github.com/acme/w/blob/main/x/AI_POLICY.md)\n"
        "[e](https://github.com/other/w/blob/main/AI_POLICY.md) [f](mailto:x@y) [g](#anchor)\n"
        "[h]: sub/contrib.md\n@AGENTS.md\n"
    )
    got = _links(text, "docs/CONTRIBUTING.md", "acme/w")
    paths = [p for p, _ in got]
    assert paths == [
        "docs/docs/ai.md",
        "top/policy.md",
        "up.md",
        "x/AI_POLICY.md",
        "docs/sub/contrib.md",
        "docs/AGENTS.md",
    ]


def test_link_score():
    assert _link_score("docs/ai-usage.md", "") > _link_score("docs/contributing/review.md", "")
    assert _link_score("CODE_OF_CONDUCT.md", "AI") == 0
    assert _link_score("logo.png", "AI") == 0
    assert _link_score("README.md", "guidelines") == 0
    assert _link_score("docs/getting-started.md", "start") == 0


def test_well_known_files_in_github_and_docs_dirs():
    files = {
        "docs/CONTRIBUTING.md": "## AI\n\nYou must disclose AI use in the PR description.\n",
        ".github/AI_POLICY.md": "Do not add `Assisted-by` trailers.\n",
        ".github/copilot-instructions.md": "Never add Co-authored-by: Copilot.\n",
        "docs/AGENTS.md": "ignored: agent files only count at the root\n",
        "README.md": "readme",
    }
    found = discover(DictReader("acme/w", files), "acme/w")
    assert [s.path for s in found.sources] == [
        "docs/CONTRIBUTING.md",
        ".github/AI_POLICY.md",
        ".github/copilot-instructions.md",
    ]
    p = extract("acme/w", found.docs, found.sources)
    assert p.assisted_by == "banned" and p.co_authored_by_ai == "banned"


def test_pr_template_directory_limit():
    files = {f".github/PULL_REQUEST_TEMPLATE/t{i}.md": f"- [ ] AI used {i}\n" for i in range(5)}
    found = discover(DictReader("acme/w", files), "acme/w")
    assert len(found.sources) == 3


def test_link_budget():
    links = " ".join(f"[policy {i}](docs/policy{i}.md)" for i in range(20))
    files = {"CONTRIBUTING.md": links} | {f"docs/policy{i}.md": f"policy {i}" for i in range(20)}
    found = discover(DictReader("acme/w", files), "acme/w", max_links=4)
    assert len([s for s in found.sources if s.kind == "linked"]) == 4
    found2 = discover(DictReader("acme/w", files), "acme/w", follow_links=False)
    assert [s.path for s in found2.sources] == ["CONTRIBUTING.md"]


def test_org_dot_github_fallback():
    repo = DictReader("acme/w", {"README.md": "hi"})
    org = DictReader(
        "acme/.github",
        {
            "CONTRIBUTING.md": "## AI\n\nYou must disclose AI use in the PR description.\n",
            ".github/pull_request_template.md": "## AI\n- [ ] I used AI\n",
            "AI_POLICY.md": "Using AI tools is acceptable.",
        },
    )
    found = discover(repo, "acme/w", org_reader=lambda r: org if r == "acme/.github" else None)
    assert [(s.kind, s.path) for s in found.sources] == [
        ("org", "acme/.github:CONTRIBUTING.md"),
        ("org", "acme/.github:AI_POLICY.md"),
        ("org", "acme/.github:.github/pull_request_template.md"),
    ]
    assert found.sources[0].url == "https://github.com/acme/.github/blob/HEAD/CONTRIBUTING.md"


def test_org_fallback_not_used_when_repo_has_files():
    repo = DictReader("acme/w", {"CONTRIBUTING.md": "x", ".github/pull_request_template.md": "y"})
    calls = []
    found = discover(repo, "acme/w", org_reader=lambda r: calls.append(r) or DictReader(r, {}))
    assert calls == [] and len(found.sources) == 2


def test_org_fallback_errors():
    repo = DictReader("acme/w", {})
    found = discover(repo, "acme/w", org_reader=lambda r: DictReader(r, {}, fail=GitHubError("boom")))
    assert any("could not read acme/.github" in w for w in found.warnings)
    with pytest.raises(RateLimitError):
        discover(repo, "acme/w", org_reader=lambda r: DictReader(r, {}, fail=RateLimitError("limit")))


ENTRY = RegistryEntry(
    id="acme-ai",
    title="Acme AI guidance",
    orgs=("acme",),
    url="https://acme.dev/ai",
    repo="acme/community",
    path="guide.md",
    section="AI Guidance",
    ai_policy=True,
)
GUIDE = "# Guide\n\n## Other\n\nDo not add Assisted-by trailers.\n\n## AI Guidance\n\nYou must disclose AI use in the PR description.\n"


def test_registry_entry_applied_with_section():
    community = DictReader("acme/community", {"guide.md": GUIDE})
    found = discover(
        DictReader("acme/w", {}),
        "acme/w",
        org_reader=lambda r: community if r == "acme/community" else DictReader(r, {}),
        registry=[ENTRY],
    )
    (src,) = [s for s in found.sources if s.registry]
    assert src.path == "registry:acme/community:guide.md"
    assert src.kind == "org" and src.url == "https://acme.dev/ai" and src.title == "Acme AI guidance"
    p = extract("acme/w", found.docs, found.sources)
    assert p.assisted_by == "unspecified"  # outside the section
    assert p.disclosure_where == ["description"]
    assert p.evidence[0].path == "registry:acme/community:guide.md" and p.evidence[0].line == 9


def test_registry_entry_problems_become_warnings():
    def reader_for(files):
        return lambda r: DictReader(r, files.get(r, {}))

    missing = discover(DictReader("acme/w", {}), "acme/w", org_reader=reader_for({}), registry=[ENTRY])
    assert any("not found" in w for w in missing.warnings)
    nosec = discover(
        DictReader("acme/w", {}),
        "acme/w",
        org_reader=reader_for({"acme/community": {"guide.md": "# Guide\n"}}),
        registry=[ENTRY],
    )
    assert any("section 'AI Guidance' not found" in w for w in nosec.warnings)
    err = discover(
        DictReader("acme/w", {}),
        "acme/w",
        org_reader=lambda r: DictReader(r, {}, fail=GitHubError("503")),
        registry=[ENTRY],
    )
    assert any("could not fetch" in w for w in err.warnings)
    other = discover(DictReader("other/w", {}), "other/w", org_reader=reader_for({}), registry=[ENTRY])
    assert other.warnings == [] and other.sources == []


def test_registry_entry_skipped_when_repo_has_same_document():
    entry = RegistryEntry(id="x", title="t", orgs=("acme",), url="https://x", repo="acme/w", path="AI_POLICY.md")
    files = {"AI_POLICY.md": "Using AI tools is acceptable."}
    found = discover(DictReader("acme/w", files), "acme/w", org_reader=lambda r: DictReader(r, files), registry=[entry])
    assert [s.path for s in found.sources] == ["AI_POLICY.md"]


def test_registry_rate_limit_propagates():
    with pytest.raises(RateLimitError):
        discover(
            DictReader("acme/w", {}),
            "acme/w",
            org_reader=lambda r: DictReader(r, {}, fail=RateLimitError("x")),
            registry=[ENTRY],
        )


def test_github_reader_delegates():
    class FakeGH:
        def list_dir(self, repo, path):
            return [("CONTRIBUTING.md", "file")] if path == "" else None

        def raw(self, repo, path):
            return "## AI\n\nYou must disclose AI use in the PR description.\n"

    r = GitHubReader(FakeGH(), "acme/w")
    found = discover(r, "acme/w")
    assert found.sources[0].url == "https://github.com/acme/w/blob/HEAD/CONTRIBUTING.md"


def test_local_fixture_without_repo_name_skips_org_lookups():
    found = discover(LocalReader(FIXTURES / "k8s_style"), "", org_reader=lambda r: pytest.fail("no org lookups"))
    assert [s.path for s in found.sources] == ["CONTRIBUTING.md"]
