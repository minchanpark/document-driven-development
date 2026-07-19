---
name: generate-development-harness
description: Install repository-local document-driven development rules, platform-specific hooks, content-hash locks, traceability files, path policies, and an optional CI gate after the project's dynamic document graph is approved. Use when all active artifacts in docs/document-manifest.json are approved and the user wants Codex, Claude Code, or Antigravity to be forced to consult them during development.
---

# Generate Development Harness

Install a self-contained harness without replacing existing agent instructions or
hook settings. The manifest must already exist and every active artifact must be
approved.

## 1. Inspect enforcement context

Read the manifest and repository structure. Identify implementation, test,
migration, generated-contract, infrastructure, and documentation paths. Detect
the CI platform but do not assume GitHub when another platform is in use.

Ask one question at a time for material choices that cannot be discovered, such
as the CI target or whether generated files are protected. Explain that local
hooks are guardrails: users can disable them and an agent may not surface every
write tool. CI is the final mechanical gate.

## 2. Propose dynamic path rules

Propose `path_rules` based on the actual repository and approved artifacts. A
rule maps path patterns to artifact ids that must be present in the current lock.
Do not copy a generic database or API rule when that concern is absent.

Example shape only:

```json
{
  "patterns": ["project-specific/path/**"],
  "requires_artifacts": ["project-specific-artifact-id"]
}
```

Get user confirmation for the mapping when multiple reasonable boundaries exist.

## 3. Install safely

Resolve the plugin root from this skill's location and run:

```text
python3 <plugin-root>/scripts/install_harness.py --root <repo> --ci <auto|github|none>
```

The installer must preserve unrelated content and merge managed rules into:

- `AGENTS.md` and `CLAUDE.md`
- `.codex/hooks.json` for Codex
- `.claude/settings.json` for Claude Code
- `.agents/hooks.json` for Antigravity
- `.document-driven/bin/`
- `.document-driven/policy.json`
- `.document-driven/traceability.json`
- optional `.github/workflows/document-driven-development.yml`

Edit only `.document-driven/policy.json` after installation to apply the approved
project-specific `path_rules`. Preserve existing rules on reinstallation.

## 4. Verify the harness

Run and report all of these checks:

```text
python3 .document-driven/bin/docflow.py validate --root <repo>
python3 .document-driven/bin/docflow.py guard-edit --root <repo> --path <one implementation path>
python3 .document-driven/bin/docflow.py guard-edit --root <repo> --path <one document path>
```

Before a task lock exists, the implementation path must fail and the document
path must pass. Test each platform adapter with a representative native payload:

- Codex: snake-case `PreToolUse` input and `Bash|apply_patch|Edit|Write`
- Claude Code: snake-case `PreToolUse` input and `Bash|Edit|Write`
- Antigravity: camel-case `toolCall` input and
  `run_command|write_to_file|replace_file_content|multi_replace_file_content`

Also verify that Codex and Claude Code receive `SessionStart` context while
Antigravity receives equivalent ephemeral context from `PreInvocation`.

If CI is not GitHub, provide the exact portable gate command for the user's CI:

```text
python3 .document-driven/bin/docflow.py verify --root . --ci --base-ref <base-commit-or-ref>
```

## 5. Handoff

Explain which layers are advisory and which are mechanical. The next allowed
workflow for implementation is `prepare-documented-change`, never direct editing.

## Non-negotiable gates

- Do not install before every active artifact is approved.
- Do not overwrite existing agent instructions or hook configuration.
- Do not claim hooks are unbypassable.
- Do not create fixed document names or fixed path rules.
- Verify blocked and allowed paths after installation.
