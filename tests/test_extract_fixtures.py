"""End-to-end extraction over synthetic repositories modelled on real 2026 policies."""

from conftest import ev, policy_for


def test_kubernetes_style_disclose_and_banned_trailers():
    p = policy_for("k8s_style")
    assert p.ai_use == "disclose"
    assert p.disclosure_required is True
    assert p.disclosure_where == ["description"]
    assert p.disclosure_example == "Parts of this PR were drafted with help from an AI assistant."
    assert p.assisted_by == "banned"
    assert p.co_authored_by_ai == "banned"
    assert p.human_only == ["review-replies", "commit-messages"]
    assert p.cla is True and p.dco is False
    # Evidence points at the exact source line.
    (tr,) = ev(p, "trailers.assisted_by")
    assert (tr.path, tr.line) == ("CONTRIBUTING.md", 18)
    assert "`assisted-by`" in tr.quote
    assert ev(p, "human_only.commit-messages")[0].line == 21
    assert ev(p, "human_only.review-replies")[0].line == 23
    assert ev(p, "disclosure.required")[0].line == 13
    assert ev(p, "guidance.explain_change")


def test_flux_style_assisted_by_required():
    p = policy_for("flux_style")
    assert p.ai_use == "disclose"
    assert p.assisted_by == "required"
    assert p.co_authored_by_ai == "banned"
    assert p.disclosure_where == ["description", "commit-trailer"]
    assert p.disclosure_example == "Assisted-by: AGENT/MODEL"
    assert p.human_only == ["review-replies"]  # the trailer sentence is not a commit-message rule
    assert p.dco is True
    lines = {e.line for e in ev(p, "trailers.assisted_by")}
    assert 16 in lines  # "You should disclose ... with an `Assisted-by` trailer"


def test_tekton_style_preferred_trailer_is_allowed_not_required():
    p = policy_for("tekton_style")
    assert p.ai_use == "disclose"
    assert p.assisted_by == "allowed"
    assert p.co_authored_by_ai == "allowed"
    assert p.disclosure_where == ["description", "commit-trailer"]
    assert p.disclosure_example == "Assisted-by: ExampleModel 2"
    # "Disclosure is not required for:" and its items do not create rules.
    assert all("not required" not in e.quote for e in ev(p, "disclosure.required"))
    assert ev(p, "guidance.size_limit")


def test_jaeger_style_headings_set_polarity_and_template_checkbox():
    p = policy_for("jaeger_style")
    assert p.ai_use == "disclose"
    assert p.disclosure_where == ["checkbox"]
    assert p.disclosure_example == "[x] Some: AI helped with parts of the code"
    assert p.checkbox_options[0].startswith("None")
    assert p.co_authored_by_ai == "allowed"
    # Only the item under "Disallowed use of AI" counts, not the one under "Good use of AI".
    assert p.human_only == ["review-replies"]
    (hr,) = ev(p, "human_only.review-replies")
    assert hr.line == 13
    assert p.dco is True


def test_radius_style_restricted_with_label_groups():
    p = policy_for("radius_style")
    assert p.ai_use == "restricted"
    assert p.required_labels == [["triaged"], ["help wanted", "good first issue"]]
    # "must not be assigned to another contributor" is not an assignment requirement.
    assert p.assignment_required is False
    assert p.issue_first is True
    assert p.dco is True and p.signed_commits is True
    assert p.ui_screenshots_required is True
    assert ev(p, "required_labels")[0].line == 7


def test_cortex_style_conditional_disclosure_and_human_authored_discussions():
    p = policy_for("cortex_style")
    assert p.ai_use == "disclose"
    assert p.disclosure_where == ["description"]
    assert p.human_only == ["review-replies", "issues"]
    # "Do not post bulk AI-generated comments..." is qualified and adds nothing new.
    assert all("bulk" not in e.quote for e in p.evidence if e.rule.startswith("human_only"))
    # Front matter is skipped but line numbers still count it.
    assert ev(p, "human_only.issues")[0].line == 23
    assert p.dco is True


def test_agent_framework_style_label_or_agreement_is_not_required_labels():
    p = policy_for("agentfw_style")
    assert p.ai_use == "disclose"
    assert p.disclosure_where == ["description", "checkbox"]
    assert p.required_labels == []
    assert p.issue_first is True
    assert p.human_only == ["review-replies"]  # "respond to review feedback themselves"
    assert "issues" not in p.human_only  # "Duplicate or low-information issues" is qualified


def test_banned():
    p = policy_for("banned")
    assert p.ai_use == "banned"
    assert [e.line for e in ev(p, "ai_use")] == [7, 7]


def test_copilot_code_review_mention_is_not_an_ai_policy():
    p = policy_for("copilot_mention")
    assert p.ai_use == "unspecified"
    assert p.co_authored_by_ai == "unspecified"  # Co-authored-by for humans is not an AI rule
    assert p.assisted_by == "unspecified"
    assert p.ui_screenshots_required is False
    assert p.evidence == []


def test_workflow_rules():
    p = policy_for("workflow")
    assert p.issue_first is True
    assert p.assignment_required is True
    assert [e.line for e in ev(p, "assignment_required")] == [6]
    assert p.dco is False  # "We don't require DCO sign-off."
    assert p.cla is False  # "we do not have a formal ... CLA"
    assert p.signed_commits is True
    assert p.ui_screenshots_required is True
    assert ev(p, "guidance.size_limit") and ev(p, "guidance.run_tests")
    assert p.ai_use == "allowed"  # AI guidance implies AI use is anticipated


def test_linked_documents_one_hop_and_dedupe():
    p = policy_for("linked", repo="acme/linked")
    paths = [(s.kind, s.path) for s in p.sources]
    assert ("repo", "CONTRIBUTING.md") in paths
    assert ("repo", "AGENTS.md") in paths
    assert ("repo", "CLAUDE.md") not in paths  # identical to AGENTS.md
    assert ("linked", "docs/policies/ai-usage.md") in paths
    assert ("linked", "docs/contributing/README.md") in paths  # directory link -> README
    assert ("linked", "docs/contributing/review.md") in paths  # same-repo github.com URL
    assert all("CODE_OF_CONDUCT" not in s.path and s.path != "DCO" for s in p.sources)
    assert all("other/repo" not in s.path for s in p.sources)
    assert p.ai_use == "disclose"
    assert p.co_authored_by_ai == "banned"  # from .github/copilot-instructions.md
    assert p.disclosure_where == ["description", "checkbox"]
