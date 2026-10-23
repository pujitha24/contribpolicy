"""Console-script entry point.

``contribpolicy hook claude-code`` runs before every Bash command Claude Code executes, so for an
unrelated command it must cost little more than starting Python. The tool call is read and
pre-filtered here, before :mod:`contribpolicy.hook` or :mod:`contribpolicy.cli` (which imports the
extractor) is loaded. Every other command goes to the CLI.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence

# Anything that cannot be a `git push` or a `gh pr ...` call; shared with contribpolicy.hook.
MAYBE_PUSH_OR_PR = re.compile(r"\bgit\b[\s\S]*\bpush\b|\bgh\b[\s\S]*\bpr\b")


def _irrelevant(raw: str) -> bool:
    """True when the hook input is certainly not a push or PR (so the hook can exit 0 at once)."""
    try:
        event = json.loads(raw)
        command = event.get("tool_input", {}).get("command")
    except (ValueError, AttributeError):
        return False  # let the hook report malformed input
    return isinstance(command, str) and not MAYBE_PUSH_OR_PR.search(command)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:2] == ["hook", "claude-code"] and not {"-h", "--help"} & set(args):
        try:
            raw = sys.stdin.read()
        except (OSError, ValueError, UnicodeDecodeError):
            return 0
        if _irrelevant(raw):
            return 0
        from .hook import main as hook_main

        return hook_main(args[2:], stdin_text=raw)
    from .cli import main as cli_main

    return cli_main(args)
