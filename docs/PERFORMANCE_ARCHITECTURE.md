# Performance architecture

## Goal and honest complexity boundary

Document-driven development must preserve explicit approval, hash-bound design,
independent review, and final evidence. The optimization target is therefore not
"make all development constant time." Reading a changed implementation, running
affected tests, and performing a final audit are necessarily proportional to the
change or evidence being checked.

The target is narrower and measurable:

- routine write authorization approaches amortized O(1) with respect to document
  count and run history;
- one run-state transition is O(1) with respect to prior event count;
- one trace update is O(1) with respect to other requirements;
- one evidence lookup is O(1) by deterministic fingerprint;
- cleanup cost is proportional to active registered worktrees, not historical
  runs;
- full document hashing, event replay, and complete test execution happen at
  explicit integrity boundaries rather than before every edit.

## Bottlenecks and replacements

| Previous bottleneck | Why it grew | Replacement | Routine complexity |
|---|---|---|---|
| Rewriting `run.json` with every event | Every transition copied all prior history | Small snapshot plus append-only `events.jsonl` | O(1) in event history |
| Scanning all package events for actor/rejection checks | Lifecycle decisions replayed history | `event_state` accumulator in the snapshot | O(1) in event history |
| Pairwise ownership checks on every run check | Package patterns were compared repeatedly | Approved package-contract hash; pairwise check only during planning or audit | O(P) routine, O(P^2) approval/audit |
| Rewriting one global trace array | Every requirement update copied all trace records | Deterministic requirement shard | O(1) in other requirements |
| Rehashing every locked document before every write | Hooks repeated immutable validation | Persistent validation lease over fixed state-file stamps | O(1) fixed metadata checks |
| Running every declared command after every change | Successful unaffected checks had no identity | Input, document, command, and environment fingerprint evidence | O(1) lookup plus affected input hashing |
| Treating missing Docker/browser/hosted access as a test failure | Environment absence entered fix loops | Structured `ready`, `unavailable`, `passed`, `failed`, `reused`, `attested` states | One preflight classification |
| Manually retaining isolated worktrees | No lifecycle registry or safe removal proof | Registered worktree GC with Git, cleanliness, and integration checks | O(active worktrees) |

`P` is the number of packages. Final verification intentionally pays the audit
cost because that is the point at which complete historical proof is required.

## Append-only run storage

New runs declare `storage_mode: append-only`.

```text
.document-driven/runs/<task-id>/
|-- run.json       # current run/package snapshot and event accumulators
`-- events.jsonl   # append-only run and package lifecycle records
```

Each event has a unique id, timestamp, actor, status, and evidence note. The
snapshot stores counts, last status, last actor per status, implementer,
reviewer, transition counts, and accumulated validation errors. Routine checks
read only the snapshot. `check-run --audit`, `approve-run`, `complete-run`, and
the final `verify` command replay the entire event log and revalidate lifecycle
ordering and independent review.

Legacy runs containing inline `events` arrays remain readable and writable. An
append-only central run importing a legacy or append-only worker copies only
events not already present and never overwrites the complete central run.

## Requirement-sharded traceability

The compatibility index remains at `.document-driven/traceability.json`, but
new entries are canonical at:

```text
.document-driven/trace/<task-id>/<requirement-id>.json
```

Updating one requirement reads and writes only that shard. Existing legacy
entries are used as a fallback and are overridden by a shard with the same task
and requirement key. `trace-export` reconstructs the legacy single-object shape
for integrations that still require it.

## Persistent validation lease

After a full successful guard validation, the harness writes an untracked lease
to `.document-driven/.cache/validation-lease.json`. The installer adds that
directory to `.gitignore`.

The lease contains:

- task and active package identity;
- selected artifacts and allowed paths;
- dynamic document paths;
- run status;
- fixed state-file size and nanosecond modification stamps;
- a short expiry time.

The next code-only write checks the fixed state-file set, package ownership, and
path policy without hashing all approved documents again. A write targeting a
document or harness path invalidates the lease before it is allowed. Changes to
the manifest, context lock, package lock, run snapshot, policy, or package
context pack also invalidate it through their file stamps.

This lease is a local latency optimization, not the source of final truth. A
change performed outside installed hooks can survive until lease expiry. The
explicit `check-lock`, audited run checks, and CI `verify` command always perform
authoritative content-hash validation. Use `invalidate-lease` when an external
tool changes document-driven state.

## Structured verification and evidence reuse

Plain `verification_commands` remain compatible. A package opts into enforced,
cacheable evidence by adding one or more `--verification-spec` JSON objects:

```json
{
  "id": "db-integration",
  "type": "integration",
  "command": "pytest tests/integration",
  "requires": ["docker"],
  "input_paths": ["src/**", "tests/integration/**"],
  "blocking_phase": "integration",
  "cache_policy": "input-hash"
}
```

Supported types are `command`, `unit`, `integration`, `hosted`, `external`, and
`manual`. Blocking phases are `package`, `integration`, and `release`. Cache
policies are `input-hash`, `environment`, and `never`.

Use:

```text
docflow.py preflight --package <id> --available docker
docflow.py verify-package --package <id> --gate <gate> --execute
```

The fingerprint binds approved document hashes, the immutable package contract,
the gate definition, matching input file hashes, and caller-supplied environment
fingerprints. Environment values are hashed and are never stored in clear text.
An `environment` cache policy cannot execute or reuse evidence without at least
one `--environment NAME=VALUE` input.

Passing global evidence is stored at a deterministic gate/fingerprint path.
Later invocations, including a superseding run, reuse it only when the complete
fingerprint matches. Command output is represented by byte counts and hashes,
not copied into repository evidence where it could leak credentials or consume
large amounts of context.

Missing prerequisites are recorded as `unavailable`, not `failed`. They do not
consume a fix iteration, but an unavailable required gate still blocks its
declared package, integration, or release transition. Manual gates require an
explicit attestation actor and note.

Runs may declare completed predecessors with repeated `start-run --supersedes
<task-id>`. The link is provenance; it does not bypass contract or evidence
fingerprints.

## Worktree lifecycle and garbage collection

Imported real Git worktrees are registered automatically. A worktree can also
be registered explicitly with `register-worktree`. On package integration its
registry state becomes `integrated`. Completion invokes GC when enabled in
`orchestration.json`; `worktree-gc` supports a dry run and explicit `--apply`.

Removal is permitted only when all of these are true:

1. the registry marks the worktree integrated or superseded;
2. Git reports it as a secondary worktree;
3. it is neither the repository root nor the current working directory;
4. its working tree is clean;
5. its source commit is an ancestor of central `HEAD`, or its owned paths are
   content-equivalent to central `HEAD` (supporting squash integration);
6. the configured retention interval has elapsed.

Removal uses `git worktree remove` without force. Successfully removed entries
are pruned from the active registry so lookup cost does not grow with history.

## Invalidation and audit matrix

| Event | Lease | Cached evidence | Event/trace audit |
|---|---|---|---|
| Code-only edit inside package paths | Reused until state changes or expiry | Input fingerprint changes if affected | Final audit unchanged |
| Locked document edit through a hook | Invalidated before write | Document fingerprint changes | Lock fails until re-approved |
| Package contract change | Run snapshot stamp changes | Contract fingerprint changes | Approved-plan hash fails |
| Toolchain/input change | Lease may remain valid | Input fingerprint changes | Relevant gate reruns |
| Hosted environment change | Lease unrelated | Environment fingerprint changes | Hosted gate reruns |
| Trace update for one requirement | Lease invalidated as harness state | Evidence unrelated | One shard rewritten |

## Performance verification

Regression tests cover append-only snapshots and audit replay, sharded trace
export, lease reuse without manifest revalidation, structured external gates,
cross-run evidence reuse, supersession metadata, and safe worktree removal.

Performance claims should be checked with both asymptotic shape and real
measurements:

- grow event history and confirm snapshot size remains bounded;
- grow unrelated trace shards and confirm one shard update touches no peers;
- count manifest validations across adjacent guarded edits;
- use a command-side counter to confirm an identical fingerprint reuses proof;
- measure final audit separately from the routine edit path.

The optimization is successful when repeated unchanged work becomes constant or
amortized constant in historical state while explicit final integrity remains
complete.
