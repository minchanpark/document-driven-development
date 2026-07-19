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
2. `author-project-document` drafts and approves one selected artifact at a time.
3. `generate-development-harness` installs repository instructions, hooks,
   deterministic validators, context locks, traceability, and optional GitHub CI.
4. `prepare-documented-change` selects approved relevant artifacts and hashes
   them into a task-specific context lock.
5. `implement-from-documents` implements only from that valid locked context.
6. `verify-document-driven-change` checks approval, drift, traceability, and tests.

## Canonical project files

- `docs/document-manifest.json`: dynamic artifact graph and approval state
- `.document-driven/policy.json`: repository-specific enforcement rules
- `.document-driven/context-lock.json`: task, requirement, and document hashes
- `.document-driven/traceability.json`: requirement-to-document/code/test links

Run `python3 scripts/docflow.py --help` for deterministic commands. The plugin
contains native plugin manifests and hook adapters for all three platforms. The
skills and policy engine are shared; hook configuration and payload handling are
platform-specific:

- Codex: `hooks/hooks.json` and `.codex/hooks.json`
- Claude Code: `hooks/claude-hooks.json` and `.claude/settings.json`
- Antigravity: `plugin.json`, `hooks.json`, and `.agents/hooks.json`

## Attribution

The conversational design principles are adapted from Superpowers. See
`NOTICE` and `LICENSE` for attribution and license terms.
