# MVP evidence schema

Use `.document-driven/mvp-evidence.json` only for Fast-MVP validation evidence.
Commit it with the validated product code and tests. Do not include the containing
commit SHA because that creates a self-reference.

```json
{
  "schema_version": "1.0",
  "mode": "fast-mvp",
  "stage": "connected",
  "accepted_flows": [
    {
      "id": "upload-and-create-session",
      "requirement_ids": ["REQ-001"],
      "status": "passed"
    }
  ],
  "verification": [
    {
      "id": "E2E-001",
      "flow_ids": ["upload-and-create-session"],
      "type": "browser-e2e",
      "command": "npm run test:e2e -- upload-session",
      "result": "passed",
      "exit_code": 0,
      "executed_at": "2026-07-23T10:00:00Z",
      "evidence_paths": []
    }
  ],
  "validated_by": "user-provided-identity",
  "validated_at": "2026-07-23T10:10:00Z"
}
```

Rules:

- Allow `stage` values `demo-ready`, `connected`, and `pilot-ready`.
- Require unique safe ids for flows and verification records.
- Require every accepted flow to carry one or more PRD requirement ids.
- Require every accepted flow to be linked from at least one passed verification.
- Use `result` values `passed`, `failed`, or `unavailable`.
- Require exact commands and exit code 0 for passed automated evidence.
- Use `type: user-attestation` for a user-only check; do not invent a command.
- Keep failed and unavailable records when they materially affect the stage claim.
- Use repository-relative `evidence_paths`; do not store secrets or raw credentials.
- Obtain `validated_by` from the user and record the real validation time.
