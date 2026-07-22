# Document-driven development

The plugin has two explicit entry lanes:

- Use `build-mvp-from-prd` only when the user explicitly chooses Fast MVP and no
  Strict policy, harness, context lock, or adoption baseline exists.
- Use the approved document graph and harness for Direct Strict entry.

Fast MVP may graduate to Strict DDD through `graduate-mvp-to-ddd`. Never
downgrade Strict DDD to Fast MVP. When an adoption baseline exists, treat it as
immutable and fail closed until Strict activation is valid.

When a repository contains `docs/document-manifest.json`, treat the approved document graph as the implementation contract.

- Do not edit implementation files until the relevant documents are approved and a valid task context lock exists.
- Start from the hash-bound context pack and open authoritative full documents when its requirement slices are insufficient.
- For complex work, keep one Main Orchestrator, approve the locked plan, activate one non-overlapping Package Lock per isolated worker, and require an independent reviewer.
- Import only the reviewed package result into central run state; use an integration lock before merging its code.
- If implementation requires a design change, update and re-approve the document or ADR before changing code.
- Keep requirement-to-code-to-test traceability current and run the repository verification gate before declaring completion.
- Use the plugin skills to discover the document graph, author artifacts, prepare a documented change, and verify the result.
