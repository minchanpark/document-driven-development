<!-- document-driven-development:start -->
## Document-driven development gate

`docs/document-manifest.json` is the index of the project's approved design
sources. The artifact list is project-specific; do not invent a fixed document
checklist.

Before changing implementation code, tests, migrations, infrastructure, or
generated contracts:

1. Read the PRD and the relevant artifacts selected from the manifest.
2. Require each selected artifact and its dependencies to be `approved`.
3. Prepare the task with `.document-driven/bin/docflow.py prepare`, including a
   task id, at least one PRD requirement id, and relevant scope or artifact ids.
4. Re-run `check-lock` immediately before implementation.

If implementation reveals a design decision not covered by the approved
documents, stop implementation. Propose a new artifact or revision, obtain
explicit user approval, record the approval hash, and prepare a new lock.
Never infer approval from silence or from the existence of a draft.

Before declaring completion, update traceability from each locked requirement to
the approved documents, code paths, and test paths, then run `docflow.py verify`.
A passing hook is not proof of correctness; run the project's tests and CI gates.
<!-- document-driven-development:end -->
