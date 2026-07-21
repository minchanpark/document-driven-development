# Document Driven Development

A Codex, Claude Code, and Antigravity plugin that turns a PRD into a
project-specific set of approved documents and makes those documents an
implementation prerequisite.

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

## Canonical project files

- `docs/document-manifest.json`: dynamic artifact graph and approval state
- `.document-driven/policy.json`: repository-specific enforcement rules
- `.document-driven/orchestration.json`: mode, review gates, loop limits, and non-secret provider routing
- `.document-driven/context-lock.json`: task, requirement, and document hashes
- `.document-driven/context-pack.json`: compact requirement slices bound to the
  full locked document hashes
- `.document-driven/package-lock.json`: active package ownership in one worktree
- `.document-driven/runs/<task-id>/run.json`: locked plan, packages, review, and integration state
- `.document-driven/traceability.json`: requirement-to-document/code/test links

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

## Fast path without weaker gates

The harness separates integrity from prompt size:

- full approved documents remain SHA-256 locked;
- implementers and reviewers start from generated requirement slices and open
  full documents only for ambiguity or cross-cutting constraints;
- package contracts can carry explicit acceptance criteria before coding;
- identical approval hashes are reused and multi-document approval is atomic;
- read-only shell commands and read tools are not sent through write guards;
- lock and run validation results are reused within one guard invocation;
- trace verification indexes entries once instead of rescanning them per
  requirement.

Use `approve-bundle` with explicit `artifact=sha256` values to record a user
approval batch. Use `context-pack --package <id>` to regenerate or inspect a
package-specific compact context.

## Attribution

The conversational design principles are adapted from Superpowers. The
locked-plan, package decomposition, cross-review, and bounded escalation ideas
are independently adapted from the former model-council build flow. See `NOTICE`
and `LICENSE` for attribution and license terms.
