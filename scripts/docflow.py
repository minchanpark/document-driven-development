#!/usr/bin/env python3
"""Deterministic state and validation tools for document-driven development."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import docflow_store as store  # noqa: E402 -- installed beside this standalone script


MANIFEST_REL = Path("docs/document-manifest.json")
STATE_REL = Path(".document-driven")
LOCK_REL = STATE_REL / "context-lock.json"
POLICY_REL = STATE_REL / "policy.json"
TRACE_REL = STATE_REL / "traceability.json"
CONTEXT_PACK_REL = STATE_REL / "context-pack.json"
ORCHESTRATION_REL = STATE_REL / "orchestration.json"
PACKAGE_LOCK_REL = STATE_REL / "package-lock.json"
RUNS_REL = STATE_REL / "runs"
TRACE_SHARDS_REL = STATE_REL / "trace"
EVIDENCE_REL = STATE_REL / "evidence"
WORKTREES_REL = STATE_REL / "worktrees.json"
CONTEXT_MATCH_LIMIT = 2
CONTEXT_WINDOW_LINES = 4
CONTEXT_OUTLINE_LIMIT = 30
CONTEXT_INSTRUCTIONS = (
    "Use these approved requirement slices first.",
    "Open a cited full document only when the slice is ambiguous or a cross-cutting constraint is required.",
    "The full document hashes remain authoritative and are verified by the context lock.",
)
STATUSES = ("proposed", "drafting", "reviewed", "approved", "superseded")
TRANSITIONS = {
    "proposed": {"drafting", "superseded"},
    "drafting": {"reviewed", "superseded"},
    "reviewed": {"drafting", "superseded"},
    "approved": {"drafting", "superseded"},
    "superseded": set(),
}
RUN_STATUSES = (
    "planning",
    "approved-for-implementation",
    "implementing",
    "completed",
    "blocked",
)
PACKAGE_STATUSES = (
    "planned",
    "approved-for-implementation",
    "implementing",
    "implemented",
    "reviewing",
    "approved",
    "rejected",
    "integrated",
    "blocked",
)
PACKAGE_TRANSITIONS = {
    "approved-for-implementation": {"implementing", "blocked"},
    "implementing": {"implemented", "blocked"},
    "implemented": {"reviewing", "blocked"},
    "reviewing": {"approved", "rejected", "blocked"},
    "approved": {"integrated", "blocked"},
    "rejected": {"implementing", "blocked"},
    "planned": set(),
    "integrated": set(),
    "blocked": set(),
}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class DocflowError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DocflowError(f"Missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DocflowError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DocflowError(f"Expected a JSON object in {path}")
    return data


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(json_bytes(value))
    temporary.replace(path)


def find_root(start: str | Path | None = None) -> Path:
    current = Path(start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / MANIFEST_REL).is_file():
            return candidate
    return current


def repo_path(root: Path, raw: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        raise DocflowError(f"Repository paths must be relative: {raw}")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise DocflowError(f"Path escapes repository root: {raw}") from exc
    return resolved


def relative_path(root: Path, raw: str | Path) -> str:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise DocflowError(f"Path is outside repository: {raw}") from exc


def load_manifest(root: Path) -> dict[str, Any]:
    return load_json(root / MANIFEST_REL)


def artifact_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        return {}
    return {
        item.get("id"): item
        for item in artifacts
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item for item in value
    )


def validate_manifest(
    root: Path, manifest: dict[str, Any], *, verify_hashes: bool = True
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != "1.0":
        errors.append("schema_version must be '1.0'")

    source = manifest.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("prd"), str):
        errors.append("source.prd must be a repository-relative path")
        prd_path = None
    else:
        try:
            prd_path = repo_path(root, source["prd"])
            if not prd_path.is_file():
                errors.append(f"PRD does not exist: {source['prd']}")
        except DocflowError as exc:
            errors.append(str(exc))
            prd_path = None

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("artifacts must be an array")
        return errors

    ids: set[str] = set()
    paths: set[str] = set()
    amap: dict[str, dict[str, Any]] = {}
    for index, artifact in enumerate(artifacts):
        label = f"artifacts[{index}]"
        if not isinstance(artifact, dict):
            errors.append(f"{label} must be an object")
            continue
        artifact_id = artifact.get("id")
        if not isinstance(artifact_id, str) or not artifact_id:
            errors.append(f"{label}.id must be a non-empty string")
            continue
        if artifact_id == "prd":
            errors.append(f"{label}.id 'prd' is reserved")
        if artifact_id in ids:
            errors.append(f"Duplicate artifact id: {artifact_id}")
        ids.add(artifact_id)
        amap[artifact_id] = artifact

        status = artifact.get("status")
        path_value = artifact.get("path")
        if not isinstance(path_value, str) or not path_value:
            errors.append(f"{label}.path must be a non-empty string")
        else:
            try:
                artifact_path = repo_path(root, path_value)
                normalized = relative_path(root, artifact_path)
                if normalized in paths:
                    errors.append(f"Duplicate artifact path: {normalized}")
                paths.add(normalized)
                if status != "proposed" and not artifact_path.is_file():
                    errors.append(f"Artifact file does not exist: {path_value}")
            except DocflowError as exc:
                errors.append(str(exc))
                artifact_path = None

        if (
            not isinstance(artifact.get("purpose"), str)
            or not artifact.get("purpose", "").strip()
        ):
            errors.append(f"{label}.purpose must be a non-empty string")
        if status not in STATUSES:
            errors.append(f"{label}.status must be one of {', '.join(STATUSES)}")
        for field in ("informed_by", "depends_on", "required_for"):
            if not _string_list(artifact.get(field, [])):
                errors.append(f"{label}.{field} must be an array of non-empty strings")

        if status == "approved":
            approval = artifact.get("approval")
            if not isinstance(approval, dict):
                errors.append(f"{label}.approval is required when approved")
            else:
                for field in ("approved_by", "approved_at", "content_sha256"):
                    if not isinstance(approval.get(field), str) or not approval[field]:
                        errors.append(f"{label}.approval.{field} is required")
                if (
                    verify_hashes
                    and artifact_path
                    and artifact_path.is_file()
                    and approval.get("content_sha256")
                ):
                    actual = sha256_file(artifact_path)
                    if approval["content_sha256"] != actual:
                        errors.append(
                            f"Approved artifact changed without re-approval: {artifact_id} ({path_value})"
                        )

    for artifact_id, artifact in amap.items():
        for dependency in artifact.get("depends_on", []):
            if dependency not in amap:
                errors.append(
                    f"{artifact_id}.depends_on references unknown artifact: {dependency}"
                )
            elif (
                artifact.get("status") == "approved"
                and amap[dependency].get("status") != "approved"
            ):
                errors.append(
                    f"Approved artifact {artifact_id} depends on non-approved {dependency}"
                )
        for source_id in artifact.get("informed_by", []):
            if source_id != "prd" and source_id not in amap:
                errors.append(
                    f"{artifact_id}.informed_by references unknown source: {source_id}"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(artifact_id: str, trail: list[str]) -> None:
        if artifact_id in visiting:
            errors.append(
                "Artifact dependency cycle: " + " -> ".join((*trail, artifact_id))
            )
            return
        if artifact_id in visited:
            return
        visiting.add(artifact_id)
        for dependency in amap.get(artifact_id, {}).get("depends_on", []):
            if dependency in amap:
                visit(dependency, [*trail, artifact_id])
        visiting.remove(artifact_id)
        visited.add(artifact_id)

    for artifact_id in amap:
        visit(artifact_id, [])

    gate = manifest.get("implementation_gate")
    if not isinstance(gate, dict):
        errors.append("implementation_gate must be an object")
    else:
        for field in ("require_relevant_documents_approved", "require_traceability"):
            if gate.get(field) is not True:
                errors.append(f"implementation_gate.{field} must be true")
    return errors


def require_valid_manifest(root: Path, *, verify_hashes: bool = True) -> dict[str, Any]:
    manifest = load_manifest(root)
    errors = validate_manifest(root, manifest, verify_hashes=verify_hashes)
    if errors:
        raise DocflowError("Manifest validation failed:\n- " + "\n- ".join(errors))
    return manifest


def dependency_closure(
    amap: dict[str, dict[str, Any]], selected: Iterable[str]
) -> set[str]:
    result: set[str] = set()

    def add(artifact_id: str) -> None:
        if artifact_id in result:
            return
        if artifact_id not in amap:
            raise DocflowError(f"Unknown artifact id: {artifact_id}")
        result.add(artifact_id)
        for dependency in amap[artifact_id].get("depends_on", []):
            add(dependency)

    for artifact_id in selected:
        add(artifact_id)
    return result


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "manifest_path": MANIFEST_REL.as_posix(),
        "documentation_paths": [
            "docs/**",
            ".document-driven/**",
            ".codex/**",
            ".claude/**",
            ".agents/**",
            ".github/workflows/document-driven-development.yml",
            "AGENTS.md",
            "CLAUDE.md",
            ".gitignore",
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "NOTICE",
        ],
        "path_rules": [],
        "require_requirement_ids": True,
        "require_traceability": True,
    }


def load_policy(root: Path) -> dict[str, Any]:
    path = root / POLICY_REL
    if not path.is_file():
        return default_policy()
    policy = load_json(path)
    if policy.get("schema_version") != "1.0":
        raise DocflowError("policy.json schema_version must be '1.0'")
    if not _string_list(policy.get("documentation_paths", [])):
        raise DocflowError("policy.json documentation_paths must be a string array")
    rules = policy.get("path_rules", [])
    if not isinstance(rules, list):
        raise DocflowError("policy.json path_rules must be an array")
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or not _string_list(rule.get("patterns", [])):
            raise DocflowError(
                f"policy.json path_rules[{index}].patterns must be a string array"
            )
        if not _string_list(rule.get("requires_artifacts", [])):
            raise DocflowError(
                f"policy.json path_rules[{index}].requires_artifacts must be a string array"
            )
    return policy


def default_orchestration() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "mode": "auto",
        "plan": {
            "peer_review": "complex-only",
            "max_debate_rounds": 2,
        },
        "difficulty": {
            "orchestrate_when": [
                "multiple ownership boundaries",
                "database, authorization, migration, or infrastructure risk",
                "parallel packages with non-overlapping paths",
            ]
        },
        "gates": {
            "approve_locked_plan": True,
            "cross_review": True,
            "green_integration": True,
        },
        "max_fix_iterations": 3,
        "max_escalation_steps": 3,
        "worktree_gc": {
            "enabled": True,
            "retention_hours": 0,
        },
        "providers": [],
    }


def load_orchestration(root: Path) -> dict[str, Any]:
    path = root / ORCHESTRATION_REL
    if not path.is_file():
        return default_orchestration()
    config = load_json(path)
    if config.get("schema_version") != "1.0":
        raise DocflowError("orchestration.json schema_version must be '1.0'")
    if config.get("mode") not in {"auto", "single", "orchestrated"}:
        raise DocflowError(
            "orchestration.json mode must be auto, single, or orchestrated"
        )
    providers = config.get("providers", [])
    if not isinstance(providers, list):
        raise DocflowError("orchestration.json providers must be an array")
    gates = config.get("gates", {})
    if not isinstance(gates, dict):
        raise DocflowError("orchestration.json gates must be an object")
    for gate in ("approve_locked_plan", "cross_review", "green_integration"):
        if gates.get(gate) is not True:
            raise DocflowError(f"orchestration.json gates.{gate} must be true")
    for field in ("max_fix_iterations", "max_escalation_steps"):
        if not isinstance(config.get(field), int) or config[field] < 1:
            raise DocflowError(f"orchestration.json {field} must be a positive integer")
    worktree_gc = config.setdefault(
        "worktree_gc", {"enabled": True, "retention_hours": 0}
    )
    if not isinstance(worktree_gc, dict) or not isinstance(
        worktree_gc.get("enabled"), bool
    ):
        raise DocflowError("orchestration.json worktree_gc.enabled must be boolean")
    retention = worktree_gc.get("retention_hours")
    if not isinstance(retention, (int, float)) or retention < 0:
        raise DocflowError(
            "orchestration.json worktree_gc.retention_hours must be non-negative"
        )
    plan = config.get("plan", {})
    if not isinstance(plan, dict):
        raise DocflowError("orchestration.json plan must be an object")
    maximum_rounds = plan.get("max_debate_rounds")
    if not isinstance(maximum_rounds, int) or maximum_rounds < 0:
        raise DocflowError(
            "orchestration.json plan.max_debate_rounds must be a non-negative integer"
        )
    return config


def safe_id(value: str, label: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise DocflowError(
            f"{label} must begin with an alphanumeric character and contain only "
            "letters, numbers, '.', '_', or '-': " + value
        )
    return value


def run_path(root: Path, task_id: str) -> Path:
    return root / RUNS_REL / safe_id(task_id, "task id") / "run.json"


def load_run(root: Path, task_id: str, *, audit: bool = False) -> dict[str, Any]:
    try:
        return store.load_run(run_path(root, task_id), hydrate_events=audit)
    except ValueError as exc:
        raise DocflowError(str(exc)) from exc


def persist_run(root: Path, run: dict[str, Any]) -> None:
    store.persist_run(run_path(root, run["task_id"]), run)


def _package_contract(package: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id",
        "summary",
        "requirement_ids",
        "artifact_ids",
        "depends_on",
        "allowed_paths",
        "acceptance_criteria",
        "verification_commands",
        "verification_specs",
    )
    return {field: package.get(field) for field in fields if field in package}


def _plan_contract_sha256(run: dict[str, Any]) -> str:
    return store.canonical_hash(
        {
            "locked_plan": run.get("locked_plan"),
            "packages": [_package_contract(item) for item in run.get("packages", [])],
        }
    )


def _record_run_event(root: Path, run: dict[str, Any], event: dict[str, Any]) -> None:
    if run.get("storage_mode") == "append-only":
        store.record_run_event(run_path(root, run["task_id"]), run, event)
    else:
        run.setdefault("events", []).append(event)


def _record_package_event(
    root: Path,
    run: dict[str, Any],
    package: dict[str, Any],
    event: dict[str, Any],
) -> None:
    if run.get("storage_mode") == "append-only":
        store.record_package_event(run_path(root, run["task_id"]), package, event)
    else:
        package.setdefault("events", []).append(event)


def _validate_package_event_state(package: dict[str, Any]) -> list[str]:
    package_id = package.get("id", "<unknown>")
    state = package.get("event_state")
    if not isinstance(state, dict) or not state.get("count"):
        return [f"{package_id} requires an event_state snapshot"]
    errors = [
        f"{package_id} event snapshot: {item}" for item in state.get("errors", [])
    ]
    if state.get("last_status") != package.get("status"):
        errors.append(
            f"{package_id} event snapshot {state.get('last_status')} does not match "
            f"package status {package.get('status')}"
        )
    return errors


def _pattern_prefix(pattern: str) -> str:
    wildcard = len(pattern)
    for token in ("*", "?", "["):
        position = pattern.find(token)
        if position >= 0:
            wildcard = min(wildcard, position)
    return pattern[:wildcard].rstrip("/")


def patterns_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    left_prefix = _pattern_prefix(left)
    right_prefix = _pattern_prefix(right)
    if not left_prefix or not right_prefix:
        return True
    return (
        left_prefix == right_prefix
        or left_prefix.startswith(right_prefix + "/")
        or right_prefix.startswith(left_prefix + "/")
    )


def validate_package_events(package: dict[str, Any]) -> list[str]:
    package_id = package.get("id", "<unknown>")
    events = package.get("events", [])
    if not isinstance(events, list) or not events:
        return [f"{package_id} requires lifecycle events"]
    errors: list[str] = []
    previous: str | None = None
    implementer: str | None = None
    reviewer: str | None = None
    allowed = {key: set(value) for key, value in PACKAGE_TRANSITIONS.items()}
    allowed["planned"] = {"approved-for-implementation"}
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"{package_id} event[{index}] must be an object")
            continue
        status = event.get("status")
        actor = event.get("actor")
        note = event.get("note", "")
        if status not in PACKAGE_STATUSES:
            errors.append(f"{package_id} event[{index}] has invalid status")
            continue
        if not isinstance(actor, str) or not actor:
            errors.append(f"{package_id} event[{index}] requires actor")
        if not isinstance(note, str):
            errors.append(f"{package_id} event[{index}].note must be a string")
            note = ""
        if previous is None and status != "planned":
            errors.append(f"{package_id} lifecycle must begin at planned")
        elif (
            previous is not None
            and status != previous
            and status not in allowed.get(previous, set())
        ):
            errors.append(
                f"{package_id} event transition is invalid: {previous} -> {status}"
            )
        transitioned = status != previous
        if transitioned and status == "implementing":
            implementer = actor
            reviewer = None
        elif transitioned and status == "reviewing":
            if actor == implementer:
                errors.append(f"{package_id} reviewer must differ from implementer")
            reviewer = actor
        elif transitioned and status == "approved" and reviewer != actor:
            errors.append(f"{package_id} approval actor must be the active reviewer")
        if (
            transitioned
            and status in {"implemented", "approved", "integrated"}
            and not note
        ):
            errors.append(f"{package_id} {status} event requires evidence note")
        previous = status
    if previous != package.get("status"):
        errors.append(
            f"{package_id} final event {previous} does not match package status {package.get('status')}"
        )
    return errors


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def check_lock(
    root: Path,
    *,
    manifest: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if manifest is None:
        try:
            manifest = require_valid_manifest(root)
        except DocflowError as exc:
            return None, [str(exc)]
    lock_path = root / LOCK_REL
    try:
        lock = load_json(lock_path)
    except DocflowError as exc:
        return None, [str(exc)]

    if lock.get("schema_version") != "1.0":
        errors.append("context-lock.json schema_version must be '1.0'")
    task = lock.get("task")
    if (
        not isinstance(task, dict)
        or not isinstance(task.get("id"), str)
        or not task.get("id")
    ):
        errors.append("context lock task.id is required")
    elif not SAFE_ID.fullmatch(task["id"]):
        errors.append("context lock task.id has invalid characters")
    if (
        not isinstance(task, dict)
        or not _string_list(task.get("requirement_ids", []))
        or not task.get("requirement_ids")
    ):
        errors.append("context lock requires at least one requirement id")

    manifest_lock = lock.get("manifest")
    actual_manifest_hash = sha256_file(root / MANIFEST_REL)
    if (
        not isinstance(manifest_lock, dict)
        or manifest_lock.get("sha256") != actual_manifest_hash
    ):
        errors.append("Manifest changed after context preparation; prepare a new lock")

    documents = lock.get("documents")
    if not isinstance(documents, list) or not documents:
        errors.append(
            "context lock documents must include the PRD and selected artifacts"
        )
        documents = []
    for item in documents:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("Invalid document entry in context lock")
            continue
        try:
            path = repo_path(root, item["path"])
            if not path.is_file():
                errors.append(f"Locked document is missing: {item['path']}")
            elif item.get("sha256") != sha256_file(path):
                errors.append(f"Locked document changed: {item['path']}")
        except DocflowError as exc:
            errors.append(str(exc))

    selected = lock.get("selected_artifact_ids")
    if not _string_list(selected) or not selected:
        errors.append(
            "context lock selected_artifact_ids must be a non-empty string array"
        )
        selected = []
    amap = artifact_map(manifest)
    for artifact_id in selected:
        artifact = amap.get(artifact_id)
        if not artifact:
            errors.append(f"Locked artifact no longer exists: {artifact_id}")
        elif artifact.get("status") != "approved":
            errors.append(f"Locked artifact is no longer approved: {artifact_id}")
    return lock, errors


def _normalize_pattern(raw: str) -> str:
    if not raw or Path(raw).is_absolute():
        raise DocflowError(
            f"Package paths must be non-empty repository-relative patterns: {raw}"
        )
    parts = Path(raw).parts
    if ".." in parts:
        raise DocflowError(f"Package path escapes repository root: {raw}")
    value = Path(raw).as_posix()
    while value.startswith("./"):
        value = value[2:]
    if not value:
        raise DocflowError(f"Invalid package path pattern: {raw}")
    return value


def check_run(
    root: Path,
    task_id: str | None = None,
    *,
    lock: dict[str, Any] | None = None,
    audit: bool = False,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        orchestration = load_orchestration(root)
    except DocflowError as exc:
        return None, [str(exc)]
    if lock is None:
        lock, lock_errors = check_lock(root)
        if lock_errors or not lock:
            return None, lock_errors
    active_task_id = lock["task"]["id"]
    if task_id and task_id != active_task_id:
        errors.append(
            f"Run task {task_id} does not match active context task {active_task_id}"
        )
    task_id = task_id or active_task_id
    try:
        run = load_run(root, task_id, audit=audit)
    except DocflowError as exc:
        return None, [str(exc)]

    if run.get("schema_version") != "1.0":
        errors.append("run.json schema_version must be '1.0'")
    if run.get("task_id") != task_id:
        errors.append("run.json task_id does not match its directory")
    if run.get("status") not in RUN_STATUSES:
        errors.append("run.json has an invalid status")
    elif run.get("status") != "planning" and not isinstance(
        run.get("approved_by"), str
    ):
        errors.append("A non-planning run requires approved_by")
    if run.get("mode") not in {"single", "orchestrated"}:
        errors.append("run.json mode must be single or orchestrated")
    supersedes = run.get("supersedes", [])
    if not _string_list(supersedes) and supersedes != []:
        errors.append("run.json supersedes must be a string array")
    elif task_id in supersedes:
        errors.append("A run cannot supersede itself")
    elif audit:
        for previous_task in supersedes:
            previous_path = run_path(root, previous_task)
            if not previous_path.is_file():
                errors.append(f"Superseded run is missing: {previous_task}")
                continue
            try:
                previous = store.load_run(previous_path)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if previous.get("status") != "completed":
                errors.append(f"Superseded run is not completed: {previous_task}")
    if run.get("context_lock_sha256") != sha256_file(root / LOCK_REL):
        errors.append("Run is stale because context-lock.json changed")
    locked_plan = run.get("locked_plan")
    if not isinstance(locked_plan, dict):
        errors.append("run.json locked_plan must be an object")
    else:
        if not isinstance(locked_plan.get("summary"), str):
            errors.append("run.json locked_plan.summary must be a string")
        rounds = locked_plan.get("debate_rounds")
        maximum_rounds = orchestration.get("plan", {}).get("max_debate_rounds", 2)
        if not isinstance(rounds, int) or rounds < 0 or rounds > maximum_rounds:
            errors.append(
                f"run.json debate_rounds must be between 0 and {maximum_rounds}"
            )
        if not _string_list(locked_plan.get("constraints", [])):
            errors.append("run.json locked_plan.constraints must be a string array")

    locked_requirements = set(lock["task"]["requirement_ids"])
    locked_artifacts = set(lock["selected_artifact_ids"])
    trusted_plan_contract = False
    if run.get("status") != "planning" and run.get("plan_contract_sha256"):
        actual_plan_contract = _plan_contract_sha256(run)
        if run.get("plan_contract_sha256") != actual_plan_contract:
            errors.append("Run package contract changed after plan approval")
        else:
            trusted_plan_contract = True
    packages = run.get("packages")
    if not isinstance(packages, list) or not packages:
        errors.append("run.json requires at least one package")
        packages = []

    ids: set[str] = set()
    package_map: dict[str, dict[str, Any]] = {}
    owners: list[tuple[str, str]] = []
    for index, package in enumerate(packages):
        label = f"packages[{index}]"
        if not isinstance(package, dict):
            errors.append(f"{label} must be an object")
            continue
        package_id = package.get("id")
        if not isinstance(package_id, str) or not SAFE_ID.fullmatch(package_id):
            errors.append(f"{label}.id is invalid")
            continue
        if package_id in ids:
            errors.append(f"Duplicate package id: {package_id}")
        ids.add(package_id)
        package_map[package_id] = package
        if package.get("status") not in PACKAGE_STATUSES:
            errors.append(f"{label}.status is invalid")
        elif run.get("status") != "planning" and package.get("status") == "planned":
            errors.append(f"{package_id} is still planned after run approval")
        requirements = package.get("requirement_ids")
        if not _string_list(requirements) or not requirements:
            errors.append(f"{label}.requirement_ids must be a non-empty string array")
        else:
            unknown = sorted(set(requirements) - locked_requirements)
            if unknown:
                errors.append(
                    f"{package_id} references unlocked requirements: {', '.join(unknown)}"
                )
        artifacts = package.get("artifact_ids")
        if not _string_list(artifacts) or not artifacts:
            errors.append(f"{label}.artifact_ids must be a non-empty string array")
        else:
            unknown = sorted(set(artifacts) - locked_artifacts)
            if unknown:
                errors.append(
                    f"{package_id} references unlocked artifacts: {', '.join(unknown)}"
                )
        if not _string_list(package.get("depends_on", [])):
            errors.append(f"{label}.depends_on must be a string array")
        allowed_paths = package.get("allowed_paths")
        if not _string_list(allowed_paths) or not allowed_paths:
            errors.append(f"{label}.allowed_paths must be a non-empty string array")
        else:
            for pattern in allowed_paths:
                try:
                    normalized = _normalize_pattern(pattern)
                    if (
                        normalized.startswith(".document-driven/")
                        or normalized == ".document-driven"
                    ):
                        errors.append(
                            f"{package_id} cannot own harness state: {normalized}"
                        )
                    owners.append((package_id, normalized))
                except DocflowError as exc:
                    errors.append(str(exc))
        commands = package.get("verification_commands")
        if not _string_list(commands) or not commands:
            errors.append(
                f"{label}.verification_commands must be a non-empty string array"
            )
        specs = package.get("verification_specs", [])
        if specs:
            if not isinstance(specs, list):
                errors.append(f"{label}.verification_specs must be an array")
            else:
                spec_ids: set[str] = set()
                for spec in specs:
                    try:
                        normalized = _parse_verification_spec(
                            json.dumps(spec, ensure_ascii=False),
                            list(allowed_paths)
                            if isinstance(allowed_paths, list)
                            else [],
                        )
                        if normalized["id"] in spec_ids:
                            errors.append(
                                f"{package_id} has duplicate verification spec {normalized['id']}"
                            )
                        spec_ids.add(normalized["id"])
                    except DocflowError as exc:
                        errors.append(f"{package_id}: {exc}")
        acceptance = package.get("acceptance_criteria", [])
        if acceptance and not _string_list(acceptance):
            errors.append(f"{label}.acceptance_criteria must be a string array")
        if run.get("storage_mode") == "append-only" and not audit:
            errors.extend(_validate_package_event_state(package))
        elif not isinstance(package.get("events", []), list):
            errors.append(f"{label}.events must be an array")
        else:
            errors.extend(validate_package_events(package))

    for package_id, package in package_map.items():
        for dependency in package.get("depends_on", []):
            if dependency not in package_map:
                errors.append(f"{package_id} depends on unknown package: {dependency}")
            if dependency == package_id:
                errors.append(f"{package_id} cannot depend on itself")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(package_id: str, trail: list[str]) -> None:
        if package_id in visiting:
            errors.append(
                "Package dependency cycle: " + " -> ".join((*trail, package_id))
            )
            return
        if package_id in visited:
            return
        visiting.add(package_id)
        for dependency in package_map.get(package_id, {}).get("depends_on", []):
            if dependency in package_map:
                visit(dependency, [*trail, package_id])
        visiting.remove(package_id)
        visited.add(package_id)

    for package_id in package_map:
        visit(package_id, [])

    if audit or not trusted_plan_contract:
        for index, (left_owner, left_pattern) in enumerate(owners):
            for right_owner, right_pattern in owners[index + 1 :]:
                if left_owner != right_owner and patterns_overlap(
                    left_pattern, right_pattern
                ):
                    errors.append(
                        f"Package path ownership overlaps: {left_owner}:{left_pattern} and "
                        f"{right_owner}:{right_pattern}"
                    )
    return run, errors


def check_package_lock(
    root: Path,
    *,
    lock: dict[str, Any] | None = None,
    run: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    package_lock_path = root / PACKAGE_LOCK_REL
    if not package_lock_path.is_file():
        return None, []
    errors: list[str] = []
    try:
        package_lock = load_json(package_lock_path)
    except DocflowError as exc:
        return None, [str(exc)]
    if package_lock.get("schema_version") != "1.0":
        errors.append("package-lock.json schema_version must be '1.0'")
    if lock is None:
        lock, lock_errors = check_lock(root)
        errors.extend(lock_errors)
        if not lock:
            return package_lock, errors
    if package_lock.get("task_id") != lock["task"]["id"]:
        errors.append("Package lock task does not match the active context lock")
    if package_lock.get("context_lock_sha256") != sha256_file(root / LOCK_REL):
        errors.append("Package lock is stale because context-lock.json changed")
    if run is None:
        run, run_errors = check_run(root, lock["task"]["id"], lock=lock)
        errors.extend(run_errors)
        if not run:
            return package_lock, errors
    package_id = package_lock.get("package_id")
    package = next(
        (
            item
            for item in run.get("packages", [])
            if isinstance(item, dict) and item.get("id") == package_id
        ),
        None,
    )
    if not package:
        errors.append(f"Active package no longer exists: {package_id}")
        return package_lock, errors
    phase = package_lock.get("phase", "implementation")
    if phase not in {"implementation", "integration"}:
        errors.append(f"Package lock has invalid phase: {phase}")
    expected_status = "implementing" if phase == "implementation" else "approved"
    if package.get("status") != expected_status:
        errors.append(
            f"Active package status {package.get('status')} is invalid for {phase}: {package_id}"
        )
    if package_lock.get("allowed_paths") != package.get("allowed_paths"):
        errors.append("Package ownership changed after activation")
    if package_lock.get("requirement_ids") != package.get("requirement_ids"):
        errors.append("Package requirements changed after activation")
    if package_lock.get("artifact_ids") != package.get("artifact_ids"):
        errors.append("Package artifacts changed after activation")
    if package_lock.get("acceptance_criteria", []) != package.get(
        "acceptance_criteria", []
    ):
        errors.append("Package acceptance criteria changed after activation")
    if package_lock.get("verification_specs", []) != package.get(
        "verification_specs", []
    ):
        errors.append("Package verification specs changed after activation")
    context_pack = package_lock.get("context_pack")
    context_pack_sha256 = package_lock.get("context_pack_sha256")
    if context_pack is None and context_pack_sha256 is None:
        pass  # Backward-compatible lock created before compact contexts existed.
    elif not isinstance(context_pack, str) or not isinstance(context_pack_sha256, str):
        errors.append("Package lock context pack metadata is incomplete")
    else:
        try:
            pack_path = repo_path(root, context_pack)
            if not pack_path.is_file():
                errors.append(f"Package context pack is missing: {context_pack}")
            elif sha256_file(pack_path) != context_pack_sha256:
                errors.append("Package context pack changed after activation")
            else:
                pack = load_json(pack_path)
                errors.extend(validate_context_pack(root, lock, pack, package=package))
        except DocflowError as exc:
            errors.append(str(exc))
    return package_lock, errors


def required_artifacts_for_path(policy: dict[str, Any], path: str) -> set[str]:
    required: set[str] = set()
    for rule in policy.get("path_rules", []):
        if _matches(path, rule.get("patterns", [])):
            required.update(rule.get("requires_artifacts", []))
    return required


def is_documentation_path(
    root: Path, manifest: dict[str, Any], policy: dict[str, Any], path: str
) -> bool:
    if _matches(path, policy.get("documentation_paths", [])):
        return True
    source = manifest.get("source", {})
    dynamic_paths = {source.get("prd")}
    dynamic_paths.update(
        artifact.get("path")
        for artifact in manifest.get("artifacts", [])
        if isinstance(artifact, dict)
    )
    return path in dynamic_paths


def _issue_guard_lease(
    root: Path,
    manifest: dict[str, Any],
    lock: dict[str, Any],
    package_lock: dict[str, Any] | None,
) -> None:
    task_id = lock["task"]["id"]
    current_run = load_run(root, task_id) if run_path(root, task_id).is_file() else None
    sources = [root / MANIFEST_REL, root / LOCK_REL]
    for candidate in (
        root / POLICY_REL,
        root / PACKAGE_LOCK_REL,
        run_path(root, task_id),
    ):
        if candidate.is_file():
            sources.append(candidate)
    if package_lock and isinstance(package_lock.get("context_pack"), str):
        context_pack = root / package_lock["context_pack"]
        if context_pack.is_file():
            sources.append(context_pack)
    store.issue_lease(
        root,
        task_id=task_id,
        selected_artifacts=list(lock.get("selected_artifact_ids", [])),
        document_paths=[
            value
            for value in [
                manifest.get("source", {}).get("prd"),
                *[
                    item.get("path")
                    for item in manifest.get("artifacts", [])
                    if isinstance(item, dict)
                ],
            ]
            if isinstance(value, str)
        ],
        allowed_paths=list(package_lock.get("allowed_paths", []))
        if package_lock
        else [],
        package_id=package_lock.get("package_id") if package_lock else None,
        run_status=current_run.get("status") if current_run else None,
        sources=sources,
    )


def _guard_from_lease(
    policy: dict[str, Any], path: str, lease: dict[str, Any]
) -> tuple[bool, str]:
    package_id = lease.get("package_id")
    if package_id:
        if path == STATE_REL.as_posix() or path.startswith(STATE_REL.as_posix() + "/"):
            return True, f"Cached package lease allows run state: {path}"
        allowed_paths = lease.get("allowed_paths", [])
        if not _matches(path, allowed_paths):
            return (
                False,
                f"{path} is outside active package {package_id} ownership: "
                + ", ".join(allowed_paths),
            )
    elif lease.get("run_status") not in {None, "completed"}:
        return (
            False,
            "Implementation write blocked until a package is activated in this worktree",
        )
    selected = set(lease.get("selected_artifacts", []))
    missing = sorted(required_artifacts_for_path(policy, path) - selected)
    if missing:
        return False, f"{path} requires locked artifacts: {', '.join(missing)}"
    return True, f"Valid cached document lease for {path}"


def guard_edit(root: Path, raw_path: str) -> tuple[bool, str]:
    try:
        policy = load_policy(root)
        path = relative_path(root, raw_path)
    except DocflowError as exc:
        return False, str(exc)
    package_lock_exists = (root / PACKAGE_LOCK_REL).is_file()
    if not package_lock_exists and _matches(
        path, policy.get("documentation_paths", [])
    ):
        store.invalidate_lease(root)
        return True, f"Documentation or harness path allowed: {path}"
    lease = store.valid_lease(root)
    if lease is not None:
        if path in lease.get("document_paths", []):
            store.invalidate_lease(root)
            if lease.get("package_id"):
                return (
                    False,
                    f"Locked document cannot change while package {lease['package_id']} is active: {path}",
                )
            return (
                True,
                f"Documentation path allowed; validation lease invalidated: {path}",
            )
        return _guard_from_lease(policy, path, lease)
    try:
        manifest = require_valid_manifest(root)
    except DocflowError as exc:
        return False, str(exc)
    lock: dict[str, Any] | None = None
    if package_lock_exists:
        lock, lock_errors = check_lock(root, manifest=manifest)
        if lock_errors or not lock:
            return False, "Package write blocked. " + " ".join(lock_errors)
        package_lock, package_errors = check_package_lock(root, lock=lock)
    else:
        package_lock, package_errors = None, []
    if package_lock:
        if package_errors:
            return False, "Package write blocked. " + " ".join(package_errors)
        if path == STATE_REL.as_posix() or path.startswith(STATE_REL.as_posix() + "/"):
            _issue_guard_lease(root, manifest, lock, package_lock)
            return True, f"Document-driven run state allowed: {path}"
        allowed_paths = package_lock.get("allowed_paths", [])
        if not _matches(path, allowed_paths):
            return False, (
                f"{path} is outside active package {package_lock.get('package_id')} ownership: "
                + ", ".join(allowed_paths)
            )
    elif is_documentation_path(root, manifest, policy, path):
        store.invalidate_lease(root)
        return True, f"Documentation or harness path allowed: {path}"
    if lock is None:
        lock, errors = check_lock(root, manifest=manifest)
    else:
        errors = []
    if errors or not lock:
        return False, "Implementation write blocked. " + " ".join(errors)
    current_run_path = run_path(root, lock["task"]["id"])
    if not package_lock and current_run_path.is_file():
        run, run_errors = check_run(root, lock["task"]["id"], lock=lock)
        if run_errors or not run:
            return False, "Implementation write blocked by invalid run. " + " ".join(
                run_errors
            )
        if run.get("status") != "completed":
            return (
                False,
                "Implementation write blocked until a package is activated in this worktree",
            )
    selected = set(lock.get("selected_artifact_ids", []))
    required = required_artifacts_for_path(policy, path)
    missing = sorted(required - selected)
    if missing:
        return False, f"{path} requires locked artifacts: {', '.join(missing)}"
    if package_lock:
        _issue_guard_lease(root, manifest, lock, package_lock)
        return True, f"Valid package and document locks for {path}"
    _issue_guard_lease(root, manifest, lock, None)
    return True, f"Valid document context lock for {path}"


def cmd_check_lease(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    lease = store.valid_lease(root)
    if lease is None:
        raise DocflowError("No valid validation lease")
    print(
        f"Validation lease valid for task {lease['task_id']} until {lease['expires_at']}"
    )


def cmd_invalidate_lease(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    store.invalidate_lease(root)
    print("Validation lease invalidated")


def cmd_init(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    manifest_path = root / MANIFEST_REL
    if manifest_path.exists() and not args.force:
        raise DocflowError(f"Manifest already exists: {manifest_path}")
    prd = relative_path(root, args.prd)
    if not repo_path(root, prd).is_file():
        raise DocflowError(f"PRD does not exist: {prd}")
    manifest = {
        "schema_version": "1.0",
        "source": {"prd": prd},
        "artifacts": [],
        "implementation_gate": {
            "require_relevant_documents_approved": True,
            "require_traceability": True,
        },
    }
    write_json(manifest_path, manifest)
    print(f"Created {MANIFEST_REL}")


def cmd_validate(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    manifest = load_manifest(root)
    errors = validate_manifest(root, manifest)
    if errors:
        raise DocflowError("Manifest validation failed:\n- " + "\n- ".join(errors))
    print(f"Manifest valid: {MANIFEST_REL}")


def cmd_set_status(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    manifest = require_valid_manifest(root, verify_hashes=False)
    amap = artifact_map(manifest)
    artifact = amap.get(args.artifact)
    if not artifact:
        raise DocflowError(f"Unknown artifact id: {args.artifact}")
    current = artifact.get("status")
    if args.to not in TRANSITIONS.get(current, set()):
        raise DocflowError(f"Invalid status transition: {current} -> {args.to}")
    artifact["status"] = args.to
    if args.to != "approved":
        artifact.pop("approval", None)
    write_json(root / MANIFEST_REL, manifest)
    print(f"{args.artifact}: {current} -> {args.to}")


def cmd_approve(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    manifest = require_valid_manifest(root)
    artifact = artifact_map(manifest).get(args.artifact)
    if not artifact:
        raise DocflowError(f"Unknown artifact id: {args.artifact}")
    if artifact.get("status") != "reviewed":
        raise DocflowError("Only a reviewed artifact can be approved")
    for dependency in artifact.get("depends_on", []):
        dep = artifact_map(manifest)[dependency]
        if dep.get("status") != "approved":
            raise DocflowError(f"Approve dependency first: {dependency}")
    path = repo_path(root, artifact["path"])
    if not path.is_file():
        raise DocflowError(f"Artifact does not exist: {artifact['path']}")
    artifact["status"] = "approved"
    artifact["approval"] = {
        "approved_by": args.approved_by,
        "approved_at": utc_now(),
        "content_sha256": sha256_file(path),
    }
    write_json(root / MANIFEST_REL, manifest)
    print(
        f"Approved {args.artifact} at sha256:{artifact['approval']['content_sha256']}"
    )


def _approval_specs(values: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        artifact_id, separator, expected_hash = raw.partition("=")
        if (
            not separator
            or not SAFE_ID.fullmatch(artifact_id)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        ):
            raise DocflowError(
                "Each --approval must use artifact-id=<64-character lowercase sha256>"
            )
        previous = result.get(artifact_id)
        if previous and previous != expected_hash:
            raise DocflowError(f"Conflicting approval hashes for {artifact_id}")
        result[artifact_id] = expected_hash
    if not result:
        raise DocflowError("At least one --approval is required")
    return result


def cmd_approve_bundle(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    manifest = require_valid_manifest(root)
    amap = artifact_map(manifest)
    approvals = _approval_specs(args.approval or [])
    pending: dict[str, tuple[dict[str, Any], str]] = {}
    unchanged: list[str] = []
    for artifact_id, expected_hash in approvals.items():
        artifact = amap.get(artifact_id)
        if not artifact:
            raise DocflowError(f"Unknown artifact id: {artifact_id}")
        path = repo_path(root, artifact["path"])
        if not path.is_file():
            raise DocflowError(f"Artifact does not exist: {artifact['path']}")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise DocflowError(
                f"Approval hash mismatch for {artifact_id}: expected {expected_hash}, got {actual_hash}"
            )
        if artifact.get("status") == "approved":
            approved_hash = artifact.get("approval", {}).get("content_sha256")
            if approved_hash != expected_hash:
                raise DocflowError(
                    f"Approved artifact hash is inconsistent: {artifact_id}"
                )
            unchanged.append(artifact_id)
            continue
        if artifact.get("status") != "reviewed":
            raise DocflowError(
                f"Only a reviewed artifact can be approved: {artifact_id}"
            )
        pending[artifact_id] = (artifact, actual_hash)

    for artifact_id, (artifact, _) in pending.items():
        for dependency in artifact.get("depends_on", []):
            if (
                dependency not in pending
                and amap[dependency].get("status") != "approved"
            ):
                raise DocflowError(
                    f"Approve dependency first or include it in the same bundle: {artifact_id} -> {dependency}"
                )

    approved_at = utc_now()
    for artifact, content_hash in pending.values():
        artifact["status"] = "approved"
        artifact["approval"] = {
            "approved_by": args.approved_by,
            "approved_at": approved_at,
            "content_sha256": content_hash,
        }
    if pending:
        write_json(root / MANIFEST_REL, manifest)
    summary = []
    if pending:
        summary.append("approved " + ", ".join(pending))
    if unchanged:
        summary.append("reused " + ", ".join(unchanged))
    print("Approval bundle: " + "; ".join(summary))


def _context_ranges(
    lines: list[str], requirements: list[str]
) -> list[tuple[int, int, set[str]]]:
    ranges: list[tuple[int, int, set[str]]] = []
    for requirement in requirements:
        matches = [index for index, line in enumerate(lines) if requirement in line][
            :CONTEXT_MATCH_LIMIT
        ]
        for index in matches:
            ranges.append(
                (
                    max(0, index - CONTEXT_WINDOW_LINES),
                    min(len(lines), index + CONTEXT_WINDOW_LINES + 1),
                    {requirement},
                )
            )
    ranges.sort(key=lambda item: (item[0], item[1]))
    merged: list[tuple[int, int, set[str]]] = []
    for start, end, matched in ranges:
        if merged and start <= merged[-1][1]:
            prior_start, prior_end, prior_matched = merged[-1]
            merged[-1] = (prior_start, max(prior_end, end), prior_matched | matched)
        else:
            merged.append((start, end, set(matched)))
    return merged


def build_context_pack(
    root: Path,
    lock: dict[str, Any],
    *,
    requirements: list[str],
    artifact_ids: list[str],
    package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wanted = {"prd", *artifact_ids}
    documents: list[dict[str, Any]] = []
    for document in lock.get("documents", []):
        if document.get("id") not in wanted:
            continue
        path = repo_path(root, document["path"])
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        outline = [
            {"line": index + 1, "text": line.strip()}
            for index, line in enumerate(lines)
            if re.match(r"^#{1,6}\s+", line)
        ][:CONTEXT_OUTLINE_LIMIT]
        slices = []
        for start, end, matched in _context_ranges(lines, requirements):
            text = "\n".join(lines[start:end])
            slices.append(
                {
                    "requirement_ids": sorted(matched),
                    "start_line": start + 1,
                    "end_line": end,
                    "sha256": sha256_bytes(text.encode("utf-8")),
                    "text": text,
                }
            )
        documents.append(
            {
                "id": document["id"],
                "path": document["path"],
                "sha256": document["sha256"],
                "outline": outline,
                "slices": slices,
            }
        )
    task = lock["task"]
    pack: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": lock.get("created_at", ""),
        "context_lock_sha256": sha256_file(root / LOCK_REL),
        "task": {
            "id": task["id"],
            "summary": task.get("summary", ""),
            "requirement_ids": requirements,
        },
        "artifact_ids": artifact_ids,
        "documents": documents,
        "instructions": list(CONTEXT_INSTRUCTIONS),
    }
    if package is not None:
        pack["package"] = {
            "id": package["id"],
            "summary": package.get("summary", ""),
            "allowed_paths": package.get("allowed_paths", []),
            "acceptance_criteria": package.get("acceptance_criteria", []),
            "verification_commands": package.get("verification_commands", []),
            "verification_specs": package.get("verification_specs", []),
        }
    return pack


def validate_context_pack(
    root: Path,
    lock: dict[str, Any],
    pack: dict[str, Any],
    *,
    package: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if pack.get("schema_version") != "1.0":
        errors.append("Context pack schema_version must be '1.0'")
    if pack.get("context_lock_sha256") != sha256_file(root / LOCK_REL):
        errors.append("Context pack is stale because context-lock.json changed")
    if pack.get("generated_at") != lock.get("created_at", ""):
        errors.append("Context pack generation marker does not match the lock")
    if pack.get("instructions") != list(CONTEXT_INSTRUCTIONS):
        errors.append("Context pack instructions changed")
    expected_requirements = (
        package.get("requirement_ids", [])
        if package
        else lock["task"]["requirement_ids"]
    )
    expected_artifacts = (
        package.get("artifact_ids", []) if package else lock["selected_artifact_ids"]
    )
    task = pack.get("task")
    if not isinstance(task, dict) or task.get("id") != lock["task"]["id"]:
        errors.append("Context pack task does not match the active lock")
    elif task.get("requirement_ids") != expected_requirements:
        errors.append("Context pack requirements do not match its task or package")
    elif task.get("summary") != lock["task"].get("summary", ""):
        errors.append("Context pack task summary does not match the active lock")
    if pack.get("artifact_ids") != expected_artifacts:
        errors.append("Context pack artifacts do not match its task or package")
    if package is not None:
        package_value = pack.get("package")
        expected_package = {
            "id": package["id"],
            "summary": package.get("summary", ""),
            "allowed_paths": package.get("allowed_paths", []),
            "acceptance_criteria": package.get("acceptance_criteria", []),
            "verification_commands": package.get("verification_commands", []),
            "verification_specs": package.get("verification_specs", []),
        }
        if package_value != expected_package:
            errors.append("Context pack package does not match the active package")

    locked_documents = {
        item.get("id"): item
        for item in lock.get("documents", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    expected_document_ids = {"prd", *expected_artifacts}
    documents = pack.get("documents")
    if not isinstance(documents, list):
        return [*errors, "Context pack documents must be an array"]
    seen: set[str] = set()
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get("id"), str):
            errors.append("Context pack has an invalid document entry")
            continue
        document_id = document["id"]
        if document_id in seen:
            errors.append(f"Context pack has a duplicate document: {document_id}")
        seen.add(document_id)
        locked = locked_documents.get(document_id)
        if (
            not locked
            or document.get("path") != locked.get("path")
            or document.get("sha256") != locked.get("sha256")
        ):
            errors.append(
                f"Context pack document is not bound to the lock: {document_id}"
            )
            continue
        try:
            lines = (
                repo_path(root, locked["path"])
                .read_text(encoding="utf-8", errors="replace")
                .splitlines()
            )
        except (DocflowError, OSError) as exc:
            errors.append(str(exc))
            continue
        expected_outline = [
            {"line": index + 1, "text": line.strip()}
            for index, line in enumerate(lines)
            if re.match(r"^#{1,6}\s+", line)
        ][:CONTEXT_OUTLINE_LIMIT]
        if document.get("outline") != expected_outline:
            errors.append(f"Context pack outline changed: {document_id}")
        slices = document.get("slices")
        if not isinstance(slices, list):
            errors.append(f"Context pack slices must be an array: {document_id}")
            continue
        for item in slices:
            if not isinstance(item, dict):
                errors.append(f"Invalid context slice: {document_id}")
                continue
            start = item.get("start_line")
            end = item.get("end_line")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 1
                or end < start
            ):
                errors.append(f"Invalid context slice range: {document_id}")
                continue
            actual_text = "\n".join(lines[start - 1 : end])
            matched_requirements = item.get("requirement_ids")
            if (
                not _string_list(matched_requirements)
                or not matched_requirements
                or not set(matched_requirements).issubset(set(expected_requirements))
                or any(
                    requirement not in actual_text
                    for requirement in matched_requirements
                )
            ):
                errors.append(
                    f"Context slice requirement labels changed: {document_id}:{start}-{end}"
                )
            if item.get("text") != actual_text:
                errors.append(
                    f"Context slice text changed: {document_id}:{start}-{end}"
                )
            if item.get("sha256") != sha256_bytes(actual_text.encode("utf-8")):
                errors.append(
                    f"Context slice hash changed: {document_id}:{start}-{end}"
                )
    missing = sorted(expected_document_ids - seen)
    unexpected = sorted(seen - expected_document_ids)
    if missing:
        errors.append("Context pack is missing documents: " + ", ".join(missing))
    if unexpected:
        errors.append("Context pack has unexpected documents: " + ", ".join(unexpected))
    return errors


def context_pack_path(root: Path, task_id: str, package_id: str | None = None) -> Path:
    if package_id is None:
        return root / CONTEXT_PACK_REL
    return run_path(root, task_id).parent / "context" / f"{package_id}.json"


def write_context_pack(
    root: Path,
    lock: dict[str, Any],
    *,
    requirements: list[str],
    artifact_ids: list[str],
    package: dict[str, Any] | None = None,
) -> Path:
    path = context_pack_path(
        root, lock["task"]["id"], package.get("id") if package else None
    )
    write_json(
        path,
        build_context_pack(
            root,
            lock,
            requirements=requirements,
            artifact_ids=artifact_ids,
            package=package,
        ),
    )
    return path


def cmd_prepare(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    safe_id(args.task_id, "task id")
    manifest = require_valid_manifest(root)
    amap = artifact_map(manifest)
    selected: set[str] = set(args.artifact or [])
    scopes = set(args.scope or [])
    for artifact_id, artifact in amap.items():
        if scopes.intersection(artifact.get("required_for", [])):
            selected.add(artifact_id)
    if not selected:
        raise DocflowError("No artifacts selected. Pass --scope or --artifact")
    selected = dependency_closure(amap, selected)
    non_approved = sorted(
        artifact_id
        for artifact_id in selected
        if amap[artifact_id].get("status") != "approved"
    )
    if non_approved:
        raise DocflowError(
            "Selected artifacts are not approved: " + ", ".join(non_approved)
        )
    requirements = list(dict.fromkeys(args.requirement or []))
    if not requirements:
        raise DocflowError("At least one --requirement id is required")

    source_path = manifest["source"]["prd"]
    search_paths = [repo_path(root, source_path)] + [
        repo_path(root, amap[item]["path"]) for item in selected
    ]
    searchable = "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in search_paths
    )
    missing_requirements = [item for item in requirements if item not in searchable]
    if missing_requirements:
        raise DocflowError(
            "Requirement ids are not present in the PRD or selected documents: "
            + ", ".join(missing_requirements)
        )

    ordered = [item["id"] for item in manifest["artifacts"] if item["id"] in selected]
    documents = [
        {
            "id": "prd",
            "path": source_path,
            "sha256": sha256_file(repo_path(root, source_path)),
        }
    ]
    documents.extend(
        {
            "id": artifact_id,
            "path": amap[artifact_id]["path"],
            "sha256": sha256_file(repo_path(root, amap[artifact_id]["path"])),
        }
        for artifact_id in ordered
    )
    lock = {
        "schema_version": "1.0",
        "created_at": utc_now(),
        "task": {
            "id": args.task_id,
            "summary": args.summary or "",
            "requirement_ids": requirements,
            "scopes": list(dict.fromkeys(args.scope or [])),
        },
        "manifest": {
            "path": MANIFEST_REL.as_posix(),
            "sha256": sha256_file(root / MANIFEST_REL),
        },
        "selected_artifact_ids": ordered,
        "documents": documents,
    }
    write_json(root / LOCK_REL, lock)
    (root / PACKAGE_LOCK_REL).unlink(missing_ok=True)
    write_context_pack(
        root,
        lock,
        requirements=requirements,
        artifact_ids=ordered,
    )
    print(f"Prepared task {args.task_id} with artifacts: {', '.join(ordered)}")


def cmd_context_pack(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    lock, errors = check_lock(root)
    if errors or not lock:
        raise DocflowError(
            "Cannot build context pack with invalid lock:\n- " + "\n- ".join(errors)
        )
    package = None
    requirements = list(lock["task"]["requirement_ids"])
    artifacts = list(lock["selected_artifact_ids"])
    if args.package:
        run, run_errors = check_run(root, lock=lock)
        if run_errors or not run:
            raise DocflowError(
                "Cannot build package context:\n- " + "\n- ".join(run_errors)
            )
        package = next(
            (
                item
                for item in run.get("packages", [])
                if item.get("id") == args.package
            ),
            None,
        )
        if not package:
            raise DocflowError(f"Unknown package id: {args.package}")
        requirements = list(package["requirement_ids"])
        artifacts = list(package["artifact_ids"])
    pack = build_context_pack(
        root,
        lock,
        requirements=requirements,
        artifact_ids=artifacts,
        package=package,
    )
    if args.stdout:
        sys.stdout.write(json_bytes(pack).decode("utf-8"))
        return
    output = context_pack_path(
        root, lock["task"]["id"], package.get("id") if package else None
    )
    write_json(output, pack)
    print(f"Wrote context pack: {output.relative_to(root)}")


def cmd_check_context_pack(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    lock, errors = check_lock(root)
    if errors or not lock:
        raise DocflowError(
            "Cannot check context pack with invalid lock:\n- " + "\n- ".join(errors)
        )
    package = None
    if args.package:
        run, run_errors = check_run(root, lock=lock)
        if run_errors or not run:
            raise DocflowError(
                "Cannot check package context:\n- " + "\n- ".join(run_errors)
            )
        package = next(
            (
                item
                for item in run.get("packages", [])
                if item.get("id") == args.package
            ),
            None,
        )
        if not package:
            raise DocflowError(f"Unknown package id: {args.package}")
    path = (
        repo_path(root, args.path)
        if args.path
        else context_pack_path(
            root, lock["task"]["id"], package.get("id") if package else None
        )
    )
    pack = load_json(path)
    pack_errors = validate_context_pack(root, lock, pack, package=package)
    if pack_errors:
        raise DocflowError("Context pack invalid:\n- " + "\n- ".join(pack_errors))
    print(f"Context pack valid: {path.relative_to(root)}")


def cmd_check_lock(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    lock, errors = check_lock(root)
    if errors or not lock:
        raise DocflowError("Context lock invalid:\n- " + "\n- ".join(errors))
    print(f"Context lock valid for task {lock['task']['id']}")


VERIFICATION_TYPES = {"command", "unit", "integration", "hosted", "external", "manual"}
BLOCKING_PHASES = {"package", "integration", "release"}
CACHE_POLICIES = {"input-hash", "environment", "never"}
TOOLCHAIN_INPUTS = {
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "poetry.lock",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "Gemfile.lock",
    "Dockerfile",
}


def _parse_verification_spec(raw: str, default_paths: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DocflowError(f"Invalid --verification-spec JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise DocflowError("--verification-spec must be a JSON object")
    gate_id = value.get("id")
    if not isinstance(gate_id, str):
        raise DocflowError("verification spec id is required")
    safe_id(gate_id, "verification spec id")
    gate_type = value.get("type", "command")
    if gate_type not in VERIFICATION_TYPES:
        raise DocflowError(
            "verification spec type must be command, unit, integration, hosted, external, or manual"
        )
    command = value.get("command", "")
    if gate_type != "manual" and (not isinstance(command, str) or not command):
        raise DocflowError(f"verification spec {gate_id} requires command")
    if not isinstance(command, str):
        raise DocflowError(f"verification spec {gate_id} command must be a string")
    requires = value.get("requires", [])
    input_paths = value.get("input_paths", default_paths)
    if not _string_list(requires) and requires != []:
        raise DocflowError(
            f"verification spec {gate_id} requires must be a string array"
        )
    if not _string_list(input_paths):
        raise DocflowError(
            f"verification spec {gate_id} input_paths must be a non-empty string array"
        )
    phase = value.get("blocking_phase", "package")
    if phase not in BLOCKING_PHASES:
        raise DocflowError(f"verification spec {gate_id} has invalid blocking_phase")
    cache_policy = value.get("cache_policy", "input-hash")
    if cache_policy not in CACHE_POLICIES:
        raise DocflowError(f"verification spec {gate_id} has invalid cache_policy")
    return {
        "id": gate_id,
        "type": gate_type,
        "command": command,
        "requires": list(dict.fromkeys(requires)),
        "input_paths": list(
            dict.fromkeys(_normalize_pattern(item) for item in input_paths)
        ),
        "blocking_phase": phase,
        "cache_policy": cache_policy,
    }


def _verification_evidence_errors(package: dict[str, Any], target: str) -> list[str]:
    phase_rank = {"package": 1, "integration": 2, "release": 3}
    target_rank = {
        "approved": 1,
        "integrated": 2,
        "completed": 3,
    }.get(target, 0)
    if target_rank == 0:
        return []
    evidence = package.get("evidence", {})
    errors: list[str] = []
    for spec in package.get("verification_specs", []):
        if (
            not isinstance(spec, dict)
            or phase_rank.get(spec.get("blocking_phase"), 99) > target_rank
        ):
            continue
        gate_id = spec.get("id")
        item = evidence.get(gate_id, {}) if isinstance(evidence, dict) else {}
        if item.get("status") not in {"passed", "reused", "attested"}:
            errors.append(
                f"{package.get('id')} gate {gate_id} is {item.get('status', 'missing')} "
                f"and blocks {spec.get('blocking_phase')}"
            )
    return errors


def _available_inputs(root: Path, patterns: list[str]) -> list[dict[str, str]]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        paths = [line for line in result.stdout.splitlines() if line]
    else:
        paths = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        ]
    selected = sorted(
        {path for path in paths if _matches(path, patterns)}
        | {path for path in paths if path in TOOLCHAIN_INPUTS}
    )
    inputs: list[dict[str, str]] = []
    for relative in selected:
        path = root / relative
        if path.is_file():
            inputs.append({"path": relative, "sha256": sha256_file(path)})
    return inputs


def _environment_map(values: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values or []:
        key, separator, value = raw.partition("=")
        if not separator or not key:
            raise DocflowError("--environment values must use NAME=VALUE")
        result[key] = sha256_bytes(value.encode("utf-8"))
    return result


def _verification_fingerprint(
    root: Path,
    lock: dict[str, Any],
    package: dict[str, Any],
    spec: dict[str, Any],
    environment: dict[str, str],
) -> tuple[str, list[dict[str, str]]]:
    inputs = _available_inputs(root, spec["input_paths"])
    context = {
        "documents": [
            {"path": item.get("path"), "sha256": item.get("sha256")}
            for item in lock.get("documents", [])
            if isinstance(item, dict)
        ],
        "package": _package_contract(package),
        "gate": spec,
        "inputs": inputs,
        "environment": environment,
    }
    return store.canonical_hash(context), inputs


def _record_verification_evidence(
    root: Path,
    run: dict[str, Any],
    package: dict[str, Any],
    spec: dict[str, Any],
    record: dict[str, Any],
) -> None:
    package.setdefault("evidence", {})[spec["id"]] = {
        "status": record["status"],
        "fingerprint": record.get("fingerprint"),
        "recorded_at": record["recorded_at"],
        "reused_from": record.get("reused_from"),
    }
    store.write_json(
        store.run_evidence_path(root, run["task_id"], package["id"], spec["id"]),
        record,
    )
    if record["status"] in {"passed", "attested"} and record.get("fingerprint"):
        store.write_json(
            store.evidence_path(root, spec["id"], record["fingerprint"]), record
        )
    persist_run(root, run)


def _package_for_verification(
    root: Path,
    package_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    run, _, lock = _editable_run(root)
    package = next(
        (item for item in run.get("packages", []) if item.get("id") == package_id), None
    )
    if not package:
        raise DocflowError(f"Unknown package id: {package_id}")
    if not package.get("verification_specs"):
        raise DocflowError(f"Package {package_id} has no structured verification specs")
    return run, package, lock


def cmd_preflight(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, package, _ = _package_for_verification(root, args.package)
    available = set(args.available or [])
    selected = [
        item
        for item in package["verification_specs"]
        if not args.gate or item["id"] == args.gate
    ]
    if not selected:
        raise DocflowError(f"Unknown verification gate: {args.gate}")
    for spec in selected:
        missing = sorted(set(spec["requires"]) - available)
        status = "unavailable" if missing or spec["type"] == "manual" else "ready"
        record = {
            "schema_version": "1.0",
            "task_id": run["task_id"],
            "package_id": package["id"],
            "gate_id": spec["id"],
            "gate_type": spec["type"],
            "status": status,
            "missing_requirements": missing,
            "recorded_at": utc_now(),
        }
        preflight_path = (
            root
            / RUNS_REL
            / run["task_id"]
            / "evidence"
            / package["id"]
            / "preflight"
            / f"{spec['id']}.json"
        )
        write_json(preflight_path, record)
        print(
            f"{spec['id']}: {status}" + (f" ({', '.join(missing)})" if missing else "")
        )


def cmd_verify_package(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, package, lock = _package_for_verification(root, args.package)
    if args.timeout < 1:
        raise DocflowError("--timeout must be a positive number of seconds")
    if (args.execute or args.attest) and run.get("status") == "planning":
        raise DocflowError(
            "Verification commands and attestations require an approved run"
        )
    available = set(args.available or [])
    environment = _environment_map(args.environment)
    selected = [
        item
        for item in package["verification_specs"]
        if not args.gate or item["id"] == args.gate
    ]
    if not selected:
        raise DocflowError(f"Unknown verification gate: {args.gate}")
    failures: list[str] = []
    changed = git_changed_paths(root, args.base_ref) if args.base_ref else None
    for spec in selected:
        missing = sorted(set(spec["requires"]) - available)
        fingerprint, inputs = _verification_fingerprint(
            root, lock, package, spec, environment
        )
        record: dict[str, Any] = {
            "schema_version": "1.0",
            "task_id": run["task_id"],
            "package_id": package["id"],
            "gate_id": spec["id"],
            "gate_type": spec["type"],
            "blocking_phase": spec["blocking_phase"],
            "fingerprint": fingerprint,
            "input_count": len(inputs),
            "environment_fingerprint": store.canonical_hash(environment),
            "recorded_at": utc_now(),
        }
        if missing:
            record.update(status="unavailable", missing_requirements=missing)
            _record_verification_evidence(root, run, package, spec, record)
            print(f"{spec['id']}: unavailable ({', '.join(missing)})")
            continue
        if spec["cache_policy"] == "environment" and not environment:
            record.update(
                status="unavailable", reason="environment-fingerprint-required"
            )
            _record_verification_evidence(root, run, package, spec, record)
            print(f"{spec['id']}: unavailable (environment fingerprint required)")
            continue
        if spec["type"] == "manual":
            if not args.attest or not args.note:
                record.update(
                    status="unavailable", reason="manual-attestation-required"
                )
                _record_verification_evidence(root, run, package, spec, record)
                print(f"{spec['id']}: unavailable (manual attestation required)")
                continue
            record.update(status="attested", note=args.note, actor=args.actor)
            _record_verification_evidence(root, run, package, spec, record)
            print(f"{spec['id']}: attested")
            continue
        reusable = store.evidence_path(root, spec["id"], fingerprint)
        if spec["cache_policy"] != "never" and reusable.is_file():
            prior = load_json(reusable)
            if prior.get("status") in {"passed", "attested"}:
                record.update(
                    status="reused",
                    reused_from={
                        "task_id": prior.get("task_id"),
                        "package_id": prior.get("package_id"),
                        "recorded_at": prior.get("recorded_at"),
                    },
                )
                _record_verification_evidence(root, run, package, spec, record)
                print(f"{spec['id']}: reused")
                continue
        impacted = changed is None or any(
            _matches(path, spec["input_paths"]) for path in changed
        )
        if not args.execute:
            record.update(status="ready", impacted=impacted)
            _record_verification_evidence(root, run, package, spec, record)
            print(
                f"{spec['id']}: ready"
                + ("" if impacted else " (not impacted; no reusable proof)")
            )
            continue
        started = utc_now()
        try:
            completed = subprocess.run(
                spec["command"],
                shell=True,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=args.timeout,
                env=os.environ.copy(),
            )
            record.update(
                status="passed" if completed.returncode == 0 else "failed",
                started_at=started,
                returncode=completed.returncode,
                command_sha256=sha256_bytes(spec["command"].encode("utf-8")),
                stdout_sha256=sha256_bytes(completed.stdout.encode("utf-8")),
                stderr_sha256=sha256_bytes(completed.stderr.encode("utf-8")),
                stdout_bytes=len(completed.stdout.encode("utf-8")),
                stderr_bytes=len(completed.stderr.encode("utf-8")),
            )
        except subprocess.TimeoutExpired as exc:
            record.update(
                status="failed",
                started_at=started,
                reason="timeout",
                timeout_seconds=exc.timeout,
            )
        _record_verification_evidence(root, run, package, spec, record)
        print(f"{spec['id']}: {record['status']}")
        if record["status"] == "failed":
            failures.append(spec["id"])
    if failures:
        raise DocflowError("Verification gates failed: " + ", ".join(failures))


def _event(actor: str, status: str, note: str = "") -> dict[str, str]:
    return store.new_event(actor, status, note)


def _editable_run(root: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    lock, errors = check_lock(root)
    if errors or not lock:
        raise DocflowError(
            "A valid context lock is required:\n- " + "\n- ".join(errors)
        )
    path = run_path(root, lock["task"]["id"])
    run = load_run(root, lock["task"]["id"])
    if run.get("context_lock_sha256") != sha256_file(root / LOCK_REL):
        raise DocflowError(
            "Run is stale because context-lock.json changed; start a new run"
        )
    return run, path, lock


def cmd_start_run(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    lock, errors = check_lock(root)
    if errors or not lock:
        raise DocflowError(
            "Cannot start run with invalid context lock:\n- " + "\n- ".join(errors)
        )
    config = load_orchestration(root)
    mode = args.mode
    if mode == "auto":
        configured = config.get("mode", "auto")
        mode = "orchestrated" if configured == "auto" else configured
    maximum_rounds = config.get("plan", {}).get("max_debate_rounds", 2)
    if args.debate_rounds < 0 or args.debate_rounds > maximum_rounds:
        raise DocflowError(f"--debate-rounds must be between 0 and {maximum_rounds}")
    path = run_path(root, lock["task"]["id"])
    if path.exists() and not args.force:
        raise DocflowError(
            f"Run already exists: {path.relative_to(root)}; pass --force to replace it"
        )
    supersedes = list(dict.fromkeys(args.supersedes or []))
    for previous_task in supersedes:
        if previous_task == lock["task"]["id"]:
            raise DocflowError("A run cannot supersede itself")
        previous_path = run_path(root, previous_task)
        if not previous_path.is_file():
            raise DocflowError(f"Superseded run does not exist: {previous_task}")
        try:
            previous = store.load_run(previous_path)
        except ValueError as exc:
            raise DocflowError(str(exc)) from exc
        if previous.get("status") != "completed":
            raise DocflowError(
                f"Only a completed run may be superseded: {previous_task}"
            )
    store.run_events_path(path).unlink(missing_ok=True)
    run = {
        "schema_version": "1.0",
        "storage_mode": "append-only",
        "task_id": lock["task"]["id"],
        "mode": mode,
        "status": "planning",
        "created_at": utc_now(),
        "context_lock_sha256": sha256_file(root / LOCK_REL),
        "locked_plan": {
            "summary": args.plan_summary or lock["task"].get("summary", ""),
            "debate_rounds": args.debate_rounds,
            "constraints": list(dict.fromkeys(args.constraint or [])),
        },
        "packages": [],
        "supersedes": supersedes,
    }
    _record_run_event(
        root, run, _event(args.actor, "planning", "Run created from approved documents")
    )
    persist_run(root, run)
    (root / PACKAGE_LOCK_REL).unlink(missing_ok=True)
    print(f"Started {mode} run for task {lock['task']['id']}")


def cmd_add_package(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, path, lock = _editable_run(root)
    if run.get("status") != "planning":
        raise DocflowError("Packages can only be added while the run is planning")
    package_id = safe_id(args.package, "package id")
    packages = run.setdefault("packages", [])
    if any(item.get("id") == package_id for item in packages if isinstance(item, dict)):
        raise DocflowError(f"Package already exists: {package_id}")
    requirements = list(dict.fromkeys(args.requirement or []))
    artifacts = list(dict.fromkeys(args.artifact or []))
    unknown_requirements = sorted(
        set(requirements) - set(lock["task"]["requirement_ids"])
    )
    unknown_artifacts = sorted(set(artifacts) - set(lock["selected_artifact_ids"]))
    if unknown_requirements:
        raise DocflowError(
            "Package references unlocked requirements: "
            + ", ".join(unknown_requirements)
        )
    if unknown_artifacts:
        raise DocflowError(
            "Package references unlocked artifacts: " + ", ".join(unknown_artifacts)
        )
    allowed_paths = list(
        dict.fromkeys(_normalize_pattern(item) for item in args.allowed_path or [])
    )
    for existing in packages:
        if not isinstance(existing, dict):
            continue
        for left in allowed_paths:
            for right in existing.get("allowed_paths", []):
                if patterns_overlap(left, right):
                    raise DocflowError(
                        f"Package path ownership overlaps with {existing.get('id')}: {left} and {right}"
                    )
    package = {
        "id": package_id,
        "summary": args.summary or "",
        "requirement_ids": requirements,
        "artifact_ids": artifacts,
        "depends_on": list(dict.fromkeys(args.depends_on or [])),
        "allowed_paths": allowed_paths,
        "acceptance_criteria": list(dict.fromkeys(args.acceptance or [])),
        "verification_commands": list(dict.fromkeys(args.verification_command or [])),
        "status": "planned",
    }
    specs = [
        _parse_verification_spec(raw, allowed_paths)
        for raw in args.verification_spec or []
    ]
    if specs:
        ids = [item["id"] for item in specs]
        if len(ids) != len(set(ids)):
            raise DocflowError("verification spec ids must be unique within a package")
        package["verification_specs"] = specs
    packages.append(package)
    _record_package_event(
        root, run, package, _event(args.actor, "planned", "Work package defined")
    )
    persist_run(root, run)
    print(f"Added package {package_id}")


def cmd_approve_run(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, path, _ = _editable_run(root)
    if run.get("status") != "planning":
        raise DocflowError("Only a planning run can be approved")
    checked, errors = check_run(root, run.get("task_id"), audit=True)
    if errors or not checked:
        raise DocflowError("Run cannot be approved:\n- " + "\n- ".join(errors))
    run = checked
    run["status"] = "approved-for-implementation"
    run["approved_by"] = args.approved_by
    run["approved_at"] = utc_now()
    run["plan_contract_sha256"] = _plan_contract_sha256(run)
    _record_run_event(
        root,
        run,
        _event(
            args.approved_by,
            "approved-for-implementation",
            "Locked plan and packages approved",
        ),
    )
    for package in run["packages"]:
        package["status"] = "approved-for-implementation"
        _record_package_event(
            root,
            run,
            package,
            _event(
                args.approved_by,
                "approved-for-implementation",
                "Approved as part of run",
            ),
        )
    persist_run(root, run)
    print(f"Approved run for task {run['task_id']}")


def cmd_activate_package(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, path, lock = _editable_run(root)
    if run.get("status") not in {"approved-for-implementation", "implementing"}:
        raise DocflowError("Run is not approved for implementation")
    active_lock = root / PACKAGE_LOCK_REL
    if active_lock.is_file():
        current, current_errors = check_package_lock(root)
        if current_errors:
            raise DocflowError(
                "Existing package lock is invalid:\n- " + "\n- ".join(current_errors)
            )
        if current and current.get("package_id") != args.package:
            raise DocflowError(
                f"Package {current.get('package_id')} is already active in this worktree"
            )
    package = next(
        (item for item in run["packages"] if item.get("id") == args.package), None
    )
    if not package:
        raise DocflowError(f"Unknown package id: {args.package}")
    if package.get("status") not in {
        "approved-for-implementation",
        "rejected",
        "implementing",
    }:
        raise DocflowError(
            f"Package cannot be activated from status {package.get('status')}"
        )
    package_map = {item["id"]: item for item in run["packages"]}
    waiting = [
        dependency
        for dependency in package.get("depends_on", [])
        if package_map[dependency].get("status") != "integrated"
    ]
    if waiting:
        raise DocflowError(
            "Package dependencies are not integrated: " + ", ".join(waiting)
        )
    if package.get("status") != "implementing":
        package["status"] = "implementing"
        _record_package_event(
            root, run, package, _event(args.actor, "implementing", args.note or "")
        )
    run["status"] = "implementing"
    persist_run(root, run)
    package_lock = {
        "schema_version": "1.0",
        "created_at": utc_now(),
        "phase": "implementation",
        "task_id": lock["task"]["id"],
        "package_id": package["id"],
        "context_lock_sha256": sha256_file(root / LOCK_REL),
        "requirement_ids": package["requirement_ids"],
        "artifact_ids": package["artifact_ids"],
        "allowed_paths": package["allowed_paths"],
        "acceptance_criteria": package.get("acceptance_criteria", []),
        "verification_commands": package["verification_commands"],
        "verification_specs": package.get("verification_specs", []),
    }
    pack_path = write_context_pack(
        root,
        lock,
        requirements=list(package["requirement_ids"]),
        artifact_ids=list(package["artifact_ids"]),
        package=package,
    )
    package_lock["context_pack"] = pack_path.relative_to(root).as_posix()
    package_lock["context_pack_sha256"] = sha256_file(pack_path)
    write_json(active_lock, package_lock)
    print(f"Activated package {package['id']}")


def _last_actor_for_status(package: dict[str, Any], status: str) -> str | None:
    state = package.get("event_state")
    if isinstance(state, dict):
        actor = state.get("actors_by_status", {}).get(status)
        return actor if isinstance(actor, str) else None
    for event in reversed(package.get("events", [])):
        if isinstance(event, dict) and event.get("status") == status:
            actor = event.get("actor")
            return actor if isinstance(actor, str) else None
    return None


def _git_worktrees(root: Path) -> dict[Path, str]:
    result = subprocess.run(
        ["git", "-C", str(root), "worktree", "list", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    worktrees: dict[Path, str] = {}
    path: Path | None = None
    commit = ""
    for line in [*result.stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree ")).resolve()
            commit = ""
        elif line.startswith("HEAD "):
            commit = line.removeprefix("HEAD ")
        elif not line and path is not None:
            worktrees[path] = commit
            path = None
    return worktrees


def _register_imported_worktree(
    root: Path,
    source: Path,
    task_id: str,
    package_id: str,
    allowed_paths: list[str],
) -> None:
    known = _git_worktrees(root)
    source = source.resolve()
    if source not in known or source == root.resolve():
        return
    try:
        registry = store.load_worktree_registry(root)
    except (ValueError, json.JSONDecodeError) as exc:
        raise DocflowError(str(exc)) from exc
    entries = registry["entries"]
    entry = next(
        (item for item in entries if Path(item.get("path", "")).resolve() == source),
        None,
    )
    value = {
        "path": str(source),
        "task_id": task_id,
        "package_id": package_id,
        "commit": known[source],
        "allowed_paths": allowed_paths,
        "status": "reviewed",
        "registered_at": utc_now(),
    }
    if entry is None:
        entries.append(value)
    else:
        entry.update(value)
    store.save_worktree_registry(root, registry)


def _mark_registered_worktree(
    root: Path, task_id: str, package_id: str, status: str
) -> None:
    try:
        registry = store.load_worktree_registry(root)
    except (ValueError, json.JSONDecodeError) as exc:
        raise DocflowError(str(exc)) from exc
    changed = False
    for entry in registry["entries"]:
        if entry.get("task_id") == task_id and entry.get("package_id") == package_id:
            entry["status"] = status
            entry["updated_at"] = utc_now()
            changed = True
    if changed:
        store.save_worktree_registry(root, registry)


def cmd_register_worktree(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    target = Path(args.path).resolve()
    known = _git_worktrees(root)
    if target == root.resolve() or target not in known:
        raise DocflowError(
            "Only a secondary worktree reported by git may be registered"
        )
    try:
        registry = store.load_worktree_registry(root)
    except (ValueError, json.JSONDecodeError) as exc:
        raise DocflowError(str(exc)) from exc
    entries = registry["entries"]
    value = {
        "path": str(target),
        "task_id": safe_id(args.task_id, "task id"),
        "package_id": safe_id(args.package, "package id"),
        "commit": known[target],
        "allowed_paths": list(dict.fromkeys(args.allowed_path or [])),
        "status": args.status,
        "registered_at": utc_now(),
    }
    existing = next((item for item in entries if item.get("path") == str(target)), None)
    if existing is None:
        entries.append(value)
    else:
        existing.update(value)
    store.save_worktree_registry(root, registry)
    print(f"Registered worktree {target}")


def _gc_worktrees(
    root: Path,
    *,
    apply: bool,
    retention_hours: float,
    quiet: bool = False,
) -> list[Path]:
    try:
        registry = store.load_worktree_registry(root)
    except (ValueError, json.JSONDecodeError) as exc:
        raise DocflowError(str(exc)) from exc
    known = _git_worktrees(root)
    now = datetime.now(timezone.utc)
    removable: list[Path] = []
    changed = False
    for entry in registry["entries"]:
        if entry.get("status") not in {"integrated", "superseded"}:
            continue
        target = Path(str(entry.get("path", ""))).resolve()
        if (
            target in {root.resolve(), Path.cwd().resolve()}
            or target not in known
            or not target.is_dir()
        ):
            continue
        try:
            registered = datetime.fromisoformat(str(entry["registered_at"]))
        except (KeyError, ValueError):
            continue
        if (now - registered).total_seconds() < max(retention_hours, 0) * 3600:
            continue
        clean = subprocess.run(
            ["git", "-C", str(target), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
        )
        if clean.returncode != 0 or clean.stdout.strip():
            continue
        commit = str(entry.get("commit") or known[target])
        ancestor = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        if ancestor.returncode != 0:
            allowed_paths = entry.get("allowed_paths", [])
            if not _string_list(allowed_paths):
                continue
            equivalent = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "diff",
                    "--quiet",
                    commit,
                    "HEAD",
                    "--",
                    *[f":(glob){pattern}" for pattern in allowed_paths],
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if equivalent.returncode != 0:
                continue
        removable.append(target)
        if apply:
            removed = subprocess.run(
                ["git", "-C", str(root), "worktree", "remove", str(target)],
                check=False,
                capture_output=True,
                text=True,
            )
            if removed.returncode != 0:
                raise DocflowError(
                    f"Unable to remove worktree {target}: {removed.stderr.strip()}"
                )
            entry["status"] = "removed"
            entry["removed_at"] = utc_now()
            changed = True
    if changed:
        registry["entries"] = [
            entry for entry in registry["entries"] if entry.get("status") != "removed"
        ]
        store.save_worktree_registry(root, registry)
    if not quiet:
        action = "Removed" if apply else "Eligible"
        for path in removable:
            print(f"{action}: {path}")
        if not removable:
            print("No worktrees eligible for garbage collection")
    return removable


def cmd_worktree_gc(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    _gc_worktrees(
        root,
        apply=args.apply,
        retention_hours=args.retention_hours,
    )


def cmd_import_package_result(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, path, lock = _editable_run(root)
    if run.get("status") not in {"approved-for-implementation", "implementing"}:
        raise DocflowError("Central run is not accepting package results")
    source_root = Path(args.from_root).resolve()
    if source_root == root:
        raise DocflowError("Package result source must be an isolated worktree")
    source_lock, source_lock_errors = check_lock(source_root)
    if source_lock_errors or not source_lock:
        raise DocflowError(
            "Source worktree context is invalid:\n- " + "\n- ".join(source_lock_errors)
        )
    if sha256_file(source_root / LOCK_REL) != sha256_file(root / LOCK_REL):
        raise DocflowError(
            "Source worktree is not bound to the central Task Context Lock"
        )
    source_run, source_errors = check_run(source_root, lock["task"]["id"], audit=True)
    if source_errors or not source_run:
        raise DocflowError(
            "Source worktree run is invalid:\n- " + "\n- ".join(source_errors)
        )
    central_package = next(
        (item for item in run.get("packages", []) if item.get("id") == args.package),
        None,
    )
    source_package = next(
        (
            item
            for item in source_run.get("packages", [])
            if item.get("id") == args.package
        ),
        None,
    )
    if not central_package or not source_package:
        raise DocflowError(
            f"Unknown package id in central or source run: {args.package}"
        )
    if source_package.get("status") not in {"approved", "rejected", "blocked"}:
        raise DocflowError(
            "Import only a reviewed package result: approved, rejected, or blocked"
        )
    immutable_fields = (
        "id",
        "summary",
        "requirement_ids",
        "artifact_ids",
        "depends_on",
        "allowed_paths",
        "acceptance_criteria",
        "verification_commands",
        "verification_specs",
    )
    changed = [
        field
        for field in immutable_fields
        if central_package.get(field) != source_package.get(field)
    ]
    if changed:
        raise DocflowError("Source package contract changed: " + ", ".join(changed))
    if run.get("storage_mode") == "append-only":
        audited_central = load_run(root, run["task_id"], audit=True)
        audited_package = next(
            item
            for item in audited_central["packages"]
            if item.get("id") == args.package
        )
        existing_ids = {
            item.get("id") or store.canonical_hash(item)
            for item in audited_package.get("events", [])
            if isinstance(item, dict)
        }
        for event in source_package.get("events", []):
            signature = (
                event.get("id") or store.canonical_hash(event)
                if isinstance(event, dict)
                else None
            )
            if isinstance(event, dict) and signature not in existing_ids:
                store.append_event(path, event, package_id=args.package)
    for field in ("status", "event_state", "escalations", "evidence"):
        if field in source_package:
            central_package[field] = source_package[field]
        elif field == "escalations":
            central_package.pop(field, None)
    run["status"] = "implementing"
    _record_run_event(
        root,
        run,
        _event(
            args.actor,
            "implementing",
            f"Imported {args.package} result from isolated worktree: {source_package['status']}",
        ),
    )
    _register_imported_worktree(
        root,
        source_root,
        run["task_id"],
        args.package,
        list(central_package.get("allowed_paths", [])),
    )
    persist_run(root, run)
    print(f"Imported package {args.package}: {source_package['status']}")


def cmd_activate_integration(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, _, lock = _editable_run(root)
    existing = root / PACKAGE_LOCK_REL
    if existing.is_file():
        package_lock, errors = check_package_lock(root)
        if errors:
            raise DocflowError(
                "Existing package lock is invalid:\n- " + "\n- ".join(errors)
            )
        raise DocflowError(
            f"Package {package_lock.get('package_id')} is already active"
        )
    package = next(
        (item for item in run.get("packages", []) if item.get("id") == args.package),
        None,
    )
    if not package:
        raise DocflowError(f"Unknown package id: {args.package}")
    if package.get("status") != "approved":
        raise DocflowError(
            "Only an independently approved package may enter integration"
        )
    package_map = {item["id"]: item for item in run["packages"]}
    waiting = [
        dependency
        for dependency in package.get("depends_on", [])
        if package_map[dependency].get("status") != "integrated"
    ]
    if waiting:
        raise DocflowError(
            "Package dependencies are not integrated: " + ", ".join(waiting)
        )
    integration_lock = {
        "schema_version": "1.0",
        "created_at": utc_now(),
        "phase": "integration",
        "task_id": lock["task"]["id"],
        "package_id": package["id"],
        "context_lock_sha256": sha256_file(root / LOCK_REL),
        "requirement_ids": package["requirement_ids"],
        "artifact_ids": package["artifact_ids"],
        "allowed_paths": package["allowed_paths"],
        "acceptance_criteria": package.get("acceptance_criteria", []),
        "verification_commands": package["verification_commands"],
        "verification_specs": package.get("verification_specs", []),
        "actor": args.actor,
    }
    pack_path = write_context_pack(
        root,
        lock,
        requirements=list(package["requirement_ids"]),
        artifact_ids=list(package["artifact_ids"]),
        package=package,
    )
    integration_lock["context_pack"] = pack_path.relative_to(root).as_posix()
    integration_lock["context_pack_sha256"] = sha256_file(pack_path)
    write_json(existing, integration_lock)
    print(f"Activated integration lock for package {args.package}")


def cmd_set_package_status(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, path, _ = _editable_run(root)
    package = next(
        (item for item in run.get("packages", []) if item.get("id") == args.package),
        None,
    )
    if not package:
        raise DocflowError(f"Unknown package id: {args.package}")
    current = package.get("status")
    if args.to not in PACKAGE_TRANSITIONS.get(current, set()):
        raise DocflowError(f"Invalid package status transition: {current} -> {args.to}")
    if args.to in {"implemented", "approved", "integrated"} and not args.note:
        raise DocflowError(f"Transition to {args.to} requires --note evidence")
    if args.to == "integrated":
        active, active_errors = check_package_lock(root)
        if active_errors or not active:
            raise DocflowError(
                "Integration requires a valid integration lock:\n- "
                + "\n- ".join(active_errors or ["No active package lock"])
            )
        if (
            active.get("phase") != "integration"
            or active.get("package_id") != args.package
        ):
            raise DocflowError("Active integration lock does not match this package")
    config = load_orchestration(root)
    if args.to == "reviewing":
        implementer = _last_actor_for_status(package, "implementing")
        if implementer == args.actor:
            raise DocflowError(
                "Cross-review requires a reviewer different from the implementing actor"
            )
    if args.to == "approved":
        reviewer = _last_actor_for_status(package, "reviewing")
        if reviewer != args.actor:
            raise DocflowError(
                "Package approval must be recorded by the active independent reviewer"
            )
    if args.to == "rejected":
        state = package.get("event_state", {})
        if isinstance(state, dict):
            rejections = int(state.get("transition_counts", {}).get("rejected", 0))
        else:
            rejections = sum(
                1
                for event in package.get("events", [])
                if isinstance(event, dict) and event.get("status") == "rejected"
            )
        if rejections >= config.get("max_fix_iterations", 3):
            raise DocflowError(
                "Package exceeded max_fix_iterations; escalate or block the run"
            )
    evidence_errors = _verification_evidence_errors(package, args.to)
    if evidence_errors:
        raise DocflowError(
            "Verification evidence gate failed:\n- " + "\n- ".join(evidence_errors)
        )
    package["status"] = args.to
    _record_package_event(
        root, run, package, _event(args.actor, args.to, args.note or "")
    )
    if args.to == "integrated":
        _mark_registered_worktree(root, run["task_id"], args.package, "integrated")
    persist_run(root, run)
    if args.to != "implementing":
        package_lock_path = root / PACKAGE_LOCK_REL
        if package_lock_path.is_file():
            active = load_json(package_lock_path)
            if active.get("package_id") == args.package:
                package_lock_path.unlink()
    print(f"{args.package}: {current} -> {args.to}")


def cmd_escalate_package(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, path, _ = _editable_run(root)
    package = next(
        (item for item in run.get("packages", []) if item.get("id") == args.package),
        None,
    )
    if not package:
        raise DocflowError(f"Unknown package id: {args.package}")
    escalations = package.setdefault("escalations", [])
    maximum = load_orchestration(root).get("max_escalation_steps", 3)
    if len(escalations) >= maximum:
        raise DocflowError(
            "Package exceeded max_escalation_steps; request user direction"
        )
    escalations.append({"at": utc_now(), "actor": args.actor, "reason": args.reason})
    _record_package_event(
        root,
        run,
        package,
        _event(args.actor, package["status"], "Escalated: " + args.reason),
    )
    persist_run(root, run)
    print(f"Escalated package {args.package} ({len(escalations)}/{maximum})")


def cmd_check_run(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, errors = check_run(root, args.task_id, audit=args.audit)
    if errors or not run:
        raise DocflowError("Run invalid:\n- " + "\n- ".join(errors))
    print(f"Run valid for task {run['task_id']} ({run['status']})")


def cmd_check_package_lock(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    package_lock, errors = check_package_lock(root)
    if errors or not package_lock:
        details = errors or ["No active package lock"]
        raise DocflowError("Package lock invalid:\n- " + "\n- ".join(details))
    print(f"Package lock valid for {package_lock['package_id']}")


def cmd_complete_run(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, path, _ = _editable_run(root)
    checked, errors = check_run(root, run.get("task_id"), audit=True)
    if errors or not checked:
        raise DocflowError("Run cannot complete:\n- " + "\n- ".join(errors))
    incomplete = [
        item["id"] for item in checked["packages"] if item.get("status") != "integrated"
    ]
    if incomplete:
        raise DocflowError("Packages are not integrated: " + ", ".join(incomplete))
    evidence_errors: list[str] = []
    for package in checked["packages"]:
        evidence_errors.extend(_verification_evidence_errors(package, "completed"))
    if evidence_errors:
        raise DocflowError(
            "Run evidence gate failed:\n- " + "\n- ".join(evidence_errors)
        )
    checked["status"] = "completed"
    checked["completed_at"] = utc_now()
    _record_run_event(
        root, checked, _event(args.actor, "completed", args.note or "Green gate passed")
    )
    persist_run(root, checked)
    (root / PACKAGE_LOCK_REL).unlink(missing_ok=True)
    config = load_orchestration(root).get("worktree_gc", {})
    if args.gc or config.get("enabled") is True:
        _gc_worktrees(
            root,
            apply=True,
            retention_hours=float(config.get("retention_hours", 0)),
            quiet=True,
        )
    print(f"Completed run for task {checked['task_id']}")


def cmd_guard_edit(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    allowed, reason = guard_edit(root, args.path)
    if not allowed:
        raise DocflowError(reason)
    print(reason)


def _existing_paths(root: Path, values: Iterable[str], label: str) -> list[str]:
    result: list[str] = []
    for raw in values:
        normalized = relative_path(root, raw)
        if not repo_path(root, normalized).exists():
            raise DocflowError(f"{label} path does not exist: {normalized}")
        if normalized not in result:
            result.append(normalized)
    return result


def cmd_trace(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    lock, errors = check_lock(root)
    if errors or not lock:
        raise DocflowError(
            "Cannot record trace with invalid lock:\n- " + "\n- ".join(errors)
        )
    if args.requirement not in lock["task"]["requirement_ids"]:
        raise DocflowError(
            f"Requirement is not in the context lock: {args.requirement}"
        )
    code = _existing_paths(root, args.code or [], "Code")
    tests = _existing_paths(root, args.test or [], "Test")
    key = (lock["task"]["id"], args.requirement)
    try:
        entries = store.load_trace_entries(
            root,
            task_id=key[0],
            requirement_ids=[key[1]],
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DocflowError(f"Cannot load trace shard: {exc}") from exc
    entry = next(
        (
            item
            for item in entries
            if (item.get("task_id"), item.get("requirement_id")) == key
        ),
        None,
    )
    if entry is None:
        entry = {
            "task_id": key[0],
            "requirement_id": key[1],
            "documents": list(lock["selected_artifact_ids"]),
            "code": [],
            "tests": [],
            "recorded_at": utc_now(),
        }
    for field, values in (("code", code), ("tests", tests)):
        current = entry.setdefault(field, [])
        for value in values:
            if value not in current:
                current.append(value)
    entry["documents"] = list(lock["selected_artifact_ids"])
    entry["recorded_at"] = utc_now()
    safe_id(key[0], "task id")
    safe_id(key[1], "requirement id")
    try:
        store.ensure_sharded_trace_index(root)
        store.write_json(store.trace_shard_path(root, key[0], key[1]), entry)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DocflowError(f"Cannot write trace shard: {exc}") from exc
    print(f"Recorded trace for {args.requirement}")


def cmd_trace_export(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    try:
        entries = store.load_trace_entries(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DocflowError(f"Cannot export traceability: {exc}") from exc
    value = {
        "schema_version": "1.0",
        "entries": sorted(
            entries,
            key=lambda item: (
                str(item.get("task_id")),
                str(item.get("requirement_id")),
            ),
        ),
    }
    if args.output:
        output = repo_path(root, args.output)
        write_json(output, value)
        print(f"Exported traceability to {relative_path(root, output)}")
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def verify_traceability(
    root: Path, lock: dict[str, Any], policy: dict[str, Any]
) -> list[str]:
    if not policy.get("require_traceability", True):
        return []
    errors: list[str] = []
    try:
        trace_entries = store.load_trace_entries(
            root,
            task_id=lock["task"]["id"],
            requirement_ids=lock["task"]["requirement_ids"],
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"Invalid traceability store: {exc}"]
    selected = set(lock.get("selected_artifact_ids", []))
    entry_index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in trace_entries:
        if (
            isinstance(item, dict)
            and isinstance(item.get("task_id"), str)
            and isinstance(item.get("requirement_id"), str)
        ):
            entry_index[(item["task_id"], item["requirement_id"])] = item
    for requirement in lock["task"]["requirement_ids"]:
        entry = entry_index.get((lock["task"]["id"], requirement))
        if entry is None:
            errors.append(f"Missing traceability entry for {requirement}")
            continue
        documents = (
            set(entry.get("documents", []))
            if isinstance(entry.get("documents"), list)
            else set()
        )
        if not selected.issubset(documents):
            errors.append(
                f"Trace for {requirement} does not include every locked artifact"
            )
        for field in ("code", "tests"):
            paths = entry.get(field)
            if not _string_list(paths):
                errors.append(
                    f"Trace for {requirement} requires at least one {field} path"
                )
                continue
            for path in paths:
                try:
                    if not repo_path(root, path).exists():
                        errors.append(f"Trace {field} path does not exist: {path}")
                except DocflowError as exc:
                    errors.append(str(exc))
        for code_path in (
            entry.get("code", []) if isinstance(entry.get("code"), list) else []
        ):
            required = required_artifacts_for_path(policy, code_path)
            missing = sorted(required - selected)
            if missing:
                errors.append(
                    f"Trace code path {code_path} requires locked artifacts: {', '.join(missing)}"
                )
    return errors


def git_changed_paths(root: Path, base_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", f"{base_ref}...HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DocflowError(
            f"Unable to compute CI change set from {base_ref}: {result.stderr.strip()}"
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def verify_changed_paths(
    root: Path,
    manifest: dict[str, Any],
    lock: dict[str, Any],
    policy: dict[str, Any],
    base_ref: str,
) -> list[str]:
    errors: list[str] = []
    try:
        task_entries = store.load_trace_entries(
            root,
            task_id=lock["task"]["id"],
            requirement_ids=lock["task"]["requirement_ids"],
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"Invalid traceability store: {exc}"]
    traced_paths: set[str] = set()
    for entry in task_entries:
        for field in ("code", "tests"):
            values = entry.get(field, [])
            if isinstance(values, list):
                traced_paths.update(value for value in values if isinstance(value, str))
    for path in git_changed_paths(root, base_ref):
        if is_documentation_path(root, manifest, policy, path):
            continue
        if path not in traced_paths:
            errors.append(
                f"Changed implementation path is not traced for this task: {path}"
            )
        required = required_artifacts_for_path(policy, path)
        missing = sorted(required - set(lock.get("selected_artifact_ids", [])))
        if missing:
            errors.append(
                f"Changed path {path} requires locked artifacts: {', '.join(missing)}"
            )
    return errors


def cmd_verify(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    manifest = require_valid_manifest(root)
    active = [
        item["id"]
        for item in manifest["artifacts"]
        if item.get("status") != "superseded"
    ]
    if not active:
        raise DocflowError("Manifest has no active project artifacts")
    lock, errors = check_lock(root)
    if not lock:
        raise DocflowError("Verification failed:\n- " + "\n- ".join(errors))
    active_package, package_errors = check_package_lock(root)
    errors.extend(package_errors)
    if active_package:
        errors.append(f"Package is still active: {active_package.get('package_id')}")
    active_run_path = run_path(root, lock["task"]["id"])
    if active_run_path.is_file():
        run, run_errors = check_run(root, lock["task"]["id"], audit=True)
        errors.extend(run_errors)
        if run and run.get("status") != "completed":
            errors.append(f"Orchestrated run is not completed: {run.get('status')}")
    policy = load_policy(root)
    errors.extend(verify_traceability(root, lock, policy))
    if args.ci:
        if not args.base_ref:
            errors.append(
                "CI verification requires --base-ref to bind every changed path to traceability"
            )
        else:
            try:
                errors.extend(
                    verify_changed_paths(root, manifest, lock, policy, args.base_ref)
                )
            except DocflowError as exc:
                errors.append(str(exc))
    if errors:
        raise DocflowError("Verification failed:\n- " + "\n- ".join(errors))
    suffix = " (CI)" if args.ci else ""
    print(f"Document-driven verification passed for task {lock['task']['id']}{suffix}")


def cmd_show_status(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    manifest = load_manifest(root)
    errors = validate_manifest(root, manifest)
    counts = {status: 0 for status in STATUSES}
    for artifact in manifest.get("artifacts", []):
        if isinstance(artifact, dict) and artifact.get("status") in counts:
            counts[artifact["status"]] += 1
    print("Artifacts: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    lock, lock_errors = check_lock(root)
    if lock and not lock_errors:
        print(f"Context lock: valid ({lock['task']['id']})")
        active_run_path = run_path(root, lock["task"]["id"])
        if active_run_path.is_file():
            run, run_errors = check_run(root, lock["task"]["id"])
            if run and not run_errors:
                package_counts = {
                    status: sum(
                        1 for item in run["packages"] if item.get("status") == status
                    )
                    for status in PACKAGE_STATUSES
                }
                populated = ", ".join(
                    f"{status}={count}"
                    for status, count in package_counts.items()
                    if count
                )
                print(f"Run: {run['status']} ({populated})")
            else:
                print("Run: invalid")
        package_lock, package_errors = check_package_lock(root)
        if package_lock and not package_errors:
            print(f"Active package: {package_lock['package_id']}")
    else:
        print("Context lock: invalid or absent")
    if errors:
        print("Manifest issues:")
        for error in errors:
            print(f"- {error}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser(
        "init", help="Create an empty manifest after the document graph is approved"
    )
    init.add_argument("--root", default=".")
    init.add_argument("--prd", required=True)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    validate = sub.add_parser("validate", help="Validate the dynamic document manifest")
    validate.add_argument("--root", default=".")
    validate.set_defaults(func=cmd_validate)

    status = sub.add_parser(
        "set-status", help="Move one artifact through its review lifecycle"
    )
    status.add_argument("--root", default=".")
    status.add_argument("--artifact", required=True)
    status.add_argument("--to", required=True, choices=STATUSES)
    status.set_defaults(func=cmd_set_status)

    approve = sub.add_parser(
        "approve", help="Record explicit approval and the artifact content hash"
    )
    approve.add_argument("--root", default=".")
    approve.add_argument("--artifact", required=True)
    approve.add_argument("--approved-by", required=True)
    approve.set_defaults(func=cmd_approve)

    approve_bundle = sub.add_parser(
        "approve-bundle",
        help="Atomically approve reviewed artifacts at explicit content hashes",
    )
    approve_bundle.add_argument("--root", default=".")
    approve_bundle.add_argument("--approval", action="append", required=True)
    approve_bundle.add_argument("--approved-by", required=True)
    approve_bundle.set_defaults(func=cmd_approve_bundle)

    prepare = sub.add_parser(
        "prepare", help="Create a task lock from approved relevant artifacts"
    )
    prepare.add_argument("--root", default=".")
    prepare.add_argument("--task-id", required=True)
    prepare.add_argument("--summary")
    prepare.add_argument("--requirement", action="append", required=True)
    prepare.add_argument("--scope", action="append")
    prepare.add_argument("--artifact", action="append")
    prepare.set_defaults(func=cmd_prepare)

    context_pack = sub.add_parser(
        "context-pack",
        help="Build a compact requirement-sliced context from the current lock",
    )
    context_pack.add_argument("--root", default=".")
    context_pack.add_argument("--package")
    context_pack.add_argument("--stdout", action="store_true")
    context_pack.set_defaults(func=cmd_context_pack)

    check_context_pack = sub.add_parser(
        "check-context-pack",
        help="Verify compact context slices against the current lock and source documents",
    )
    check_context_pack.add_argument("--root", default=".")
    check_context_pack.add_argument("--package")
    check_context_pack.add_argument("--path")
    check_context_pack.set_defaults(func=cmd_check_context_pack)

    check = sub.add_parser(
        "check-lock", help="Verify that the current task lock is still valid"
    )
    check.add_argument("--root", default=".")
    check.set_defaults(func=cmd_check_lock)

    start_run = sub.add_parser(
        "start-run", help="Create an execution run bound to the task lock"
    )
    start_run.add_argument("--root", default=".")
    start_run.add_argument(
        "--mode", choices=("auto", "single", "orchestrated"), default="auto"
    )
    start_run.add_argument("--plan-summary")
    start_run.add_argument("--debate-rounds", type=int, default=0)
    start_run.add_argument("--constraint", action="append")
    start_run.add_argument(
        "--supersedes", action="append", help="Completed task run replaced by this run"
    )
    start_run.add_argument("--actor", required=True)
    start_run.add_argument("--force", action="store_true")
    start_run.set_defaults(func=cmd_start_run)

    add_package = sub.add_parser(
        "add-package", help="Add a locked work package to a planning run"
    )
    add_package.add_argument("--root", default=".")
    add_package.add_argument("--package", required=True)
    add_package.add_argument("--summary")
    add_package.add_argument("--requirement", action="append", required=True)
    add_package.add_argument("--artifact", action="append", required=True)
    add_package.add_argument("--depends-on", action="append")
    add_package.add_argument("--allowed-path", action="append", required=True)
    add_package.add_argument("--acceptance", action="append")
    add_package.add_argument("--verification-command", action="append", required=True)
    add_package.add_argument(
        "--verification-spec",
        action="append",
        help="Structured verification gate as a JSON object",
    )
    add_package.add_argument("--actor", required=True)
    add_package.set_defaults(func=cmd_add_package)

    approve_run = sub.add_parser(
        "approve-run", help="Approve the locked plan and every work package"
    )
    approve_run.add_argument("--root", default=".")
    approve_run.add_argument("--approved-by", required=True)
    approve_run.set_defaults(func=cmd_approve_run)

    activate_package = sub.add_parser(
        "activate-package", help="Activate one package lock in this worktree"
    )
    activate_package.add_argument("--root", default=".")
    activate_package.add_argument("--package", required=True)
    activate_package.add_argument("--actor", required=True)
    activate_package.add_argument("--note")
    activate_package.set_defaults(func=cmd_activate_package)

    package_status = sub.add_parser(
        "set-package-status", help="Advance a package through implementation and review"
    )
    package_status.add_argument("--root", default=".")
    package_status.add_argument("--package", required=True)
    package_status.add_argument("--to", required=True, choices=PACKAGE_STATUSES)
    package_status.add_argument("--actor", required=True)
    package_status.add_argument("--note")
    package_status.set_defaults(func=cmd_set_package_status)

    import_package = sub.add_parser(
        "import-package-result",
        help="Merge one reviewed worktree package into the central run",
    )
    import_package.add_argument("--root", default=".")
    import_package.add_argument("--package", required=True)
    import_package.add_argument("--from-root", required=True)
    import_package.add_argument("--actor", required=True)
    import_package.set_defaults(func=cmd_import_package_result)

    integration = sub.add_parser(
        "activate-integration",
        help="Lock one approved package path set for central integration",
    )
    integration.add_argument("--root", default=".")
    integration.add_argument("--package", required=True)
    integration.add_argument("--actor", required=True)
    integration.set_defaults(func=cmd_activate_integration)

    escalate = sub.add_parser(
        "escalate-package", help="Record a bounded package escalation"
    )
    escalate.add_argument("--root", default=".")
    escalate.add_argument("--package", required=True)
    escalate.add_argument("--actor", required=True)
    escalate.add_argument("--reason", required=True)
    escalate.set_defaults(func=cmd_escalate_package)

    check_run_parser = sub.add_parser(
        "check-run", help="Validate the current orchestration run"
    )
    check_run_parser.add_argument("--root", default=".")
    check_run_parser.add_argument("--task-id")
    check_run_parser.add_argument(
        "--audit", action="store_true", help="Replay the append-only event log"
    )
    check_run_parser.set_defaults(func=cmd_check_run)

    check_package = sub.add_parser(
        "check-package-lock", help="Validate the active package ownership lock"
    )
    check_package.add_argument("--root", default=".")
    check_package.set_defaults(func=cmd_check_package_lock)

    complete_run = sub.add_parser(
        "complete-run", help="Close a run after every package is integrated"
    )
    complete_run.add_argument("--root", default=".")
    complete_run.add_argument("--actor", required=True)
    complete_run.add_argument("--note")
    complete_run.add_argument(
        "--gc", action="store_true", help="Garbage collect safe integrated worktrees"
    )
    complete_run.set_defaults(func=cmd_complete_run)

    guard = sub.add_parser(
        "guard-edit", help="Check whether a repository path may be edited"
    )
    guard.add_argument("--root", default=".")
    guard.add_argument("--path", required=True)
    guard.set_defaults(func=cmd_guard_edit)

    check_lease = sub.add_parser(
        "check-lease", help="Check the persistent validation lease"
    )
    check_lease.add_argument("--root", default=".")
    check_lease.set_defaults(func=cmd_check_lease)

    invalidate_lease = sub.add_parser(
        "invalidate-lease", help="Invalidate the persistent validation lease"
    )
    invalidate_lease.add_argument("--root", default=".")
    invalidate_lease.set_defaults(func=cmd_invalidate_lease)

    trace = sub.add_parser(
        "trace", help="Record requirement-to-document/code/test traceability"
    )
    trace.add_argument("--root", default=".")
    trace.add_argument("--requirement", required=True)
    trace.add_argument("--code", action="append")
    trace.add_argument("--test", action="append")
    trace.set_defaults(func=cmd_trace)

    trace_export = sub.add_parser(
        "trace-export",
        help="Export sharded traceability as one legacy-compatible JSON object",
    )
    trace_export.add_argument("--root", default=".")
    trace_export.add_argument("--output")
    trace_export.set_defaults(func=cmd_trace_export)

    preflight = sub.add_parser(
        "preflight", help="Classify structured verification gates without running them"
    )
    preflight.add_argument("--root", default=".")
    preflight.add_argument("--package", required=True)
    preflight.add_argument("--gate")
    preflight.add_argument("--available", action="append")
    preflight.set_defaults(func=cmd_preflight)

    verify_package = sub.add_parser(
        "verify-package", help="Run or reuse impact-scoped package verification"
    )
    verify_package.add_argument("--root", default=".")
    verify_package.add_argument("--package", required=True)
    verify_package.add_argument("--gate")
    verify_package.add_argument("--available", action="append")
    verify_package.add_argument("--environment", action="append")
    verify_package.add_argument("--base-ref")
    verify_package.add_argument("--execute", action="store_true")
    verify_package.add_argument("--attest", action="store_true")
    verify_package.add_argument("--actor", default="docflow")
    verify_package.add_argument("--note")
    verify_package.add_argument("--timeout", type=int, default=900)
    verify_package.set_defaults(func=cmd_verify_package)

    register_worktree = sub.add_parser(
        "register-worktree",
        help="Register a secondary git worktree for safe lifecycle cleanup",
    )
    register_worktree.add_argument("--root", default=".")
    register_worktree.add_argument("--path", required=True)
    register_worktree.add_argument("--task-id", required=True)
    register_worktree.add_argument("--package", required=True)
    register_worktree.add_argument("--allowed-path", action="append")
    register_worktree.add_argument(
        "--status",
        choices=("active", "reviewed", "integrated", "superseded"),
        default="active",
    )
    register_worktree.set_defaults(func=cmd_register_worktree)

    worktree_gc = sub.add_parser(
        "worktree-gc", help="Safely remove clean integrated secondary worktrees"
    )
    worktree_gc.add_argument("--root", default=".")
    worktree_gc.add_argument("--apply", action="store_true")
    worktree_gc.add_argument("--retention-hours", type=float, default=24)
    worktree_gc.set_defaults(func=cmd_worktree_gc)

    verify = sub.add_parser("verify", help="Run the final document-driven gate")
    verify.add_argument("--root", default=".")
    verify.add_argument("--ci", action="store_true")
    verify.add_argument(
        "--base-ref",
        help="Git base commit or ref used to verify every changed path in CI",
    )
    verify.set_defaults(func=cmd_verify)

    show = sub.add_parser("show-status", help="Summarize artifact and lock state")
    show.add_argument("--root", default=".")
    show.set_defaults(func=cmd_show_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except DocflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
