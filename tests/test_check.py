import subprocess

import pytest

from conftest import policy_for
from contribpolicy.check import Commit, git_commits, parse_commits, run_check
from contribpolicy.model import Policy

SIGNED = "Signed-off-by: Ada Example <ada@example.com>"


def commit(msg: str, sha: str = "a" * 40, sig: str = "") -> Commit:
    return Commit(sha, msg, sig)


def rules(res, severity="error"):
    return [f.rule for f in res.findings if f.severity == severity]


# --- parsing --------------------------------------------------------------------


def test_parse_commits_nul_separated_with_shas():
    text = f"{'1' * 40}\nFirst\n\n{SIGNED}\n\x00{'2' * 40}\nSecond\n\x00"
    cs = parse_commits(text)
    assert [(c.sha[:1], c.subject) for c in cs] == [("1", "First"), ("2", "Second")]
    assert cs[0].trailers() == [("Signed-off-by", "Ada Example <ada@example.com>")]


def test_parse_commits_nul_without_sha():
    assert [c.subject for c in parse_commits("one\x00two\x00")] == ["one", "two"]


def test_parse_commits_git_log_format():
    text = (
        "commit 0123456789abcdef0123456789abcdef01234567\n"
        "Author: A <a@x>\nDate:   Mon\n\n    Subject one\n\n    Assisted-by: Tool/1\n\n"
        "commit fedcba9876543210fedcba9876543210fedcba98\nAuthor: B <b@x>\n\n    Subject two\n"
    )
    cs = parse_commits(text)
    assert [c.subject for c in cs] == ["Subject one", "Subject two"]
    assert cs[0].trailers() == [("Assisted-by", "Tool/1")]
    assert cs[0].label.startswith("commit 0123456789")


def test_parse_commits_dash_separated_and_single():
    assert [c.subject for c in parse_commits("a\n---\nb\n")] == ["a", "b"]
    assert [c.subject for c in parse_commits("just one\n")] == ["just one"]
    assert parse_commits("   \n") == []
    assert Commit("", "msg").label == "commit 'msg'"


def test_git_commits_uses_git_log(tmp_path):
    def fake_run(cmd, **kw):
        assert cmd[:2] == ["git", "log"] and "A..B" in cmd
        out = f"{'a' * 40}\x1fG\x1fSubject\n\n{SIGNED}\n\x00\n{'b' * 40}\x1fN\x1fOther\n\x00"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    cs = git_commits("A..B", run=fake_run)
    assert [(c.signature, c.subject) for c in cs] == [("G", "Subject"), ("N", "Other")]


def test_git_commits_errors():
    with pytest.raises(ValueError):
        git_commits("--output=/tmp/x")

    def bad(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: bad range")

    with pytest.raises(RuntimeError, match="bad range"):
        git_commits("A..B", run=bad)


def test_git_commits_real_repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    git("init", "-q")
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
        "base",
    )
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
        "second\n\nSigned-off-by: T <t@x>",
    )
    cs = git_commits("HEAD~1..HEAD", cwd=str(tmp_path))
    assert len(cs) == 1 and cs[0].subject == "second" and cs[0].signature == "N"


# --- kubernetes-style (banned trailers) -----------------------------------------


def test_banned_trailers_and_ai_coauthor():
    p = policy_for("k8s_style")
    cs = [
        commit("Fix\n\nAssisted-by: Claude Code\nCo-authored-by: Claude <noreply@anthropic.com>"),
        commit("Docs\n\nCo-authored-by: Jane Human <jane@example.com>", sha="b" * 40),
    ]
    res = run_check(p, "This PR was partly written with help from Claude.", cs)
    assert sorted(rules(res)) == ["trailers.assisted_by", "trailers.co_authored_by_ai"]
    assert res.findings[0].evidence[0].path == "CONTRIBUTING.md"
    assert res.violations


def test_disclosure_missing_when_ai_detected():
    p = policy_for("k8s_style")
    res = run_check(
        p, "Fixes #12\n\nRefactor the parser.", [commit("x\n\nCo-authored-by: Copilot <copilot@github.com>")]
    )
    assert "disclosure" in rules(res)


def test_disclosure_unknown_is_a_warning_and_yes_is_an_error():
    p = policy_for("k8s_style")
    body = "Refactor the parser."
    assert "disclosure" in rules(run_check(p, body, []), "warning")
    assert "disclosure" in rules(run_check(p, body, [], ai_assisted="yes"))
    assert not rules(run_check(p, body, [], ai_assisted="no"))


def test_disclosure_satisfied_by_description_but_not_by_template_boilerplate():
    p = policy_for("k8s_style")
    ok = run_check(p, "Parts of this PR were drafted with help from an AI assistant.", [], ai_assisted="yes")
    assert "disclosure" not in rules(ok)
    boiler = "## AI usage\nSee the AI policy.\nAI assistance details:\n"
    assert "disclosure" in rules(run_check(p, boiler, [], ai_assisted="yes"))


def test_generated_footer_violates_human_written_commit_messages():
    p = policy_for("k8s_style")
    res = run_check(p, None, [commit("Fix\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")])
    assert "human_only.commit-messages" in rules(res)


def test_reminders_for_unverifiable_rules():
    p = policy_for("k8s_style")
    res = run_check(p, "Drafted with AI help.", [])
    rem = rules(res, "reminder")
    assert "human_only.review-replies" in rem and "cla" in rem and "guidance.explain_change" in rem


# --- flux-style (Assisted-by required, DCO) -------------------------------------------


def test_assisted_by_required_and_dco():
    p = policy_for("flux_style")
    good = commit(f"Fix\n\n{SIGNED}\nAssisted-by: agent/model")
    missing = commit("Fix\n\nSigned-off-by: Claude <noreply@anthropic.com>", sha="c" * 40)
    res = run_check(p, "Written with an AI agent's help.", [good, missing])
    errs = [(f.rule, f.where) for f in res.violations]
    assert ("trailers.assisted_by", "commit cccccccccc") in errs
    assert ("dco", "commit cccccccccc") in errs  # sign-off names an AI
    assert all(w != "commit aaaaaaaaaa" for _, w in errs)


def test_assisted_by_required_unknown_is_warning():
    p = policy_for("flux_style")
    res = run_check(p, "Plain change.", [commit(f"Fix\n\n{SIGNED}")])
    assert "trailers.assisted_by" in rules(res, "warning")
    assert not res.violations


def test_missing_sign_off():
    p = policy_for("flux_style")
    res = run_check(p, None, [commit("No sign-off")], ai_assisted="no")
    assert rules(res) == ["dco"]


def test_commit_trailer_satisfies_disclosure_channel():
    p = policy_for("tekton_style")
    res = run_check(p, "Small fix.", [commit("Fix\n\nAssisted-by: Model 2")], ai_assisted="yes")
    assert "disclosure" not in rules(res)


# --- jaeger / agent-framework (checkbox) -----------------------------------------------


TEMPLATE_BODY = """## AI usage (pick one)
- [{a}] **None**: no AI tools were used
- [{b}] **Some**: AI helped with parts of the code
- [{c}] **Most**: AI wrote most of the change
"""


def test_checkbox_unticked_is_violation():
    p = policy_for("jaeger_style")
    res = run_check(p, TEMPLATE_BODY.format(a=" ", b=" ", c=" "), [])
    assert "disclosure.where.checkbox" in rules(res)


def test_checkbox_section_missing():
    p = policy_for("jaeger_style")
    res = run_check(p, "Just a description.", [], ai_assisted="yes")
    assert "disclosure.where.checkbox" in rules(res)
    assert "disclosure" in rules(res)


def test_checkbox_ticked_ok_and_multiple_ticked_warns():
    p = policy_for("jaeger_style")
    ok = run_check(p, TEMPLATE_BODY.format(a=" ", b="x", c=" "), [])
    assert not ok.violations
    multi = run_check(p, TEMPLATE_BODY.format(a=" ", b="x", c="x"), [])
    assert "disclosure.where.checkbox" in rules(multi, "warning")


def test_none_ticked_but_ai_assisted():
    p = policy_for("jaeger_style")
    res = run_check(p, TEMPLATE_BODY.format(a="x", b=" ", c=" "), [], ai_assisted="yes")
    assert "disclosure" in rules(res)


# --- radius-style (labels, signing, screenshots) ------------------------------------------


def issue(labels, assignees=(), state="open"):
    return {"labels": [{"name": n} for n in labels], "assignees": [{"login": a} for a in assignees], "state": state}


def test_issue_reference_required_for_label_rules():
    p = policy_for("radius_style", repo="acme/radius")
    res = run_check(p, "Adds a flag.", [])
    assert "required_labels" in rules(res)


def test_labels_checked_against_linked_issue():
    p = policy_for("radius_style", repo="acme/radius")
    calls = []

    def lookup(repo, num):
        calls.append((repo, num))
        return issue(["triaged"], assignees=["someone"], state="closed")

    res = run_check(p, "Fixes #42", [], issue_lookup=lookup, author="me")
    assert calls == [("acme/radius", 42)]
    msgs = [f.message for f in res.violations]
    assert any("help wanted or good first issue" in m for m in msgs)
    assert any("is closed" in m for m in msgs)
    assert "required_labels" in rules(res, "warning")  # assigned to someone else


def test_labels_satisfied_cross_repo_url():
    p = policy_for("radius_style", repo="acme/radius")
    res = run_check(
        p,
        "Closes https://github.com/acme/radius/issues/7\n\n![shot](https://example.org/a.png)",
        [],
        issue_lookup=lambda r, n: issue(["triaged", "good first issue"]),
    )
    assert "required_labels" not in rules(res)
    assert "ui_screenshots_required" not in rules(res, "reminder")


def test_issue_lookup_failure_is_warning():
    p = policy_for("radius_style", repo="acme/radius")

    def boom(repo, num):
        raise OSError("offline")

    res = run_check(p, "Fixes #1", [], issue_lookup=boom)
    assert any("Could not look up" in f.message for f in res.findings if f.severity == "warning")


def test_signatures_known():
    p = policy_for("radius_style", repo="acme/radius")
    cs = [
        commit(f"a\n\n{SIGNED}", sig="G"),
        commit(f"b\n\n{SIGNED}", sha="b" * 40, sig="N"),
        commit(f"c\n\n{SIGNED}", sha="c" * 40, sig="U"),
    ]
    res = run_check(p, None, cs, signatures_known=True)
    assert [f.where for f in res.violations if f.rule == "signed_commits"] == ["commit bbbbbbbbbb"]
    assert "signed_commits" in rules(res, "warning")
    res2 = run_check(p, None, cs)
    assert "signed_commits" in rules(res2, "reminder")


def test_ui_change_requires_screenshot():
    p = policy_for("radius_style", repo="acme/radius")
    res = run_check(p, "Fixes #3", [], ui_change=True, issue_lookup=lambda r, n: issue(["triaged", "help wanted"]))
    assert "ui_screenshots_required" in rules(res)


# --- assignment ----------------------------------------------------------------------------


def test_assignment_checks():
    p = policy_for("workflow", repo="acme/wf")
    assert "assignment_required" in rules(run_check(p, "Fixes #5", [], issue_lookup=lambda r, n: issue([])))
    other = run_check(p, "Fixes #5", [], author="me", issue_lookup=lambda r, n: issue([], assignees=["you"]))
    assert "assignment_required" in rules(other)
    mine = run_check(p, "Fixes #5", [], author="Me", issue_lookup=lambda r, n: issue([], assignees=["me"]))
    assert "assignment_required" not in rules(mine)


def test_issue_first_only_warns():
    p = Policy(repo="acme/x", issue_first=True)
    res = run_check(p, "No issue here", [])
    assert rules(res, "warning") == ["issue_first"]


def test_to_dict_shape():
    p = policy_for("k8s_style")
    d = run_check(p, "x", [commit("a\n\nAssisted-by: X")]).to_dict()
    assert set(d) == {"repo", "ok", "ai_assisted", "violations", "warnings", "reminders"}
    assert d["ok"] is False
    assert set(d["violations"][0]) == {"rule", "severity", "message", "where", "evidence"}


def test_pr_description_footer_rules():
    p = Policy(repo="a/b", human_only=["pr-description"])
    res = run_check(p, "Body\n\nGenerated with Claude Code", [])
    assert rules(res) == ["human_only.pr-description"]
    p2 = Policy(repo="a/b", co_authored_by_ai="banned")
    assert rules(run_check(p2, "Body\n\n🤖 Generated with Claude", []), "warning") == ["trailers.co_authored_by_ai"]
