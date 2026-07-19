---
name: setup-development-providers
description: Configure optional host-native and external Codex, Claude Code, or Antigravity providers for document-driven architecture review, package implementation, and independent review without storing credentials. Use after the harness exists and before orchestrate-documented-change when the user wants multi-agent or multi-provider development. Do not use model-council as a runtime dependency.
---

# Setup Development Providers

Configure execution capability; do not start implementation in this skill.

## 1. Inspect the host and repository

Read `.document-driven/orchestration.json`, the valid context lock if one exists,
and the repository's existing agent configuration. Treat the current agent as the
Main Orchestrator. Prefer host-native subagents because they inherit the current
repository context and permission boundary.

External CLIs are optional diversity or capacity providers. They never replace
the repository harness and do not require `model-council` to be installed.

## 2. Probe installed providers

Resolve the plugin root from this skill, then run:

```text
node <plugin-root>/scripts/development-provider-runner.mjs probe --provider all
node <plugin-root>/scripts/development-provider-runner.mjs route --host-vendor <openai|anthropic|google|unknown>
```

By default, exclude an external CLI from the same vendor as the host while
keeping host-native agents enabled. Allow same-vendor external execution only
when the user wants it. Do not claim Antigravity is mechanically read-only; its
read-only mode is prompt-enforced.

## 3. Agree on role routing

Propose the minimum useful routing for these roles:

- `architect`: read-only attack on the locked implementation plan
- `coder`: package-scoped workspace write only
- `reviewer`: read-only independent diff and verification review

Discuss cost, latency, installed authentication, and provider diversity. Do not
hard-code model ids that were not successfully probed or explicitly chosen.
Never copy API keys, tokens, or credentials into repository files.

## 4. Save non-secret configuration

Update only the `providers` array and user-approved policy fields in
`.document-driven/orchestration.json`. A provider entry should identify its id,
vendor, roles, tier, optional verified model id, and whether it is enabled.

```json
{
  "id": "claude-code-cli",
  "vendor": "anthropic",
  "roles": ["architect", "reviewer"],
  "tier": "deep",
  "enabled": true
}
```

Authentication remains in each provider's normal user-level store. Environment
overrides, when needed, are `DOCUMENT_DRIVEN_CODEX_BIN`,
`DOCUMENT_DRIVEN_CLAUDE_BIN`, and `DOCUMENT_DRIVEN_ANTIGRAVITY_BIN`.

## 5. Verify safely

Run the provider self-test and dry-run every selected role. Do not make a live
workspace-write call during setup. A future coder call is allowed only in an
isolated worktree containing a valid `.document-driven/package-lock.json`.

```text
node <plugin-root>/scripts/provider-self-test.mjs
```

Report installed and unavailable providers, actual routing, read-only caveats,
and which choices still require the user.

## Non-negotiable gates

- No credentials in repository configuration.
- Architect and reviewer are always read-only.
- Only coder may request workspace-write.
- Workspace-write requires a Package Lock and isolated ownership boundary.
- The DDD plugin remains fully functional without any external CLI.
