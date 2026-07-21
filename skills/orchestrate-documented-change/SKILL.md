---
name: orchestrate-documented-change
description: Orchestrate a complex implementation from an approved document context lock through locked-plan challenge, package decomposition, isolated implementation, independent cross-review, bounded fixes, green integration, and final traceability verification. Use when a documented change spans multiple ownership boundaries or carries database, authorization, migration, infrastructure, or other high risk. Keep one Main Orchestrator and do not revise approved design in code.
---

# Orchestrate Documented Change

The current host agent is the single Main Orchestrator. It owns decisions,
package boundaries, status, integration, and the final report. Subagents and
external providers are workers; they do not become peer orchestrators.

## 1. Validate the task boundary

Run `check-lock`, then read the context lock and its compact context pack. Open
full locked documents for ambiguous excerpts, cross-cutting constraints, or
plan challenge evidence. If the lock is absent or stale, return to
`prepare-documented-change`.

Choose execution mode from actual risk:

- `single`: one low-risk boundary; hand off directly to `implement-from-documents`
- `orchestrated`: multiple boundaries, meaningful parallelism, or data,
  authorization, migration, infrastructure, security, or operational risk
- `auto`: make the above choice and explain it briefly

Do not create an orchestrated run merely to add agents to a small change.

## 2. Challenge, then lock the plan

Create an implementation plan only inside the approved document boundary. Ask a
read-only architect, preferably a different provider when available, to attack:

- requirement coverage and missing verification
- package boundaries and dependency order
- file ownership overlap and integration risk
- contradictions with approved documents

Limit debate to the configured rounds. Better architecture is a document change,
not a coding shortcut. If review exposes design drift, stop, revise the relevant
artifact, obtain explicit approval, and prepare a new context lock.

Start the run with `docflow.py start-run`. Present the locked plan and package
graph to the user and obtain explicit approval before implementation writes.

## 3. Decompose into owned packages

Use `add-package` for every package. Each package must include requirement ids,
artifact ids, dependencies, non-overlapping `allowed_paths`, and executable
verification commands. Add explicit `--acceptance` entries covering the happy
path, authorization or tenant negatives, concurrency/idempotency, dependency
failure, cleanup, accessibility, and rollback whenever relevant. Label required
evidence as static, unit, integration, real-local, hosted, or manual in the
criterion or command. Prefer vertical boundaries that can be implemented and
reviewed independently. Shared interfaces should be settled in an earlier
dependency package rather than edited concurrently.

For cacheable or environment-dependent checks, add a structured
`--verification-spec` with a stable id, type, command, prerequisites, input
paths, blocking phase, and cache policy. Plain commands remain compatible, but
only structured gates create reusable evidence and mechanically block their
declared phase.

Before locking the package graph, remove speculative packages, files,
dependencies, configuration, and abstractions. Reuse settled repository
interfaces and keep fewer packages when ownership and risk permit it. Minimalism
never removes locked acceptance, security, accessibility, test, traceability, or
evidence obligations.

Run `check-run`, then `approve-run --approved-by <user>` only after explicit
approval. The run state belongs under `.document-driven/runs/<task-id>/`; it is
not a long-lived artifact in `docs/document-manifest.json`.

## 4. Implement in isolation

Keep the original worktree's run as the canonical central state. Create one
isolated worktree or equivalent isolated copy per ready package from the approved
run snapshot. Each worker must contain the identical Task Context Lock and its own
copy of both `run.json` and `events.jsonl`, then activate exactly one package in
that workspace:

```text
python3 .document-driven/bin/docflow.py activate-package --root <worktree> \
  --package <id> --actor <worker>
```

Delegate the package to a host-native coder or call the optional provider runner
with `--role coder --access workspace-write`. Every coder follows
`implement-from-documents`, edits only owned paths, runs declared verification,
and reports exact evidence. Packages with unmet dependencies do not start.

Run `docflow.py preflight --package <id>` before implementation. Separate
an unavailable Docker, browser, credential, or hosted environment from a product
failure. Record unavailable external evidence once and keep it pending for the
integration gate; do not spend fix iterations rerunning an unchanged missing
environment.

Run affected structured checks with `verify-package --package <id> --execute`.
Supply non-secret environment identity through `--environment NAME=VALUE` for
an environment-scoped cache; values are hashed in evidence. Reuse is valid only
when the complete document, package, input, command, and environment fingerprint
matches.

When implementation is complete, transition the package to `implemented` with
test evidence. A design conflict stops the worker immediately and returns to the
document approval workflow.

## 5. Cross-review and bounded fix loop

Assign a reviewer different from the implementer. The reviewer is read-only and
checks the package contract, approved documents, complete diff, tests, security,
ownership, and unjustified files, dependencies, configuration, or abstractions.
Move the package through `reviewing` to `approved` or `rejected`.

On rejection, reactivate the same package for the implementer, apply only the
review findings, and return the changed diff, prior findings, and affected tests
to the same reviewer. Do not resend unchanged full documents or successful logs.
Respect `max_fix_iterations`.
Use `escalate-package` for a stronger model, another provider, or user judgment;
respect `max_escalation_steps` and never silently broaden scope.

When a worker package reaches `approved`, `rejected`, or `blocked`, import only
that validated package result into the central run. Never copy an entire stale
worker `run.json` over the central run:

```text
python3 .document-driven/bin/docflow.py import-package-result --root <central> \
  --package <id> --from-root <worktree> --actor <main-orchestrator>
```

## 6. Integrate through the green gate

The Main Orchestrator integrates approved packages in dependency order. Before
merging or cherry-picking one package, activate its central integration lock so
the same `allowed_paths` remain enforced:

```text
python3 .document-driven/bin/docflow.py activate-integration --root <central> \
  --package <id> --actor <main-orchestrator>
```

Run package verification and affected integration tests, record traceability,
and inspect the combined diff. Run the full repository-wide gate once after all
packages are combined, unless an affected invariant requires it earlier. Mark a
package `integrated` only with evidence; the
integration lock is then released. If integration reveals a document conflict,
stop and re-approve the design before continuing.

Register secondary Git worktrees when they are not imported automatically.
After every package is integrated, run `complete-run`, then use
`verify-document-driven-change`. CI must pass `docflow.py verify --ci` before
merge. Report document/hash state, package results, reviewers, tests, traceability,
and remaining risks.

Run completion automatically removes only registered, clean, integrated
secondary worktrees that Git proves reachable or content-equivalent. Use
`worktree-gc` without `--apply` to inspect eligibility when cleanup is uncertain.

## Non-negotiable gates

- Exactly one Main Orchestrator owns the run.
- No approved locked plan, no orchestrated implementation.
- No active Package Lock, no package write.
- No overlapping file ownership.
- Implementer and reviewer must differ.
- Code never silently changes approved architecture.
- No green package and final document gate, no integration or merge.
