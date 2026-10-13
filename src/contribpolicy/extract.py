"""Deterministic rule extraction.

Everything here is regular expressions and a few structural heuristics over
the units produced by :mod:`contribpolicy.markdown`. There is no model and no
network access. The guiding principle is *precision over coverage*: a rule is
only reported when a sentence states it in normative language ("must",
"do not", "is not allowed", an imperative, a "Disallowed" heading, ...), and
every reported rule carries the sentence it came from.

A project that merely *mentions* an AI product (say, "Copilot reviews every
PR") is not an AI policy; ``ai_use`` stays ``unspecified``.
"""

from __future__ import annotations

import itertools
import posixpath
import re
from dataclasses import dataclass, field

from .markdown import Unit, parse
from .model import DISCLOSURE_WHERE, HUMAN_ONLY, Evidence, Policy, Source

__all__ = ["Doc", "extract", "role_for_path"]

# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass
class Doc:
    source: Source
    text: str
    role: str = "other"  # contributing | agent | ai_policy | pr_template | other
    ai_scope: bool = False  # the whole document is about AI use
    section: str = ""  # only read the section under this heading


_AI_POLICY_NAME = re.compile(
    r"(?ix)^(?:"
    r"(?:gen[-_]?)?ai[-_]?(?:usage[-_]?|use[-_]?|contribution[-_]?|contributions[-_]?|assisted[-_]?contributions?[-_]?)?"
    r"(?:policy|policies|guidelines?|guidance|contributions?|usage|use)?"
    r"|llm[-_]?(?:policy|usage|guidelines?)"
    r"|genai"
    r")\.(?:md|markdown|rst|txt)$"
)
_AGENT_NAME = re.compile(r"(?i)^(?:agents|claude|gemini|copilot-instructions|agent)\.md$")
_PR_TEMPLATE_NAME = re.compile(r"(?i)^pull_request_template\.md$")
_CONTRIB_NAME = re.compile(r"(?i)^contributing(?:[-_]?guidelines?)?(?:\.(?:md|markdown|rst|txt))?$")


def role_for_path(path: str) -> str:
    name = posixpath.basename(path)
    if _AI_POLICY_NAME.match(name):
        return "ai_policy"
    if _AGENT_NAME.match(name):
        return "agent"
    if _PR_TEMPLATE_NAME.match(name) or "/PULL_REQUEST_TEMPLATE/" in path.upper():
        return "pr_template"
    if _CONTRIB_NAME.match(name):
        return "contributing"
    return "other"


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

# Case-sensitive: "AI" must be a whole upper-case word (not "mail", "Ail", "OpenAI").
_AI_CS = re.compile(
    r"\b(?:AI|A\.I\.|GenAI|LLMs?|Copilot|ChatGPT|Claude|Codex|Gemini|Devin|Aider|Windsurf)\b"
    r"|\bAI-(?=\w)"
)
_AI_CI = re.compile(
    r"(?i)\b(?:generative|large language models?|machine[- ]generated|coding (?:assistants?|agents?|bots?)"
    r"|agentic|autonomous agents?|chat ?bots?)\b"
)
# Loose words that only count as AI when the surrounding context already is.
_AGENTISH = re.compile(r"(?i)\b(?:agents?|bots?|automation|automated|tools?|generated|generator)\b")

_NEG = re.compile(
    r"(?i)\b(?:must\s+not|mustn['’]t|should\s+not|shouldn['’]t|shall\s+not|do\s+not|don['’]t|does\s+not|"
    r"doesn['’]t|never|cannot|can['’]t|may\s+not|will\s+not|won['’]t|"
    r"not\s+(?:be\s+)?(?:allowed|permitted|accepted|acceptable|welcome|ok|okay)|"
    r"prohibited|forbidden|banned|disallowed|refrain|no\s+longer)\b"
    r"|^\W*no\b"
)
_NOT_REQUIRED = re.compile(
    r"(?i)\b(?:(?:is|are)\s+not\s+required|not\s+(?:be\s+)?(?:required|necessary|needed)|"
    r"(?:does|do)\s*n[o']t\s+(?:require|need)|(?:don['’]t|do\s+not)\s+need|no\s+need|optional(?:ly)?)\b"
)
_REQ = re.compile(
    r"(?i)\b(?:must|shall|required?|requires|need(?:s)?\s+to|have\s+to|has\s+to|should|"
    r"(?:is|are)\s+expected\s+to|please|mandatory|always|make\s+sure|ensure)\b"
)
_IMPERATIVE = re.compile(
    r"(?i)^\W*(?:(?:when|if|before|after|for|while)\b[^,]{0,80},\s*)?(?:disclose|declare|add|include|use|sign|mention|state|note|open|check|tick|mark|complete|fill|"
    r"run|write|ensure|provide|attach|link|keep|split|indicate|credit|identify|tag|follow|read|respond|reply|"
    r"explain|test|verify|review|validate|ask|wait|start|discuss|file|create|submit|honou?r|understand|be)\b"
)
_OPT = re.compile(
    r"(?i)\b(?:may|can|could|optional(?:ly)?|up\s+to\s+you|prefer(?:red|ably)?|encouraged?|recommended|"
    r"welcome[sd]?|feel\s+free|(?:is|are)\s+fine|(?:is|are)\s+ok(?:ay)?|acceptable|alternative(?:ly)?|"
    r"consider|natively|support)\b"
)
_QUAL = re.compile(
    r"(?i)\b(?:bulk|large|huge|mass|high[- ]volume|volumes?\s+of|low[- ](?:quality|effort|information)|"
    r"duplicate|speculative|unreviewed|unverified|untested|spam\w*|slop|fully|entirely|verbatim|blindly|"
    r"without\s+(?:review|reading|testing|understanding)|minimal\s+human|copy-?past\w*)\b"
)

_NEG_HEADING = re.compile(
    r"(?i)\b(?:disallowed|not\s+allowed|prohibited|forbidden|unacceptable|not\s+acceptable|don['’]?ts?|"
    r"do\s+not|never|bad\s+use|what\s+not\s+to)\b"
)
_POS_HEADING = re.compile(
    r"(?i)\b(?:good\s+use|allowed|permitted|acceptable|encouraged|welcome|do['’]?s\b|what\s+you\s+can)\b"
)
_DISCLOSE_HEADING = re.compile(r"(?i)\bdisclos")

# Trailers ------------------------------------------------------------------
_ASSISTED = re.compile(r"(?i)\b(?:assisted-by|co-developed(?:-by)?|generated-by|ai-assisted-by)\b")
_ASSISTED_TRAILER_TEXT = re.compile(r"(?i)\bAssisted-by:\s*[^\"'`\n]{1,80}")
_COAUTHOR = re.compile(r"(?i)\bco-?authou?r(?:ed)?(?:-by)?\b|\bco-?sign(?:ing|ed|s)?\b|\bauthorship\s+markers?\b")
_CONSEQUENCE = re.compile(r"(?i)\b(?:breaks?|fails?|blocks?|invalidates?)\b")
_ALT_SPLIT = re.compile(r"(?i)\b(?:instead\s+of|rather\s+than)\b")
_CLAUSE_SPLIT = re.compile(r"(?i);|\s[—–]\s|\s--\s|,\s*but\b|\bwhereas\b|\bhowever\b")

# Disclosure -----------------------------------------------------------------
_DISCLOSE = re.compile(
    r"(?i)\b(?:disclos\w*|declar\w*)\b|\bnote\s+this\b|"
    r"\b(?:state|mention|indicate|identify|say)\b[^.]{0,50}\b(?:AI|LLM|generative|assist\w*|tools?|usage)\b|"
    r"\b(?:say|state|mention|note)\s+(?:so|this|it)\b"
)
_WHERE_DESC = re.compile(
    r"(?i)\b(?:PR|pull[- ]request|merge[- ]request|MR)(?:['’]s)?\s+(?:description|body)\b|"
    r"\bdescription\s+of\s+(?:your|the|a)\s+(?:PR|pull[- ]request)\b|"
    r"\bin\s+(?:the|your)\s+(?:PR|pull[- ]request)\b(?!\s+template)|"
    r"\bissue\s+or\s+pull[- ]request\s+description\b"
)
_WHERE_CHECKBOX = re.compile(
    r"(?i)check[- ]?box|\btick\b|check\s+exactly\s+one|\b(?:PR|pull[- ]request)\s+template\b|"
    r"\bAI\s+(?:assistance|usage|use|disclosure)\s+section\b"
)
_WHERE_TRAILER = re.compile(r"(?i)\btrailers?\b|assisted-by|\bcommit\s+messages?\b")
_EXAMPLE_CUE = re.compile(r"(?i)\b(?:for\s+example|e\.g\.|such\s+as|for\s+instance|sufficient|like)\b")
_QUOTED = re.compile(r"[\"“]([^\"”]{12,200}?)[\"”]")
_NONE_OPTION = re.compile(r"(?i)^\W*(?:none\b|no\s+(?:material\s+)?AI\b|no\s+AI\s+tools?\b|I\s+did\s+not\s+use)")

# Human-only -----------------------------------------------------------------
_T_REVIEW = re.compile(
    r"(?i)\breview(?:er)?s?['’]?\s+(?:comments?|feedback|questions?|threads?|replies|responses)\b|"
    r"\b(?:respond\w*|repl(?:y|ies|ying)|answer\w*|engag\w*)\b[^.;]{0,60}\b(?:reviews?|reviewers?|maintainers?|feedback|comments?)\b|"
    r"\b(?:pull[- ]request|PR|code)\s+reviews?\b|"
    r"\b(?:generate|write|post)\w*\s+(?:responses|replies)\b"
)
_T_COMMIT = re.compile(r"(?i)\bcommit\s+messages?\b")
_T_DESC = re.compile(
    r"(?i)\b(?:PR|pull[- ]request)\s+descriptions?\b|\bdescriptions?\s+of\s+(?:your|the)\s+(?:PR|pull[- ]request)s?\b"
)
_T_ISSUE = re.compile(r"(?i)\bissues?(?:\s+reports?)?\b|\bbug\s+reports?\b")
_HUMAN = re.compile(
    r"(?i)\bhuman[- ](?:authored|written)\b|\bsubstantively\s+human\b|\bwritten\s+by\s+(?:you|a\s+human|yourself|hand)\b|"
    r"\bin\s+your\s+own\s+words\b|\byourself\b|\bthemselves\b|\bpersonally\b|\bby\s+hand\b|"
    r"\bwithout\s+(?:relying\s+on|using|the\s+(?:help|use|assistance)\s+of)\s+(?:any\s+)?(?:AI|LLMs?|generative|tools?|agents?)\b|"
    r"\bnot\s+(?:be\s+)?(?:AI|machine)[- ]generated\b|\bbe\s+your\s+own\b"
)
_SEGMENT_SPLIT = re.compile(r"(?i);|\band\b|,\s*but\b")

# Policy stance ----------------------------------------------------------------
_AI_NOUN = (
    r"(?:AI|LLMs?|GenAI|generative\s+AI|AI[- ]?(?:tools?|assistants?|agents?|coding\s+(?:assistants?|tools?|agents?))|"
    r"coding\s+(?:assistants?|agents?))"
)
_ALLOW = [
    re.compile(
        rf"\b(?i:using|use\s+of|usage\s+of)\s+{_AI_NOUN}\b[^.]{{0,80}}?\b(?:is|are)\s+"
        r"(?:acceptable|allowed|permitted|welcome|fine|ok(?:ay)?|encouraged)\b"
    ),
    re.compile(r"\bwelcome[sd]?\b[^.]{0,80}\b(?:AI|LLMs?|GenAI)\b|(?i:\bwelcome[sd]?\b[^.]{0,80}\bgenerative\b)"),
    re.compile(rf"\b{_AI_NOUN}\s+(?:may|can)\s+be\s+used\b"),
    re.compile(r"(?i)\bpermits?\s+the\s+use\s+of\b[^.]{0,40}\b(?:AI|generative)"),
    re.compile(
        r"\b(?:AI|LLM)[- ]assisted\s+(?:contributions?|pull\s+requests|PRs?|code)\s+(?:are|is)\s+(?:welcome|accepted|allowed|fine)\b"
    ),
]
_BAN = [
    re.compile(
        r"(?i)\b(?:do\s+not|don['’]t|will\s+not|won['’]t|cannot|can['’]t|no\s+longer|not)\s+accept\w*\b[^.]{0,80}"
        r"\b(?:AI|LLM|machine|generative)[- ]?(?:generated|assisted|written|authored|tools?|contributions?|code)\b"
    ),
    re.compile(
        r"(?i)\b(?:AI|LLM|machine)[- ](?:generated|written|authored|assisted)\s+(?:code|contributions?|content|"
        r"pull\s+requests|PRs|patches|changes|submissions)\b[^.]{0,40}\b(?:(?:is|are|will\s+be)\s+(?:not|never)\s+"
        r"(?:be\s+)?(?:accepted|allowed|permitted|welcome)|(?:is|are)\s+(?:prohibited|forbidden|banned|rejected))\b"
    ),
    re.compile(
        rf"\b(?:use\s+of|using|usage\s+of)\s+{_AI_NOUN}\b[^.]{{0,40}}\b(?:is|are)\s+"
        r"(?:not\s+(?:allowed|permitted|accepted)|prohibited|forbidden|banned)\b"
    ),
    re.compile(
        r"(?i)\bno\s+(?:AI|LLM)[- ](?:generated|assisted|written)\s+(?:code|contributions|pull\s+requests|PRs|patches|content)\b"
    ),
    re.compile(
        rf"(?i)\b(?:must\s+not|do\s+not|don['’]t|never)\s+(?:use|submit)\s+(?:any\s+)?{_AI_NOUN}"
        r"(?:[- ]generated\s+\w+)?\s*(?:[.!]|$|\bfor\s+(?:any|your)\s+contributions?\b|\bto\s+(?:write|create)\s+(?:code|contributions?|pull\s+requests|PRs)\b)"
    ),
]
_RESTRICT = [
    re.compile(r"(?i)\bmust\s+restrict\b"),
    re.compile(r"(?i)\bonly\b[^.]{0,60}\b(?:issues?|tasks?)\b[^.]{0,60}\blabel"),
    re.compile(r"(?i)\b(?:may|can)\b[^.]{0,100}\bonly\s+(?:when|if)\b"),
    re.compile(r"(?i)\bonly\s+(?:accept|allow|permit)\w*\b[^.]{0,60}\b(?:AI|automated|agents?|bots?)\b"),
    re.compile(
        r"(?i)\b(?:AI|automated|automation|agents?|bots?)\b[^.]{0,60}\b(?:only|exclusively)\s+(?:on|for|when|if|from)\b"
    ),
    re.compile(
        r"(?i)\bnot\s+(?:accept|allow)\w*\s+(?:AI|automated|agent)[^.]{0,60}\bfrom\s+(?:first[- ]time|new)\s+contributors\b"
    ),
]
_AUTOMATION = re.compile(r"(?i)\b(?:automation|automated|bots?|agents?)\b")

# Workflow -------------------------------------------------------------------
_ASSIGN = re.compile(r"(?i)\b(?:un)?assign(?:ed|ment|ee)?\b")
_ASSIGN_REQ = [
    re.compile(
        r"(?i)\b(?:must|need\s+to|needs\s+to|should|required\s+to|have\s+to|has\s+to|wait\s+(?:until|for|to)|only\s+after|before)\b[^.]{0,80}\b(?:assigned|assignment)\b"
    ),
    re.compile(r"(?i)\bassigned\b[^.]{0,80}\b(?:before|prior\s+to)\b"),
    re.compile(
        r"(?i)\b(?:unassigned|not\s+(?:been\s+)?assigned|without\s+(?:being\s+|an\s+)?assign\w*)\b[^.]{0,100}"
        r"\b(?:clos\w*|reject\w*|not\s+be\s+(?:reviewed|accepted|merged))"
    ),
    re.compile(r"(?i)\b(?:clos\w*|reject\w*)\b[^.]{0,60}\b(?:unassigned|not\s+assigned\s+to)\b"),
]
_ASSIGN_NOT = re.compile(
    r"(?i)\b(?:do\s+not|don['’]t|never|won['’]t|will\s+not|not)\s+assign\b|\bmust\s+not\s+be\s+assigned\b|"
    r"\bnot\s+(?:be\s+|already\s+)?assigned\s+to\s+(?:another|someone\s+else|other)\b|\b(?:can|may)\s+(?:request|ask)\b"
)
_ISSUE_FIRST = [
    re.compile(
        r"(?i)\b(?:open|file|create|raise)\w*\s+(?:an?|the|new|a\s+new|one)\s+(?:\w+\s+)?issue\b[^.]{0,80}\b(?:first|before|prior\s+to)\b"
    ),
    re.compile(r"(?i)\bdiscuss\w*\b[^.]{0,120}\bissue\b[^.]{0,80}\bbefore\b"),
    re.compile(
        r"(?i)\bbefore\s+(?:making|starting|opening|submitting|sending|implementing|working|you\s+start)\b[^.]{0,100}\b(?:open|file|create|discuss|raise)\w*\b[^.]{0,40}\bissue"
    ),
    re.compile(r"(?i)\bissue[- ]first\b"),
    re.compile(r"(?i)\bstart\s+(?:with|by)\b[^.]{0,60}\bissue\b"),
    re.compile(
        r"(?i)\b(?:must|should|need\s+to)\s+(?:be\s+)?(?:linked|link|reference|tied)\w*\s+(?:to\s+)?(?:an?\s+)?(?:existing|agreed|open|approved|accepted)?\s*issue\b"
    ),
]
_ASSIGN_PERSON = re.compile(
    r"(?i)\bassigned\s+(?:\w+\s+){0,3}?to\s+(?:you|yourself|them|the\s+(?:author|contributor|submitter))\b|\bget\s+(?:\w+\s+){0,3}assigned\b|"
    r"\b(?:issue|you|contributors?)\b[^.]{0,30}\b(?:be|been|being|is|are)\s+(?:not\s+)?assigned\b|\bassignment\b|\bunassigned\b|"
    r"\bself-assign\w*"
)
_ISSUE_FIRST_NOT = re.compile(
    r"(?i)\bwithout\s+(?:first\s+)?(?:opening|filing|creating)\b|\bno\s+need\s+to\s+open\b|\bdon['’]t\s+need\s+(?:to\s+open\s+)?an\s+issue\b"
)
_LABEL_PHRASES = re.compile(r"(?i)\bgood[- ]first[- ]issues?\b|\bhelp[- ]wanted\b|\bin\s+the\s+first\s+place\b")
_LABEL_WORD = re.compile(r"(?i)\blabel(?:s|ed|led)?\b")
_LABEL_TOKEN = re.compile(r"`([^`]{1,50})`|[\"“]([^\"”]{1,50})[\"”]|[‘']([^’']{1,50})[’']")

# Legal / signing -------------------------------------------------------------
_DCO = re.compile(
    r"\bDCO\b|(?i:developer\s+certificate\s+of\s+origin|signed-off-by|\bsign[- ]?off\b|\bsignoff\b|git\s+commit\s+(?:-\w*\s+)*-s\b|--signoff)"
)
_LEGAL_NOT = re.compile(
    r"(?i)\b(?:do\s+not|don['’]t|does\s+not|doesn['’]t|no\s+longer)\s+(?:have|use|require|need|enforce|ask\s+for)\b"
    r"[^.]{0,30}\b(?:CLA|contributor\s+license|DCO|developer\s+certificate|sign-?off|signed-off-by)"
)
_DCO_NOT = re.compile(
    r"(?i)\b(?:do\s+not|don['’]t|does\s+not|doesn['’]t)\s+(?:use|require|need|enforce)\b[^.]{0,20}\b(?:DCO|sign-?off|signed-off-by)"
)
_CLA = re.compile(r"\bCLA\b|EasyCLA|(?i:contributor\s+license\s+agreement|cla[- ]assistant)")
_SIGNED_STRONG = re.compile(
    r"(?i:\bsigned\s+and\s+verified\b|\bcryptographic\w*\b|\bGPG\b|\bSSH\s+(?:signing\s+)?keys?\b|\bsigning\s+keys?\b|"
    r"\bverified\s+(?:badge|commits?|signatures?)\b|\bcommit\s+signature\s+verification\b|\bvigilant\s+mode\b|\bgpgsign\b)"
    r"|\bgit\s+commit\s+(?:-\w+\s+)*-S\b"  # upper-case -S only; -s is the DCO sign-off
)
_SIGNED_WEAK = re.compile(r"(?i)\bsigned\s+commits?\b")

# UI ------------------------------------------------------------------------
_SHOT = re.compile(
    r"(?i)\bscreen\s?shots?\b|\bscreen[- ]?(?:recordings?|captures?|casts?)\b|\bGIFs?\b|\bvideos?\b|\brecordings?\b"
)
_UI = re.compile(
    r"\bUI\b|(?i:\buser[- ]interface\b|\bvisual\b|\bfront[- ]?end\b|\bCSS\b|\blayout\b|\bstyling\b|\bweb\s?UI\b)"
)

# Guidance (evidence only) ----------------------------------------------------
_G_TESTS = re.compile(
    r"(?i)\brun\b[^.]{0,50}\btests?\b|\btests?\s+locally\b|\bwithout\s+testing\b|\buntested\b|\btest\s+all\b"
)
_G_EXPLAIN = re.compile(
    r"(?i)\bexplain\b|\bunderstand\s+(?:every|all|each|the\s+code|your\s+code|what)\b|\b(?:do\s+not|don['’]t)\s+understand\b"
)
_G_SIZE = re.compile(
    r"(?i)\b(?:keep|split|break)\w*\b[^.]{0,40}\b(?:small|smaller|focused|up|into)\b"
    r"|\b(?:small|smaller|focused)\s+(?:and\s+focused\s+)?(?:PRs?|pull\s+requests?|changes?|diffs?|contributions?)\b"
    r"|\b(?:large|huge|bulk|mass|broad)\b[^.]{0,30}\b(?:PRs?|pull\s+requests?|diffs?|refactor\w*)\b[^.]{0,40}"
    r"\b(?:not\s+(?:be\s+)?(?:allowed|accepted)|will\s+be\s+closed|discouraged|must|should)"
)
_G_REVIEW = re.compile(
    r"(?i)\b(?:review|verify|verifying|validate|check)\w*\s+(?:\w+[\s,]+){0,3}?(?:output|changes|content|code|line)s?\b"
    r"|\bfirst\s+review\b"
)

_GUIDANCE = (
    ("guidance.run_tests", _G_TESTS),
    ("guidance.explain_change", _G_EXPLAIN),
    ("guidance.size_limit", _G_SIZE),
    ("guidance.review_ai_output", _G_REVIEW),
)

_MAX_QUOTE = 240
_MAX_EVIDENCE_PER_RULE = 4
_MAX_EVIDENCE_PER_DOC = 2


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _has_ai(text: str) -> bool:
    return bool(_AI_CS.search(text) or _AI_CI.search(text))


def _quote(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) > _MAX_QUOTE:
        t = t[: _MAX_QUOTE - 1].rstrip() + "…"
    return t


@dataclass
class _Ctx:
    doc: Doc
    unit: Unit
    ai: bool  # the unit is about AI (itself, its heading, its lead, or the doc)
    polarity: int  # -1 under a "Disallowed" heading, +1 under "Good use", 0 otherwise
    disclose_heading: bool
    agent_doc: bool

    @property
    def text(self) -> str:
        return self.unit.text

    @property
    def lead(self) -> str:
        return self.unit.lead

    def negative(self) -> bool:
        if _NEG.search(self.text):
            return True
        if self.unit.kind in ("item", "checkbox") and self.lead and _NEG.search(self.lead):
            return True
        return self.polarity < 0 and self.unit.kind in ("item", "checkbox", "prose")

    def required(self) -> bool:
        if _REQ.search(self.text) or _IMPERATIVE.search(self.text):
            return True
        return self.unit.kind in ("item", "checkbox") and bool(self.lead) and bool(_REQ.search(self.lead))


@dataclass
class _Acc:
    """Accumulates findings across all documents."""

    evidence: list[Evidence] = field(default_factory=list)
    seen: set[tuple[str, str, str]] = field(default_factory=set)
    assisted: dict[str, list[Evidence]] = field(default_factory=dict)
    coauthor: dict[str, list[Evidence]] = field(default_factory=dict)
    stance: dict[str, list[Evidence]] = field(default_factory=dict)
    disclosure_required: bool = False
    where: set[str] = field(default_factory=set)
    examples: list[tuple[int, int, str]] = field(default_factory=list)  # (priority, order, text)
    human_only: set[str] = field(default_factory=set)
    labels: list[list[str]] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)
    guidance_seen: set[tuple[str, str]] = field(default_factory=set)
    checkbox_options: list[str] = field(default_factory=list)
    order: int = 0

    def add(self, rule: str, ctx: _Ctx, value: str = "", text: str | None = None) -> Evidence:
        quote = _quote(text if text is not None else ctx.text)
        key = (rule, quote, ctx.doc.source.path)
        ev = Evidence(rule=rule, quote=quote, path=ctx.doc.source.path, line=ctx.unit.line, value=value)
        per_rule = sum(1 for e in self.evidence if e.rule == rule)
        per_doc = sum(1 for e in self.evidence if e.rule == rule and e.path == ev.path)
        if key not in self.seen and per_rule < _MAX_EVIDENCE_PER_RULE and per_doc < _MAX_EVIDENCE_PER_DOC:
            self.seen.add(key)
            self.evidence.append(ev)
        return ev


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------


def _clauses(text: str) -> list[str]:
    parts = [p for p in _CLAUSE_SPLIT.split(text) if p and p.strip()]
    return parts or [text]


def _trailers(ctx: _Ctx, acc: _Acc) -> None:
    text = ctx.text
    has_assisted = bool(_ASSISTED.search(text))
    has_coauthor = bool(_COAUTHOR.search(text)) and (ctx.ai or ctx.agent_doc)
    if not (has_assisted or has_coauthor):
        return

    def classify(clause: str, forced_neg: bool = False) -> str | None:
        if forced_neg or _NEG.search(clause):
            return "banned"
        if ctx.unit.kind in ("item", "checkbox") and ctx.lead and _NEG.search(ctx.lead):
            return "banned"
        if ctx.polarity < 0 and ctx.unit.kind in ("item", "prose"):
            return "banned"
        if ctx.unit.kind == "code":
            lead = ctx.lead
            if lead and _NEG.search(lead):
                return "banned"
            return "allowed"
        if _OPT.search(clause):
            return "allowed"
        if _REQ.search(clause) or _IMPERATIVE.search(clause):
            return "required"
        return None

    for clause in _clauses(text):
        parts = _ALT_SPLIT.split(clause, maxsplit=1)
        segments = [(parts[0], False)] + ([(parts[1], True)] if len(parts) > 1 else [])
        for seg, alt in segments:
            if _ASSISTED.search(seg):
                v = classify(seg, alt)
                if v is None and (ctx.disclose_heading or _DISCLOSE.search(text)):
                    v = "allowed"
                if v:
                    acc.assisted.setdefault(v, []).append(acc.add("trailers.assisted_by", ctx, v))
            if _COAUTHOR.search(seg) and (ctx.ai or ctx.agent_doc):
                if alt or _NEG.search(seg) or _CONSEQUENCE.search(seg):
                    v = "banned"
                else:
                    v = classify(seg)
                    if v == "required":
                        v = "allowed"
                if v:
                    acc.coauthor.setdefault(v, []).append(acc.add("trailers.co_authored_by_ai", ctx, v))


def _disclosure(ctx: _Ctx, acc: _Acc) -> None:
    if not ctx.ai:
        return
    text = ctx.text
    u = ctx.unit
    # PR template checkboxes that ask about AI use.
    if ctx.doc.role == "pr_template" and u.kind == "checkbox" and (_has_ai(text) or _has_ai(" ".join(u.headings))):
        acc.disclosure_required = True
        acc.where.add("checkbox")
        acc.checkbox_options.append(text)
        acc.add("disclosure.where.checkbox", ctx, "checkbox", text=f"[ ] {text}")
        if not _NONE_OPTION.search(text):
            acc.examples.append((4, acc.order, f"[x] {text}"))
        return
    if u.kind != "code" and _EXAMPLE_CUE.search(text):
        for m in _QUOTED.finditer(text):
            cand = m.group(1).strip().rstrip(",")
            if _has_ai(cand) or re.search(r"(?i)assist", cand):
                acc.examples.append((1, acc.order, cand))
    checkbox_ask = bool(_WHERE_CHECKBOX.search(text)) and _has_ai(text)
    is_disc = (
        bool(_DISCLOSE.search(text))
        or checkbox_ask
        or ctx.disclose_heading
        or bool(u.lead and _DISCLOSE.search(u.lead))
    )
    if not is_disc:
        return
    lead_not_req = bool(u.lead and _NOT_REQUIRED.search(u.lead)) and u.kind in ("item", "checkbox")
    if _NOT_REQUIRED.search(text) or lead_not_req:
        return
    if u.kind != "code" and (_DISCLOSE.search(text) or checkbox_ask) and ctx.required() and not _NEG.search(text):
        acc.disclosure_required = True
        acc.add("disclosure.required", ctx, "true")
    if u.kind != "code":
        where_text = text
        if _WHERE_DESC.search(where_text):
            acc.where.add("description")
            acc.add("disclosure.where.description", ctx, "description")
        if _WHERE_CHECKBOX.search(where_text):
            acc.where.add("checkbox")
            acc.add("disclosure.where.checkbox", ctx, "checkbox")
        if _WHERE_TRAILER.search(where_text) and not _NEG.search(where_text):
            acc.where.add("commit-trailer")
            acc.add("disclosure.where.commit-trailer", ctx, "commit-trailer")
    # Examples.
    if u.kind == "code":
        m = _ASSISTED_TRAILER_TEXT.search(text)
        if m:
            acc.examples.append((2, acc.order, m.group(0).strip()))
        elif _has_ai(text) and len(text) <= 200:
            acc.examples.append((2, acc.order, text.strip()))
        return
    m = _ASSISTED_TRAILER_TEXT.search(text)
    if m:
        acc.examples.append((3, acc.order, m.group(0).strip()))


def _target_qualified(text: str, match: re.Match[str]) -> bool:
    """True when a qualifier like "bulk" or "low-quality" governs the target."""
    start = match.start()
    seg_start = 0
    for m in _SEGMENT_SPLIT.finditer(text):
        if m.end() <= start:
            seg_start = m.end()
    return bool(_QUAL.search(text[seg_start:start]))


def _human_only(ctx: _Ctx, acc: _Acc) -> None:
    if not ctx.ai:
        return
    text = ctx.text
    explicit_neg = bool(_NEG.search(text))
    if ctx.polarity > 0 and not explicit_neg:
        return
    human = bool(_HUMAN.search(text))
    ai_here = _has_ai(text) or _has_ai(ctx.lead) or bool(_AGENTISH.search(text))
    neg = ctx.negative()
    if not (human or (neg and ai_here)):
        return
    for name, rx in (
        ("review-replies", _T_REVIEW),
        ("commit-messages", _T_COMMIT),
        ("pr-description", _T_DESC),
        ("issues", _T_ISSUE),
    ):
        hit = None
        for m in rx.finditer(text):
            if not _target_qualified(text, m):
                hit = m
                break
        if hit is None:
            continue
        if name == "issues":
            # "issue" is common; require that the sentence is about writing issues,
            # not about picking an issue to work on.
            if not (
                re.search(r"(?i)\b(?:file|filing|open|opening|writ\w*|creat\w*|generat\w*|submit\w*|report\w*)\b", text)
                or re.search(r"(?i)\bissues?\b[^.]{0,40}\b(?:must|should)\b[^.]{0,40}\bhuman", text)
                or re.search(r"(?i)^\W*issues?\b", text)
            ):
                continue
            if re.search(r"(?i)\b(?:implement|work\s+on|pick)\b", text):
                continue
        clause = next((c for c in _clauses(text) if _T_COMMIT.search(c)), text)
        if name == "commit-messages" and (
            _COAUTHOR.search(clause)
            or _ASSISTED.search(clause)
            or re.search(r"(?i)signed-off-by|\btrailers?\b|\btags?\b", clause)
        ):
            # "Do not add Co-authored-by to the commit message" is a trailer rule.
            continue
        if name == "pr-description" and _T_COMMIT.search(text) is None and _DISCLOSE.search(text):
            # "disclose ... in the PR description" is a disclosure rule, not a human-only rule.
            continue
        acc.human_only.add(name)
        acc.add(f"human_only.{name}", ctx, name)


def _stance(ctx: _Ctx, acc: _Acc) -> None:
    text = ctx.text
    u = ctx.unit
    if u.kind in ("code", "heading", "table"):
        return
    ai_text = _has_ai(text) or (ctx.ai and bool(_AGENTISH.search(text)))
    if not ai_text:
        return
    # Ban: AI contributions as a whole are not accepted.
    targets_part = _T_COMMIT.search(text) or _T_REVIEW.search(text) or _T_DESC.search(text)
    if (
        not targets_part
        and not _QUAL.search(text)
        and not re.search(r"(?i)\bto\s+(?:produce|generate|answer|respond|reply)\b", text)
    ):
        for rx in _BAN:
            if rx.search(text):
                acc.stance.setdefault("banned", []).append(acc.add("ai_use", ctx, "banned"))
                return
    if (_AUTOMATION.search(text) or _has_ai(text)) and any(rx.search(text) for rx in _RESTRICT):
        acc.stance.setdefault("restricted", []).append(acc.add("ai_use", ctx, "restricted"))
        return
    if not _NEG.search(text) and any(rx.search(text) for rx in _ALLOW):
        acc.stance.setdefault("allowed", []).append(acc.add("ai_use", ctx, "allowed"))


def _labels(ctx: _Ctx, acc: _Acc) -> None:
    text = ctx.text
    if not _LABEL_WORD.search(text):
        return
    if not (re.search(r"(?i)\b(?:only|must|required?|restrict\w*)\b", text)):
        return
    if not (ctx.doc.ai_scope or _has_ai(text) or _AUTOMATION.search(text)):
        return
    tokens = [(m.start(), m.end(), next(g for g in m.groups() if g)) for m in _LABEL_TOKEN.finditer(text)]
    tokens = [t for t in tokens if not re.search(r"[/#:]|\s{2,}", t[2]) and len(t[2].split()) <= 4]
    if not tokens:
        return
    # "... the `x` label or explicitly agree ..." is not a hard requirement.
    tail = text[tokens[-1][1] : tokens[-1][1] + 40]
    if re.match(r"(?i)\s*(?:labels?)?\s*,?\s*or\b(?!\s*[`\"“‘'])", tail):
        return
    groups: list[list[str]] = [[tokens[0][2]]]
    for prev, cur in itertools.pairwise(tokens):
        between = text[prev[1] : cur[0]]
        if re.search(r"(?i)\bor\b", between) and not re.search(r"(?i)\band\b", between):
            groups[-1].append(cur[2])
        else:
            groups.append([cur[2]])
    if not acc.labels:  # the first statement wins; later ones are kept as evidence
        acc.labels = groups
    acc.add("required_labels", ctx, "labels")


def _workflow(ctx: _Ctx, acc: _Acc) -> None:
    text = ctx.text
    u = ctx.unit
    if u.kind in ("code", "table"):
        return
    # Assignment
    if _ASSIGN.search(text) and not _ASSIGN_NOT.search(text) and _ASSIGN_PERSON.search(text):
        if any(rx.search(text) for rx in _ASSIGN_REQ):
            acc.flags["assignment_required"] = True
            acc.add("assignment_required", ctx, "true")
    # Issue first
    plain = _LABEL_PHRASES.sub(" ", text)
    if not _ISSUE_FIRST_NOT.search(plain) and any(rx.search(plain) for rx in _ISSUE_FIRST):
        if not _NOT_REQUIRED.search(plain) and not (
            _OPT.search(plain) and not _REQ.search(plain) and "first" not in plain.lower()
        ):
            acc.flags["issue_first"] = True
            acc.add("issue_first", ctx, "true")
    # DCO / CLA
    if u.kind != "heading":
        if (
            _DCO.search(text)
            and not _DCO_NOT.search(text)
            and not _LEGAL_NOT.search(text)
            and not _NOT_REQUIRED.search(text)
        ):
            acc.flags["dco"] = True
            acc.add("dco", ctx, "true")
        if (
            _CLA.search(text)
            and not _NOT_REQUIRED.search(text)
            and not _LEGAL_NOT.search(text)
            and not re.search(r"(?i)\bno\s+(?:formal\s+)?(?:CLA|contributor\s+license)\b", text)
        ):
            acc.flags["cla"] = True
            acc.add("cla", ctx, "true")
    # Signed commits
    strong = _SIGNED_STRONG.search(text)
    weak = _SIGNED_WEAK.search(text) and not _DCO.search(text)
    if (strong or weak) and re.search(r"(?i)\bsign", text):
        if (
            (ctx.required() or re.search(r"(?i)\brequire", text))
            and not _NOT_REQUIRED.search(text)
            and not (_OPT.search(text) and not _REQ.search(text))
        ):
            acc.flags["signed_commits"] = True
            acc.add("signed_commits", ctx, "true")
    # Screenshots
    if _SHOT.search(text) and (_UI.search(text) or _UI.search(" ".join(u.headings))):
        if ctx.required() or ctx.doc.role == "pr_template" or u.kind == "heading":
            acc.flags["ui_screenshots_required"] = True
            acc.add("ui_screenshots_required", ctx, "true")


def _guidance(ctx: _Ctx, acc: _Acc) -> None:
    u = ctx.unit
    if u.kind in ("code", "heading", "table", "checkbox"):
        return
    # Only AI-specific guidance: the doc, heading or sentence must be about AI
    # (an AGENTS.md alone is not enough, it is full of build instructions).
    if not (ctx.doc.ai_scope or _has_ai(ctx.text) or _has_ai(u.lead) or any(_has_ai(h) for h in u.headings)):
        return
    if not (ctx.required() or ctx.negative()):
        return
    for rule, rx in _GUIDANCE:
        if rx.search(ctx.text) and (rule, ctx.doc.source.path) not in acc.guidance_seen:
            acc.guidance_seen.add((rule, ctx.doc.source.path))
            acc.add(rule, ctx, "true")


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def _section_filter(units: list[Unit], section: str) -> list[Unit]:
    want = section.strip().lower()
    return [u for u in units if any(h.lower() == want for h in u.headings)]


def _contexts(doc: Doc):
    units = parse(doc.text)
    if doc.section:
        units = _section_filter(units, doc.section)
    agent_doc = doc.role == "agent"
    for u in units:
        polarity = 0
        disclose_heading = False
        heading_ai = False
        for h in u.headings:
            if _has_ai(h):
                heading_ai = True
        # The closest heading decides polarity; only headings about AI count.
        for h in reversed(u.headings):
            if _NEG_HEADING.search(h):
                polarity = -1
                break
            if _POS_HEADING.search(h):
                polarity = 1
                break
        if u.kind == "heading":
            polarity = 0
        if u.headings and _DISCLOSE_HEADING.search(u.headings[-1]):
            disclose_heading = True
        ai = doc.ai_scope or heading_ai or _has_ai(u.text) or _has_ai(u.lead)
        if polarity and not (doc.ai_scope or heading_ai):
            polarity = 0
        yield _Ctx(
            doc=doc, unit=u, ai=ai, polarity=polarity, disclose_heading=disclose_heading and ai, agent_doc=agent_doc
        )


def _pick(found: dict[str, list[Evidence]], order: tuple[str, ...]) -> str:
    for v in order:
        if found.get(v):
            return v
    return "unspecified"


def extract(repo: str, docs: list[Doc], sources: list[Source] | None = None) -> Policy:
    """Extract a :class:`Policy` from already-fetched documents."""
    acc = _Acc()
    for doc in docs:
        for ctx in _contexts(doc):
            acc.order += 1
            _stance(ctx, acc)
            _trailers(ctx, acc)
            _disclosure(ctx, acc)
            _human_only(ctx, acc)
            _labels(ctx, acc)
            _workflow(ctx, acc)
            _guidance(ctx, acc)

    p = Policy(repo=repo, sources=list(sources if sources is not None else [d.source for d in docs]))
    p.assisted_by = _pick(acc.assisted, ("banned", "required", "allowed"))
    p.co_authored_by_ai = _pick(acc.coauthor, ("banned", "allowed"))
    if acc.assisted.get("banned") and acc.assisted.get("required"):
        p.warnings.append(
            "Sources disagree about the Assisted-by trailer (both banned and required); reporting 'banned'."
        )
    if acc.coauthor.get("banned") and acc.coauthor.get("allowed"):
        p.warnings.append("Sources disagree about AI Co-authored-by trailers; reporting 'banned'.")

    p.disclosure_required = acc.disclosure_required
    where = set(acc.where)
    if p.assisted_by == "banned":
        where.discard("commit-trailer")
    elif p.assisted_by in ("required", "allowed") and acc.disclosure_required:
        where.add("commit-trailer")
    p.disclosure_where = [w for w in DISCLOSURE_WHERE if w in where]
    if acc.examples:
        examples = sorted(acc.examples)
        if p.assisted_by == "banned":
            examples = [e for e in examples if not e[2].lower().startswith("assisted-by")] or []
        if examples:
            p.disclosure_example = examples[0][2]

    p.human_only = [h for h in HUMAN_ONLY if h in acc.human_only]
    p.required_labels = acc.labels
    p.assignment_required = acc.flags.get("assignment_required", False)
    p.issue_first = acc.flags.get("issue_first", False)
    p.dco = acc.flags.get("dco", False)
    p.cla = acc.flags.get("cla", False)
    p.signed_commits = acc.flags.get("signed_commits", False)
    p.ui_screenshots_required = acc.flags.get("ui_screenshots_required", False)
    p.checkbox_options = acc.checkbox_options

    # Overall stance.
    if acc.stance.get("banned"):
        p.ai_use = "banned"
    elif acc.stance.get("restricted") or (acc.labels and any(e.rule == "required_labels" for e in acc.evidence)):
        p.ai_use = "restricted"
    elif p.disclosure_required:
        p.ai_use = "disclose"
    elif (
        acc.stance.get("allowed")
        or p.assisted_by != "unspecified"
        or p.co_authored_by_ai != "unspecified"
        or p.human_only
        or any(e.rule.startswith("guidance.") for e in acc.evidence)
    ):
        p.ai_use = "allowed"
    else:
        p.ai_use = "unspecified"

    # Keep only the stance evidence that supports the reported value, plus the rest.
    kept = [e for e in acc.evidence if not (e.rule == "ai_use" and e.value != p.ai_use)]
    p.evidence = sorted(kept, key=lambda e: _rule_rank(e.rule))  # stable: document order within a rule
    return p


_RULE_ORDER = (
    "ai_use",
    "required_labels",
    "disclosure",
    "trailers.assisted_by",
    "trailers.co_authored_by_ai",
    "human_only",
    "assignment_required",
    "issue_first",
    "dco",
    "cla",
    "signed_commits",
    "ui_screenshots_required",
    "guidance",
)


def _rule_rank(rule: str) -> int:
    for i, prefix in enumerate(_RULE_ORDER):
        if rule == prefix or rule.startswith(prefix + "."):
            return i
    return len(_RULE_ORDER)
