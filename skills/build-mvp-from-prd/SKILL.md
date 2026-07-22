---
name: build-mvp-from-prd
description: Build and validate a Fast MVP from a PRD by selecting one critical user journey, implementing it vertically through real dependencies, distinguishing demo-ready from connected or pilot-ready, and recording flow-linked evidence. Use only when the user explicitly chooses rapid product validation instead of Direct Strict DDD. Refuse in repositories with a Strict policy, installed Strict harness, context lock, or adoption baseline.
---

# Build MVP from PRD

Build the smallest real product slice that can test the product direction. Do not
imitate Strict document approval or Task Locks during this lane.

## 1. Confirm the lane

Require an explicit Fast-MVP choice. Inspect these paths before implementation:

- `.document-driven/policy.json`
- `.document-driven/adoption-baseline.json`
- `.document-driven/context-lock.json`
- `.document-driven/bin/docflow.py`
- `docs/document-manifest.json`

Refuse Fast-MVP execution when a Strict policy or harness, an adoption baseline,
or a valid Strict context lock exists. Never downgrade Strict DDD to Fast MVP.

## 2. Select one vertical journey

Read the PRD completely, then inspect existing design documents, repository
instructions, source layout, tests, runtime configuration, and recent history.
Select the smallest critical user journey that can falsify the product direction.
State its PRD requirement ids, actor, entry point, expected outcome, and required
real dependencies.

Prefer one end-to-end journey over several disconnected screens or stubs. Ask the
user only when materially different journeys would test different product bets.

## 3. Implement the smallest connected slice

Implement the journey through its actual boundaries: UI or API input, business
logic, authentication when required, persistence, backend or external service,
and observable result. Reuse repository primitives and installed dependencies.
Do not add speculative architecture.

Classify the current stage accurately:

- `demo-ready`: the journey can be demonstrated but still uses a material stub,
  fixture, fake identity, or non-persistent boundary.
- `connected`: every material boundary required by the accepted journey is real
  in the target development or test environment.
- `pilot-ready`: connected behavior plus the operational, security, recovery,
  and user-validation checks required for a limited pilot.

Do not label a stubbed demo as connected.

## 4. Verify the journey

Run the narrowest meaningful checks first, then the complete affected suite.
Prefer browser E2E for browser journeys, API E2E for service journeys, and an
equivalent executable scenario for CLI or worker products. Record unavailable
external environments honestly. A required unavailable gate does not pass.

Obtain explicit user validation for the accepted journey and identity string.
Never invent `validated_by` or treat silence as approval.

## 5. Record evidence

Read `../../references/mvp-evidence-schema.md`, then write
`.document-driven/mvp-evidence.json`. Bind every accepted flow to at least one
passed verification through `flow_ids`. Record exact commands, exit codes,
timestamps, and evidence paths. Do not put the containing Git commit SHA in this
file.

Run:

```text
python3 <plugin-root>/scripts/docflow.py check-mvp-evidence --root <repo>
```

Validate the JSON shape and confirm every referenced requirement occurs in the
PRD or existing design source. If the user authorized a validation commit, commit
the product code, tests, and evidence together. That commit becomes the candidate
MVP baseline.

## 6. Report

Lead with the achieved stage and accepted flow. List commands and results,
remaining product risk, unavailable environments, and the candidate validation
commit when one exists. If the product direction is accepted and the user wants
Strict governance, hand off to `graduate-mvp-to-ddd`.

## Non-negotiable gates

- Require an explicit Fast-MVP choice.
- Never run in Strict or adoption-in-progress repositories.
- Do not fabricate user validation or evidence.
- Do not call a stubbed flow connected.
- Keep failed and unavailable checks visible.
- Do not weaken security or destructive-action safeguards for speed.
