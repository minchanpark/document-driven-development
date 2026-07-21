---
name: implement-from-documents
description: Implement product code, tests, migrations, infrastructure, or generated contracts only from a valid document context lock, maintaining requirement-to-document/code/test traceability. Use for any repository change after prepare-documented-change has selected and hashed approved relevant documents. Do not use when the lock is absent, stale, or incomplete.
---

# Implement From Documents

Implement only inside the prepared decision boundary. A valid lock is necessary,
but it does not replace reading and understanding the locked files.

## 1. Recheck immediately

Run:

```text
python3 .document-driven/bin/docflow.py check-lock --root <repo>
python3 .document-driven/bin/docflow.py check-context-pack --root <repo>
```

Read `.document-driven/context-lock.json` and the referenced
`.document-driven/context-pack.json`. Start from its requirement slices. Open
a full locked document only when a cited slice is insufficient or a
cross-cutting constraint applies. If the lock is missing or invalid, stop and
use `prepare-documented-change`.

If `.document-driven/package-lock.json` exists, this is a package worker run.
Read it completely, verify the package is `implementing`, and edit only its
`allowed_paths`. Read its `context_pack`, satisfy every
`acceptance_criteria`, and run every declared `verification_command`. If an unfinished
orchestrated run exists without an active package, do not write implementation
files; return to `orchestrate-documented-change`.

When `verification_specs` are present, preflight prerequisites once and execute
the affected gates through `verify-package`. Do not repeatedly run an unchanged
unavailable external gate or resend a successful log. A reusable pass must come
from the harness fingerprint, not memory.

```text
python3 .document-driven/bin/docflow.py check-package-lock --root <repo>
```

For a package worker, validate its narrower context directly when diagnosing
context drift:

```text
python3 .document-driven/bin/docflow.py check-context-pack --root <repo> \
  --package <package-id>
```

## 2. Map work to requirements

Restate the implementation plan as small steps. Each step must cite at least one
locked requirement id and the artifact constraints it satisfies. Avoid unrelated
refactoring. Follow the repository's existing conventions unless an approved
artifact explicitly changes them.

## 3. Use test-first implementation

For behavior changes, write or update a test that fails for the intended reason,
make the smallest implementation change that passes it, then refactor while tests
remain green. For changes where a conventional automated test is not applicable,
define the approved verification before editing and record its artifact or test
path in traceability.

Re-run `check-lock` after any document, manifest, policy, run-contract, or lock
operation. Rely on the write guard for unchanged code-only steps; do not
re-hash the same immutable context between adjacent edits.

## 4. Stop on design drift

Stop implementation when you discover any of the following:

- a new architectural choice not owned by a locked artifact
- a contradiction between code reality and an approved document
- a required data, API, security, deployment, migration, or operational decision
  that is unresolved
- a change that would violate a locked constraint

Do not silently make the decision in code. Use `author-project-document` to
revise the existing artifact or propose a graph change. Obtain explicit approval,
then create a new context lock before resuming.

## 5. Maintain traceability

For every locked requirement, record actual code and test paths:

```text
python3 .document-driven/bin/docflow.py trace --root <repo> \
  --requirement <id> --code <path> --test <path>
```

Paths must exist. Do not list planned files as if implemented. A requirement may
cite several code and test paths by repeating the flags.

## 6. Verify before completion

Run focused tests, then the full relevant suite. Finish with
`verify-document-driven-change`. Do not claim completion from passing unit tests
alone when the approved artifacts require migration, security, performance,
deployment, or operational verification.

## Non-negotiable gates

- No valid lock, no implementation.
- Read the hash-bound requirement pack first and open authoritative full
  documents whenever the slices are insufficient.
- Code never silently outranks an approved document.
- Design changes require document revision, explicit re-approval, and a new lock.
- Every locked requirement links to actual code and tests or approved verification.
- An active Package Lock narrows the Task Lock; it never broadens it.
