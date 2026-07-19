const EFFORT = {
  fast: "low",
  balanced: "medium",
  deep: "high",
  maximum: "xhigh",
};

export const claudeCodeAdapter = {
  id: "claude-code-cli",
  vendor: "anthropic",
  command: process.env.DOCUMENT_DRIVEN_CLAUDE_BIN || "claude",
  versionArgs: ["--version"],
  capabilities: {
    nonInteractive: true,
    structuredOutput: true,
    resume: true,
    capturesSessionId: true,
    enforcedReadOnly: true,
    workspaceWrite: true,
    perCallModel: true,
    perCallEffort: true,
  },

  buildInvocation({ operation, sessionId, cwd, access, model, tier }) {
    const effort = EFFORT[tier] || null;
    const args = ["-p", "--output-format", "json"];
    if (operation === "continue") args.push("--resume", sessionId);
    if (model) args.push("--model", model);
    if (effort) args.push("--effort", effort);
    if (access === "read-only") {
      args.push(
        "--permission-mode",
        "plan",
        "--tools",
        "Read,Grep,Glob,WebSearch,WebFetch",
        "--disallowedTools",
        "Write,Edit,Bash,NotebookEdit",
      );
    } else {
      args.push("--permission-mode", "dontAsk");
    }
    return {
      args,
      cwd,
      stdin: true,
      actual: { model: model || "default", effort: effort || "default", access },
      warnings: [],
    };
  },

  async parse({ stdout }) {
    try {
      const parsed = JSON.parse(stdout);
      return {
        sessionId: parsed.session_id || parsed.sessionId || null,
        result: typeof parsed.result === "string"
          ? parsed.result.trim()
          : JSON.stringify(parsed.result ?? parsed),
      };
    } catch {
      return { sessionId: null, result: stdout.trim() };
    }
  },
};
