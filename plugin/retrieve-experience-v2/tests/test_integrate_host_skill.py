from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "integrate_host_skill.py"


class IntegrateHostSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.skill_dir = Path(self.tmp.name) / "patent-search"
        self.skill_dir.mkdir()
        self.skill = self.skill_dir / "SKILL.md"
        self.original = "---\nname: patent-search\ndescription: search patents\n---\n\n# Patent search\n\nOriginal workflow.\n"
        self.skill.write_text(self.original, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--skill", str(self.skill), *args],
            capture_output=True, text=True, encoding="utf-8",
        )

    def test_dry_run_does_not_modify_host(self) -> None:
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry run only", result.stdout)
        self.assertEqual(self.skill.read_text(encoding="utf-8"), self.original)

    def test_apply_is_append_only_backed_up_and_idempotent(self) -> None:
        result = self.run_cli("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = self.skill.read_text(encoding="utf-8")
        self.assertTrue(updated.startswith(self.original.rstrip()))
        self.assertEqual(updated.count("retrieve-experience-v2:begin"), 1)
        backups = list((self.skill_dir / ".retrieve-experience-backups").glob("*-SKILL.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), self.original)

        second = self.run_cli("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already integrated", second.stdout)
        self.assertEqual(self.skill.read_text(encoding="utf-8"), updated)

    def test_partial_marker_is_rejected(self) -> None:
        self.skill.write_text(self.original + "\n<!-- retrieve-experience-v2:begin -->\n", encoding="utf-8")
        result = self.run_cli("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only one integration marker", result.stderr)

    def test_dry_run_blocks_a_tool_bound_to_the_wrong_server(self) -> None:
        self.skill.write_text(
            self.original + "\nCall server_a.query_records for a count.\n", encoding="utf-8"
        )
        catalog = Path(self.tmp.name) / "runtime-tools.json"
        catalog.write_text(
            json.dumps({"servers": {"server_a": [], "server_b": ["query_records"]}}),
            encoding="utf-8",
        )

        result = self.run_cli("--catalog", str(catalog), "--apply")

        self.assertEqual(result.returncode, 2)
        self.assertIn("belongs to ['server_b']", result.stdout)
        self.assertNotIn("retrieve-experience-v2:begin", self.skill.read_text(encoding="utf-8"))

    def test_json_dry_run_reports_invisible_instruction_without_writing(self) -> None:
        self.skill.write_text(
            "---\nname: patent-search\ndescription: search patents\n"
            "whenToUse: unavailable then fallback to `other-skill`\n---\n\n# Patent search\n",
            encoding="utf-8",
        )
        before = self.skill.read_bytes()
        result = self.run_cli("--json")
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(any(item["code"] == "FM-1" for item in payload["architecture"]["findings"]))
        self.assertEqual(self.skill.read_bytes(), before)

    def test_identical_sibling_descriptions_block_apply(self) -> None:
        sibling = self.skill_dir.parent / "patent-search-copy"
        sibling.mkdir()
        (sibling / "SKILL.md").write_text(
            "---\nname: patent-search-copy\ndescription: search patents\n---\n\n# Copy\n",
            encoding="utf-8",
        )
        result = self.run_cli("--apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("FM-2", result.stdout)
        self.assertNotIn("retrieve-experience-v2:begin", self.skill.read_text(encoding="utf-8"))

    def test_already_integrated_skill_still_reports_and_honors_blockers(self) -> None:
        first = self.run_cli("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        integrated = self.skill.read_bytes()
        sibling = self.skill_dir.parent / "patent-search-copy"
        sibling.mkdir()
        (sibling / "SKILL.md").write_text(
            "---\nname: patent-search-copy\ndescription: search patents\n---\n\n# Copy\n",
            encoding="utf-8",
        )

        second = self.run_cli("--apply", "--json")

        payload = json.loads(second.stdout)
        self.assertEqual(second.returncode, 2)
        self.assertFalse(payload["changed"])
        self.assertTrue(any(item["code"] == "FM-2" for item in payload["architecture"]["findings"]))
        self.assertEqual(self.skill.read_bytes(), integrated)


if __name__ == "__main__":
    unittest.main()
