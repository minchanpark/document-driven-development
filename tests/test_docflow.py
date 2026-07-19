from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DOCFLOW = PLUGIN_ROOT / "scripts/docflow.py"
INSTALLER = PLUGIN_ROOT / "scripts/install_harness.py"
CODEX_GUARD = PLUGIN_ROOT / "scripts/codex_pre_tool.py"
CLAUDE_GUARD = PLUGIN_ROOT / "scripts/claude_pre_tool.py"
ANTIGRAVITY_GUARD = PLUGIN_ROOT / "scripts/antigravity_pre_tool.py"
CODEX_SESSION = PLUGIN_ROOT / "scripts/codex_session_context.py"
CLAUDE_SESSION = PLUGIN_ROOT / "scripts/claude_session_context.py"
ANTIGRAVITY_INVOCATION = PLUGIN_ROOT / "scripts/antigravity_pre_invocation.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class DocflowScenarioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="document-driven-test-")
        self.root = Path(self.temporary.name)
        (self.root / "docs").mkdir()
        (self.root / "docs/PRD.md").write_text(
            "# PRD\n\nREQ-1: The service returns a health response.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_python(
        self,
        script: Path,
        *args: str,
        input_text: str | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd,
        )

    def run_docflow(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run_python(DOCFLOW, *args, "--root", str(self.root))

    def approved_manifest(self) -> None:
        architecture = self.root / "docs/ARCHITECTURE.md"
        architecture.write_text(
            "# Architecture\n\nREQ-1 is implemented by a small HTTP service.\n",
            encoding="utf-8",
        )
        write_json(
            self.root / "docs/document-manifest.json",
            {
                "schema_version": "1.0",
                "source": {"prd": "docs/PRD.md"},
                "artifacts": [
                    {
                        "id": "architecture",
                        "path": "docs/ARCHITECTURE.md",
                        "purpose": "Own the service boundary",
                        "status": "approved",
                        "informed_by": ["prd"],
                        "depends_on": [],
                        "required_for": ["backend"],
                        "approval": {
                            "approved_by": "test-user",
                            "approved_at": "2026-07-19T00:00:00+00:00",
                            "content_sha256": digest(architecture),
                        },
                    }
                ],
                "implementation_gate": {
                    "require_relevant_documents_approved": True,
                    "require_traceability": True,
                },
            },
        )

    def test_proposed_artifact_can_precede_file_then_requires_review_and_approval(self) -> None:
        write_json(
            self.root / "docs/document-manifest.json",
            {
                "schema_version": "1.0",
                "source": {"prd": "docs/PRD.md"},
                "artifacts": [
                    {
                        "id": "dynamic-choice",
                        "path": "docs/WHATEVER_THE_PROJECT_NEEDS.md",
                        "purpose": "Own a project-specific decision",
                        "status": "proposed",
                        "informed_by": ["prd"],
                        "depends_on": [],
                        "required_for": ["backend"],
                    }
                ],
                "implementation_gate": {
                    "require_relevant_documents_approved": True,
                    "require_traceability": True,
                },
            },
        )
        self.assertEqual(self.run_docflow("validate").returncode, 0)
        self.assertEqual(
            self.run_docflow("set-status", "--artifact", "dynamic-choice", "--to", "drafting").returncode,
            0,
        )
        self.assertNotEqual(self.run_docflow("validate").returncode, 0)
        artifact = self.root / "docs/WHATEVER_THE_PROJECT_NEEDS.md"
        artifact.write_text("# Dynamic decision\n\nREQ-1 is covered.\n", encoding="utf-8")
        self.assertEqual(self.run_docflow("validate").returncode, 0)
        self.assertEqual(
            self.run_docflow("set-status", "--artifact", "dynamic-choice", "--to", "reviewed").returncode,
            0,
        )
        approved = self.run_docflow(
            "approve", "--artifact", "dynamic-choice", "--approved-by", "test-user"
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        manifest = json.loads((self.root / "docs/document-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["artifacts"][0]["approval"]["content_sha256"], digest(artifact))

    def test_harness_lock_guard_trace_ci_and_document_drift(self) -> None:
        self.approved_manifest()
        (self.root / "AGENTS.md").write_text("# Existing agent rules\n", encoding="utf-8")
        legacy_command = "python3 .document-driven/bin/pre_tool_guard.py"
        write_json(
            self.root / ".claude/settings.json",
            {
                "permissions": {"allow": ["Read"]},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Write",
                            "hooks": [{"type": "command", "command": legacy_command}],
                        },
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "echo unrelated"}],
                        },
                    ]
                },
            },
        )
        write_json(
            self.root / ".agents/hooks.json",
            {"existing-integration": {"PreInvocation": [{"type": "command", "command": "echo keep"}]}},
        )

        install = self.run_python(INSTALLER, "--root", str(self.root), "--ci", "github")
        self.assertEqual(install.returncode, 0, install.stderr)
        install_again = self.run_python(INSTALLER, "--root", str(self.root), "--ci", "github")
        self.assertEqual(install_again.returncode, 0, install_again.stderr)
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("# Existing agent rules", agents)
        self.assertEqual(agents.count("<!-- document-driven-development:start -->"), 1)
        claude = json.loads((self.root / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(claude["permissions"]["allow"], ["Read"])
        self.assertIn("PreToolUse", claude["hooks"])
        claude_commands = json.dumps(claude["hooks"], ensure_ascii=False)
        self.assertIn("claude_pre_tool.py", claude_commands)
        self.assertNotIn("pre_tool_guard.py", claude_commands)
        self.assertIn("echo unrelated", claude_commands)

        codex = json.loads((self.root / ".codex/hooks.json").read_text(encoding="utf-8"))
        codex_hooks = json.dumps(codex["hooks"], ensure_ascii=False)
        self.assertIn("codex_pre_tool.py", codex_hooks)
        self.assertIn("apply_patch", codex_hooks)
        self.assertNotIn("claude_pre_tool.py", codex_hooks)

        antigravity = json.loads((self.root / ".agents/hooks.json").read_text(encoding="utf-8"))
        self.assertIn("existing-integration", antigravity)
        antigravity_hooks = json.dumps(
            antigravity["document-driven-development"], ensure_ascii=False
        )
        self.assertIn("antigravity_pre_tool.py", antigravity_hooks)
        self.assertIn("write_to_file", antigravity_hooks)
        self.assertIn("PreInvocation", antigravity["document-driven-development"])

        policy_path = self.root / ".document-driven/policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["path_rules"] = [
            {"patterns": ["src/**"], "requires_artifacts": ["architecture"]}
        ]
        write_json(policy_path, policy)

        blocked = self.run_docflow("guard-edit", "--path", "src/app.py")
        self.assertNotEqual(blocked.returncode, 0)
        allowed_doc = self.run_docflow("guard-edit", "--path", "docs/ARCHITECTURE.md")
        self.assertEqual(allowed_doc.returncode, 0, allowed_doc.stderr)

        codex_payload = json.dumps(
            {
                "cwd": str(self.root),
                "tool_name": "apply_patch",
                "tool_input": {
                    "patch": "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n*** End Patch"
                },
            }
        )
        claude_payload = json.dumps(
            {
                "cwd": str(self.root),
                "tool_name": "Write",
                "tool_input": {"file_path": "src/app.py"},
            }
        )
        antigravity_payload = json.dumps(
            {
                "workspacePaths": [str(self.root)],
                "toolCall": {
                    "name": "write_to_file",
                    "args": {"TargetFile": "src/app.py"},
                },
            }
        )
        for guard, payload in (
            (CODEX_GUARD, codex_payload),
            (CLAUDE_GUARD, claude_payload),
        ):
            hook_blocked = self.run_python(guard, input_text=payload)
            hook_value = json.loads(hook_blocked.stdout)
            self.assertEqual(
                hook_value["hookSpecificOutput"]["permissionDecision"],
                "deny",
            )
        antigravity_blocked = json.loads(
            self.run_python(ANTIGRAVITY_GUARD, input_text=antigravity_payload).stdout
        )
        self.assertEqual(antigravity_blocked["decision"], "deny")

        codex_context = json.loads(self.run_python(CODEX_SESSION, cwd=self.root).stdout)
        claude_context = json.loads(self.run_python(CLAUDE_SESSION, cwd=self.root).stdout)
        antigravity_context = json.loads(
            self.run_python(ANTIGRAVITY_INVOCATION, cwd=self.root).stdout
        )
        self.assertEqual(
            codex_context["hookSpecificOutput"]["hookEventName"], "SessionStart"
        )
        self.assertEqual(
            claude_context["hookSpecificOutput"]["hookEventName"], "SessionStart"
        )
        self.assertIn("ephemeralMessage", antigravity_context["injectSteps"][0])

        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True, text=True, capture_output=True
        ).stdout.strip()

        prepared = self.run_docflow(
            "prepare",
            "--task-id",
            "TASK-1",
            "--summary",
            "Implement health response",
            "--requirement",
            "REQ-1",
            "--scope",
            "backend",
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertEqual(self.run_docflow("check-lock").returncode, 0)
        self.assertEqual(self.run_docflow("guard-edit", "--path", "src/app.py").returncode, 0)
        for guard, payload in (
            (CODEX_GUARD, codex_payload),
            (CLAUDE_GUARD, claude_payload),
        ):
            hook_allowed = self.run_python(guard, input_text=payload)
            self.assertEqual(json.loads(hook_allowed.stdout), {})
        antigravity_allowed = json.loads(
            self.run_python(ANTIGRAVITY_GUARD, input_text=antigravity_payload).stdout
        )
        self.assertEqual(antigravity_allowed["decision"], "allow")

        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src/app.py").write_text("def health(): return 'ok'\n", encoding="utf-8")
        (self.root / "tests/test_app.py").write_text("from src.app import health\n", encoding="utf-8")
        traced = self.run_docflow(
            "trace",
            "--requirement",
            "REQ-1",
            "--code",
            "src/app.py",
            "--test",
            "tests/test_app.py",
        )
        self.assertEqual(traced.returncode, 0, traced.stderr)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "implement health response"], cwd=self.root, check=True)
        verified = self.run_docflow("verify", "--ci", "--base-ref", base)
        self.assertEqual(verified.returncode, 0, verified.stderr)

        (self.root / "src/untraced.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/untraced.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "add untraced implementation"], cwd=self.root, check=True)
        untraced = self.run_docflow("verify", "--ci", "--base-ref", base)
        self.assertNotEqual(untraced.returncode, 0)
        self.assertIn("not traced", untraced.stderr)
        retraced = self.run_docflow(
            "trace",
            "--requirement",
            "REQ-1",
            "--code",
            "src/untraced.py",
            "--test",
            "tests/test_app.py",
        )
        self.assertEqual(retraced.returncode, 0, retraced.stderr)
        self.assertEqual(self.run_docflow("verify", "--ci", "--base-ref", base).returncode, 0)

        with (self.root / "docs/ARCHITECTURE.md").open("a", encoding="utf-8") as handle:
            handle.write("\nUnapproved design change.\n")
        stale = self.run_docflow("check-lock")
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("re-approval", stale.stderr)


if __name__ == "__main__":
    unittest.main()
