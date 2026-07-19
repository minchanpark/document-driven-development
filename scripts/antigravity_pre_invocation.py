#!/usr/bin/env python3
"""Antigravity PreInvocation adapter for document-driven project context."""

from __future__ import annotations

import json
import sys

import session_context


def main() -> int:
    message = session_context.context_message()
    output = {} if message is None else {"injectSteps": [{"ephemeralMessage": message}]}
    sys.stdout.write(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
