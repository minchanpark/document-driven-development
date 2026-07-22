# Fast-MVP adoption schemas

## Adoption plan

Create `.document-driven/adoption-plan.json` after comparing PRD, design, code,
tests, and MVP evidence. Commit it before running `adopt-baseline`.

```json
{
  "schema_version": "1.0",
  "status": "approved",
  "blocking_gaps": [],
  "known_debt": [
    {
      "id": "DEBT-001",
      "summary": "Hosted conversion smoke test remains",
      "disposition": "accepted",
      "follow_up_requirement_id": "REQ-OPS-003"
    }
  ],
  "approved_by": "user-provided-identity",
  "approved_at": "2026-07-23T10:20:00Z"
}
```

Set `status` to `approved` only after explicit user approval. Adoption is blocked
when `blocking_gaps` is non-empty. Store only non-blocking debt in `known_debt`.

## Adoption baseline

`docflow.py adopt-baseline` creates
`.document-driven/adoption-baseline.json`; agents must not author it manually.

```json
{
  "schema_version": "1.0",
  "mode": "strict",
  "source_mode": "fast-mvp",
  "baseline_commit": "<validated-mvp-git-sha>",
  "baseline_tree": "<git-tree-sha>",
  "adoption_plan": {
    "path": ".document-driven/adoption-plan.json",
    "sha256": "<approved-plan-sha256>"
  },
  "evidence": {
    "path": ".document-driven/mvp-evidence.json",
    "sha256": "<evidence-blob-sha256>"
  },
  "accepted_flows": [
    {
      "id": "upload-and-create-session",
      "requirement_ids": ["REQ-001"],
      "evidence_ids": ["E2E-001"]
    }
  ],
  "known_debt": [],
  "enforcement": {
    "changes_after": "<validated-mvp-git-sha>"
  },
  "approved_by": "user-provided-identity",
  "approved_at": "2026-07-23T10:30:00Z"
}
```

The command reads MVP evidence from the baseline commit blob, binds the approved
plan hash, derives accepted flow-to-evidence links, and records the commit tree.
After creation the entire baseline file is immutable. Required CI and protected
Git history are the remote trust boundary.
## Strict policy

Fast adoption uses:

```json
{
  "schema_version": "1.1",
  "mode": "strict",
  "source_mode": "fast-mvp",
  "adoption_baseline_path": ".document-driven/adoption-baseline.json",
  "manifest_path": "docs/document-manifest.json",
  "require_requirement_ids": true,
  "require_traceability": true,
  "documentation_paths": [],
  "path_rules": []
}
```

Direct Strict omits `adoption_baseline_path` and uses
`source_mode: direct-strict`. Policy 1.0 is normalized to Direct Strict; never
infer that an existing Strict installation came from Fast MVP.
