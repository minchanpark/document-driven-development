<!-- document-driven-development:start -->
## Document-driven development gate

`docs/document-manifest.json` is the index of the project's approved design
sources. The artifact list is project-specific; do not invent a fixed document
checklist.

Run `docflow.py check-baseline` before implementation. A Fast-MVP adoption
baseline is immutable: do not edit, delete, move, or replace it. Strict DDD may
not be downgraded to Fast MVP. Implementation at or before an accepted baseline
is exempt from retroactive traceability; every implementation change after it
remains subject to the normal Strict gates.

Before changing implementation code, tests, migrations, infrastructure, or
generated contracts:

1. Read the PRD and the relevant artifacts selected from the manifest.
2. Require each selected artifact and its dependencies to be `approved`.
3. Prepare the task with `.document-driven/bin/docflow.py prepare`, including a
   task id, at least one PRD requirement id, and relevant scope or artifact ids.
4. Re-run `check-lock` immediately before implementation.
5. Read the generated context pack first. Open full locked documents when a
   cited slice is ambiguous or a cross-cutting constraint applies.

Choose the execution mode after the lock is valid:

- Small, low-risk work may use `implement-from-documents` directly.
- Work spanning multiple ownership boundaries, data, authorization, migration,
  or infrastructure should use `orchestrate-documented-change`.
- During an orchestrated run, implementation writes require an active Package
  Lock and must stay inside its `allowed_paths`. The Main Orchestrator owns the
  plan, package boundaries, cross-review, integration, and final gate.
- Isolated worker run files are snapshots. Import only the reviewed package
  result into the central run, then activate an integration lock before merging
  that package's code.

Apply the minimum-correct implementation policy after understanding the affected
flow: add no code when locked behavior already exists; otherwise reuse repository
code, then the standard library or native platform, then an already-installed
dependency, and only then write the smallest correct diff. Do not add speculative
abstractions, dependencies, configuration, or files. This policy never permits
omitting locked requirements, validation, security, accessibility, error handling,
tests, traceability, or evidence; any conflict returns to document approval.

If implementation reveals a design decision not covered by the approved
documents, stop implementation. Propose a new artifact or revision, obtain
explicit user approval, record the approval hash, and prepare a new lock.
Never infer approval from silence or from the existence of a draft.

Before declaring completion, update traceability from each locked requirement to
the approved documents, code paths, and test paths, then run `docflow.py verify`.
A run is complete only after every package passes independent review and is
integrated. A package reviewer must not be the package implementer.
A passing hook is not proof of correctness; run the project's tests and CI gates.
<!-- document-driven-development:end -->
