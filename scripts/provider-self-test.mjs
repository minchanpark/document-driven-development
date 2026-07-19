#!/usr/bin/env node

import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const pluginRoot = path.dirname(scriptDir);
const runner = path.join(scriptDir, "development-provider-runner.mjs");

function invoke(args, input = "") {
  return JSON.parse(execFileSync(process.execPath, [runner, ...args], {
    cwd: pluginRoot,
    input,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "inherit"],
  }));
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const dryRuns = [];
for (const provider of ["codex", "claude", "antigravity"]) {
  for (const role of ["architect", "reviewer"]) {
    const result = invoke([
      "run",
      "--provider",
      provider,
      "--role",
      role,
      "--cwd",
      pluginRoot,
      "--access",
      "read-only",
      "--tier",
      "deep",
      "--dry-run",
    ], "Review the locked plan without changing files.");
    assert(result.dryRun === true, `${provider}/${role}: expected dry-run`);
    assert(result.actual.access !== "workspace-write", `${provider}/${role}: unexpected write access`);
    dryRuns.push({ provider: result.provider, role, access: result.actual.access });
  }
}

const forbidden = spawnSync(process.execPath, [runner,
  "run", "--provider", "codex", "--role", "reviewer", "--cwd", pluginRoot,
  "--access", "workspace-write", "--dry-run",
], { input: "Do not write.", encoding: "utf8" });
assert(forbidden.status !== 0, "Reviewer workspace-write should be rejected");

const temporary = mkdtempSync(path.join(os.tmpdir(), "document-driven-provider-"));
try {
  mkdirSync(path.join(temporary, ".document-driven"));
  writeFileSync(path.join(temporary, ".document-driven", "package-lock.json"), JSON.stringify({
    schema_version: "1.0",
    package_id: "backend",
    allowed_paths: ["src/**"],
  }));
  const coder = invoke([
    "run", "--provider", "codex", "--role", "coder", "--cwd", temporary,
    "--access", "workspace-write", "--dry-run",
  ], "Implement only the package scope.");
  assert(coder.packageId === "backend", "Coder dry-run did not bind the Package Lock");
  assert(coder.actual.access === "workspace-write", "Coder write access was not forwarded");
} finally {
  rmSync(temporary, { recursive: true, force: true });
}

process.stdout.write(`${JSON.stringify({ status: "passed", dryRuns }, null, 2)}\n`);
