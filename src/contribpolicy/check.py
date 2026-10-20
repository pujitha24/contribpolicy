"""Lint a draft pull request (description + commit messages) against a policy.

The checks are deliberately literal: they look for trailers, sign-offs,
ticked checkboxes and issue references. Rules that cannot be verified from
text (e.g. "write review replies yourself") are reported as reminders, never
as violations.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .model import Evidence, Policy

__all__ = [
    "AI_IDENTITY",
    "CheckResult",
    "Commit",
    "Finding",
    "git_commits",
    "parse_commits",
    "run_check",
]

AI_IDENTITY = re.compile(
    r"(?i)\b(?:claude|anthropic|copilot|chatgpt|openai|gpt-?\d[\w.]*|codex|gemini|bard|cursor(?:\s*agent)?|devin|aider|"
    r"windsurf|codeium|tabnine|codewhisperer|amazon\s+q|jules|sweep|cline|roo\s*code|opencode|"
    r"ai\s+(?:assistant|agent)|llm)\b|\[bot\]|noreply@anthropic\.com|@openai\.com"
)
_AI_MENTION = re.compile(
    r"\b(?:AI|LLMs?|GenAI)\b|(?i:\b(?:generative|copilot|claude|chatgpt|codex|gemini|cursor|devin|aider|windsurf|"
    r"assisted-by|ai-assisted|coding\s+agent|language\s+model)\b)"
)
_GENERATED_FOOTER = re.compile(
    r"(?i)generated\s+(?:with|by|using)\s+\[?(?:claude|copilot|chatgpt|codex|cursor|gemini|an?\s+AI|AI)\b|🤖\s*generated"
)
_TRAILER = re.compile(r"^\s*([A-Za-z][A-Za-z0-9-]*)\s*:\s*(.+?)\s*$")
_AI_TRAILERS = {"assisted-by", "co-developed-by", "generated-by", "ai-assisted-by", "ai-assistant", "made-with"}
_CHECKBOX = re.compile(r"^\s*[-*+]\s*\[( |x|X)\]\s*(.+)$")
_NONE_OPTION = re.compile(r"(?i)^\W*(?:none\b|no\s+(?:material\s+)?AI\b|no\s+AI\s+tools?\b|I\s+did\s+not\s+use)")
_ISSUE_REF = re.compile(r"(?:(?<![\w/])([\w.-]+/[\w.-]+))?#(\d+)\b|https?://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)|<img\s|<video\s|\.(?:png|jpe?g|gif|webp|mp4|mov)\b|user-attachments/", re.I)


@dataclass
class Commit:
    sha: str
    message: str
    signature: str = ""  # git %G? status when known

    @property
    def subject(self) -> str:
        return self.message.strip().splitlines()[0] if self.message.strip() else ""

    @property
    def label(self) -> str:
        return f"commit {self.sha[:10]}" if self.sha else f"commit '{self.subject[:50]}'"

    def trailers(self) -> list[tuple[str, str]]:
        out = []
        for line in self.message.splitlines():
            m = _TRAILER.match(line)
            if m and ("-" in m.group(1) or m.group(1).lower() in _AI_TRAILERS):
                out.append((m.group(1), m.group(2)))
        return out


@dataclass
class Finding:
    rule: str
    message: str
    where: str
    severity: str = "error"  # error | warning | reminder
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "where": self.where,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class CheckResult:
    repo: str
    findings: list[Finding] = field(default_factory=list)
    ai_assisted: str = "auto"

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "ok": not self.violations,
            "ai_assisted": self.ai_assisted,
            "violations": [f.to_dict() for f in self.findings if f.severity == "error"],
            "warnings": [f.to_dict() for f in self.findings if f.severity == "warning"],
            "reminders": [f.to_dict() for f in self.findings if f.severity == "reminder"],
        }


def parse_commits(text: str) -> list[Commit]:
    """Parse commit messages from a file.

    Accepted layouts (auto-detected):
    * NUL-separated messages, each optionally starting with a 40-hex SHA line
      (what ``git log --format=%H%n%B%x00`` prints);
    * plain ``git log`` output (``commit <sha>`` headers, indented bodies);
    * messages separated by a line containing only ``---``;
    * a single message.
    """
    if not text.strip():
        return []
    commits: list[Commit] = []
    if "\x00" in text:
        for chunk in text.split("\x00"):
            if not chunk.strip():
                continue
            chunk = chunk.lstrip("\n")
            first, _, rest = chunk.partition("\n")
            if re.fullmatch(r"[0-9a-f]{40}", first.strip()):
                commits.append(Commit(first.strip(), rest.strip("\n")))
            else:
                commits.append(Commit("", chunk.strip("\n")))
        return commits
    if re.search(r"(?m)^commit [0-9a-f]{7,40}\b", text):
        blocks = re.split(r"(?m)^(?=commit [0-9a-f]{7,40}\b)", text)
        for b in blocks:
            if not b.strip():
                continue
            lines = b.splitlines()
            sha = lines[0].split()[1]
            body = []
            for ln in lines[1:]:
                if ln.startswith("    "):
                    body.append(ln[4:])
                elif not ln.strip():
                    body.append("")
            commits.append(Commit(sha, "\n".join(body).strip("\n")))
        return commits
    parts = re.split(r"(?m)^---\s*$", text)
    return [Commit("", p.strip("\n")) for p in parts if p.strip()]


def git_commits(
    rev_range: str, cwd: str | None = None, run: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> list[Commit]:
    """Read the non-merge commits in ``A..B`` with their messages and signature status."""
    if rev_range.startswith("-"):
        raise ValueError(f"invalid revision range: {rev_range!r}")
    out = run(
        ["git", "log", "--no-merges", "--format=%H%x1f%G?%x1f%B%x00", rev_range, "--"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"git log {rev_range} failed: {out.stderr.strip()}")
    commits = []
    for chunk in out.stdout.split("\x00"):
        chunk = chunk.lstrip("\n")
        if not chunk.strip():
            continue
        sha, sig, msg = [*chunk.split("\x1f", 2), "", ""][:3]
        commits.append(Commit(sha.strip(), msg.strip("\n"), sig.strip()))
    return commits


def _ai_checkboxes(body: str) -> list[tuple[bool, str]]:
    """Checkbox lines in the PR body that are about AI use."""
    out = []
    in_ai_section = False
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            in_ai_section = bool(_AI_MENTION.search(line))
            continue
        m = _CHECKBOX.match(line)
        if m and (in_ai_section or _AI_MENTION.search(m.group(2))):
            out.append((m.group(1) in "xX", m.group(2).strip()))
    return out


_USE_VERB = re.compile(
    r"(?i)\b(?:assist\w*|generat\w*|writ\w*|wrote|used?|using|help\w*|draft\w*|creat\w*|produc\w*|suggest\w*|with)\b"
)


def _describes_ai_use(body: str) -> bool:
    """True when a line of prose in the PR body says AI was (or was not) used.

    Template boilerplate is ignored: headings, checkbox lines, empty
    "AI assistance details:" prompts and links to the policy itself.
    """
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or _CHECKBOX.match(line) or s.endswith(":"):
            continue
        if re.search(r"(?i)\bpolicy\b", s):
            continue
        if _AI_MENTION.search(s) and _USE_VERB.search(s):
            return True
    return False


def _strip_html_comments(s: str) -> str:
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def run_check(
    policy: Policy,
    pr_body: str | None,
    commits: list[Commit],
    *,
    ai_assisted: str = "auto",
    issue_lookup: Callable[[str, int], dict[str, Any] | None] | None = None,
    author: str | None = None,
    ui_change: bool = False,
    signatures_known: bool = False,
) -> CheckResult:
    res = CheckResult(repo=policy.repo, ai_assisted=ai_assisted)
    add = res.findings.append
    body = _strip_html_comments(pr_body or "")
    have_body = pr_body is not None

    # --- Did the author use AI? --------------------------------------------
    ai_trailer_commits = [
        c
        for c in commits
        if any(
            k.lower() in _AI_TRAILERS or (k.lower() == "co-authored-by" and AI_IDENTITY.search(v))
            for k, v in c.trailers()
        )
    ]
    footer_commits = [c for c in commits if _GENERATED_FOOTER.search(c.message)]
    boxes = _ai_checkboxes(body) if have_body else []
    ticked = [t for ok, t in boxes if ok]
    ticked_ai = [t for t in ticked if not _NONE_OPTION.search(t)]
    body_mentions_ai = have_body and _describes_ai_use(body)
    detected = bool(ai_trailer_commits or footer_commits or ticked_ai or body_mentions_ai)
    if ai_assisted == "yes":
        used = True
    elif ai_assisted == "no":
        used = False
    else:
        used = detected
    unknown = ai_assisted == "auto" and not detected

    def ev(prefix: str) -> list[Evidence]:
        return policy.evidence_for(prefix)[:2]

    # --- Trailers -------------------------------------------------------------
    for c in commits:
        for k, v in c.trailers():
            kl = k.lower()
            if kl == "co-authored-by" and AI_IDENTITY.search(v) and policy.co_authored_by_ai == "banned":
                add(
                    Finding(
                        "trailers.co_authored_by_ai",
                        f"AI co-author trailer is not allowed: '{k}: {v}'",
                        c.label,
                        evidence=ev("trailers.co_authored_by_ai"),
                    )
                )
            if kl in _AI_TRAILERS and policy.assisted_by == "banned":
                add(
                    Finding(
                        "trailers.assisted_by",
                        f"AI attribution trailer is not allowed: '{k}: {v}'",
                        c.label,
                        evidence=ev("trailers.assisted_by"),
                    )
                )
            if kl == "signed-off-by" and AI_IDENTITY.search(v):
                add(
                    Finding(
                        "dco",
                        f"Sign-off names an AI tool ('{v}'); only a human can certify the DCO",
                        c.label,
                        evidence=ev("dco"),
                    )
                )
    if policy.assisted_by == "required" and commits:
        missing = [c for c in commits if not any(k.lower() == "assisted-by" for k, _ in c.trailers())]
        for c in missing:
            if used:
                add(
                    Finding(
                        "trailers.assisted_by",
                        "Missing 'Assisted-by: <agent>/<model>' trailer",
                        c.label,
                        evidence=ev("trailers.assisted_by"),
                    )
                )
            elif unknown:
                add(
                    Finding(
                        "trailers.assisted_by",
                        "No 'Assisted-by' trailer; required if AI tools helped with this commit",
                        c.label,
                        severity="warning",
                        evidence=ev("trailers.assisted_by"),
                    )
                )

    # --- Disclosure -----------------------------------------------------------
    if policy.disclosure_required and have_body:
        where = set(policy.disclosure_where) or {"description"}
        satisfied = []
        if "description" in where and body_mentions_ai:
            satisfied.append("description")
        if "checkbox" in where and ticked:
            satisfied.append("checkbox")
        if (
            "commit-trailer" in where
            and commits
            and all(any(k.lower() == "assisted-by" for k, _ in c.trailers()) for c in commits)
        ):
            satisfied.append("commit-trailer")
        if "checkbox" in where and boxes and not ticked:
            add(
                Finding(
                    "disclosure.where.checkbox",
                    "The AI-usage checkbox in the PR template is not ticked",
                    "PR description",
                    evidence=ev("disclosure.where.checkbox"),
                )
            )
        elif "checkbox" in where and policy.checkbox_options and not boxes:
            add(
                Finding(
                    "disclosure.where.checkbox",
                    "The PR description is missing the template's AI-usage checkboxes",
                    "PR description",
                    evidence=ev("disclosure.where.checkbox"),
                )
            )
        if len(ticked) > 1:
            add(
                Finding(
                    "disclosure.where.checkbox",
                    "More than one AI-usage option is ticked; tick exactly one",
                    "PR description",
                    severity="warning",
                    evidence=ev("disclosure.where.checkbox"),
                )
            )
        if used and ticked and not ticked_ai and ai_assisted == "yes":
            add(
                Finding(
                    "disclosure",
                    "The ticked AI-usage option says no AI was used, but this PR is AI-assisted",
                    "PR description",
                    evidence=ev("disclosure"),
                )
            )
        if used and not satisfied:
            add(
                Finding(
                    "disclosure",
                    f"AI assistance is not disclosed (expected in: {', '.join(sorted(where))})",
                    "PR description",
                    evidence=ev("disclosure"),
                )
            )
        elif unknown and not satisfied:
            add(
                Finding(
                    "disclosure",
                    "No AI disclosure found; this repository requires one if AI tools were used",
                    "PR description",
                    severity="warning",
                    evidence=ev("disclosure"),
                )
            )

    # --- Human-only text ------------------------------------------------------
    if "commit-messages" in policy.human_only:
        for c in footer_commits:
            add(
                Finding(
                    "human_only.commit-messages",
                    "Commit message carries an AI 'Generated with ...' footer",
                    c.label,
                    evidence=ev("human_only.commit-messages"),
                )
            )
    if "pr-description" in policy.human_only and have_body and _GENERATED_FOOTER.search(body):
        add(
            Finding(
                "human_only.pr-description",
                "PR description carries an AI 'Generated with ...' footer",
                "PR description",
                evidence=ev("human_only.pr-description"),
            )
        )
    elif have_body and _GENERATED_FOOTER.search(body) and policy.co_authored_by_ai == "banned":
        add(
            Finding(
                "trailers.co_authored_by_ai",
                "PR description carries an AI authorship footer",
                "PR description",
                severity="warning",
                evidence=ev("trailers.co_authored_by_ai"),
            )
        )
    if "review-replies" in policy.human_only:
        add(
            Finding(
                "human_only.review-replies",
                "Write replies to review comments yourself, without AI tools",
                "review",
                severity="reminder",
                evidence=ev("human_only.review-replies"),
            )
        )
    if "issues" in policy.human_only:
        add(
            Finding(
                "human_only.issues",
                "Issues must be written by a human",
                "issues",
                severity="reminder",
                evidence=ev("human_only.issues"),
            )
        )

    # --- DCO / signing --------------------------------------------------------
    if policy.dco:
        for c in commits:
            if not any(k.lower() == "signed-off-by" for k, _ in c.trailers()):
                add(Finding("dco", "Missing 'Signed-off-by:' line (git commit -s)", c.label, evidence=ev("dco")))
    if policy.signed_commits:
        if signatures_known:
            for c in commits:
                if c.signature in ("N", ""):
                    add(
                        Finding(
                            "signed_commits",
                            "Commit is not cryptographically signed",
                            c.label,
                            evidence=ev("signed_commits"),
                        )
                    )
                elif c.signature not in ("G",):
                    add(
                        Finding(
                            "signed_commits",
                            f"Signature status '{c.signature}' (not a good, verified signature locally)",
                            c.label,
                            severity="warning",
                            evidence=ev("signed_commits"),
                        )
                    )
        else:
            add(
                Finding(
                    "signed_commits",
                    "Commits must be signed and verified (cannot check without --git-range)",
                    "commits",
                    severity="reminder",
                    evidence=ev("signed_commits"),
                )
            )
    if policy.cla:
        msg = (
            "The project asks for a CLA only for some changes (see the source); check whether this one needs it"
            if policy.cla_conditional
            else "The human author must have signed the CLA"
        )
        add(Finding("cla", msg, "author", severity="reminder", evidence=ev("cla")))

    # --- Issues, labels, assignment ------------------------------------------
    needs_issue = policy.issue_first or policy.assignment_required or bool(policy.required_labels)
    refs: list[tuple[str, int]] = []
    if have_body:
        for m in _ISSUE_REF.finditer(body):
            if m.group(4):
                refs.append((m.group(3), int(m.group(4))))
            else:
                refs.append((m.group(1) or policy.repo, int(m.group(2))))
    if needs_issue and have_body and not refs:
        strict = policy.assignment_required or bool(policy.required_labels)
        rule = (
            "required_labels"
            if policy.required_labels
            else ("assignment_required" if policy.assignment_required else "issue_first")
        )
        add(
            Finding(
                rule,
                "The PR description does not reference an issue (e.g. 'Fixes #123')",
                "PR description",
                severity="error" if strict else "warning",
                evidence=ev(rule),
            )
        )
    if refs and issue_lookup and (policy.required_labels or policy.assignment_required):
        repo, num = refs[0]
        issue = None
        try:
            issue = issue_lookup(repo, num)
        except Exception as e:  # network trouble must not crash a pre-push hook
            add(Finding("required_labels", f"Could not look up {repo}#{num}: {e}", "issue", severity="warning"))
        if issue is not None:
            labels = {(lb.get("name") or "").lower() for lb in issue.get("labels", []) if isinstance(lb, dict)}
            if policy.required_labels:
                for group in policy.required_labels:
                    if not any(g.lower() in labels for g in group):
                        add(
                            Finding(
                                "required_labels",
                                f"{repo}#{num} lacks a required label: {' or '.join(group)}",
                                f"{repo}#{num}",
                                evidence=ev("required_labels"),
                            )
                        )
                if issue.get("state") == "closed":
                    add(
                        Finding(
                            "required_labels",
                            f"{repo}#{num} is closed",
                            f"{repo}#{num}",
                            evidence=ev("required_labels"),
                        )
                    )
            assignees = {(a.get("login") or "").lower() for a in issue.get("assignees", []) if isinstance(a, dict)}
            if policy.assignment_required:
                if not assignees:
                    add(
                        Finding(
                            "assignment_required",
                            f"{repo}#{num} is not assigned to anyone",
                            f"{repo}#{num}",
                            evidence=ev("assignment_required"),
                        )
                    )
                elif author and author.lower() not in assignees:
                    add(
                        Finding(
                            "assignment_required",
                            f"{repo}#{num} is assigned to someone else",
                            f"{repo}#{num}",
                            evidence=ev("assignment_required"),
                        )
                    )
            elif policy.required_labels and assignees and author and author.lower() not in assignees:
                add(
                    Finding(
                        "required_labels",
                        f"{repo}#{num} is already assigned to another contributor",
                        f"{repo}#{num}",
                        severity="warning",
                        evidence=ev("required_labels"),
                    )
                )

    # --- UI ---------------------------------------------------------------------
    if policy.ui_screenshots_required and have_body:
        if not _IMAGE.search(body):
            add(
                Finding(
                    "ui_screenshots_required",
                    "UI changes need screenshots or a recording in the PR description",
                    "PR description",
                    severity="error" if ui_change else "reminder",
                    evidence=ev("ui_screenshots_required"),
                )
            )

    # --- Guidance reminders ---------------------------------------------------
    for rule, text in (
        ("guidance.explain_change", "Be ready to explain every change yourself"),
        ("guidance.run_tests", "Run the tests locally before opening or updating the PR"),
    ):
        if policy.evidence_for(rule):
            add(Finding(rule, text, "PR", severity="reminder", evidence=ev(rule)))
    return res
