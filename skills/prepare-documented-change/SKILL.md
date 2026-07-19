---
name: prepare-documented-change
description: Prepare an implementation task by locating its PRD requirement ids, selecting the relevant approved artifacts and dependencies from docs/document-manifest.json, reading them, and creating a SHA-256 context lock. Use before any product code, test, migration, infrastructure, or generated-contract change in a repository with the document-driven harness.
---

# Prepare Documented Change

Create the implementation boundary; do not implement inside this skill.

## 1. Define the task

Read the user request, PRD, manifest, and policy. Identify:

- one stable task id
- a concise task summary
- at least one requirement id present in the PRD or selected approved artifacts
- implementation scopes matching manifest `required_for` tags
- paths likely to change and any matching policy `path_rules`

If the requested behavior has no traceable requirement, stop. Use the appropriate
document workflow to add and approve the requirement before implementation.

## 2. Select the minimum sufficient context

Select artifacts whose `required_for` scopes cover the task, plus every recursive
`depends_on` artifact and every artifact required by matching path rules. Do not
lock the entire document graph merely because it exists. Do not omit an artifact
to avoid an approval problem.

Present the selected PRD requirements, artifacts, and likely paths. Ask for user
confirmation only when there are multiple materially different scope choices.
If an artifact is not approved or its approval hash is stale, stop and use
`author-project-document`.

## 3. Prepare the lock

Run the repository-local tool, repeating flags as necessary:

```text
python3 .document-driven/bin/docflow.py prepare --root <repo> \
  --task-id <id> --summary <summary> \
  --requirement <requirement-id> \
  --scope <scope> \
  --artifact <explicit-artifact-id>
```

Then run `check-lock`. The lock must contain hashes for the manifest, PRD, and
selected approved artifacts.

## 4. Read and synthesize before implementation

Read every file listed in `.document-driven/context-lock.json` completely. Create
a compact implementation plan mapping each requirement to:

- intended code boundary
- intended test or verification
- constraints from each relevant artifact
- uncertainty that would require a document revision

Do not write product code, tests, migrations, or infrastructure yet. Hand off the
valid lock and plan to `implement-from-documents`.

## Non-negotiable gates

- Requirement ids must occur in the PRD or selected documents.
- Only approved artifacts can be locked.
- Dependencies and path-rule artifacts cannot be skipped.
- The task lock is invalid after any locked document or manifest change.
- Preparation ends before implementation begins.
