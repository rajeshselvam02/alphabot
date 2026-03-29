#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys


DENY_PATTERNS = [
    (r"\bgit\s+reset\s+--hard\b", "Destructive git reset is blocked in AlphaBot."),
    (r"\bgit\s+checkout\s+--\b", "Discarding tracked changes is blocked in AlphaBot."),
    (r"\brm\s+-rf\b", "Recursive force deletion is blocked in AlphaBot."),
    (r"\brm\s+-fr\b", "Recursive force deletion is blocked in AlphaBot."),
    (r">\s*/root/alphabot/\.env\b", "Direct overwrite of .env is blocked in AlphaBot."),
    (
        r"(?:^|\s)sed\s+-i\b.*reports/benchmarks/xauusd_best_validated_v1_dataset\.json\b",
        "Editing the frozen benchmark dataset in place is blocked; regenerate it intentionally.",
    ),
]


def main() -> int:
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")

    for pattern, reason in DENY_PATTERNS:
        if re.search(pattern, command):
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": reason,
                        },
                        "systemMessage": reason,
                    }
                )
            )
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
