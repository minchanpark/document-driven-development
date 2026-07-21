---
name: verify-document-driven-change
description: Verify a completed implementation against its approved dynamic document graph, current context lock, path policy, requirement traceability, code, and tests. Use before claiming completion, committing, opening a PR, or merging a document-driven change, and when diagnosing document/code drift or CI gate failures.
---

# Verify Document-Driven Change

Verification is evidence gathering, not a ceremonial final command.

## 1. Verify deterministic state

Run:

```text
python3 .document-driven/bin/docflow.py validate --root <repo>
python3 .document-driven/bin/docflow.py check-lock --root <repo>
python3 .document-driven/bin/docflow.py check-run --root <repo> --audit
python3 .document-driven/bin/docflow.py verify --root <repo>
```

These checks must confirm current approval hashes, manifest and document hashes,
requirement ids, locked dependencies, policy-required artifacts, existing code and
test paths, and complete traceability for the active task.

When `.document-driven/runs/<task-id>/run.json` exists, also require a valid,
`completed` run, no active Package Lock, independent review evidence, and every
package in `integrated` status. `verify` replays append-only lifecycle history;
the routine snapshot or a validation lease is not sufficient final proof. A
direct single-agent task remains valid without a run file.

## 2. Review the actual change

Inspect the complete diff and map every changed implementation path to:

- a locked requirement id
- one or more locked approved artifacts
- a traceability entry
- an executed test or approved verification

Flag unrelated changes, undocumented behavior, stale generated output, and path
rules that are too broad or too weak. Generic tooling cannot prove semantic API,
schema, security, or infrastructure consistency; run the repository-specific
contract, migration, policy, or deployment checks required by the artifacts.

## 3. Run tests and operational checks

Run focused affected tests first, then the full relevant suite once for the
final integration state. Reuse immutable successful evidence only when its input
hashes, toolchain, command, and environment fingerprint are unchanged. Include lint, type checks,
builds, migration validation, rollback exercises, security checks, or deployment
validation only when relevant to the locked decisions. Record commands and
results accurately.

## 4. Resolve failures by cause

- If code violates approved documents, fix code and tests.
- If the approved design must change, stop code work, revise the artifact through
  `author-project-document`, obtain explicit re-approval, prepare a new lock, and
  re-run verification.
- If a new independent decision boundary is needed, return to
  `discover-document-graph` for a graph amendment.
- If traceability alone is missing, record actual paths; never fabricate evidence.

## 5. Report the gate result

Lead with pass or fail. List requirement coverage, document/hash state, tests and
other checks run, and any remaining risk. Do not claim a clean gate while any
required check is skipped or failing.

## Non-negotiable gates

- Verify against current files, not remembered context.
- No fabricated traceability or test evidence.
- A changed approved document always requires re-approval and a new lock.
- Hook success alone is not final verification; CI is the merge gate.
- An unfinished orchestration run is a failed final gate.
- Environment-unavailable evidence is not a product failure, but a required
  pending external gate still blocks final completion.
