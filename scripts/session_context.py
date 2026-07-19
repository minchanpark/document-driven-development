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
            message = (
                f"Document-driven development: valid context lock for task {lock['task']['id']}. "
                "Read every locked document and re-check the lock immediately before implementation."
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
