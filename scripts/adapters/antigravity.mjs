export const antigravityAdapter = {
  id: "antigravity-cli",
  vendor: "google",
  command: process.env.DOCUMENT_DRIVEN_ANTIGRAVITY_BIN || "agy",
  versionArgs: ["--version"],
  capabilities: {
    nonInteractive: true,
    structuredOutput: false,
    resume: true,
    capturesSessionId: false,
    enforcedReadOnly: false,
    workspaceWrite: true,
    perCallModel: true,
    perCallEffort: false,
  },

  buildInvocation({ operation, sessionId, cwd, access, model, timeoutSeconds, prompt }) {
    const args = [];
    if (operation === "continue") args.push("--conversation", sessionId);
    if (model) args.push("--model", model);
    args.push("--sandbox", "--print-timeout", `${timeoutSeconds}s`, "--print", prompt);
    const warnings = [
      "Antigravity print mode has no structured JSON output; text is normalized.",
    ];
    if (access === "read-only") {
      warnings.push(
        "Antigravity read-only is prompt-enforced; the CLI does not mechanically block every file write.",
      );
    }
    return {
      args,
      cwd,
      stdin: false,
      actual: {
        model: model || "default",
        effort: "inherited",
        access: access === "read-only" ? "prompt-only-read-only" : "workspace-write",
      },
      warnings,
    };
  },

  async parse({ stdout }) {
    return { sessionId: null, result: stdout.trim() };
  },
};
