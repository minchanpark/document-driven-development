# Changelog

## 0.5.0 - 2026-07-23

- Added an explicit Fast-MVP lane that implements and validates one critical
  vertical journey without imitating Strict document or Task Lock gates.
- Added `graduate-mvp-to-ddd`, approved adoption plans, immutable Git baselines,
  baseline-blob evidence hashing, and one-way Fast-to-Strict activation.
- Added policy schema 1.1 with backward-compatible 1.0 normalization and explicit
  `direct-strict` versus `fast-mvp` provenance.
- Made prepare, lock, write guard, session context, validation leases, final
  verification, and CI baseline-aware while preserving pre-baseline code from
  retroactive traceability.
- Added ancestry-based effective CI ranges, first-adoption bootstrap handling,
  baseline tamper detection, and managed GitHub workflow upgrades.
- Preserved package locks, append-only run history, evidence reuse, provider
  routing, and worktree lifecycle behavior from 0.4.x.

## 0.4.1 - 2026-07-22

- Adapted Ponytail's core minimum-correct implementation ladder into the
  existing harness, implementation, orchestration, review, and provider paths.
- Kept the full Ponytail ruleset and lifecycle hooks out of the plugin to avoid
  duplicate session injection and reasoning-token regressions.
- Added a stable provider prompt-policy id and a regression-enforced 512-byte
  policy budget.
- Made approved requirements, validation, security, accessibility, tests,
  traceability, and evidence explicitly outrank implementation minimalism.

## 0.4.0 - 2026-07-22

- Replaced growing inline run histories with bounded snapshots and append-only
  event logs, including full lifecycle replay at approval and final audit gates.
- Sharded traceability by task and requirement with a legacy-compatible export.
- Added persistent short-lived validation leases for adjacent code-only edits,
  with document/state invalidation and authoritative final hash verification.
- Added structured verification preflight, external-environment states,
  impact/input/environment fingerprints, deterministic evidence reuse, manual
  attestation, and release-phase blocking.
- Added completed-run supersession provenance and cross-run evidence reuse.
- Added registered Git worktree lifecycle tracking and conservative automatic
  cleanup for clean, integrated, reachable or content-equivalent worktrees.
- Updated provider prompts and skills to start from compact context packs and
  avoid resending unchanged full documents or successful logs.

## 0.3.0 - 2026-07-22

- Added atomic, hash-explicit multi-artifact approval with identical-hash reuse.
- Added task and package context packs containing compact requirement slices
  bound to authoritative full-document hashes, plus deterministic pack
  integrity checks and package-lock hashes.
- Added package acceptance criteria to implementation and integration locks.
- Removed write-guard false positives for read-only `sed`, `/dev/null`
  redirection, and read tools carrying path fields while preserving mutation
  checks. Codex command hooks now honor an explicit tool `workdir`.
- Reused validated lock state inside one guard invocation and indexed
  traceability verification to avoid repeated full scans.
- Updated authoring, preparation, implementation, orchestration, verification,
  and harness skills for token-efficient progressive disclosure.

## 0.2.0 — 2026-07-19

- Added one Main Orchestrator for complex documented changes.
- Added locked-plan approval, deterministic work packages, Task and Package
  Locks, non-overlapping path ownership, independent cross-review, bounded fix
  and escalation loops, and a green integration gate.
- Added canonical central run state with isolated worker result import so parallel
  worktrees cannot overwrite one another.
- Added an integration-phase Package Lock for merges and cherry-picks.
- Added optional host-native and external Codex, Claude Code, and Antigravity
  provider routing without a runtime dependency on model-council.
- Preserved the direct single-agent path for small, low-risk changes.
- Added model-council and NewDawn MIT attribution for the independently adapted
  build orchestration concepts.
