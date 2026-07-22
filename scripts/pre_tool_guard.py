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
REDIRECT_PATH = re.compile(
    r"(?:^|[\s;&|])(?P<fd>\d*)(?P<operator>>>?)\s*(?!&)(?:['\"])?(?P<path>[^\s'\";&|]+)"
)
PACKAGE_INSTALL = re.compile(r"(?:^|[;&|]\s*|\s)(?:npm|pnpm|yarn|pip|pip3)\s+install\b")
WRITE_TOOL_NAMES = {
    "apply_patch",
    "edit",
    "write",
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
}
SHELL_TOOL_NAMES = {"bash", "shell", "exec_command", "run_command"}
SHELL_COMMANDS_WITH_TARGETS = {
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


def tool_paths(tool_name: str, tool_input: Any) -> list[str]:
    if tool_name.lower() not in WRITE_TOOL_NAMES:
        return []
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


def _shell_segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in {";", "&&", "||", "|", "&"}:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _command_index(tokens: list[str]) -> int | None:
    index = 0
    while index < len(tokens) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index]):
        index += 1
    if index < len(tokens) and Path(tokens[index]).name in {"command", "env", "sudo"}:
        index += 1
        while index < len(tokens) and tokens[index].startswith("-"):
            index += 1
        while index < len(tokens) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index]):
            index += 1
    return index if index < len(tokens) else None


def _redirect_paths(command: str) -> list[str]:
    paths: list[str] = []
    for match in REDIRECT_PATH.finditer(command):
        path = match.group("path")
        if path in {"/dev/null", "/dev/stdout", "/dev/stderr"}:
            continue
        paths.append(path)
    return paths


def _in_place_edit(base: str, options: list[str]) -> bool:
    if base == "sed":
        return any(
            option == "-i"
            or option.startswith("-i.")
            or option == "--in-place"
            or option.startswith("--in-place=")
            for option in options
        )
    if base == "perl":
        return any(
            option == "--in-place"
            or option.startswith("--in-place=")
            or (option.startswith("-") and not option.startswith("--") and "i" in option[1:])
            for option in options
        )
    return False


def shell_write_paths(command: str) -> tuple[list[str], bool]:
    paths = [match.strip() for match in PATCH_PATH.findall(command)]
    paths.extend(_redirect_paths(command))
    mutating = bool(paths or PACKAGE_INSTALL.search(command))
    for segment in _shell_segments(command):
        index = _command_index(segment)
        if index is None:
            continue
        base = Path(segment[index]).name
        if base == "apply_patch":
            mutating = True
            continue
        if base not in SHELL_COMMANDS_WITH_TARGETS:
            continue
        arguments = segment[index + 1 :]
        options = [candidate for candidate in arguments if candidate.startswith("-")]
        operands = [candidate for candidate in arguments if candidate and not candidate.startswith("-")]
        if base in {"sed", "perl"} and not _in_place_edit(base, options):
            continue
        mutating = True
        if base in {"mv", "cp", "install"} and operands:
            paths.append(operands[-1])
        elif base in {"sed", "perl"} and operands:
            paths.append(operands[-1])
        elif base == "tee" and operands:
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
    explicit_cwd = (
        tool_input.get("workdir")
        or tool_input.get("cwd")
        or tool_input.get("working_directory")
    )
    cwd = str(
        explicit_cwd
        or payload_value(payload, "cwd", "working_directory", default=os.getcwd())
    )
    return tool_name, tool_input, cwd


def evaluate(payload: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate a normalized write attempt. Return ``(allowed, reason)``."""
    tool_name, tool_input, cwd = normalized_call(payload)
    root = docflow.find_root(cwd)
    if not (root / docflow.MANIFEST_REL).is_file() and not (
        root / docflow.ADOPTION_BASELINE_REL
    ).exists():
        return True, "Document-driven development is not initialized for this repository."

    try:
        paths: list[str] = []
        if tool_name.lower() in SHELL_TOOL_NAMES:
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
        else:
            paths.extend(tool_paths(tool_name, tool_input))
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
