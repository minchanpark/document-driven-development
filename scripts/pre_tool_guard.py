#!/usr/bin/env python3
"""Shared policy engine for platform-specific PreToolUse hook adapters."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

import docflow


PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
REDIRECT_PATH = re.compile(r"(?:^|\s)(?:>|>>|1>|1>>|2>|2>>)\s*['\"]?([^\s'\";&|]+)")
MUTATING_SHELL = re.compile(
    r"(?:^|[;&|]\s*|\s)(?:apply_patch|sed\s+-i|perl\s+-pi|tee|touch|mkdir|rm|mv|cp|install|truncate|(?:npm|pnpm|yarn|pip|pip3)\s+install)\b"
)


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return value if isinstance(value, dict) else {}


def payload_value(payload: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return default


def tool_paths(tool_input: Any) -> list[str]:
    found: list[str] = []

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str):
            normalized_key = key.replace("_", "").lower()
            if normalized_key in {"path", "filepath", "targetfile"}:
                found.append(value)
            if normalized_key in {"patch", "input", "patchtext"} or "*** Begin Patch" in value:
                found.extend(match.strip() for match in PATCH_PATH.findall(value))

    visit(tool_input)
    return list(dict.fromkeys(found))


def shell_write_paths(command: str) -> tuple[list[str], bool]:
    paths = [match.strip() for match in PATCH_PATH.findall(command)]
    paths.extend(match.strip() for match in REDIRECT_PATH.findall(command))
    mutating = bool(paths or MUTATING_SHELL.search(command))
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    commands_with_targets = {
        "touch",
        "mkdir",
        "rm",
        "mv",
        "cp",
        "install",
        "truncate",
        "tee",
        "sed",
        "perl",
    }
    for index, token in enumerate(tokens):
        base = Path(token).name
        if base not in commands_with_targets:
            continue
        operands: list[str] = []
        for candidate in tokens[index + 1 :]:
            if candidate in {";", "&&", "||", "|"}:
                break
            if not candidate.startswith("-"):
                operands.append(candidate)
        if base in {"mv", "cp", "install"} and operands:
            paths.append(operands[-1])
        elif base in {"sed", "perl", "tee"} and operands:
            paths.append(operands[-1])
        elif base in {"touch", "mkdir", "rm", "truncate"}:
            paths.extend(operands)
    return list(dict.fromkeys(paths)), mutating


def normalized_call(payload: dict[str, Any]) -> tuple[str, Any, str]:
    """Return tool name, tool input, and cwd for supported hook payload shapes."""
    antigravity_call = payload.get("toolCall")
    if isinstance(antigravity_call, dict):
        tool_name = str(antigravity_call.get("name") or "")
        tool_input = antigravity_call.get("args")
        if not isinstance(tool_input, dict):
            tool_input = {}
        workspace_paths = payload.get("workspacePaths")
        workspace = (
            workspace_paths[0]
            if isinstance(workspace_paths, list) and workspace_paths
            else os.getcwd()
        )
        cwd = str(tool_input.get("Cwd") or workspace)
        return tool_name, tool_input, cwd

    tool_name = str(payload_value(payload, "tool_name", "toolName", default=""))
    tool_input = payload_value(payload, "tool_input", "toolInput", default={})
    if not isinstance(tool_input, dict):
        tool_input = {}
    cwd = str(payload_value(payload, "cwd", "working_directory", default=os.getcwd()))
    return tool_name, tool_input, cwd


def evaluate(payload: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate a normalized write attempt. Return ``(allowed, reason)``."""
    tool_name, tool_input, cwd = normalized_call(payload)
    root = docflow.find_root(cwd)
    if not (root / docflow.MANIFEST_REL).is_file():
        return True, "Document-driven development is not initialized for this repository."

    try:
        paths = tool_paths(tool_input)
        if tool_name.lower() in {"bash", "shell", "exec_command", "run_command"}:
            command = str(
                tool_input.get("command")
                or tool_input.get("cmd")
                or tool_input.get("CommandLine")
                or ""
            )
            shell_paths, mutating = shell_write_paths(command)
            paths.extend(shell_paths)
            if mutating and not paths:
                lock, errors = docflow.check_lock(root)
                if errors or not lock:
                    return False, (
                        "Potentially mutating shell command blocked because its target cannot be verified and no valid document context lock exists."
                    )
        if not paths:
            return True, "No implementation write target was detected."
        for path in dict.fromkeys(paths):
            allowed, reason = docflow.guard_edit(root, path)
            if not allowed:
                return False, reason
    except Exception as exc:  # Fail closed only inside an initialized document-driven repository.
        return False, f"Document-driven guard could not verify this write: {exc}"
    return True, "The write is covered by the current approved document context."


def main() -> int:
    """Diagnostic entry point; platform adapters should be used by hook configs."""
    allowed, reason = evaluate(read_payload())
    sys.stdout.write(json.dumps({"allowed": allowed, "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
