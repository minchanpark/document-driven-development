#!/usr/bin/env python3
"""Low-amplification storage primitives for the document-driven harness.

The public CLI remains in ``docflow.py``.  This module keeps append-only history,
sharded trace records, validation leases, reusable evidence, and worktree state
behind small deterministic files so routine checks do not grow with history.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


PACKAGE_TRANSITIONS = {
    "planned": {"approved-for-implementation"},
    "approved-for-implementation": {"implementing", "blocked"},
    "implementing": {"implemented", "blocked"},
    "implemented": {"reviewing", "blocked"},
    "reviewing": {"approved", "rejected", "blocked"},
    "approved": {"integrated", "blocked"},
    "rejected": {"implementing", "blocked"},
    "integrated": set(),
    "blocked": set(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(json_bytes(value))
    temporary.replace(path)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def new_event(actor: str, status: str, note: str = "") -> dict[str, str]:
    return {
        "id": uuid.uuid4().hex,
        "at": utc_now(),
        "actor": actor,
        "status": status,
        "note": note,
    }


def run_events_path(run_json_path: Path) -> Path:
    return run_json_path.with_name("events.jsonl")


def _empty_event_state() -> dict[str, Any]:
    return {
        "count": 0,
        "last_status": None,
        "last_actor": None,
        "last_note": "",
        "actors_by_status": {},
        "transition_counts": {},
        "implementer": None,
        "reviewer": None,
        "errors": [],
    }


def accumulate_event(
    current: dict[str, Any] | None,
    event: dict[str, Any],
    *,
    package: bool,
) -> dict[str, Any]:
    state = (
        json.loads(json.dumps(current))
        if isinstance(current, dict)
        else _empty_event_state()
    )
    errors = state.setdefault("errors", [])
    previous = state.get("last_status")
    status = event.get("status")
    actor = event.get("actor")
    note = event.get("note", "")
    if not isinstance(status, str) or not status:
        errors.append("event status is required")
    if not isinstance(actor, str) or not actor:
        errors.append("event actor is required")
    if not isinstance(note, str):
        errors.append("event note must be a string")
        note = ""
    transitioned = status != previous
    if package:
        if previous is None and status != "planned":
            errors.append("package lifecycle must begin at planned")
        elif (
            previous is not None
            and transitioned
            and status not in PACKAGE_TRANSITIONS.get(previous, set())
        ):
            errors.append(f"invalid package transition: {previous} -> {status}")
        if transitioned and status == "implementing":
            state["implementer"] = actor
            state["reviewer"] = None
        elif transitioned and status == "reviewing":
            if actor == state.get("implementer"):
                errors.append("reviewer must differ from implementer")
            state["reviewer"] = actor
        elif transitioned and status == "approved" and state.get("reviewer") != actor:
            errors.append("approval actor must be the active reviewer")
        if (
            transitioned
            and status in {"implemented", "approved", "integrated"}
            and not note
        ):
            errors.append(f"{status} event requires evidence note")
    state["count"] = int(state.get("count", 0)) + 1
    state["last_status"] = status
    state["last_actor"] = actor
    state["last_note"] = note
    state.setdefault("actors_by_status", {})[str(status)] = actor
    counts = state.setdefault("transition_counts", {})
    counts[str(status)] = int(counts.get(str(status), 0)) + (1 if transitioned else 0)
    return state


def event_state_from_events(
    events: Iterable[dict[str, Any]], *, package: bool
) -> dict[str, Any]:
    state: dict[str, Any] | None = None
    for event in events:
        state = accumulate_event(state, event, package=package)
    return state or _empty_event_state()


def append_event(
    run_json_path: Path,
    event: dict[str, Any],
    *,
    package_id: str | None = None,
) -> None:
    path = run_events_path(run_json_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "1.0",
        "scope": "package" if package_id else "run",
        "package_id": package_id,
        "event": event,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


def record_run_event(
    run_json_path: Path, run: dict[str, Any], event: dict[str, Any]
) -> None:
    append_event(run_json_path, event)
    run["event_state"] = accumulate_event(run.get("event_state"), event, package=False)


def record_package_event(
    run_json_path: Path,
    package: dict[str, Any],
    event: dict[str, Any],
) -> None:
    append_event(run_json_path, event, package_id=str(package["id"]))
    package["event_state"] = accumulate_event(
        package.get("event_state"), event, package=True
    )


def persist_run(run_json_path: Path, run: dict[str, Any]) -> None:
    if run.get("storage_mode") != "append-only":
        write_json(run_json_path, run)
        return
    snapshot = json.loads(json.dumps(run))
    snapshot.pop("events", None)
    for package in snapshot.get("packages", []):
        if isinstance(package, dict):
            package.pop("events", None)
    write_json(run_json_path, snapshot)


def load_run(run_json_path: Path, *, hydrate_events: bool = False) -> dict[str, Any]:
    try:
        run = json.loads(run_json_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing required file: {run_json_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {run_json_path}: {exc}") from exc
    if not isinstance(run, dict):
        raise ValueError(f"Expected a JSON object in {run_json_path}")
    if run.get("storage_mode") != "append-only" or not hydrate_events:
        return run
    run["events"] = []
    packages = {
        item.get("id"): item
        for item in run.get("packages", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for package in packages.values():
        package["events"] = []
    path = run_events_path(run_json_path)
    if not path.is_file():
        raise ValueError(f"Missing append-only event log: {path}")
    seen: set[str] = set()
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid event log line {number} in {path}: {exc}"
            ) from exc
        event = record.get("event") if isinstance(record, dict) else None
        if not isinstance(event, dict):
            raise ValueError(f"Invalid event log line {number} in {path}")
        event_id = event.get("id")
        if isinstance(event_id, str):
            if event_id in seen:
                raise ValueError(f"Duplicate event id {event_id} in {path}")
            seen.add(event_id)
        if record.get("scope") == "run":
            run["events"].append(event)
        elif record.get("scope") == "package":
            package = packages.get(record.get("package_id"))
            if package is None:
                raise ValueError(
                    f"Event references unknown package on line {number}: {record.get('package_id')}"
                )
            package["events"].append(event)
        else:
            raise ValueError(f"Invalid event scope on line {number} in {path}")
    return run


def trace_shard_path(root: Path, task_id: str, requirement_id: str) -> Path:
    return root / ".document-driven" / "trace" / task_id / f"{requirement_id}.json"


def load_trace_entries(
    root: Path,
    *,
    task_id: str | None = None,
    requirement_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    index_path = root / ".document-driven" / "traceability.json"
    legacy: list[dict[str, Any]] = []
    if index_path.is_file():
        value = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != "1.0":
            raise ValueError("traceability.json must have schema_version '1.0'")
        entries = value.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("traceability.json entries must be an array")
        legacy = [item for item in entries if isinstance(item, dict)]
    merged = {
        (item.get("task_id"), item.get("requirement_id")): item
        for item in legacy
        if (task_id is None or item.get("task_id") == task_id)
    }
    if task_id is not None and requirement_ids is not None:
        candidates = [
            trace_shard_path(root, task_id, requirement)
            for requirement in requirement_ids
        ]
    else:
        base = root / ".document-driven" / "trace"
        candidates = list(base.glob("*/*.json")) if base.is_dir() else []
    for path in candidates:
        if not path.is_file():
            continue
        item = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(item, dict):
            merged[(item.get("task_id"), item.get("requirement_id"))] = item
    return list(merged.values())


def ensure_sharded_trace_index(root: Path) -> None:
    path = root / ".document-driven" / "traceability.json"
    value: dict[str, Any]
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        value = loaded if isinstance(loaded, dict) else {"entries": []}
    else:
        value = {"entries": []}
    if value.get("storage_mode") != "sharded":
        value["schema_version"] = "1.0"
        value["storage_mode"] = "sharded"
        value.setdefault("entries", [])
        write_json(path, value)


def lease_path(root: Path) -> Path:
    return root / ".document-driven" / ".cache" / "validation-lease.json"


def invalidate_lease(root: Path) -> None:
    lease_path(root).unlink(missing_ok=True)


def _file_stamp(root: Path, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": path.relative_to(root).as_posix(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def issue_lease(
    root: Path,
    *,
    task_id: str,
    selected_artifacts: list[str],
    document_paths: list[str],
    allowed_paths: list[str],
    package_id: str | None,
    run_status: str | None,
    sources: Iterable[Path],
    ttl_seconds: int = 300,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    existing = [path for path in sources if path.is_file()]
    lease = {
        "schema_version": "1.0",
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=max(ttl_seconds, 1))).isoformat(),
        "task_id": task_id,
        "selected_artifacts": selected_artifacts,
        "document_paths": document_paths,
        "allowed_paths": allowed_paths,
        "package_id": package_id,
        "run_status": run_status,
        "sources": [_file_stamp(root, path) for path in existing],
    }
    write_json(lease_path(root), lease)
    return lease


def valid_lease(root: Path) -> dict[str, Any] | None:
    path = lease_path(root)
    if not path.is_file():
        return None
    try:
        lease = json.loads(path.read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(str(lease["expires_at"]))
        if expires <= datetime.now(timezone.utc):
            return None
        for source in lease.get("sources", []):
            target = root / source["path"]
            stat = target.stat()
            if stat.st_size != source["size"] or stat.st_mtime_ns != source["mtime_ns"]:
                return None
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return lease if isinstance(lease, dict) else None


def evidence_path(root: Path, gate_id: str, fingerprint: str) -> Path:
    return root / ".document-driven" / "evidence" / gate_id / f"{fingerprint}.json"


def run_evidence_path(root: Path, task_id: str, package_id: str, gate_id: str) -> Path:
    return (
        root
        / ".document-driven"
        / "runs"
        / task_id
        / "evidence"
        / package_id
        / f"{gate_id}.json"
    )


def worktree_registry_path(root: Path) -> Path:
    return root / ".document-driven" / "worktrees.json"


def load_worktree_registry(root: Path) -> dict[str, Any]:
    path = worktree_registry_path(root)
    if not path.is_file():
        return {"schema_version": "1.0", "entries": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise ValueError(f"Invalid worktree registry: {path}")
    return value


def save_worktree_registry(root: Path, registry: dict[str, Any]) -> None:
    write_json(worktree_registry_path(root), registry)
