# Development provider runtime

The provider runner normalizes three optional external CLIs behind one envelope:

```text
probe -> route -> run -> continue
```

Roles and permissions are intentionally asymmetric:

| Role | Access | Purpose |
|---|---|---|
| architect | read-only | challenge the locked plan within approved documents |
| coder | workspace-write | implement exactly one activated package |
| reviewer | read-only | independently inspect the diff and run verification |

`workspace-write` is rejected for every role except `coder`. It also requires a
Package Lock in the provider cwd. The repository's platform-specific hooks and
CI remain the enforcement boundary; the runner is an additional guardrail.

The routing command excludes an external provider from the same vendor as the
host by default. This exclusion does not disable host-native subagents. The user
may explicitly allow the same-vendor external CLI.

No adapter owns credentials. Codex, Claude Code, and Antigravity continue using
their normal authenticated CLI state. Configuration stores only routing choices.
