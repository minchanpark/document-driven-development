from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SELF_TEST = PLUGIN_ROOT / "scripts/provider-self-test.mjs"


class ProviderRunnerTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for provider adapter tests")
    def test_provider_contracts_and_package_lock(self) -> None:
        completed = subprocess.run(
            ["node", str(SELF_TEST)],
            cwd=PLUGIN_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertEqual(json.loads(completed.stdout)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
