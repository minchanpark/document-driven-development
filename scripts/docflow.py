#!/usr/bin/env python3
"""Deterministic state and validation tools for document-driven development."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
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
STATUSES = ("proposed", "drafting", "reviewed", "approved", "superseded")
TRANSITIONS = {
    "proposed": {"drafting", "superseded"},
    "drafting": {"reviewed", "superseded"},
    "reviewed": {"drafting", "superseded"},
    "approved": {"drafting", "superseded"},
    "superseded": set(),
}


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
            ".github/workflows/document-driven-development.yml",
            "AGENTS.md",
            "CLAUDE.md",
            "README.md",
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


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def check_lock(root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
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
        manifest = require_valid_manifest(root)
        policy = load_policy(root)
        path = relative_path(root, raw_path)
    except DocflowError as exc:
        return False, str(exc)
    if is_documentation_path(root, manifest, policy, path):
        return True, f"Documentation or harness path allowed: {path}"
    lock, errors = check_lock(root)
    if errors or not lock:
        return False, "Implementation write blocked. " + " ".join(errors)
    selected = set(lock.get("selected_artifact_ids", []))
    required = required_artifacts_for_path(policy, path)
    missing = sorted(required - selected)
    if missing:
        return False, f"{path} requires locked artifacts: {', '.join(missing)}"
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


def cmd_prepare(args: argparse.Namespace) -> None:
    root = find_root(args.root)
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
    print(f"Prepared task {args.task_id} with artifacts: {', '.join(ordered)}")


def cmd_check_lock(args: argparse.Namespace) -> None:
    root = find_root(args.root)
    lock, errors = check_lock(root)
    if errors or not lock:
        raise DocflowError("Context lock invalid:\n- " + "\n- ".join(errors))
    print(f"Context lock valid for task {lock['task']['id']}")


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
    for requirement in lock["task"]["requirement_ids"]:
        matches = [
            entry
            for entry in trace["entries"]
            if isinstance(entry, dict)
            and entry.get("task_id") == lock["task"]["id"]
            and entry.get("requirement_id") == requirement
        ]
        if not matches:
            errors.append(f"Missing traceability entry for {requirement}")
            continue
        entry = matches[-1]
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

    prepare = sub.add_parser("prepare", help="Create a task lock from approved relevant artifacts")
    prepare.add_argument("--root", default=".")
    prepare.add_argument("--task-id", required=True)
    prepare.add_argument("--summary")
    prepare.add_argument("--requirement", action="append", required=True)
    prepare.add_argument("--scope", action="append")
    prepare.add_argument("--artifact", action="append")
    prepare.set_defaults(func=cmd_prepare)

    check = sub.add_parser("check-lock", help="Verify that the current task lock is still valid")
    check.add_argument("--root", default=".")
    check.set_defaults(func=cmd_check_lock)

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
