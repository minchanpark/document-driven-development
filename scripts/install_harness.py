#!/usr/bin/env python3
"""Install a self-contained document-driven development harness in a repository."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import docflow


BLOCK_START = "<!-- document-driven-development:start -->"
BLOCK_END = "<!-- document-driven-development:end -->"
MANAGED_HOOK_SCRIPTS = {
    "pre_tool_guard.py",
    "session_context.py",
    "codex_pre_tool.py",
    "codex_session_context.py",
    "claude_pre_tool.py",
    "claude_session_context.py",
    "antigravity_pre_tool.py",
    "antigravity_pre_invocation.py",
}


def local_command(script_name: str) -> str:
    target = f".document-driven/bin/{script_name}"
    return (
        f'TARGET={target}; d="$PWD"; '
        'while [ "$d" != "/" ]; do '
        'if [ -f "$d/$TARGET" ]; then exec python3 "$d/$TARGET"; fi; '
        'd="${d%/*}"; [ -n "$d" ] || d=/; done; '
        'echo "Unable to locate $TARGET" >&2; exit 1'
    )


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise docflow.DocflowError(
            f"Cannot merge invalid JSON file {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise docflow.DocflowError(f"Cannot merge non-object JSON file: {path}")
    return value


def merge_managed_block(path: Path, block: str) -> str:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(
        re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END), re.DOTALL
    )
    if pattern.search(current):
        updated = pattern.sub(block.strip(), current)
        result = "updated"
    else:
        separator = "\n\n" if current.strip() else ""
        updated = current.rstrip() + separator + block.strip() + "\n"
        result = "created" if not current else "updated"
    if updated != current:
        path.write_text(updated, encoding="utf-8")
    return result


def codex_hook_entries() -> dict[str, list[dict[str, Any]]]:
    return {
        "SessionStart": [
            {
                "matcher": "startup|resume|clear|compact",
                "hooks": [
                    {
                        "type": "command",
                        "command": local_command("codex_session_context.py"),
                        "statusMessage": "Loading document-driven project state",
                    }
                ],
            }
        ],
        "PreToolUse": [
            {
                "matcher": "Bash|apply_patch|Edit|Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": local_command("codex_pre_tool.py"),
                        "statusMessage": "Checking approved document context",
                    }
                ],
            }
        ],
    }


def claude_hook_entries() -> dict[str, list[dict[str, Any]]]:
    return {
        "SessionStart": [
            {
                "matcher": "startup|resume|clear|compact",
                "hooks": [
                    {
                        "type": "command",
                        "command": local_command("claude_session_context.py"),
                        "statusMessage": "Loading document-driven project state",
                    }
                ],
            }
        ],
        "PreToolUse": [
            {
                "matcher": "Bash|Edit|Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": local_command("claude_pre_tool.py"),
                        "statusMessage": "Checking approved document context",
                    }
                ],
            }
        ],
    }


def antigravity_hook_entries() -> dict[str, list[dict[str, Any]]]:
    return {
        "PreToolUse": [
            {
                "matcher": (
                    "run_command|write_to_file|replace_file_content|"
                    "multi_replace_file_content"
                ),
                "hooks": [
                    {
                        "type": "command",
                        "command": local_command("antigravity_pre_tool.py"),
                        "timeout": 30,
                    }
                ],
            }
        ],
        "PreInvocation": [
            {
                "type": "command",
                "command": local_command("antigravity_pre_invocation.py"),
                "timeout": 30,
            }
        ],
    }


def managed_hook_group(group: Any) -> bool:
    if not isinstance(group, dict):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = str(hook.get("command") or "")
        if ".document-driven/bin/" in command and any(
            name in command for name in MANAGED_HOOK_SCRIPTS
        ):
            return True
    return False


def merge_event_hooks(path: Path, additions: dict[str, list[dict[str, Any]]]) -> str:
    existed = path.exists()
    data = load_optional_json(path)
    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise docflow.DocflowError(f"Existing hooks field is not an object: {path}")
    for event, event_additions in additions.items():
        current = hooks.setdefault(event, [])
        if not isinstance(current, list):
            raise docflow.DocflowError(
                f"Existing hooks.{event} is not an array: {path}"
            )
        hooks[event] = [group for group in current if not managed_hook_group(group)]
        hooks[event].extend(event_additions)
    after = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if before != after or not existed:
        docflow.write_json(path, data)
        return "updated" if existed else "created"
    return "unchanged"


def merge_antigravity_hooks(path: Path) -> str:
    existed = path.exists()
    data = load_optional_json(path)
    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    data["document-driven-development"] = antigravity_hook_entries()
    after = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if before != after or not existed:
        docflow.write_json(path, data)
        return "updated" if existed else "created"
    return "unchanged"


def copy_if_changed(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() == source.read_bytes():
        return "unchanged"
    shutil.copy2(source, target)
    target.chmod(0o755)
    return "updated" if target.exists() else "created"


def write_if_absent(path: Path, content: bytes) -> str:
    if path.exists():
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return "created"


def merge_gitignore(path: Path) -> str:
    existed = path.exists()
    current = path.read_text(encoding="utf-8") if existed else ""
    managed = ".document-driven/.cache/"
    lines = current.splitlines()
    if managed in lines:
        return "unchanged"
    separator = "\n" if current and not current.endswith("\n") else ""
    path.write_text(current + separator + managed + "\n", encoding="utf-8")
    return "updated" if existed else "created"


def merge_policy(path: Path) -> str:
    """Add new managed defaults while preserving project-specific policy rules."""
    existed = path.exists()
    policy = load_optional_json(path)
    before = json.dumps(policy, ensure_ascii=False, sort_keys=True)
    defaults = docflow.default_policy()
    for key in (
        "schema_version",
        "manifest_path",
        "path_rules",
        "require_requirement_ids",
        "require_traceability",
    ):
        policy.setdefault(key, defaults[key])
    documentation_paths = policy.setdefault("documentation_paths", [])
    if not isinstance(documentation_paths, list) or not all(
        isinstance(item, str) for item in documentation_paths
    ):
        raise docflow.DocflowError(
            f"Existing policy documentation_paths is not a string array: {path}"
        )
    for managed_path in defaults["documentation_paths"]:
        if managed_path not in documentation_paths:
            documentation_paths.append(managed_path)
    after = json.dumps(policy, ensure_ascii=False, sort_keys=True)
    if before != after or not existed:
        docflow.write_json(path, policy)
        return "updated" if existed else "created"
    return "unchanged"


def merge_orchestration(path: Path) -> str:
    """Install safe orchestration defaults without replacing provider choices."""
    existed = path.exists()
    config = load_optional_json(path)
    before = json.dumps(config, ensure_ascii=False, sort_keys=True)
    defaults = docflow.default_orchestration()

    def apply_defaults(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if key not in target:
                target[key] = value
            elif isinstance(value, dict) and isinstance(target.get(key), dict):
                apply_defaults(target[key], value)

    apply_defaults(config, defaults)
    after = json.dumps(config, ensure_ascii=False, sort_keys=True)
    if before != after or not existed:
        docflow.write_json(path, config)
        return "updated" if existed else "created"
    return "unchanged"


def install(root: Path, ci: str) -> list[tuple[str, str]]:
    plugin_root = Path(__file__).resolve().parent.parent
    manifest = docflow.require_valid_manifest(root)
    active = [
        artifact
        for artifact in manifest["artifacts"]
        if artifact.get("status") != "superseded"
    ]
    if not active:
        raise docflow.DocflowError(
            "The approved document graph has no active artifacts"
        )
    unapproved = [
        artifact["id"] for artifact in active if artifact.get("status") != "approved"
    ]
    if unapproved:
        raise docflow.DocflowError(
            "Approve every active artifact before installing the implementation harness: "
            + ", ".join(unapproved)
        )

    results: list[tuple[str, str]] = []
    bin_dir = root / docflow.STATE_REL / "bin"
    for name in (
        "docflow.py",
        "docflow_store.py",
        "pre_tool_guard.py",
        "session_context.py",
        "codex_pre_tool.py",
        "codex_session_context.py",
        "claude_pre_tool.py",
        "claude_session_context.py",
        "antigravity_pre_tool.py",
        "antigravity_pre_invocation.py",
    ):
        target = bin_dir / name
        existed = target.exists()
        status = copy_if_changed(plugin_root / "scripts" / name, target)
        if status == "updated" and not existed:
            status = "created"
        results.append((target.relative_to(root).as_posix(), status))

    policy_path = root / docflow.POLICY_REL
    policy_status = merge_policy(policy_path)
    results.append((docflow.POLICY_REL.as_posix(), policy_status))
    orchestration_path = root / docflow.ORCHESTRATION_REL
    orchestration_status = merge_orchestration(orchestration_path)
    docflow.load_orchestration(root)
    results.append((docflow.ORCHESTRATION_REL.as_posix(), orchestration_status))
    trace_path = root / docflow.TRACE_REL
    trace_status = write_if_absent(
        trace_path,
        docflow.json_bytes(
            {"schema_version": "1.0", "storage_mode": "sharded", "entries": []}
        ),
    )
    results.append((docflow.TRACE_REL.as_posix(), trace_status))
    results.append((".gitignore", merge_gitignore(root / ".gitignore")))

    rules = (plugin_root / "assets/harness/AGENT_RULES.md").read_text(encoding="utf-8")
    for name in ("AGENTS.md", "CLAUDE.md"):
        status = merge_managed_block(root / name, rules)
        results.append((name, status))

    platform_hooks = (
        (Path(".codex/hooks.json"), codex_hook_entries()),
        (Path(".claude/settings.json"), claude_hook_entries()),
    )
    for relative, entries in platform_hooks:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        results.append((relative.as_posix(), merge_event_hooks(path, entries)))

    antigravity_path = root / ".agents/hooks.json"
    antigravity_path.parent.mkdir(parents=True, exist_ok=True)
    results.append(
        (
            antigravity_path.relative_to(root).as_posix(),
            merge_antigravity_hooks(antigravity_path),
        )
    )

    wants_github = ci == "github" or (ci == "auto" and (root / ".github").exists())
    if wants_github:
        workflow = root / ".github/workflows/document-driven-development.yml"
        content = (plugin_root / "assets/harness/github-workflow.yml").read_bytes()
        results.append(
            (workflow.relative_to(root).as_posix(), write_if_absent(workflow, content))
        )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Target repository root")
    parser.add_argument(
        "--ci",
        choices=("auto", "github", "none"),
        default="auto",
        help="Install GitHub Actions gate, detect it, or skip CI files",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    try:
        results = install(root, args.ci)
    except docflow.DocflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for path, status in results:
        print(f"{status:9} {path}")
    print(
        "Harness installed. Review .document-driven/policy.json path_rules before implementation."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
