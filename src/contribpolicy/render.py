"""Output formats: table (default), json, yaml, markdown and the agent block."""

from __future__ import annotations

import json
import shutil
import textwrap

from . import yamlout
from ._meta import HOMEPAGE, TOOL_NAME, VERSION
from .model import Evidence, Policy, Source

__all__ = ["FORMATS", "evidence_link", "render", "render_agents_md"]

FORMATS = ("table", "json", "yaml", "markdown")

AI_USE_TEXT = {
    "allowed": "AI assistance is allowed",
    "disclose": "AI assistance is allowed if you disclose it",
    "restricted": "AI/automated contributions only under stated conditions",
    "banned": "AI-generated contributions are not accepted",
    "unspecified": "no AI policy found",
}

HUMAN_ONLY_TEXT = {
    "review-replies": "replies to review comments",
    "commit-messages": "commit messages",
    "pr-description": "the PR description",
    "issues": "issues",
}

GUIDANCE_TEXT = {
    "guidance.run_tests": "Run the tests locally before opening or updating the PR.",
    "guidance.explain_change": "Be able to explain every line of the change yourself.",
    "guidance.size_limit": "Keep PRs small and focused; split large AI-generated changes.",
    "guidance.review_ai_output": "Review and verify AI output yourself before asking for review.",
}

DISCLAIMER = "Heuristic extraction: always read the sources before relying on this. Not legal advice."


def _source_for(policy: Policy, path: str) -> Source | None:
    for s in policy.sources:
        if s.path == path:
            return s
    return None


def evidence_link(policy: Policy, ev: Evidence) -> str:
    """Best link to the evidence line (GitHub blob URL with #L anchor when possible)."""
    path = ev.path
    if path.startswith("registry:"):
        _, repo, p = path.split(":", 2)
        return f"https://github.com/{repo}/blob/HEAD/{p}#L{ev.line}"
    if ":" in path and "/.github:" in path:
        repo, p = path.split(":", 1)
        return f"https://github.com/{repo}/blob/HEAD/{p}#L{ev.line}"
    src = _source_for(policy, path)
    if src and src.url.startswith("https://github.com/"):
        return f"{src.url}#L{ev.line}"
    return f"{path}:{ev.line}"


def _kind_label(s: Source) -> str:
    if s.path.startswith("registry:"):
        return "org registry"
    return s.kind


def _labels_text(groups: list[list[str]]) -> str:
    parts = []
    for g in groups:
        inner = " OR ".join(f"`{x}`" for x in g)
        parts.append(f"({inner})" if len(g) > 1 else inner)
    return " AND ".join(parts)


def _yn(b: bool) -> str:
    return "yes" if b else "no"


# --------------------------------------------------------------------------
# table
# --------------------------------------------------------------------------


def _summary_rows(p: Policy) -> list[tuple[str, str]]:
    rows = [("AI use", f"{p.ai_use.upper()}  ({AI_USE_TEXT[p.ai_use]})")]
    if p.disclosure_required or p.disclosure_where:
        where = ", ".join(p.disclosure_where) or "(not stated)"
        rows.append(("Disclosure", f"{'required' if p.disclosure_required else 'mentioned'} in: {where}"))
        if p.disclosure_example:
            rows.append(("  example", f'"{p.disclosure_example}"'))
    else:
        rows.append(("Disclosure", "not required (none found)"))
    rows.append(("Assisted-by trailer", p.assisted_by))
    rows.append(("AI Co-authored-by", p.co_authored_by_ai))
    rows.append(("Human-only", ", ".join(p.human_only) or "-"))
    rows.append(("Issue first", _yn(p.issue_first)))
    rows.append(("Assignment required", _yn(p.assignment_required)))
    rows.append(("Required labels", _labels_text(p.required_labels) or "-"))
    rows.append(
        (
            "DCO / CLA / signed",
            f"DCO {_yn(p.dco)}  ·  CLA {_yn(p.cla)}  ·  signed+verified commits {_yn(p.signed_commits)}",
        )
    )
    rows.append(("UI screenshots", "required" if p.ui_screenshots_required else "-"))
    return rows


def render_table(p: Policy, width: int | None = None) -> str:
    width = width or min(shutil.get_terminal_size((100, 24)).columns, 120)
    out: list[str] = [f"{TOOL_NAME} · {p.repo or '(local)'}", ""]
    label_w = max(len(k) for k, _ in _summary_rows(p)) + 2
    for k, v in _summary_rows(p):
        wrapped = textwrap.wrap(v, max(width - label_w, 30)) or [""]
        out.append(k.ljust(label_w) + wrapped[0])
        out.extend(" " * label_w + w for w in wrapped[1:])
    out += ["", "Sources"]
    if not p.sources:
        out.append("  (no contribution documents found)")
    for s in p.sources:
        lab = f"[{_kind_label(s)}]"
        title = f"  {s.title}" if s.title and s.path.startswith("registry:") else ""
        out.append(f"  {lab:<15}{s.path}{title}")
        if s.url and s.url != s.path:
            out.append(f"  {'':<15}{s.url}")
    if p.evidence:
        out += ["", "Evidence"]
        for e in p.evidence:
            out.append(f"  {e.rule}  ({e.path}:{e.line})")
            for w in textwrap.wrap(f'"{e.quote}"', max(width - 6, 30)):
                out.append(f"      {w}")
    if p.warnings:
        out += ["", "Warnings"]
        out += [f"  ! {w}" for w in p.warnings]
    out += ["", DISCLAIMER]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_markdown(p: Policy) -> str:
    out = [f"## Contribution policy: `{p.repo or '(local)'}`", ""]
    out += ["| Rule | Value |", "| --- | --- |"]
    for k, v in _summary_rows(p):
        out.append(f"| {_md_escape(k.strip())} | {_md_escape(v)} |")
    out += ["", "### Sources", ""]
    for s in p.sources:
        link = f"[{s.path}]({s.url})" if s.url.startswith("http") else f"`{s.path}`"
        extra = f" ({s.title})" if s.title and s.path.startswith("registry:") else ""
        out.append(f"- **{_kind_label(s)}**: {link}{extra}")
    if not p.sources:
        out.append("- no contribution documents found")
    if p.evidence:
        out += ["", "### Evidence", "", "| Rule | Quote | Where |", "| --- | --- | --- |"]
        for e in p.evidence:
            link = evidence_link(p, e)
            where = f"[{e.path}:{e.line}]({link})" if link.startswith("http") else f"`{e.path}:{e.line}`"
            out.append(f"| `{e.rule}` | {_md_escape(e.quote)} | {where} |")
    if p.warnings:
        out += ["", "### Warnings", ""] + [f"- {w}" for w in p.warnings]
    out += ["", f"_{DISCLAIMER} Generated by [{TOOL_NAME}]({HOMEPAGE}) {VERSION}._", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------
# agents-md
# --------------------------------------------------------------------------


def _cite(p: Policy, rule_prefix: str) -> str:
    evs = p.evidence_for(rule_prefix)
    if not evs:
        return ""
    return f" ({evidence_link(p, evs[0])})"


def render_agents_md(p: Policy) -> str:
    """A short, imperative block an agent can paste into its context."""
    lines: list[str] = []

    def add(text: str, rule: str) -> None:
        lines.append(f"- {text}{_cite(p, rule)}")

    repo = p.repo or "this repository"
    if p.ai_use == "banned":
        add(f"STOP: {repo} does not accept AI-generated contributions. Do not open a PR.", "ai_use")
    elif p.ai_use == "restricted":
        add("AI/automated contributions are restricted; follow the conditions below exactly.", "ai_use")
    if p.required_labels:
        add(
            f"Only work on open issues labelled {_labels_text(p.required_labels)}, that are not assigned to "
            "someone else and have no open PR; re-check right before opening the PR.",
            "required_labels",
        )
    if p.assignment_required:
        add("Get the issue assigned to the human contributor before opening a PR.", "assignment_required")
    if p.issue_first:
        add("For non-trivial changes, open or find an issue and agree on the approach first.", "issue_first")
    if p.disclosure_required or p.disclosure_where:
        if "description" in p.disclosure_where:
            ex = p.disclosure_example
            ex_text = f' Example: "{ex}"' if ex and not ex.startswith(("[x]", "Assisted-by")) else ""
            add(f"Disclose AI assistance in the PR description.{ex_text}", "disclosure.where.description")
        if "checkbox" in p.disclosure_where:
            add("Tick the AI-usage checkbox in the PR template (exactly one option).", "disclosure.where.checkbox")
        if "commit-trailer" in p.disclosure_where and p.assisted_by != "required":
            add(
                "An `Assisted-by: <agent>/<model>` commit trailer is an accepted way to disclose.",
                "trailers.assisted_by",
            )
        if not p.disclosure_where:
            add("Disclose AI assistance.", "disclosure")
    if p.assisted_by == "required":
        add("Add an `Assisted-by: <agent>/<model>` trailer to every commit.", "trailers.assisted_by")
    elif p.assisted_by == "banned":
        add("Do NOT add `Assisted-by`, `Co-developed-by` or similar AI commit trailers.", "trailers.assisted_by")
    if p.co_authored_by_ai == "banned":
        add("Do NOT list an AI tool as co-author (no `Co-authored-by: <AI>` lines).", "trailers.co_authored_by_ai")
    for h in p.human_only:
        add(f"A human must write {HUMAN_ONLY_TEXT[h]}; do not generate or post them.", "human_only." + h)
    if p.dco:
        add("Sign off every commit (`git commit -s`) with the human's name and email, never the agent's.", "dco")
    if p.cla:
        add("The human contributor must have signed the CLA.", "cla")
    if p.signed_commits:
        add("Commits must be cryptographically signed (GPG/SSH) and show as Verified.", "signed_commits")
    if p.ui_screenshots_required:
        add("Include screenshots or recordings for UI changes.", "ui_screenshots_required")
    for rule, text in GUIDANCE_TEXT.items():
        if p.evidence_for(rule):
            add(text, rule)

    head = [
        f"<!-- generated by {TOOL_NAME} {VERSION}; heuristic, verify against the sources -->",
        f"## Contribution rules for {repo}",
        "",
    ]
    if not lines:
        lines.append(
            "- No AI-specific contribution rules were found. Follow CONTRIBUTING and ask maintainers when unsure."
        )
    tail = ["", "Sources:"]
    for s in p.sources:
        label = "org registry" if s.path.startswith("registry:") else s.kind
        tail.append(f"- [{label}] {s.url or s.path}")
    return "\n".join(head + lines + tail) + "\n"


def render(p: Policy, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(p.to_dict(), indent=2, ensure_ascii=False) + "\n"
    if fmt == "yaml":
        return yamlout.dump(p.to_dict())
    if fmt == "markdown":
        return render_markdown(p)
    if fmt == "table":
        return render_table(p)
    raise ValueError(f"unknown format {fmt!r}")
