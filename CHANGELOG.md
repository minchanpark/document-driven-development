# Changelog

## 0.3.0  2026-07-22

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
