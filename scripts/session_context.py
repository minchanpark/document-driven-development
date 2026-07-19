#!/usr/bin/env python3
"""Build shared context for platform-specific session/invocation adapters."""

from __future__ import annotations

import os
from pathlib import Path

import docflow


def context_message(root: Path | None = None) -> str | None:
    root = root or docflow.find_root(os.getcwd())
    if not (root / docflow.MANIFEST_REL).is_file():
        return None
    try:
        manifest = docflow.load_manifest(root)
        errors = docflow.validate_manifest(root, manifest)
        active = [
            item
            for item in manifest.get("artifacts", [])
            if isinstance(item, dict) and item.get("status") != "superseded"
        ]
        approved = [item for item in active if item.get("status") == "approved"]
        lock, lock_errors = docflow.check_lock(root)
        if errors:
            message = (
                "Document-driven development is initialized, but the manifest is invalid. "
                "Do not implement. Repair and approve the document graph first."
            )
        elif not active:
            message = (
                "Document-driven development is initialized, but the approved graph has no artifacts. "
                "Complete document discovery before implementation."
            )
        elif len(approved) != len(active):
            message = (
                f"Document-driven development: {len(approved)}/{len(active)} active artifacts are approved. "
                "Do not implement work that depends on unapproved artifacts."
            )
        elif lock and not lock_errors:
            package_lock, package_errors = docflow.check_package_lock(root)
            active_run_path = docflow.run_path(root, lock["task"]["id"])
            if package_lock and not package_errors:
                phase = package_lock.get("phase", "implementation")
                message = (
                    f"Document-driven development: package {package_lock['package_id']} {phase} lock is active "
                    f"for task {lock['task']['id']}. Read every locked document and edit only the "
                    "package-owned paths."
                )
            elif active_run_path.is_file():
                run, run_errors = docflow.check_run(root, lock["task"]["id"])
                if run and not run_errors and run.get("status") == "completed":
                    message = (
                        f"Document-driven development: completed run and valid context lock for task "
                        f"{lock['task']['id']}. Final verification remains required."
                    )
                elif run and not run_errors:
                    message = (
                        f"Document-driven development: run {run['status']} for task {lock['task']['id']}. "
                        "Implementation writes require an active package lock in this worktree."
                    )
                else:
                    message = "Document-driven development run state is invalid. Do not implement."
            else:
                message = (
                    f"Document-driven development: valid context lock for task {lock['task']['id']}. "
                    "Read every locked document and choose single or orchestrated implementation."
                )
        else:
            message = (
                "Document-driven development: all active artifacts are approved, but no valid task lock exists. "
                "Run the prepare-documented-change workflow before implementation."
            )
    except Exception as exc:
        message = f"Document-driven project state could not be verified: {exc}. Do not implement."
    return message


if __name__ == "__main__":
    message = context_message()
    print(message or "")
