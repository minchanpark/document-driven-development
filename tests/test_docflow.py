from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DOCFLOW_SPEC = importlib.util.spec_from_file_location(
    "document_driven_docflow",
    PLUGIN_ROOT / "scripts/docflow.py",
)
assert DOCFLOW_SPEC and DOCFLOW_SPEC.loader
docflow_module = importlib.util.module_from_spec(DOCFLOW_SPEC)
DOCFLOW_SPEC.loader.exec_module(docflow_module)

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
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


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

    def test_append_only_snapshot_size_is_bounded_by_current_state(self) -> None:
        run = {
            "schema_version": "1.0",
            "storage_mode": "append-only",
            "task_id": "TASK-SIZE",
            "status": "planning",
            "packages": [],
        }
        docflow_module._record_run_event(
            self.root,
            run,
            docflow_module._event("orchestrator", "planning", "start"),
        )
        docflow_module.persist_run(self.root, run)
        path = self.root / ".document-driven/runs/TASK-SIZE/run.json"
        initial_size = path.stat().st_size
        for _ in range(250):
            docflow_module._record_run_event(
                self.root,
                run,
                docflow_module._event("orchestrator", "planning", "heartbeat"),
            )
            docflow_module.persist_run(self.root, run)
        final_size = path.stat().st_size
        events = path.with_name("events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(events), 251)
        self.assertLess(final_size - initial_size, 16)

    def test_proposed_artifact_can_precede_file_then_requires_review_and_approval(
        self,
    ) -> None:
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
            self.run_docflow(
                "set-status", "--artifact", "dynamic-choice", "--to", "drafting"
            ).returncode,
            0,
        )
        self.assertNotEqual(self.run_docflow("validate").returncode, 0)
        artifact = self.root / "docs/WHATEVER_THE_PROJECT_NEEDS.md"
        artifact.write_text(
            "# Dynamic decision\n\nREQ-1 is covered.\n", encoding="utf-8"
        )
        self.assertEqual(self.run_docflow("validate").returncode, 0)
        self.assertEqual(
            self.run_docflow(
                "set-status", "--artifact", "dynamic-choice", "--to", "reviewed"
            ).returncode,
            0,
        )
        approved = self.run_docflow(
            "approve", "--artifact", "dynamic-choice", "--approved-by", "test-user"
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        manifest = json.loads(
            (self.root / "docs/document-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["artifacts"][0]["approval"]["content_sha256"], digest(artifact)
        )

    def test_approval_bundle_is_atomic_and_reuses_identical_hashes(self) -> None:
        foundation = self.root / "docs/FOUNDATION.md"
        feature = self.root / "docs/FEATURE.md"
        foundation.write_text("# Foundation\n\nREQ-1 foundation.\n", encoding="utf-8")
        feature.write_text("# Feature\n\nREQ-1 feature.\n", encoding="utf-8")
        artifacts = [
            {
                "id": "foundation",
                "path": "docs/FOUNDATION.md",
                "purpose": "Own the base decision",
                "status": "reviewed",
                "informed_by": ["prd"],
                "depends_on": [],
                "required_for": ["backend"],
            },
            {
                "id": "feature",
                "path": "docs/FEATURE.md",
                "purpose": "Own the dependent decision",
                "status": "reviewed",
                "informed_by": ["prd"],
                "depends_on": ["foundation"],
                "required_for": ["backend"],
            },
        ]
        write_json(
            self.root / "docs/document-manifest.json",
            {
                "schema_version": "1.0",
                "source": {"prd": "docs/PRD.md"},
                "artifacts": artifacts,
                "implementation_gate": {
                    "require_relevant_documents_approved": True,
                    "require_traceability": True,
                },
            },
        )
        command = (
            "approve-bundle",
            "--approval",
            f"feature={digest(feature)}",
            "--approval",
            f"foundation={digest(foundation)}",
            "--approved-by",
            "test-user",
        )
        approved = self.run_docflow(*command)
        self.assertEqual(approved.returncode, 0, approved.stderr)
        manifest_path = self.root / "docs/document-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        approved_at = {
            item["approval"]["approved_at"] for item in manifest["artifacts"]
        }
        self.assertEqual(len(approved_at), 1)
        before = manifest_path.read_bytes()
        reused = self.run_docflow(*command)
        self.assertEqual(reused.returncode, 0, reused.stderr)
        self.assertIn("reused feature, foundation", reused.stdout)
        self.assertEqual(manifest_path.read_bytes(), before)
        mismatch = self.run_docflow(
            "approve-bundle",
            "--approval",
            f"feature={'0' * 64}",
            "--approved-by",
            "test-user",
        )
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_harness_lock_guard_trace_ci_and_document_drift(self) -> None:
        self.approved_manifest()
        (self.root / "AGENTS.md").write_text(
            "# Existing agent rules\n", encoding="utf-8"
        )
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
            {
                "existing-integration": {
                    "PreInvocation": [{"type": "command", "command": "echo keep"}]
                }
            },
        )

        install = self.run_python(INSTALLER, "--root", str(self.root), "--ci", "github")
        self.assertEqual(install.returncode, 0, install.stderr)
        installed_policy_path = self.root / ".document-driven/policy.json"
        installed_policy = json.loads(installed_policy_path.read_text(encoding="utf-8"))
        installed_policy["documentation_paths"].remove(".agents/**")
        installed_policy["documentation_paths"].append("custom-docs/**")
        write_json(installed_policy_path, installed_policy)
        install_again = self.run_python(
            INSTALLER, "--root", str(self.root), "--ci", "github"
        )
        self.assertEqual(install_again.returncode, 0, install_again.stderr)
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("# Existing agent rules", agents)
        self.assertEqual(agents.count("<!-- document-driven-development:start -->"), 1)
        self.assertIn("minimum-correct implementation policy", agents)
        claude = json.loads(
            (self.root / ".claude/settings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(claude["permissions"]["allow"], ["Read"])
        self.assertIn("PreToolUse", claude["hooks"])
        claude_commands = json.dumps(claude["hooks"], ensure_ascii=False)
        self.assertIn("claude_pre_tool.py", claude_commands)
        self.assertNotIn("pre_tool_guard.py", claude_commands)
        self.assertIn("echo unrelated", claude_commands)

        codex = json.loads(
            (self.root / ".codex/hooks.json").read_text(encoding="utf-8")
        )
        codex_hooks = json.dumps(codex["hooks"], ensure_ascii=False)
        self.assertIn("codex_pre_tool.py", codex_hooks)
        self.assertIn("apply_patch", codex_hooks)
        self.assertNotIn("claude_pre_tool.py", codex_hooks)

        antigravity = json.loads(
            (self.root / ".agents/hooks.json").read_text(encoding="utf-8")
        )
        self.assertIn("existing-integration", antigravity)
        antigravity_hooks = json.dumps(
            antigravity["document-driven-development"], ensure_ascii=False
        )
        self.assertIn("antigravity_pre_tool.py", antigravity_hooks)
        self.assertIn("write_to_file", antigravity_hooks)
        self.assertIn("PreInvocation", antigravity["document-driven-development"])

        read_only_payloads = [
            {
                "cwd": str(self.root),
                "tool_name": "exec_command",
                "tool_input": {
                    "cmd": "sed -n '1,20p' docs/ARCHITECTURE.md 2>/dev/null",
                },
            },
            {
                "cwd": str(self.root),
                "tool_name": "view_image",
                "tool_input": {"path": "/tmp/reference.png"},
            },
        ]
        for payload in read_only_payloads:
            allowed = self.run_python(CODEX_GUARD, input_text=json.dumps(payload))
            self.assertEqual(json.loads(allowed.stdout), {}, allowed.stdout)

        explicit_workdir_payload = {
            "cwd": "/tmp",
            "tool_name": "exec_command",
            "tool_input": {
                "workdir": str(self.root),
                "cmd": "touch src/app.py",
            },
        }
        explicit_workdir_denied = json.loads(
            self.run_python(
                CODEX_GUARD,
                input_text=json.dumps(explicit_workdir_payload),
            ).stdout
        )
        self.assertEqual(
            explicit_workdir_denied["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        in_place_payload = {
            "cwd": str(self.root),
            "tool_name": "exec_command",
            "tool_input": {"cmd": "sed -i.bak 's/old/new/' src/app.py"},
        }
        denied = json.loads(
            self.run_python(CODEX_GUARD, input_text=json.dumps(in_place_payload)).stdout
        )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

        policy_path = self.root / ".document-driven/policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertIn(".agents/**", policy["documentation_paths"])
        self.assertIn(".gitignore", policy["documentation_paths"])
        self.assertIn("custom-docs/**", policy["documentation_paths"])
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
        claude_context = json.loads(
            self.run_python(CLAUDE_SESSION, cwd=self.root).stdout
        )
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
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"], cwd=self.root, check=True
        )
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root,
            check=True,
            text=True,
            capture_output=True,
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
        context_pack = json.loads(
            (self.root / ".document-driven/context-pack.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(context_pack["task"]["requirement_ids"], ["REQ-1"])
        self.assertTrue(
            any(
                "REQ-1" in item["text"]
                for document in context_pack["documents"]
                for item in document["slices"]
            )
        )
        self.assertEqual(self.run_docflow("check-context-pack").returncode, 0)
        task_context_path = self.root / ".document-driven/context-pack.json"
        task_context_bytes = task_context_path.read_bytes()
        task_context_path.write_text("{}\n", encoding="utf-8")
        invalid_session = json.loads(
            self.run_python(CODEX_SESSION, cwd=self.root).stdout
        )
        self.assertIn(
            "context pack is invalid",
            invalid_session["hookSpecificOutput"]["additionalContext"],
        )
        task_context_path.write_bytes(task_context_bytes)
        self.assertEqual(self.run_docflow("check-lock").returncode, 0)
        self.assertEqual(
            self.run_docflow("guard-edit", "--path", "src/app.py").returncode, 0
        )
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
        (self.root / "src/app.py").write_text(
            "def health(): return 'ok'\n", encoding="utf-8"
        )
        (self.root / "tests/test_app.py").write_text(
            "from src.app import health\n", encoding="utf-8"
        )
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
        subprocess.run(
            ["git", "commit", "-qm", "implement health response"],
            cwd=self.root,
            check=True,
        )
        verified = self.run_docflow("verify", "--ci", "--base-ref", base)
        self.assertEqual(verified.returncode, 0, verified.stderr)

        (self.root / "src/untraced.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/untraced.py"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "add untraced implementation"],
            cwd=self.root,
            check=True,
        )
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
        self.assertEqual(
            self.run_docflow("verify", "--ci", "--base-ref", base).returncode, 0
        )

        with (self.root / "docs/ARCHITECTURE.md").open("a", encoding="utf-8") as handle:
            handle.write("\nUnapproved design change.\n")
        stale = self.run_docflow("check-lock")
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("re-approval", stale.stderr)
        repair_allowed = self.run_docflow(
            "guard-edit",
            "--path",
            "docs/ARCHITECTURE.md",
        )
        self.assertEqual(repair_allowed.returncode, 0, repair_allowed.stderr)

    def test_orchestrated_run_enforces_package_ownership_and_cross_review(self) -> None:
        self.approved_manifest()
        install = self.run_python(INSTALLER, "--root", str(self.root), "--ci", "none")
        self.assertEqual(install.returncode, 0, install.stderr)
        prepared = self.run_docflow(
            "prepare",
            "--task-id",
            "TASK-ORCH",
            "--summary",
            "Implement the documented service",
            "--requirement",
            "REQ-1",
            "--scope",
            "backend",
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        started = self.run_docflow(
            "start-run",
            "--mode",
            "orchestrated",
            "--plan-summary",
            "One isolated backend package",
            "--debate-rounds",
            "1",
            "--actor",
            "main-orchestrator",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        package = self.run_docflow(
            "add-package",
            "--package",
            "backend",
            "--summary",
            "Implement health endpoint",
            "--requirement",
            "REQ-1",
            "--artifact",
            "architecture",
            "--allowed-path",
            "src/**",
            "--acceptance",
            "REQ-1 returns the documented health response",
            "--verification-command",
            "python -m unittest",
            "--actor",
            "main-orchestrator",
        )
        self.assertEqual(package.returncode, 0, package.stderr)
        overlap = self.run_docflow(
            "add-package",
            "--package",
            "overlap",
            "--requirement",
            "REQ-1",
            "--artifact",
            "architecture",
            "--allowed-path",
            "src/app.py",
            "--verification-command",
            "true",
            "--actor",
            "main-orchestrator",
        )
        self.assertNotEqual(overlap.returncode, 0)
        self.assertIn("overlaps", overlap.stderr)
        approved = self.run_docflow("approve-run", "--approved-by", "test-user")
        self.assertEqual(approved.returncode, 0, approved.stderr)

        no_package = self.run_docflow("guard-edit", "--path", "src/app.py")
        self.assertNotEqual(no_package.returncode, 0)
        self.assertIn("activated", no_package.stderr)
        activated = self.run_docflow(
            "activate-package",
            "--package",
            "backend",
            "--actor",
            "coder-a",
        )
        self.assertEqual(activated.returncode, 0, activated.stderr)
        package_lock = json.loads(
            (self.root / ".document-driven/package-lock.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            package_lock["acceptance_criteria"],
            ["REQ-1 returns the documented health response"],
        )
        package_context = self.root / package_lock["context_pack"]
        self.assertTrue(package_context.is_file())
        self.assertEqual(package_lock["context_pack_sha256"], digest(package_context))
        legacy_lock = dict(package_lock)
        legacy_lock.pop("context_pack")
        legacy_lock.pop("context_pack_sha256")
        write_json(self.root / ".document-driven/package-lock.json", legacy_lock)
        self.assertEqual(self.run_docflow("check-package-lock").returncode, 0)
        write_json(self.root / ".document-driven/package-lock.json", package_lock)
        package_context_value = json.loads(package_context.read_text(encoding="utf-8"))
        self.assertEqual(package_context_value["package"]["id"], "backend")
        original_context = package_context.read_bytes()
        package_context.write_text("{}\n", encoding="utf-8")
        stale_context = self.run_docflow("check-package-lock")
        self.assertNotEqual(stale_context.returncode, 0)
        self.assertIn("context pack changed", stale_context.stderr)
        package_context.write_bytes(original_context)
        self.assertEqual(self.run_docflow("check-package-lock").returncode, 0)
        regenerated = self.run_docflow("context-pack", "--package", "backend")
        self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
        self.assertEqual(package_lock["context_pack_sha256"], digest(package_context))
        self.assertEqual(self.run_docflow("check-package-lock").returncode, 0)
        with mock.patch.object(
            docflow_module,
            "require_valid_manifest",
            wraps=docflow_module.require_valid_manifest,
        ) as manifest_validation:
            direct_allowed, _ = docflow_module.guard_edit(self.root, "src/app.py")
        self.assertTrue(direct_allowed)
        self.assertEqual(manifest_validation.call_count, 1)
        self.assertEqual(
            self.run_docflow("guard-edit", "--path", "src/app.py").returncode, 0
        )
        outside = self.run_docflow("guard-edit", "--path", "tests/test_app.py")
        self.assertNotEqual(outside.returncode, 0)
        design_write = self.run_docflow("guard-edit", "--path", "docs/ARCHITECTURE.md")
        self.assertNotEqual(design_write.returncode, 0)

        implemented = self.run_docflow(
            "set-package-status",
            "--package",
            "backend",
            "--to",
            "implemented",
            "--actor",
            "coder-a",
            "--note",
            "Focused tests passed",
        )
        self.assertEqual(implemented.returncode, 0, implemented.stderr)
        same_reviewer = self.run_docflow(
            "set-package-status",
            "--package",
            "backend",
            "--to",
            "reviewing",
            "--actor",
            "coder-a",
        )
        self.assertNotEqual(same_reviewer.returncode, 0)
        reviewing = self.run_docflow(
            "set-package-status",
            "--package",
            "backend",
            "--to",
            "reviewing",
            "--actor",
            "reviewer-b",
        )
        self.assertEqual(reviewing.returncode, 0, reviewing.stderr)
        accepted = self.run_docflow(
            "set-package-status",
            "--package",
            "backend",
            "--to",
            "approved",
            "--actor",
            "reviewer-b",
            "--note",
            "Document and code review passed",
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        integration_lock = self.run_docflow(
            "activate-integration",
            "--package",
            "backend",
            "--actor",
            "main-orchestrator",
        )
        self.assertEqual(integration_lock.returncode, 0, integration_lock.stderr)
        integrated = self.run_docflow(
            "set-package-status",
            "--package",
            "backend",
            "--to",
            "integrated",
            "--actor",
            "main-orchestrator",
            "--note",
            "Integration checks passed",
        )
        self.assertEqual(integrated.returncode, 0, integrated.stderr)
        completed = self.run_docflow(
            "complete-run",
            "--actor",
            "main-orchestrator",
            "--note",
            "Green integration gate passed",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.run_docflow("check-run").returncode, 0)
        self.assertEqual(
            self.run_docflow("guard-edit", "--path", "src/app.py").returncode, 0
        )

    def test_isolated_package_result_merges_without_overwriting_central_run(
        self,
    ) -> None:
        self.approved_manifest()
        self.assertEqual(
            self.run_python(
                INSTALLER, "--root", str(self.root), "--ci", "none"
            ).returncode,
            0,
        )
        self.assertEqual(
            self.run_docflow(
                "prepare",
                "--task-id",
                "TASK-WORKTREE",
                "--requirement",
                "REQ-1",
                "--scope",
                "backend",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.run_docflow(
                "start-run",
                "--mode",
                "orchestrated",
                "--actor",
                "main-orchestrator",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.run_docflow(
                "add-package",
                "--package",
                "backend",
                "--requirement",
                "REQ-1",
                "--artifact",
                "architecture",
                "--allowed-path",
                "src/**",
                "--verification-command",
                "python -m unittest",
                "--actor",
                "main-orchestrator",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.run_docflow("approve-run", "--approved-by", "test-user").returncode, 0
        )

        with tempfile.TemporaryDirectory(
            prefix="document-driven-worker-"
        ) as worker_temp:
            worker = Path(worker_temp) / "backend"
            shutil.copytree(self.root, worker)

            def worker_docflow(*args: str) -> subprocess.CompletedProcess[str]:
                return self.run_python(DOCFLOW, *args, "--root", str(worker))

            self.assertEqual(
                worker_docflow(
                    "activate-package",
                    "--package",
                    "backend",
                    "--actor",
                    "coder-a",
                ).returncode,
                0,
            )
            self.assertEqual(
                worker_docflow(
                    "set-package-status",
                    "--package",
                    "backend",
                    "--to",
                    "implemented",
                    "--actor",
                    "coder-a",
                    "--note",
                    "Package tests passed",
                ).returncode,
                0,
            )
            self.assertEqual(
                worker_docflow(
                    "set-package-status",
                    "--package",
                    "backend",
                    "--to",
                    "reviewing",
                    "--actor",
                    "reviewer-b",
                ).returncode,
                0,
            )
            self.assertEqual(
                worker_docflow(
                    "set-package-status",
                    "--package",
                    "backend",
                    "--to",
                    "approved",
                    "--actor",
                    "reviewer-b",
                    "--note",
                    "Independent review passed",
                ).returncode,
                0,
            )
            imported = self.run_docflow(
                "import-package-result",
                "--package",
                "backend",
                "--from-root",
                str(worker),
                "--actor",
                "main-orchestrator",
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)

        central_run = json.loads(
            (self.root / ".document-driven/runs/TASK-WORKTREE/run.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(central_run["packages"][0]["status"], "approved")
        self.assertEqual(
            self.run_docflow(
                "activate-integration",
                "--package",
                "backend",
                "--actor",
                "main-orchestrator",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.run_docflow("guard-edit", "--path", "src/app.py").returncode, 0
        )
        integrated = self.run_docflow(
            "set-package-status",
            "--package",
            "backend",
            "--to",
            "integrated",
            "--actor",
            "main-orchestrator",
            "--note",
            "Integration checks passed",
        )
        self.assertEqual(integrated.returncode, 0, integrated.stderr)
        completed = self.run_docflow(
            "complete-run",
            "--actor",
            "main-orchestrator",
            "--note",
            "Green integration gate passed",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.run_docflow("check-run").returncode, 0)
        self.assertEqual(
            self.run_docflow("guard-edit", "--path", "src/app.py").returncode, 0
        )

    def test_append_only_run_sharded_trace_and_persistent_validation_lease(
        self,
    ) -> None:
        self.approved_manifest()
        self.assertEqual(
            self.run_python(
                INSTALLER, "--root", str(self.root), "--ci", "none"
            ).returncode,
            0,
        )
        self.assertEqual(
            self.run_docflow(
                "prepare",
                "--task-id",
                "TASK-PERF",
                "--requirement",
                "REQ-1",
                "--scope",
                "backend",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.run_docflow("start-run", "--actor", "orchestrator").returncode,
            0,
        )
        self.assertEqual(
            self.run_docflow(
                "add-package",
                "--package",
                "backend",
                "--requirement",
                "REQ-1",
                "--artifact",
                "architecture",
                "--allowed-path",
                "src/**",
                "--verification-command",
                "true",
                "--actor",
                "orchestrator",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.run_docflow("approve-run", "--approved-by", "user").returncode, 0
        )
        self.assertEqual(
            self.run_docflow(
                "activate-package",
                "--package",
                "backend",
                "--actor",
                "coder",
            ).returncode,
            0,
        )
        run_dir = self.root / ".document-driven/runs/TASK-PERF"
        snapshot = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["storage_mode"], "append-only")
        self.assertNotIn("events", snapshot)
        self.assertNotIn("events", snapshot["packages"][0])
        self.assertGreaterEqual(
            len((run_dir / "events.jsonl").read_text().splitlines()), 5
        )
        audited = self.run_docflow("check-run", "--audit")
        self.assertEqual(audited.returncode, 0, audited.stderr)

        self.assertEqual(self.run_docflow("invalidate-lease").returncode, 0)
        with mock.patch.object(
            docflow_module,
            "require_valid_manifest",
            wraps=docflow_module.require_valid_manifest,
        ) as first_validation:
            allowed, _ = docflow_module.guard_edit(self.root, "src/app.py")
        self.assertTrue(allowed)
        self.assertEqual(first_validation.call_count, 1)
        with mock.patch.object(
            docflow_module,
            "require_valid_manifest",
            wraps=docflow_module.require_valid_manifest,
        ) as cached_validation:
            allowed, reason = docflow_module.guard_edit(self.root, "src/app.py")
        self.assertTrue(allowed)
        self.assertIn("cached", reason.lower())
        self.assertEqual(cached_validation.call_count, 0)
        self.assertEqual(self.run_docflow("check-lease").returncode, 0)

        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "tests/test_app.py").write_text("assert True\n", encoding="utf-8")
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
        shard = self.root / ".document-driven/trace/TASK-PERF/REQ-1.json"
        self.assertTrue(shard.is_file())
        exported = self.run_docflow("trace-export")
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertIn('"requirement_id": "REQ-1"', exported.stdout)

    def test_structured_environment_gates_cache_and_run_supersession(self) -> None:
        self.approved_manifest()
        self.assertEqual(
            self.run_python(
                INSTALLER, "--root", str(self.root), "--ci", "none"
            ).returncode,
            0,
        )
        (self.root / "src").mkdir()
        (self.root / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
        unit = json.dumps(
            {
                "id": "unit",
                "type": "unit",
                "command": "python3 -c \"print('unit-ok')\"",
                "input_paths": ["src/**"],
                "blocking_phase": "package",
                "cache_policy": "input-hash",
            }
        )
        hosted = json.dumps(
            {
                "id": "hosted",
                "type": "hosted",
                "command": "python3 -c \"print('hosted-ok')\"",
                "requires": ["browser"],
                "input_paths": ["src/**"],
                "blocking_phase": "release",
                "cache_policy": "environment",
            }
        )

        def prepare_and_plan(task: str, *extra: str) -> None:
            self.assertEqual(
                self.run_docflow(
                    "prepare",
                    "--task-id",
                    task,
                    "--requirement",
                    "REQ-1",
                    "--scope",
                    "backend",
                ).returncode,
                0,
            )
            self.assertEqual(
                self.run_docflow(
                    "start-run", "--actor", "orchestrator", *extra
                ).returncode,
                0,
            )
            planned = self.run_docflow(
                "add-package",
                "--package",
                "backend",
                "--requirement",
                "REQ-1",
                "--artifact",
                "architecture",
                "--allowed-path",
                "src/**",
                "--verification-command",
                "python -m unittest",
                "--verification-spec",
                unit,
                "--verification-spec",
                hosted,
                "--actor",
                "orchestrator",
            )
            self.assertEqual(planned.returncode, 0, planned.stderr)

        prepare_and_plan("TASK-EVIDENCE")
        self.assertEqual(
            self.run_docflow("approve-run", "--approved-by", "user").returncode, 0
        )
        self.assertEqual(
            self.run_docflow(
                "activate-package",
                "--package",
                "backend",
                "--actor",
                "coder",
            ).returncode,
            0,
        )
        passed = self.run_docflow(
            "verify-package",
            "--package",
            "backend",
            "--gate",
            "unit",
            "--execute",
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("unit: passed", passed.stdout)
        reused = self.run_docflow(
            "verify-package", "--package", "backend", "--gate", "unit"
        )
        self.assertEqual(reused.returncode, 0, reused.stderr)
        self.assertIn("unit: reused", reused.stdout)
        unavailable = self.run_docflow(
            "preflight", "--package", "backend", "--gate", "hosted"
        )
        self.assertEqual(unavailable.returncode, 0, unavailable.stderr)
        self.assertIn("unavailable", unavailable.stdout)
        for arguments in (
            ("implemented", "coder", "Unit evidence passed"),
            ("reviewing", "reviewer", None),
            ("approved", "reviewer", "Independent review passed"),
        ):
            command = [
                "set-package-status",
                "--package",
                "backend",
                "--to",
                arguments[0],
                "--actor",
                arguments[1],
            ]
            if arguments[2]:
                command.extend(["--note", arguments[2]])
            changed = self.run_docflow(*command)
            self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertEqual(
            self.run_docflow(
                "activate-integration",
                "--package",
                "backend",
                "--actor",
                "orchestrator",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.run_docflow(
                "set-package-status",
                "--package",
                "backend",
                "--to",
                "integrated",
                "--actor",
                "orchestrator",
                "--note",
                "Integration passed",
            ).returncode,
            0,
        )
        blocked = self.run_docflow("complete-run", "--actor", "orchestrator")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("hosted", blocked.stderr)
        no_environment = self.run_docflow(
            "verify-package",
            "--package",
            "backend",
            "--gate",
            "hosted",
            "--available",
            "browser",
            "--execute",
        )
        self.assertEqual(no_environment.returncode, 0, no_environment.stderr)
        self.assertIn("environment fingerprint required", no_environment.stdout)
        hosted_passed = self.run_docflow(
            "verify-package",
            "--package",
            "backend",
            "--gate",
            "hosted",
            "--available",
            "browser",
            "--environment",
            "target=local-browser",
            "--execute",
        )
        self.assertEqual(hosted_passed.returncode, 0, hosted_passed.stderr)
        self.assertEqual(
            self.run_docflow("complete-run", "--actor", "orchestrator").returncode, 0
        )

        prepare_and_plan("TASK-EVIDENCE-2", "--supersedes", "TASK-EVIDENCE")
        reused_across_runs = self.run_docflow(
            "verify-package",
            "--package",
            "backend",
            "--gate",
            "unit",
        )
        self.assertEqual(reused_across_runs.returncode, 0, reused_across_runs.stderr)
        self.assertIn("unit: reused", reused_across_runs.stdout)
        second_run = json.loads(
            (self.root / ".document-driven/runs/TASK-EVIDENCE-2/run.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(second_run["supersedes"], ["TASK-EVIDENCE"])

    def test_registered_integrated_worktree_is_safely_garbage_collected(self) -> None:
        self.approved_manifest()
        self.assertEqual(
            self.run_python(
                INSTALLER, "--root", str(self.root), "--ci", "none"
            ).returncode,
            0,
        )
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"], cwd=self.root, check=True
        )
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)
        worktree = Path(tempfile.mkdtemp(prefix="document-driven-gc-"))
        shutil.rmtree(worktree)
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
            )
            registered = self.run_docflow(
                "register-worktree",
                "--path",
                str(worktree),
                "--task-id",
                "TASK-GC",
                "--package",
                "backend",
                "--status",
                "integrated",
            )
            self.assertEqual(registered.returncode, 0, registered.stderr)
            dry_run = self.run_docflow("worktree-gc", "--retention-hours", "0")
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertIn("Eligible", dry_run.stdout)
            applied = self.run_docflow(
                "worktree-gc", "--retention-hours", "0", "--apply"
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertFalse(worktree.exists())
        finally:
            if worktree.exists():
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree)],
                    cwd=self.root,
                    check=False,
                    capture_output=True,
                )


if __name__ == "__main__":
    unittest.main()
