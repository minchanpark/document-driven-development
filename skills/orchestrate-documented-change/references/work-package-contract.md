# Work package contract

Every package is a bounded implementation contract derived from the active Task
Context Lock. It must contain:

```json
{
  "id": "backend",
  "summary": "Implement the approved endpoint behavior",
  "requirement_ids": ["REQ-12"],
  "artifact_ids": ["architecture", "api-contract"],
  "depends_on": [],
  "allowed_paths": ["src/server/**", "tests/server/**"],
  "verification_commands": ["pytest tests/server"],
  "status": "approved-for-implementation"
}
```

The deterministic lifecycle is:

```text
planned
  -> approved-for-implementation
  -> implementing
  -> implemented
  -> reviewing
  -> approved | rejected
  -> integrated
```

`rejected` returns to `implementing` through package activation. `blocked` is
terminal for the current run. Evidence notes are required when declaring
implemented, approved, or integrated. The harness rejects an implementing actor
as the cross-reviewer and caps fix and escalation loops from orchestration policy.

Path ownership is conservative. Patterns with common static prefixes are treated
as overlapping. Package paths never include `.document-driven/**`; the harness
owns that state. During an active package, design documents are also outside the
write boundary. A necessary design change invalidates the Task Lock and requires
explicit re-approval.

The central run is authoritative. Isolated worktrees receive snapshots and may
advance only their owned package. `import-package-result` validates the identical
Task Lock, immutable package contract, complete lifecycle events, independent
reviewer, and evidence before merging that package record into central state.
It never replaces the whole central run.

Central code integration uses a Package Lock with `phase: integration`. It keeps
the package's same path ownership while the Main Orchestrator merges the branch.
The package cannot become `integrated` without this lock.
