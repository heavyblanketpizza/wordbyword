from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class PackageImportHygieneTest(unittest.TestCase):
    def test_top_level_import_does_not_load_heavy_workflow_modules(self) -> None:
        script = "\n".join(
            [
                "import sys",
                "import wordbyword",
                "print('wordbyword.classroom_printout_studio.class_rewriting.rewrite' in sys.modules)",
                "print('kiwipiepy' in sys.modules)",
                "print(wordbyword.__version__)",
            ]
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=True,
            text=True,
        )

        self.assertEqual(result.stdout.splitlines(), ["False", "False", "0.1.0"])

    def test_top_level_lazy_export_still_resolves_existing_symbol(self) -> None:
        script = "\n".join(
            [
                "import sys",
                "from wordbyword import ChatMessage",
                "print(ChatMessage(role='user', content='hello').role)",
                "print('wordbyword.classroom_printout_studio.class_rewriting.rewrite' in sys.modules)",
            ]
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=True,
            text=True,
        )

        self.assertEqual(result.stdout.splitlines(), ["user", "False"])
