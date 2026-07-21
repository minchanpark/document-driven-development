---
name: author-project-document
description: Draft, review, revise, and explicitly approve one project-specific artifact already listed in docs/document-manifest.json. Use when a dynamic document graph exists and the user wants to create or revise architecture, data, API, security, infrastructure, operations, testing, ADR, or any other selected document. Do not use to invent a fixed document suite or implement product code.
---

# Author Project Document

Author exactly one manifest artifact through dialogue. The artifact's purpose,
not its filename, determines the questions and structure.

## 1. Select and verify the artifact

Read the PRD, the full manifest, and every `informed_by` and `depends_on` source.
Select one `proposed`, `drafting`, or explicitly reopened artifact. Explain its
decision boundary and what it must not duplicate from neighboring artifacts.

Dependencies should normally be approved first. If writing this artifact would
force an unapproved dependency decision, stop and author the dependency.

For a new artifact, move `proposed → drafting` with:

```text
python3 <plugin-root>/scripts/docflow.py set-status --root <repo> \
  --artifact <id> --to drafting
```

For an approved artifact that needs revision, first move `approved → drafting`.
This removes the old approval hash and invalidates existing context locks.

## 2. Interview for this decision boundary

Ask exactly one question per message. Start with gaps that would materially
change the design. Do not repeat questions already settled by the PRD or approved
dependencies. For consequential decisions, present 2-3 approaches with a clear
recommendation and trade-offs.

Adapt the questions to the artifact. A data artifact may need ownership,
retention, consistency, migration, and rollback decisions; an on-premises
deployment artifact may need network zones, installation, secrets, backup, and
upgrade decisions. These are examples, not mandatory sections.

## 3. Validate sections incrementally

Propose a short outline, then present the content in sections scaled to its
complexity. Ask for confirmation after each meaningful section. Include only
material necessary to implement, test, operate, or approve the decisions owned
by this artifact.

Use stable requirement or decision ids when the project needs traceability.
Clearly label unresolved questions. An artifact with unresolved implementation-
blocking questions cannot be approved.

## 4. Write and self-review one file

Write only the selected artifact and the manifest state needed for it. Preserve
the repository's document style. Then check:

- no accidental TODO, TBD, placeholder, or ambiguous normative language
- no contradiction with the PRD or approved artifacts
- responsibilities and boundaries are testable
- failure, security, migration, rollback, and operational consequences are
  covered when relevant
- `required_for`, `depends_on`, and `informed_by` still reflect reality

If a new independent decision boundary is discovered, do not silently add a
document. Propose a graph change, explain merge/split options, and obtain explicit
user approval before editing the manifest.

## 5. Review and approval gate

Move `drafting → reviewed`, show the written file path, and ask the user to review
it. If they request changes, return it to `drafting`, revise, and review again.

Only after an explicit approval message, record the content hash:

```text
python3 <plugin-root>/scripts/docflow.py approve --root <repo> \
  --artifact <id> --approved-by <user-provided-identity>
```

Never choose an approval identity without the user's wording. Never treat a
positive reaction to an outline as approval of the written file.

When the user explicitly approves several reviewed artifacts and names their
hashes, record them atomically instead of repeating one command per artifact:

```text
python3 <plugin-root>/scripts/docflow.py approve-bundle --root <repo> \
  --approved-by <user-provided-identity> \
  --approval <artifact-id>=<sha256> \
  --approval <artifact-id>=<sha256>
```

The bundle may include dependencies together. Reuse an already approved
identical hash without rewriting the manifest. Reject the entire bundle when any
hash, state, identity, or dependency is invalid.

## 6. Continue in graph order

Report the artifact state and the next unapproved artifact whose dependencies
are ready. Continue with this skill for that artifact. After every active
artifact is approved, hand off to `generate-development-harness`.

## Non-negotiable gates

- Exactly one artifact per authoring and review cycle; explicit approvals may be
  recorded as one atomic multi-artifact bundle.
- One question at a time.
- No product code, migration, infrastructure change, or implementation scaffold.
- Explicit review of the written artifact before approval.
- Any content change after approval requires re-review and a new hash.
