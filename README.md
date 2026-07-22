# Document Driven Development

A Codex, Claude Code, and Antigravity plugin with two explicit entry lanes:
build and validate a vertical Fast MVP, then optionally graduate it through an
immutable baseline; or start directly with a project-specific approved document
graph. Both routes converge on the same Strict implementation gates.

The plugin does **not** prescribe `ERD.md`, `DATABASE.md`, or any other fixed
artifact list. It prescribes a decision process. The user and agent agree on the
minimum useful document graph, then the repository records only the selected
artifacts, their relationships, state, approval, and implementation relevance.

## Installation

### Codex

```bash
codex plugin marketplace add minchanpark/document-driven-development --ref main
codex plugin add document-driven-development@minchanpark-plugins
```

Start a new Codex task after installation so the bundled skills and hooks are
loaded from the installed plugin cache.

### Claude Code

```bash
claude plugin marketplace add minchanpark/document-driven-development@main
claude plugin install document-driven-development@minchanpark-plugins --scope user
```

Run `/reload-plugins` in an active Claude Code session, or start a new session.

## Workflow

### Fast MVP, then Strict adoption

1. `build-mvp-from-prd` runs only after an explicit Fast choice, implements one
   critical journey through real boundaries, and records flow-linked evidence.
2. `graduate-mvp-to-ddd` compares PRD, design, code, tests, and evidence;
   separates blocking gaps from Known Debt; approves the minimum document graph;
   and binds the approved adoption plan.
3. `docflow.py adopt-baseline` records the validated MVP commit/tree and hashes
   the committed evidence and approved adoption plan.
4. `generate-development-harness` immediately activates Strict DDD. The baseline
   is immutable, Strict cannot downgrade to Fast, and only changes after the
   baseline require traceability.

### Direct Strict

1. `discover-document-graph` reads the PRD and repository, interviews the user,
   compares approaches, and proposes a minimal artifact graph.
2. `author-project-document` drafts and reviews one selected artifact at a
   time, then records one or more explicitly approved hashes atomically.
3. `generate-development-harness` installs repository instructions, hooks,
   deterministic validators, context locks, traceability, and optional GitHub CI.
4. `prepare-documented-change` selects approved relevant artifacts and hashes
   them into a task-specific context lock.
5. `setup-development-providers` optionally configures host-native agents and
   external Codex, Claude Code, or Antigravity CLIs without storing credentials.
6. `orchestrate-documented-change` challenges the locked plan, decomposes complex
   work into non-overlapping packages, and runs isolated implementation,
   independent review, bounded fixes, and green integration. Small changes still
   go directly to `implement-from-documents`.
7. `implement-from-documents` implements only from the valid Task Lock and, when
   present, the narrower Package Lock.
8. `verify-document-driven-change` checks approval, run completion, drift,
   traceability, and tests.

Fast MVP is deliberately independent of Strict document approval and Task Locks.
It is also unavailable once a Strict policy, harness, context lock, or adoption
baseline exists.

## Canonical project files

- `docs/document-manifest.json`: dynamic artifact graph and approval state
- `.document-driven/mvp-evidence.json`: Fast-MVP flow and verification evidence
- `.document-driven/adoption-plan.json`: explicitly approved graduation plan
- `.document-driven/adoption-baseline.json`: immutable Fast-to-Strict boundary
- `.document-driven/policy.json`: repository-specific enforcement rules
- `.document-driven/orchestration.json`: mode, review gates, loop limits, and non-secret provider routing
- `.document-driven/context-lock.json`: task, requirement, and document hashes
- `.document-driven/context-pack.json`: compact requirement slices bound to the
  full locked document hashes
- `.document-driven/package-lock.json`: active package ownership in one worktree
- `.document-driven/runs/<task-id>/run.json`: bounded current run/package snapshot
- `.document-driven/runs/<task-id>/events.jsonl`: append-only lifecycle audit log
- `.document-driven/trace/<task-id>/<requirement-id>.json`: sharded requirement links
- `.document-driven/traceability.json`: legacy-compatible trace index and fallback
- `.document-driven/evidence/`: deterministic reusable verification evidence
- `.document-driven/worktrees.json`: active isolated-worktree lifecycle registry

Run `python3 scripts/docflow.py --help` for deterministic commands. The plugin
contains native plugin manifests and hook adapters for all three platforms. The
skills and policy engine are shared; hook configuration and payload handling are
platform-specific:

- Codex: `hooks/hooks.json` and `.codex/hooks.json`
- Claude Code: `hooks/claude-hooks.json` and `.claude/settings.json`
- Antigravity: `plugin.json`, `hooks.json`, and `.agents/hooks.json`

Optional external provider adapters are available through
`scripts/development-provider-runner.mjs`. They are not a dependency on
`model-council`; host-native agents are the default and the DDD workflow remains
fully functional without any external CLI.

## Strict performance optimizations without weaker gates

The harness separates integrity from prompt size:

- full approved documents remain SHA-256 locked;
- implementers and reviewers start from generated requirement slices and open
  full documents only for ambiguity or cross-cutting constraints;
- package contracts can carry explicit acceptance criteria before coding;
- identical approval hashes are reused and multi-document approval is atomic;
- read-only shell commands and read tools are not sent through write guards;
- lock and run validation results are reused within one guard invocation;
- a short persistent validation lease avoids re-hashing immutable documents on
  adjacent code-only writes;
- run events append without growing `run.json`, while final verification replays
  the complete audit log;
- one trace update writes only its requirement shard;
- structured verification gates distinguish unavailable environments from
  product failures and reuse identical input/environment evidence;
- completed runs safely garbage collect clean integrated Git worktrees.
- a compact Ponytail-derived minimum-correct policy prefers existing code and
  primitives without adding a second always-on hook; its provider text has a
  512-byte budget and cannot weaken locked obligations.

Use `approve-bundle` with explicit `artifact=sha256` values to record a user
approval batch. Use `context-pack --package <id>` to regenerate or inspect a
package-specific compact context.

Use `preflight` and `verify-package` for structured evidence,
`check-run --audit` to replay a run, `trace-export` for legacy trace consumers,
and `worktree-gc` to inspect cleanup eligibility. See
[`docs/PERFORMANCE_ARCHITECTURE.md`](docs/PERFORMANCE_ARCHITECTURE.md) for the
complexity model, invalidation rules, compatibility behavior, and safety gates.

## Attribution

The conversational design principles are adapted from Superpowers. The
locked-plan, package decomposition, cross-review, and bounded escalation ideas
are independently adapted from the former model-council build flow. The compact
minimum-correct implementation ladder is adapted from Ponytail without a runtime
dependency. See `NOTICE` and `LICENSE` for attribution and license terms.
