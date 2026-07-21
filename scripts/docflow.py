#!/usr/bin/env python3
"""Deterministic state and validation tools for document-driven development."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MANIFEST_REL = Path("docs/document-manifest.json")
STATE_REL = Path(".document-driven")
LOCK_REL = STATE_REL / "context-lock.json"
POLICY_REL = STATE_REL / "policy.json"
TRACE_REL = STATE_REL / "traceability.json"
CONTEXT_PACK_REL = STATE_REL / "context-pack.json"
ORCHESTRATION_REL = STATE_REL / "orchestration.json"
PACKAGE_LOCK_REL = STATE_REL / "package-lock.json"
RUNS_REL = STATE_REL / "runs"
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
RUN_STATUSES = ("planning", "approved-for-implementation", "implementing", "completed", "blocked")
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
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


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
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def validate_manifest(root: Path, manifest: dict[str, Any], *, verify_hashes: bool = True) -> list[str]:
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

        if not isinstance(artifact.get("purpose"), str) or not artifact.get("purpose", "").strip():
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
                if verify_hashes and artifact_path and artifact_path.is_file() and approval.get("content_sha256"):
                    actual = sha256_file(artifact_path)
                    if approval["content_sha256"] != actual:
                        errors.append(
                            f"Approved artifact changed without re-approval: {artifact_id} ({path_value})"
                        )

    for artifact_id, artifact in amap.items():
        for dependency in artifact.get("depends_on", []):
            if dependency not in amap:
                errors.append(f"{artifact_id}.depends_on references unknown artifact: {dependency}")
            elif artifact.get("status") == "approved" and amap[dependency].get("status") != "approved":
                errors.append(f"Approved artifact {artifact_id} depends on non-approved {dependency}")
        for source_id in artifact.get("informed_by", []):
            if source_id != "prd" and source_id not in amap:
                errors.append(f"{artifact_id}.informed_by references unknown source: {source_id}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(artifact_id: str, trail: list[str]) -> None:
        if artifact_id in visiting:
            errors.append("Artifact dependency cycle: " + " -> ".join((*trail, artifact_id)))
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


def dependency_closure(amap: dict[str, dict[str, Any]], selected: Iterable[str]) -> set[str]:
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
            raise DocflowError(f"policy.json path_rules[{index}].patterns must be a string array")
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
        raise DocflowError("orchestration.json mode must be auto, single, or orchestrated")
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
    plan = config.get("plan", {})
    if not isinstance(plan, dict):
        raise DocflowError("orchestration.json plan must be an object")
    maximum_rounds = plan.get("max_debate_rounds")
    if not isinstance(maximum_rounds, int) or maximum_rounds < 0:
        raise DocflowError("orchestration.json plan.max_debate_rounds must be a non-negative integer")
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
        elif previous is not None and status != previous and status not in allowed.get(previous, set()):
            errors.append(f"{package_id} event transition is invalid: {previous} -> {status}")
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
        if transitioned and status in {"implemented", "approved", "integrated"} and not note:
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
    if not isinstance(task, dict) or not isinstance(task.get("id"), str) or not task.get("id"):
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
    if not isinstance(manifest_lock, dict) or manifest_lock.get("sha256") != actual_manifest_hash:
        errors.append("Manifest changed after context preparation; prepare a new lock")

    documents = lock.get("documents")
    if not isinstance(documents, list) or not documents:
        errors.append("context lock documents must include the PRD and selected artifacts")
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
        errors.append("context lock selected_artifact_ids must be a non-empty string array")
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
        raise DocflowError(f"Package paths must be non-empty repository-relative patterns: {raw}")
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
        errors.append(f"Run task {task_id} does not match active context task {active_task_id}")
    task_id = task_id or active_task_id
    try:
        run = load_json(run_path(root, task_id))
    except DocflowError as exc:
        return None, [str(exc)]

    if run.get("schema_version") != "1.0":
        errors.append("run.json schema_version must be '1.0'")
    if run.get("task_id") != task_id:
        errors.append("run.json task_id does not match its directory")
    if run.get("status") not in RUN_STATUSES:
        errors.append("run.json has an invalid status")
    elif run.get("status") != "planning" and not isinstance(run.get("approved_by"), str):
        errors.append("A non-planning run requires approved_by")
    if run.get("mode") not in {"single", "orchestrated"}:
        errors.append("run.json mode must be single or orchestrated")
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
            errors.append(f"run.json debate_rounds must be between 0 and {maximum_rounds}")
        if not _string_list(locked_plan.get("constraints", [])):
            errors.append("run.json locked_plan.constraints must be a string array")

    locked_requirements = set(lock["task"]["requirement_ids"])
    locked_artifacts = set(lock["selected_artifact_ids"])
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
                errors.append(f"{package_id} references unlocked requirements: {', '.join(unknown)}")
        artifacts = package.get("artifact_ids")
        if not _string_list(artifacts) or not artifacts:
            errors.append(f"{label}.artifact_ids must be a non-empty string array")
        else:
            unknown = sorted(set(artifacts) - locked_artifacts)
            if unknown:
                errors.append(f"{package_id} references unlocked artifacts: {', '.join(unknown)}")
        if not _string_list(package.get("depends_on", [])):
            errors.append(f"{label}.depends_on must be a string array")
        allowed_paths = package.get("allowed_paths")
        if not _string_list(allowed_paths) or not allowed_paths:
            errors.append(f"{label}.allowed_paths must be a non-empty string array")
        else:
            for pattern in allowed_paths:
                try:
                    normalized = _normalize_pattern(pattern)
                    if normalized.startswith(".document-driven/") or normalized == ".document-driven":
                        errors.append(f"{package_id} cannot own harness state: {normalized}")
                    owners.append((package_id, normalized))
                except DocflowError as exc:
                    errors.append(str(exc))
        commands = package.get("verification_commands")
        if not _string_list(commands) or not commands:
            errors.append(f"{label}.verification_commands must be a non-empty string array")
        acceptance = package.get("acceptance_criteria", [])
        if acceptance and not _string_list(acceptance):
            errors.append(f"{label}.acceptance_criteria must be a string array")
        if not isinstance(package.get("events", []), list):
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
            errors.append("Package dependency cycle: " + " -> ".join((*trail, package_id)))
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

    for index, (left_owner, left_pattern) in enumerate(owners):
        for right_owner, right_pattern in owners[index + 1 :]:
            if left_owner != right_owner and patterns_overlap(left_pattern, right_pattern):
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
        (item for item in run.get("packages", []) if isinstance(item, dict) and item.get("id") == package_id),
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
    if package_lock.get("acceptance_criteria", []) != package.get("acceptance_criteria", []):
        errors.append("Package acceptance criteria changed after activation")
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


def is_documentation_path(root: Path, manifest: dict[str, Any], policy: dict[str, Any], path: str) -> bool:
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


def guard_edit(root: Path, raw_path: str) -> tuple[bool, str]:
    try:
        policy = load_policy(root)
        path = relative_path(root, raw_path)
    except DocflowError as exc:
        return False, str(exc)
    package_lock_exists = (root / PACKAGE_LOCK_REL).is_file()
    if not package_lock_exists and _matches(path, policy.get("documentation_paths", [])):
        return True, f"Documentation or harness path allowed: {path}"
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
            return True, f"Document-driven run state allowed: {path}"
        allowed_paths = package_lock.get("allowed_paths", [])
        if not _matches(path, allowed_paths):
            return False, (
                f"{path} is outside active package {package_lock.get('package_id')} ownership: "
                + ", ".join(allowed_paths)
            )
    elif is_documentation_path(root, manifest, policy, path):
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
            return False, "Implementation write blocked by invalid run. " + " ".join(run_errors)
        if run.get("status") != "completed":
            return False, "Implementation write blocked until a package is activated in this worktree"
    selected = set(lock.get("selected_artifact_ids", []))
    required = required_artifacts_for_path(policy, path)
    missing = sorted(required - selected)
    if missing:
        return False, f"{path} requires locked artifacts: {', '.join(missing)}"
    if package_lock:
        return True, f"Valid package and document locks for {path}"
    return True, f"Valid document context lock for {path}"


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
    print(f"Approved {args.artifact} at sha256:{artifact['approval']['content_sha256']}")


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
                raise DocflowError(f"Approved artifact hash is inconsistent: {artifact_id}")
            unchanged.append(artifact_id)
            continue
        if artifact.get("status") != "reviewed":
            raise DocflowError(f"Only a reviewed artifact can be approved: {artifact_id}")
        pending[artifact_id] = (artifact, actual_hash)

    for artifact_id, (artifact, _) in pending.items():
        for dependency in artifact.get("depends_on", []):
            if dependency not in pending and amap[dependency].get("status") != "approved":
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


def _context_ranges(lines: list[str], requirements: list[str]) -> list[tuple[int, int, set[str]]]:
    ranges: list[tuple[int, int, set[str]]] = []
    for requirement in requirements:
        matches = [
            index for index, line in enumerate(lines) if requirement in line
        ][:CONTEXT_MATCH_LIMIT]
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
        package.get("requirement_ids", []) if package else lock["task"]["requirement_ids"]
    )
    expected_artifacts = package.get("artifact_ids", []) if package else lock["selected_artifact_ids"]
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
            errors.append(f"Context pack document is not bound to the lock: {document_id}")
            continue
        try:
            lines = repo_path(root, locked["path"]).read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
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
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                errors.append(f"Invalid context slice range: {document_id}")
                continue
            actual_text = "\n".join(lines[start - 1 : end])
            matched_requirements = item.get("requirement_ids")
            if (
                not _string_list(matched_requirements)
                or not matched_requirements
                or not set(matched_requirements).issubset(set(expected_requirements))
                or any(requirement not in actual_text for requirement in matched_requirements)
            ):
                errors.append(f"Context slice requirement labels changed: {document_id}:{start}-{end}")
            if item.get("text") != actual_text:
                errors.append(f"Context slice text changed: {document_id}:{start}-{end}")
            if item.get("sha256") != sha256_bytes(actual_text.encode("utf-8")):
                errors.append(f"Context slice hash changed: {document_id}:{start}-{end}")
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
    path = context_pack_path(root, lock["task"]["id"], package.get("id") if package else None)
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
        artifact_id for artifact_id in selected if amap[artifact_id].get("status") != "approved"
    )
    if non_approved:
        raise DocflowError("Selected artifacts are not approved: " + ", ".join(non_approved))
    requirements = list(dict.fromkeys(args.requirement or []))
    if not requirements:
        raise DocflowError("At least one --requirement id is required")

    source_path = manifest["source"]["prd"]
    search_paths = [repo_path(root, source_path)] + [repo_path(root, amap[item]["path"]) for item in selected]
    searchable = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in search_paths)
    missing_requirements = [item for item in requirements if item not in searchable]
    if missing_requirements:
        raise DocflowError(
            "Requirement ids are not present in the PRD or selected documents: "
            + ", ".join(missing_requirements)
        )

    ordered = [item["id"] for item in manifest["artifacts"] if item["id"] in selected]
    documents = [
        {"id": "prd", "path": source_path, "sha256": sha256_file(repo_path(root, source_path))}
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
        raise DocflowError("Cannot build context pack with invalid lock:\n- " + "\n- ".join(errors))
    package = None
    requirements = list(lock["task"]["requirement_ids"])
    artifacts = list(lock["selected_artifact_ids"])
    if args.package:
        run, run_errors = check_run(root, lock=lock)
        if run_errors or not run:
            raise DocflowError("Cannot build package context:\n- " + "\n- ".join(run_errors))
        package = next(
            (item for item in run.get("packages", []) if item.get("id") == args.package),
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
        raise DocflowError("Cannot check context pack with invalid lock:\n- " + "\n- ".join(errors))
    package = None
    if args.package:
        run, run_errors = check_run(root, lock=lock)
        if run_errors or not run:
            raise DocflowError("Cannot check package context:\n- " + "\n- ".join(run_errors))
        package = next(
            (item for item in run.get("packages", []) if item.get("id") == args.package),
            None,
        )
        if not package:
            raise DocflowError(f"Unknown package id: {args.package}")
    path = (
        repo_path(root, args.path)
        if args.path
        else context_pack_path(root, lock["task"]["id"], package.get("id") if package else None)
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


def _event(actor: str, status: str, note: str = "") -> dict[str, str]:
    return {"at": utc_now(), "actor": actor, "status": status, "note": note}


def _editable_run(root: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    lock, errors = check_lock(root)
    if errors or not lock:
        raise DocflowError("A valid context lock is required:\n- " + "\n- ".join(errors))
    path = run_path(root, lock["task"]["id"])
    run = load_json(path)
    if run.get("context_lock_sha256") != sha256_file(root / LOCK_REL):
        raise DocflowError("Run is stale because context-lock.json changed; start a new run")
    return run, path, lock


def cmd_start_run(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    lock, errors = check_lock(root)
    if errors or not lock:
        raise DocflowError("Cannot start run with invalid context lock:\n- " + "\n- ".join(errors))
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
        raise DocflowError(f"Run already exists: {path.relative_to(root)}; pass --force to replace it")
    run = {
        "schema_version": "1.0",
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
        "events": [_event(args.actor, "planning", "Run created from approved documents")],
    }
    write_json(path, run)
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
    unknown_requirements = sorted(set(requirements) - set(lock["task"]["requirement_ids"]))
    unknown_artifacts = sorted(set(artifacts) - set(lock["selected_artifact_ids"]))
    if unknown_requirements:
        raise DocflowError("Package references unlocked requirements: " + ", ".join(unknown_requirements))
    if unknown_artifacts:
        raise DocflowError("Package references unlocked artifacts: " + ", ".join(unknown_artifacts))
    allowed_paths = list(dict.fromkeys(_normalize_pattern(item) for item in args.allowed_path or []))
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
        "events": [_event(args.actor, "planned", "Work package defined")],
    }
    packages.append(package)
    write_json(path, run)
    print(f"Added package {package_id}")


def cmd_approve_run(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, path, _ = _editable_run(root)
    if run.get("status") != "planning":
        raise DocflowError("Only a planning run can be approved")
    checked, errors = check_run(root, run.get("task_id"))
    if errors or not checked:
        raise DocflowError("Run cannot be approved:\n- " + "\n- ".join(errors))
    run = checked
    run["status"] = "approved-for-implementation"
    run["approved_by"] = args.approved_by
    run["approved_at"] = utc_now()
    run.setdefault("events", []).append(
        _event(args.approved_by, "approved-for-implementation", "Locked plan and packages approved")
    )
    for package in run["packages"]:
        package["status"] = "approved-for-implementation"
        package.setdefault("events", []).append(
            _event(args.approved_by, "approved-for-implementation", "Approved as part of run")
        )
    write_json(path, run)
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
            raise DocflowError("Existing package lock is invalid:\n- " + "\n- ".join(current_errors))
        if current and current.get("package_id") != args.package:
            raise DocflowError(f"Package {current.get('package_id')} is already active in this worktree")
    package = next((item for item in run["packages"] if item.get("id") == args.package), None)
    if not package:
        raise DocflowError(f"Unknown package id: {args.package}")
    if package.get("status") not in {"approved-for-implementation", "rejected", "implementing"}:
        raise DocflowError(f"Package cannot be activated from status {package.get('status')}")
    package_map = {item["id"]: item for item in run["packages"]}
    waiting = [
        dependency
        for dependency in package.get("depends_on", [])
        if package_map[dependency].get("status") != "integrated"
    ]
    if waiting:
        raise DocflowError("Package dependencies are not integrated: " + ", ".join(waiting))
    if package.get("status") != "implementing":
        package["status"] = "implementing"
        package.setdefault("events", []).append(_event(args.actor, "implementing", args.note or ""))
    run["status"] = "implementing"
    write_json(path, run)
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
    for event in reversed(package.get("events", [])):
        if isinstance(event, dict) and event.get("status") == status:
            actor = event.get("actor")
            return actor if isinstance(actor, str) else None
    return None


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
        raise DocflowError("Source worktree context is invalid:\n- " + "\n- ".join(source_lock_errors))
    if sha256_file(source_root / LOCK_REL) != sha256_file(root / LOCK_REL):
        raise DocflowError("Source worktree is not bound to the central Task Context Lock")
    source_run, source_errors = check_run(source_root, lock["task"]["id"])
    if source_errors or not source_run:
        raise DocflowError("Source worktree run is invalid:\n- " + "\n- ".join(source_errors))
    central_package = next(
        (item for item in run.get("packages", []) if item.get("id") == args.package),
        None,
    )
    source_package = next(
        (item for item in source_run.get("packages", []) if item.get("id") == args.package),
        None,
    )
    if not central_package or not source_package:
        raise DocflowError(f"Unknown package id in central or source run: {args.package}")
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
    )
    changed = [
        field
        for field in immutable_fields
        if central_package.get(field) != source_package.get(field)
    ]
    if changed:
        raise DocflowError("Source package contract changed: " + ", ".join(changed))
    for field in ("status", "events", "escalations"):
        if field in source_package:
            central_package[field] = source_package[field]
        elif field == "escalations":
            central_package.pop(field, None)
    run["status"] = "implementing"
    run.setdefault("events", []).append(
        _event(
            args.actor,
            "implementing",
            f"Imported {args.package} result from isolated worktree: {source_package['status']}",
        )
    )
    write_json(path, run)
    print(f"Imported package {args.package}: {source_package['status']}")


def cmd_activate_integration(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, _, lock = _editable_run(root)
    existing = root / PACKAGE_LOCK_REL
    if existing.is_file():
        package_lock, errors = check_package_lock(root)
        if errors:
            raise DocflowError("Existing package lock is invalid:\n- " + "\n- ".join(errors))
        raise DocflowError(f"Package {package_lock.get('package_id')} is already active")
    package = next((item for item in run.get("packages", []) if item.get("id") == args.package), None)
    if not package:
        raise DocflowError(f"Unknown package id: {args.package}")
    if package.get("status") != "approved":
        raise DocflowError("Only an independently approved package may enter integration")
    package_map = {item["id"]: item for item in run["packages"]}
    waiting = [
        dependency
        for dependency in package.get("depends_on", [])
        if package_map[dependency].get("status") != "integrated"
    ]
    if waiting:
        raise DocflowError("Package dependencies are not integrated: " + ", ".join(waiting))
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
    package = next((item for item in run.get("packages", []) if item.get("id") == args.package), None)
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
        if active.get("phase") != "integration" or active.get("package_id") != args.package:
            raise DocflowError("Active integration lock does not match this package")
    config = load_orchestration(root)
    if args.to == "reviewing":
        implementer = _last_actor_for_status(package, "implementing")
        if implementer == args.actor:
            raise DocflowError("Cross-review requires a reviewer different from the implementing actor")
    if args.to == "approved":
        reviewer = _last_actor_for_status(package, "reviewing")
        if reviewer != args.actor:
            raise DocflowError("Package approval must be recorded by the active independent reviewer")
    if args.to == "rejected":
        rejections = sum(
            1
            for event in package.get("events", [])
            if isinstance(event, dict) and event.get("status") == "rejected"
        )
        if rejections >= config.get("max_fix_iterations", 3):
            raise DocflowError("Package exceeded max_fix_iterations; escalate or block the run")
    package["status"] = args.to
    package.setdefault("events", []).append(_event(args.actor, args.to, args.note or ""))
    write_json(path, run)
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
    package = next((item for item in run.get("packages", []) if item.get("id") == args.package), None)
    if not package:
        raise DocflowError(f"Unknown package id: {args.package}")
    escalations = package.setdefault("escalations", [])
    maximum = load_orchestration(root).get("max_escalation_steps", 3)
    if len(escalations) >= maximum:
        raise DocflowError("Package exceeded max_escalation_steps; request user direction")
    escalations.append({"at": utc_now(), "actor": args.actor, "reason": args.reason})
    package.setdefault("events", []).append(_event(args.actor, package["status"], "Escalated: " + args.reason))
    write_json(path, run)
    print(f"Escalated package {args.package} ({len(escalations)}/{maximum})")


def cmd_check_run(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    run, errors = check_run(root, args.task_id)
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
    checked, errors = check_run(root, run.get("task_id"))
    if errors or not checked:
        raise DocflowError("Run cannot complete:\n- " + "\n- ".join(errors))
    incomplete = [item["id"] for item in checked["packages"] if item.get("status") != "integrated"]
    if incomplete:
        raise DocflowError("Packages are not integrated: " + ", ".join(incomplete))
    checked["status"] = "completed"
    checked["completed_at"] = utc_now()
    checked.setdefault("events", []).append(_event(args.actor, "completed", args.note or "Green gate passed"))
    write_json(path, checked)
    (root / PACKAGE_LOCK_REL).unlink(missing_ok=True)
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
        raise DocflowError("Cannot record trace with invalid lock:\n- " + "\n- ".join(errors))
    if args.requirement not in lock["task"]["requirement_ids"]:
        raise DocflowError(f"Requirement is not in the context lock: {args.requirement}")
    code = _existing_paths(root, args.code or [], "Code")
    tests = _existing_paths(root, args.test or [], "Test")
    trace_path = root / TRACE_REL
    trace = load_json(trace_path) if trace_path.is_file() else {"schema_version": "1.0", "entries": []}
    entries = trace.setdefault("entries", [])
    if not isinstance(entries, list):
        raise DocflowError("traceability.json entries must be an array")
    key = (lock["task"]["id"], args.requirement)
    entry = next(
        (
            item
            for item in entries
            if isinstance(item, dict) and (item.get("task_id"), item.get("requirement_id")) == key
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
        entries.append(entry)
    for field, values in (("code", code), ("tests", tests)):
        current = entry.setdefault(field, [])
        for value in values:
            if value not in current:
                current.append(value)
    entry["documents"] = list(lock["selected_artifact_ids"])
    entry["recorded_at"] = utc_now()
    write_json(trace_path, trace)
    print(f"Recorded trace for {args.requirement}")


def verify_traceability(root: Path, lock: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    if not policy.get("require_traceability", True):
        return []
    errors: list[str] = []
    try:
        trace = load_json(root / TRACE_REL)
    except DocflowError as exc:
        return [str(exc)]
    if trace.get("schema_version") != "1.0" or not isinstance(trace.get("entries"), list):
        return ["traceability.json must have schema_version '1.0' and an entries array"]
    selected = set(lock.get("selected_artifact_ids", []))
    entry_index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in trace["entries"]:
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
        documents = set(entry.get("documents", [])) if isinstance(entry.get("documents"), list) else set()
        if not selected.issubset(documents):
            errors.append(f"Trace for {requirement} does not include every locked artifact")
        for field in ("code", "tests"):
            paths = entry.get(field)
            if not _string_list(paths):
                errors.append(f"Trace for {requirement} requires at least one {field} path")
                continue
            for path in paths:
                try:
                    if not repo_path(root, path).exists():
                        errors.append(f"Trace {field} path does not exist: {path}")
                except DocflowError as exc:
                    errors.append(str(exc))
        for code_path in entry.get("code", []) if isinstance(entry.get("code"), list) else []:
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
    trace = load_json(root / TRACE_REL)
    task_entries = [
        entry
        for entry in trace.get("entries", [])
        if isinstance(entry, dict) and entry.get("task_id") == lock["task"]["id"]
    ]
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
            errors.append(f"Changed implementation path is not traced for this task: {path}")
        required = required_artifacts_for_path(policy, path)
        missing = sorted(required - set(lock.get("selected_artifact_ids", [])))
        if missing:
            errors.append(f"Changed path {path} requires locked artifacts: {', '.join(missing)}")
    return errors


def cmd_verify(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    manifest = require_valid_manifest(root)
    active = [item["id"] for item in manifest["artifacts"] if item.get("status") != "superseded"]
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
        run, run_errors = check_run(root, lock["task"]["id"])
        errors.extend(run_errors)
        if run and run.get("status") != "completed":
            errors.append(f"Orchestrated run is not completed: {run.get('status')}")
    policy = load_policy(root)
    errors.extend(verify_traceability(root, lock, policy))
    if args.ci:
        if not args.base_ref:
            errors.append("CI verification requires --base-ref to bind every changed path to traceability")
        else:
            try:
                errors.extend(verify_changed_paths(root, manifest, lock, policy, args.base_ref))
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
                    status: sum(1 for item in run["packages"] if item.get("status") == status)
                    for status in PACKAGE_STATUSES
                }
                populated = ", ".join(
                    f"{status}={count}" for status, count in package_counts.items() if count
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

    init = sub.add_parser("init", help="Create an empty manifest after the document graph is approved")
    init.add_argument("--root", default=".")
    init.add_argument("--prd", required=True)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    validate = sub.add_parser("validate", help="Validate the dynamic document manifest")
    validate.add_argument("--root", default=".")
    validate.set_defaults(func=cmd_validate)

    status = sub.add_parser("set-status", help="Move one artifact through its review lifecycle")
    status.add_argument("--root", default=".")
    status.add_argument("--artifact", required=True)
    status.add_argument("--to", required=True, choices=STATUSES)
    status.set_defaults(func=cmd_set_status)

    approve = sub.add_parser("approve", help="Record explicit approval and the artifact content hash")
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

    prepare = sub.add_parser("prepare", help="Create a task lock from approved relevant artifacts")
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

    check = sub.add_parser("check-lock", help="Verify that the current task lock is still valid")
    check.add_argument("--root", default=".")
    check.set_defaults(func=cmd_check_lock)

    start_run = sub.add_parser("start-run", help="Create an execution run bound to the task lock")
    start_run.add_argument("--root", default=".")
    start_run.add_argument("--mode", choices=("auto", "single", "orchestrated"), default="auto")
    start_run.add_argument("--plan-summary")
    start_run.add_argument("--debate-rounds", type=int, default=0)
    start_run.add_argument("--constraint", action="append")
    start_run.add_argument("--actor", required=True)
    start_run.add_argument("--force", action="store_true")
    start_run.set_defaults(func=cmd_start_run)

    add_package = sub.add_parser("add-package", help="Add a locked work package to a planning run")
    add_package.add_argument("--root", default=".")
    add_package.add_argument("--package", required=True)
    add_package.add_argument("--summary")
    add_package.add_argument("--requirement", action="append", required=True)
    add_package.add_argument("--artifact", action="append", required=True)
    add_package.add_argument("--depends-on", action="append")
    add_package.add_argument("--allowed-path", action="append", required=True)
    add_package.add_argument("--acceptance", action="append")
    add_package.add_argument("--verification-command", action="append", required=True)
    add_package.add_argument("--actor", required=True)
    add_package.set_defaults(func=cmd_add_package)

    approve_run = sub.add_parser("approve-run", help="Approve the locked plan and every work package")
    approve_run.add_argument("--root", default=".")
    approve_run.add_argument("--approved-by", required=True)
    approve_run.set_defaults(func=cmd_approve_run)

    activate_package = sub.add_parser("activate-package", help="Activate one package lock in this worktree")
    activate_package.add_argument("--root", default=".")
    activate_package.add_argument("--package", required=True)
    activate_package.add_argument("--actor", required=True)
    activate_package.add_argument("--note")
    activate_package.set_defaults(func=cmd_activate_package)

    package_status = sub.add_parser("set-package-status", help="Advance a package through implementation and review")
    package_status.add_argument("--root", default=".")
    package_status.add_argument("--package", required=True)
    package_status.add_argument("--to", required=True, choices=PACKAGE_STATUSES)
    package_status.add_argument("--actor", required=True)
    package_status.add_argument("--note")
    package_status.set_defaults(func=cmd_set_package_status)

    import_package = sub.add_parser("import-package-result", help="Merge one reviewed worktree package into the central run")
    import_package.add_argument("--root", default=".")
    import_package.add_argument("--package", required=True)
    import_package.add_argument("--from-root", required=True)
    import_package.add_argument("--actor", required=True)
    import_package.set_defaults(func=cmd_import_package_result)

    integration = sub.add_parser("activate-integration", help="Lock one approved package path set for central integration")
    integration.add_argument("--root", default=".")
    integration.add_argument("--package", required=True)
    integration.add_argument("--actor", required=True)
    integration.set_defaults(func=cmd_activate_integration)

    escalate = sub.add_parser("escalate-package", help="Record a bounded package escalation")
    escalate.add_argument("--root", default=".")
    escalate.add_argument("--package", required=True)
    escalate.add_argument("--actor", required=True)
    escalate.add_argument("--reason", required=True)
    escalate.set_defaults(func=cmd_escalate_package)

    check_run_parser = sub.add_parser("check-run", help="Validate the current orchestration run")
    check_run_parser.add_argument("--root", default=".")
    check_run_parser.add_argument("--task-id")
    check_run_parser.set_defaults(func=cmd_check_run)

    check_package = sub.add_parser("check-package-lock", help="Validate the active package ownership lock")
    check_package.add_argument("--root", default=".")
    check_package.set_defaults(func=cmd_check_package_lock)

    complete_run = sub.add_parser("complete-run", help="Close a run after every package is integrated")
    complete_run.add_argument("--root", default=".")
    complete_run.add_argument("--actor", required=True)
    complete_run.add_argument("--note")
    complete_run.set_defaults(func=cmd_complete_run)

    guard = sub.add_parser("guard-edit", help="Check whether a repository path may be edited")
    guard.add_argument("--root", default=".")
    guard.add_argument("--path", required=True)
    guard.set_defaults(func=cmd_guard_edit)

    trace = sub.add_parser("trace", help="Record requirement-to-document/code/test traceability")
    trace.add_argument("--root", default=".")
    trace.add_argument("--requirement", required=True)
    trace.add_argument("--code", action="append")
    trace.add_argument("--test", action="append")
    trace.set_defaults(func=cmd_trace)

    verify = sub.add_parser("verify", help="Run the final document-driven gate")
    verify.add_argument("--root", default=".")
    verify.add_argument("--ci", action="store_true")
    verify.add_argument("--base-ref", help="Git base commit or ref used to verify every changed path in CI")
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
