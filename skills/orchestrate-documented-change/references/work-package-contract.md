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
  "verification_specs": [
    {
      "id": "server-unit",
      "type": "unit",
      "command": "pytest tests/server",
      "requires": [],
      "input_paths": ["src/server/**", "tests/server/**"],
      "blocking_phase": "package",
      "cache_policy": "input-hash"
    }
  ],
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

Structured verification specs are optional for legacy compatibility and
required when a gate needs mechanical phase blocking, environment preflight, or
evidence reuse. `unavailable` is pending evidence rather than rejection, but it
still blocks the declared phase. Reuse requires an identical approved-document,
package-contract, input, command, and environment fingerprint.

New runs keep only lifecycle accumulators in `run.json`; complete events live in
`events.jsonl`. Routine checks use the bounded snapshot. Plan approval and final
verification replay the append-only log. A completed predecessor may be named by
`start-run --supersedes`, but provenance never bypasses the package contract.
