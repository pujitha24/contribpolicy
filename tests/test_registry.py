import json

import pytest

from contribpolicy.registry import RegistryError, entries_for, load_registry


def test_bundled_registry_covers_required_orgs():
    entries = load_registry()
    orgs = {o for e in entries for o in e.orgs}
    assert {"kubernetes", "kubernetes-sigs", "kubernetes-csi", "kubernetes-client", "tektoncd", "fluxcd"} <= orgs
    for e in entries:
        assert e.url.startswith("https://") and e.scope and e.verified
        assert e.source_path == f"registry:{e.repo}:{e.path}"


def test_entries_for_and_exclude(tmp_path):
    entries = load_registry()
    ids = [e.id for e in entries_for("Kubernetes-SIGs/kueue", entries)]
    assert ids == ["kubernetes-ai-guidance"]
    assert entries_for("someone/else", entries) == []
    extra = tmp_path / "r.json"
    extra.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "kubernetes-ai-guidance",
                        "title": "override",
                        "orgs": ["kubernetes-sigs"],
                        "url": "https://example.org",
                        "repo": "a/b",
                        "path": "x.md",
                        "exclude": ["kubernetes-sigs/kueue"],
                    }
                ]
            }
        )
    )
    merged = load_registry(extra)
    (k,) = [e for e in merged if e.id == "kubernetes-ai-guidance"]
    assert k.title == "override"
    assert entries_for("kubernetes-sigs/kueue", merged) == []


@pytest.mark.parametrize(
    ("data", "msg"),
    [
        ([], "entries"),
        ({"entries": ["x"]}, "not an object"),
        ({"entries": [{"id": "a"}]}, "missing"),
        (
            {"entries": [{"id": "a", "title": "t", "orgs": "k8s", "url": "https://x", "repo": "a/b", "path": "p"}]},
            "orgs",
        ),
        (
            {"entries": [{"id": "a", "title": "t", "orgs": ["k"], "url": "https://x", "repo": "ab", "path": "p"}]},
            "owner/name",
        ),
        (
            {"entries": [{"id": "a", "title": "t", "orgs": ["k"], "url": "http://x", "repo": "a/b", "path": "p"}]},
            "https",
        ),
        (
            {"entries": [{"id": "a", "title": "t", "orgs": ["k"], "url": "https://x", "repo": "a/b", "path": "p"}] * 2},
            "duplicate",
        ),
    ],
)
def test_validation(tmp_path, data, msg):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(data))
    with pytest.raises(RegistryError, match=msg):
        load_registry(f)


def test_invalid_json(tmp_path):
    f = tmp_path / "r.json"
    f.write_text("{nope")
    with pytest.raises(RegistryError, match="invalid JSON"):
        load_registry(f)
