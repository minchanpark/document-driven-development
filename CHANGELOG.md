# Changelog

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
