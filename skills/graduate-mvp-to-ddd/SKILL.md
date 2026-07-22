---
name: graduate-mvp-to-ddd
description: Graduate a validated Fast MVP into Strict document-driven development by comparing PRD, design, code, tests, and evidence; separating blocking gaps from accepted debt; approving the minimum document graph and adoption plan; binding an immutable Git baseline; and installing the Strict harness. Use when the user explicitly asks to adopt, graduate, harden, or promote an evidence-backed MVP into Strict DDD. Never use to downgrade an existing Strict repository.
---

# Graduate MVP to Strict DDD

Treat product code as evidence of decisions, not as approved design. Preserve the
existing Strict approval strength and make the transition fail closed.

## 1. Establish the candidate baseline

Require:

- a Git repository;
- `.document-driven/mvp-evidence.json` committed with the validated MVP;
- explicit user intent to graduate;
- no existing Strict policy, Strict harness, context lock, or adoption baseline.

Read the PRD, existing design documents, source, migrations, tests, configuration,
MVP evidence, and Git history. Identify the exact validated MVP commit. Do not use
an unvalidated newer commit merely because it is HEAD.

## 2. Perform the adoption review

Compare PRD, design, implementation, tests, and executable evidence. Report:

- implemented and verified flows;
- required behavior that is absent or contradicted;
- consequential decisions that exist only in code;
- security, authorization, data, migration, infrastructure, and operations gaps;
- document drift;
- non-blocking Known Debt with follow-up requirement ids.

Separate `blocking_gaps` from `known_debt`. Return to Fast MVP when any blocking
gap remains. Known Debt cannot waive approval, security, test, traceability, or
future verification.

## 3. Approve the adoption plan and document graph

Use the existing dynamic graph discovery rules. Propose the minimum project-
specific document graph; do not impose a fixed architecture checklist. Obtain
explicit user approval for the complete graph.

Read `../../references/adoption-baseline-schema.md`, then create
`.document-driven/adoption-plan.json` with:

- `status: approved` only after explicit approval;
- an empty `blocking_gaps` array;
- every accepted Known Debt item and follow-up requirement id;
- the user-provided approval identity and time.

Author, review, and explicitly approve every active artifact through the existing
document workflow. Existing DESIGN files and implemented behavior are not
automatically approved. Record approval hashes exactly as Strict DDD requires.

## 4. Freeze the transition input

Commit the approved manifest, documents, approval hashes, and adoption plan when
the user authorized commits. Do not change product implementation after the
validated MVP commit. If implementation changed, re-run Fast-MVP verification
and select a newer validated baseline.

Require a clean working tree before adoption. Resolve the plugin root from this
skill and run:

```text
python3 <plugin-root>/scripts/docflow.py adopt-baseline --root <repo> \
  --baseline-commit <validated-mvp-commit> --approved-by <user-identity>
```

Never replace an existing baseline or choose an approval identity for the user.

## 5. Activate Strict DDD immediately

After baseline creation, install the harness without returning to Fast MVP:

```text
python3 <plugin-root>/scripts/install_harness.py --root <repo> --ci <auto|github|none>
```

If installation fails, treat the baseline as an adoption-in-progress fail-closed
marker. Do not delete or rewrite it to resume Fast MVP; repair the installation
and retry.

Apply the approved project-specific `path_rules`, then run:

```text
python3 .document-driven/bin/docflow.py validate --root <repo>
python3 .document-driven/bin/docflow.py check-baseline --root <repo>
python3 .document-driven/bin/docflow.py guard-edit --root <repo> --path <implementation-path>
python3 .document-driven/bin/docflow.py guard-edit --root <repo> --path <document-path>
```

The baseline check must pass, an implementation path must fail before a Task
Lock, and a document path must pass. Confirm policy `mode=strict`,
`source_mode=fast-mvp`, and the canonical baseline path.

## 6. Handoff

Commit the activation state only when authorized. Report the immutable baseline
commit and tree, accepted flows, plan/evidence hashes, approved documents, Known
Debt, CI status, and any remaining operational gate. Continue future product
changes only through `prepare-documented-change`.

## Non-negotiable gates

- Do not adopt with a Blocking Gap.
- Do not infer approval from code, existing drafts, or silence.
- Do not register baseline before relevant documents are approved.
- Do not modify, move, or replace a created baseline.
- Do not downgrade Strict DDD to Fast MVP.
- Treat required CI and protected history as the remote trust boundary.
