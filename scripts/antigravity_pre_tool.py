#!/usr/bin/env python3
"""Antigravity PreToolUse adapter for the shared document guard."""

from __future__ import annotations

import json
import sys

import pre_tool_guard


def main() -> int:
    allowed, reason = pre_tool_guard.evaluate(pre_tool_guard.read_payload())
    sys.stdout.write(
        json.dumps(
            {"decision": "allow" if allowed else "deny", "reason": reason},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
