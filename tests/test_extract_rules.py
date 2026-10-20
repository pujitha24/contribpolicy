"""Sentence-level rules, false-positive guards and edge cases."""

import pytest

from conftest import FIXTURES, ev, policy_from_text
from contribpolicy.extract import Doc, extract, role_for_path
from contribpolicy.model import Policy, Source

# --- role detection ---------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "role"),
    [
        ("AI_POLICY.md", "ai_policy"),
        ("GENAI_POLICY.md", "ai_policy"),
        ("AI_CONTRIBUTIONS.md", "ai_policy"),
        ("docs/ai-contribution-policy.md", "ai_policy"),
        ("AI_USAGE.md", "ai_policy"),
        ("LLM_POLICY.md", "ai_policy"),
        ("AGENTS.md", "agent"),
        ("CLAUDE.md", "agent"),
        (".github/copilot-instructions.md", "agent"),
        (".github/pull_request_template.md", "pr_template"),
        (".github/PULL_REQUEST_TEMPLATE/bug.md", "pr_template"),
        ("CONTRIBUTING.md", "contributing"),
        ("CONTRIBUTING_GUIDELINES.md", "contributing"),
        ("docs/AIRFLOW.md", "other"),
        ("MAINTAINERS.md", "other"),
    ],
)
def test_role_for_path(path, role):
    assert role_for_path(path) == role


# --- "AI" detection false positives -------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Do not add co-authors to the MAIL template.",
        "Please do not use the AIX toolchain to write commit messages.",
        "Our maintainers will never merge code from OpenAI-compatible forks without review.",
        "Do not let the domain.ai module answer review comments.",
    ],
)
def test_ai_inside_words_is_not_ai(sentence):
    p = policy_from_text(f"## Notes\n\n{sentence}\n")
    assert p.ai_use == "unspecified"
    assert p.human_only == []
    assert p.co_authored_by_ai == "unspecified"


def test_product_mention_alone_is_not_a_policy():
    p = policy_from_text(
        """
        # Contributing

        Copilot reviews every pull request automatically. ChatGPT was used to
        translate the README in 2024.
        """
    )
    assert p.ai_use == "unspecified"
    assert p.evidence == []


# --- Assisted-by --------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "assisted", "coauthor"),
    [
        ("may.md", "allowed", "unspecified"),
        ("banned.md", "banned", "unspecified"),
        ("instead.md", "required", "banned"),
        ("semicolon.md", "allowed", "unspecified"),
        ("neutral.md", "unspecified", "unspecified"),
    ],
)
def test_assisted_by_allowed_vs_banned(name, assisted, coauthor):
    text = (FIXTURES / "trailers_mixed" / name).read_text()
    p = extract("acme/x", [Doc(Source(name, "", "repo"), text, role="other")])
    assert p.assisted_by == assisted
    assert p.co_authored_by_ai == coauthor


def test_semicolon_clause_keeps_human_only_commit_messages():
    text = (FIXTURES / "trailers_mixed" / "semicolon.md").read_text()
    p = extract("acme/x", [Doc(Source("s.md", "", "repo"), text)])
    assert p.human_only == ["commit-messages"]


def test_assisted_by_prose_without_hyphen_is_not_a_trailer():
    p = policy_from_text("## AI\n\nThis change was assisted by AI tools, which is fine.\n")
    assert p.assisted_by == "unspecified"


def test_assisted_by_under_disallowed_heading_is_banned():
    p = policy_from_text("## Disallowed AI practices\n\n- Adding `Assisted-by` trailers to commits.\n")
    assert p.assisted_by == "banned"


def test_conflicting_sources_report_banned_and_warn():
    a = Doc(Source("a.md", "", "repo"), "## AI\n\nAdd an `Assisted-by` trailer to every commit.\n")
    b = Doc(Source("b.md", "", "org"), "## AI\n\nNever use `Assisted-by` trailers.\n")
    p = extract("acme/x", [a, b])
    assert p.assisted_by == "banned"
    assert any("disagree" in w for w in p.warnings)
    assert "commit-trailer" not in p.disclosure_where


# --- Co-authored-by -----------------------------------------------------------


def test_co_author_ai_breaks_cla_is_banned():
    p = policy_from_text("## AI\n\nA `Co-authored-by` trailer for an AI account breaks the EasyCLA check.\n")
    assert p.co_authored_by_ai == "banned"
    assert p.cla is True


def test_co_author_for_humans_outside_ai_context_is_ignored():
    p = policy_from_text("## Pairing\n\nUse `Co-authored-by` to credit the people you paired with.\n")
    assert p.co_authored_by_ai == "unspecified"


def test_co_author_in_agent_file_is_ai_context():
    p = policy_from_text("Never add a `Co-authored-by` trailer.\n", path="AGENTS.md")
    assert p.co_authored_by_ai == "banned"


# --- Disclosure ---------------------------------------------------------------


def test_disclosure_not_required_is_not_a_requirement():
    p = policy_from_text("## AI\n\nDisclosure of AI use is not required for typo fixes.\n")
    assert p.disclosure_required is False


def test_maintainers_may_request_disclosure_is_not_required():
    p = policy_from_text("## AI\n\nMaintainers may request disclosure of AI usage.\n")
    assert p.disclosure_required is False
    assert p.ai_use == "unspecified"


def test_disclosure_checkbox_in_prose():
    p = policy_from_text("## AI\n\nPlease tick the AI checkbox in the PR template when you used an LLM.\n")
    assert p.disclosure_where == ["checkbox"]


def test_disclosure_example_curly_quotes():
    p = policy_from_text(
        "## AI use\n\nYou must disclose AI use in the PR description, for example “Drafted with help from Claude.”\n"
    )
    assert p.disclosure_example == "Drafted with help from Claude."


def test_disclosure_example_without_ai_words_is_ignored():
    p = policy_from_text('## AI\n\nYou must disclose AI use in the PR body, e.g. "see the notes section below".\n')
    assert p.disclosure_example == ""
    assert p.disclosure_where == ["description"]


def test_none_checkbox_is_not_used_as_example():
    doc = Doc(
        Source(".github/pull_request_template.md", "", "repo"),
        "## AI use\n- [ ] None: no AI tools were used\n",
        role="pr_template",
    )
    p = extract("acme/x", [doc])
    assert p.disclosure_required is True
    assert p.disclosure_example == ""


def test_pr_template_checkboxes_without_ai_are_ignored():
    doc = Doc(
        Source(".github/pull_request_template.md", "", "repo"), "- [ ] Tests added\n- [ ] Docs\n", role="pr_template"
    )
    p = extract("acme/x", [doc])
    assert p.disclosure_required is False and p.disclosure_where == []


# --- Human-only ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "## AI\n\nPlease write the PR description yourself; do not have an AI tool generate it.\n",
            ["pr-description"],
        ),
        ("## AI\n\nDo not use AI to file issues.\n", ["issues"]),
        ("## AI\n\nDo not use an LLM to answer reviewer questions.\n", ["review-replies"]),
        ("## AI\n\nCommit messages must be written by a human, not AI-generated.\n", ["commit-messages"]),
        ("## AI\n\nYou may use AI to draft commit messages.\n", []),
        ("## AI\n\nDo not use AI to generate large volumes of low-quality issues.\n", []),
        ("## AI\n\nIf a requirement is not met, do not open a pull request for the issue.\n", []),
    ],
)
def test_human_only_variants(text, expected):
    assert policy_from_text(text).human_only == expected


def test_positive_heading_suppresses_human_only():
    p = policy_from_text("## Good use of AI\n\n- Using AI to help word a reply to review feedback.\n")
    assert p.human_only == []


def test_disallowed_heading_without_ai_context_does_not_flip_polarity():
    p = policy_from_text("## Disallowed\n\n- Force pushes during review.\n")
    assert p.human_only == [] and p.evidence == []


# --- Stance -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "stance"),
    [
        ("## AI\n\nWe do not accept AI-generated contributions.\n", "banned"),
        ("## AI\n\nNo AI-generated code, please.\n", "banned"),
        ("## AI\n\nThe use of AI tools is prohibited in this project.\n", "banned"),
        ("## AI\n\nDo not use AI tools.\n", "banned"),
        ("## AI\n\nNever submit AI-generated code without testing it.\n", "allowed"),
        ("## AI\n\nAI tools may be used to prepare contributions.\n", "allowed"),
        ("## AI\n\nUsing coding assistants is fine.\n", "allowed"),
        ("## AI\n\nAutomated agents may only open PRs when an issue is labelled `agent-ok`.\n", "restricted"),
        ("## AI\n\nWe only accept AI-assisted PRs from existing maintainers.\n", "restricted"),
    ],
)
def test_stance(text, stance):
    assert policy_from_text(text).ai_use == stance


def test_large_ai_prs_not_allowed_is_not_a_ban():
    p = policy_from_text("## AI\n\nLarge AI generated PRs and AI generated commit messages are not allowed.\n")
    assert p.ai_use == "allowed"
    assert p.human_only == ["commit-messages"]
    assert ev(p, "guidance.size_limit")


def test_ban_evidence_only_lists_reported_stance():
    p = policy_from_text("## AI\n\nAI tools may be used. We do not accept AI-generated contributions.\n")
    assert p.ai_use == "banned"
    assert all(e.value == "banned" for e in ev(p, "ai_use"))


# --- Labels -------------------------------------------------------------------


def test_labels_parse_single_and_quotes():
    p = policy_from_text('## Bots\n\nAutomated contributions are only accepted on issues labelled "help wanted".\n')
    assert p.required_labels == [["help wanted"]]
    assert p.ai_use == "restricted"


def test_labels_three_groups():
    p = policy_from_text(
        "## AI\n\nAI agents must only pick issues labeled `triaged`, `area/cli` and either `help wanted` or `good first issue`.\n"
    )
    assert p.required_labels == [["triaged"], ["help wanted", "good first issue"]]


def test_labels_need_ai_or_automation_context():
    p = policy_from_text("## Triage\n\nMaintainers must label issues `bug` or `feature`.\n")
    assert p.required_labels == []


# --- Workflow / legal -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "flag", "value"),
    [
        ("You must be assigned to the issue before you open a PR.", "assignment_required", True),
        ("Wait until a maintainer has assigned the issue to you.", "assignment_required", True),
        ("Unassigned pull requests are closed automatically.", "assignment_required", True),
        ("PRs for issues that are not assigned to you will be closed.", "assignment_required", True),
        ("We do not assign issues; just open a PR.", "assignment_required", False),
        ("The issue must not be assigned to someone else.", "assignment_required", False),
        ("Please open an issue first for any new feature.", "issue_first", True),
        ("Pull requests must be linked to an approved issue.", "issue_first", True),
        ("You don't need to open an issue for typos.", "issue_first", False),
        ("Start with a good first issue to learn the codebase.", "issue_first", False),
        ("To file an issue in the first place, see the issue guide.", "issue_first", False),
        ("All commits must carry a DCO sign-off.", "dco", True),
        ("We do not require a DCO.", "dco", False),
        ("Sign the Contributor License Agreement before your first PR.", "cla", True),
        ("No CLA is needed.", "cla", False),
        ("Commits must be signed with your GPG key.", "signed_commits", True),
        ("Each commit must be signed off with git commit -s.", "signed_commits", False),
        ("Run git commit -S to sign; signed commits are required.", "signed_commits", True),
        ("You can sign your commits with SSH keys if you like.", "signed_commits", False),
        ("Please include screenshots for UI changes.", "ui_screenshots_required", True),
        ("Screenshots of logs are welcome.", "ui_screenshots_required", False),
    ],
)
def test_workflow_flags(text, flag, value):
    p = policy_from_text(f"## Process\n\n{text}\n")
    assert getattr(p, flag) is value, (text, [(e.rule, e.quote) for e in p.evidence])


def test_unassigned_closed_variant():
    p = policy_from_text("## Process\n\nPull requests from contributors who are unassigned will be closed.\n")
    assert p.assignment_required is True


def test_label_assigned_is_not_person_assignment():
    p = policy_from_text(
        "## Triage\n\nThe `needs-info` label is assigned to issues that must be clarified before work.\n"
    )
    assert p.assignment_required is False


# --- Guidance and evidence ------------------------------------------------------


def test_guidance_requires_ai_context():
    p = policy_from_text("## Testing\n\nYou must run the tests locally before opening a PR.\n")
    assert ev(p, "guidance.run_tests") == []
    p2 = policy_from_text("## AI\n\nYou must run the tests locally before opening a PR.\n")
    assert ev(p2, "guidance.run_tests")


def test_guidance_in_agent_file_without_ai_words_is_ignored():
    p = policy_from_text("Run `make test` before every commit.\n", path="AGENTS.md")
    assert p.evidence == []


def test_evidence_caps_and_quote_truncation():
    long = "You must sign off every commit with the DCO " + "and keep doing so " * 30 + "."
    text = "\n\n".join(["Every commit needs a DCO sign-off."] * 5 + [long])
    p = policy_from_text(f"## Legal\n\n{text}\n")
    assert len(ev(p, "dco")) <= 2  # per-document cap
    p2 = policy_from_text(f"## Legal\n\n{long}\n")
    assert len(ev(p2, "dco")[0].quote) <= 240 and ev(p2, "dco")[0].quote.endswith("…")


def test_section_filter_only_reads_that_section():
    text = "# Guide\n\n## Commits\n\nDo not add Assisted-by trailers.\n\n## AI Guidance\n\nYou must disclose AI use in the PR description.\n"
    doc = Doc(Source("registry:a/b:guide.md", "", "org"), text, role="ai_policy", ai_scope=True, section="AI Guidance")
    p = extract("acme/x", [doc])
    assert p.assisted_by == "unspecified"
    assert p.disclosure_where == ["description"]
    assert ev(p, "disclosure.required")[0].line == 9


def test_to_dict_has_exact_schema_keys():
    p = policy_from_text("## AI\n\nYou must disclose AI use in the PR description.\n")
    d = p.to_dict()
    assert list(d) == [
        "version",
        "repo",
        "sources",
        "ai_use",
        "disclosure",
        "trailers",
        "human_only",
        "assignment_required",
        "issue_first",
        "required_labels",
        "dco",
        "cla",
        "signed_commits",
        "ui_screenshots_required",
        "evidence",
    ]
    assert list(d["disclosure"]) == ["required", "where", "example"]
    assert list(d["trailers"]) == ["assisted_by", "co_authored_by_ai"]
    assert list(d["evidence"][0]) == ["rule", "quote", "path", "line"]
    assert list(d["sources"][0]) == ["path", "url", "kind"]


def test_note_that_is_not_a_disclosure_requirement():
    p = policy_from_text("## AI agents\n\nNote that the `Signed-off-by` line must use your real name.\n")
    assert p.disclosure_required is False


def test_evidence_is_grouped_by_rule_in_document_order():
    p = policy_from_text(
        "## AI\n\nEvery commit needs a DCO sign-off.\n\nYou must disclose AI use in the PR description.\n\n"
        "Do not add `Assisted-by` trailers.\n"
    )
    rules = [e.rule for e in p.evidence]
    assert rules.index("disclosure.required") < rules.index("trailers.assisted_by") < rules.index("dco")


# --- 0.1.1: CLA / DCO false positives -------------------------------------------


APACHE_HEADER = """\
<!--
  Licensed to the Apache Software Foundation (ASF) under one or more
  contributor license agreements.  See the NOTICE file distributed with
  this work for additional information regarding copyright ownership.
  The ASF licenses this file to You under the Apache License, Version 2.0.
-->

# Contributing

Thanks for helping out! Please open an issue first for any new feature.
"""


def test_apache_license_header_is_not_a_cla_requirement():
    p = policy_from_text(APACHE_HEADER)
    assert p.cla is False
    assert ev(p, "cla") == [] and ev(p, "cla.conditional") == []


def test_apache_license_header_does_not_hide_a_real_cla_rule():
    p = policy_from_text(APACHE_HEADER + "\nYou must sign the Contributor License Agreement before your first PR.\n")
    assert p.cla is True and p.cla_conditional is False
    assert ev(p, "cla")[0].quote.startswith("You must sign")


@pytest.mark.parametrize(
    "text",
    [
        "We use DCO instead of a CLA.",
        "Instead of a CLA, we use the Developer Certificate of Origin.",
        "This project uses the DCO rather than a Contributor License Agreement.",
        "In 2024 we switched from a CLA to the DCO; sign off every commit.",
        "The DCO sign-off replaces the CLA.",
    ],
)
def test_dco_instead_of_cla_is_not_a_cla(text):
    p = policy_from_text(f"## Legal\n\n{text}\n")
    assert p.cla is False, [(e.rule, e.quote) for e in p.evidence]
    assert p.dco is True


def test_cla_instead_of_dco_is_not_a_dco():
    p = policy_from_text("## Legal\n\nWe ask for a signed CLA instead of a DCO sign-off.\n")
    assert p.cla is True
    assert p.dco is False


def test_maintainer_sign_off_is_not_the_dco():
    p = policy_from_text(
        "## Checks\n\nThe linter warns and never blocks; promote it to a gate only with maintainer sign-off.\n"
    )
    assert p.dco is False
    p2 = policy_from_text("## Releases\n\nA release needs sign-off from a maintainer.\n")
    assert p2.dco is False


@pytest.mark.parametrize(
    "text",
    [
        "A CLA is only required for larger changes.",
        "For significant contributions, you must sign our Contributor License Agreement.",
        "Trivial changes such as typo fixes do not require a CLA.",
        "No CLA is needed for small fixes; anything bigger needs one.",
        "Everyone must sign the CLA, except for trivial fixes.",
    ],
)
def test_conditional_cla_is_reported_as_conditional(text):
    p = policy_from_text(f"## Legal\n\n{text}\n")
    assert p.cla is True and p.cla_conditional is True, [(e.rule, e.quote) for e in p.evidence]
    assert ev(p, "cla") == [] and len(ev(p, "cla.conditional")) == 1
    # The shared schema keeps `cla` a boolean; the condition travels as evidence.
    d = p.to_dict()
    assert d["cla"] is True
    assert [e["rule"] for e in d["evidence"]] == ["cla.conditional"]


def test_blanket_cla_wins_over_a_conditional_one():
    p = policy_from_text(
        "## Legal\n\nAll contributors must sign the CLA.\n\nA second CLA is only required for larger changes.\n"
    )
    assert p.cla is True and p.cla_conditional is False


def test_conditional_cla_survives_json_round_trip():
    p = policy_from_text("## Legal\n\nA CLA is only required for larger changes.\n")
    q = Policy.from_dict(p.to_dict())
    assert q.cla is True and q.cla_conditional is True
    blanket = Policy.from_dict(policy_from_text("## Legal\n\nSign the CLA first.\n").to_dict())
    assert blanket.cla is True and blanket.cla_conditional is False


# --- 0.1.1: generic AI-attribution trailer bans ------------------------------------


@pytest.mark.parametrize(
    ("text", "path"),
    [
        ("## Commits\n\n- No AI attribution/authorship trailers.\n", "CONTRIBUTING.md"),
        ("## Git\n\n- No AI attribution or authorship trailers in commit messages.\n", "AGENTS.md"),
        ("## Git commit conventions\n\n- Never add any AI-attribution trailers to commit messages.\n", "AGENTS.md"),
        ("## AI tools\n\nDo not add AI trailers of any kind to your commits.\n", "CONTRIBUTING.md"),
    ],
)
def test_generic_ai_trailer_ban_bans_assisted_by_and_co_author(text, path):
    p = policy_from_text(text, path=path)
    assert p.assisted_by == "banned", [(e.rule, e.quote) for e in p.evidence]
    assert p.co_authored_by_ai == "banned"


def test_openmetadata_style_co_author_and_attribution_trailers():
    # Modelled on OpenMetadata's AGENTS.md: the co-author ban is explicit, the Assisted-by ban is generic.
    p = policy_from_text(
        '## Git commit conventions\n\n- Never add "Co-Authored-By" lines or any AI-attribution trailers to commit '
        "messages.\n- The commit is authored by me; you are a tool, not a co-author.\n",
        path="AGENTS.md",
    )
    assert p.assisted_by == "banned"
    assert p.co_authored_by_ai == "banned"
    assert not p.warnings


def test_generic_trailer_ban_keeps_an_explicitly_allowed_co_author():
    p = policy_from_text(
        "## AI tools\n\nYou may credit your AI tool with `Co-authored-by`; do not add other AI-attribution trailers.\n"
    )
    assert p.assisted_by == "banned"
    assert p.co_authored_by_ai == "allowed"


def test_attribution_trailers_outside_ai_context_are_ignored():
    p = policy_from_text("## Commits\n\nDo not add credit trailers for reviewers.\n")
    assert p.assisted_by == "unspecified" and p.co_authored_by_ai == "unspecified"
