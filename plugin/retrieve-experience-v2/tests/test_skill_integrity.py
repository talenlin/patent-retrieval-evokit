from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_skill_integrity.py"
SPEC = importlib.util.spec_from_file_location("integrity", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class SkillIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "skills"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def add_skill(self, name: str, description: str, body: str = "# Skill\n", extra: str = "") -> Path:
        directory = self.root / name
        directory.mkdir()
        path = directory / "SKILL.md"
        path.write_text(
            f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n{body}", encoding="utf-8"
        )
        return path

    def codes(self) -> list[str]:
        return [item["code"] for item in MODULE.scan(self.root)["findings"]]

    def test_identical_descriptions_are_blocked(self) -> None:
        self.add_skill("patent-one", "search patents v3")
        self.add_skill("patent-two", "search patents v3")
        report = MODULE.scan(self.root)
        item = next(item for item in report["findings"] if item["code"] == "FM-2")
        self.assertEqual(item["level"], "blocker")

    def test_prefix_above_80_percent_warns_but_short_prefix_does_not(self) -> None:
        base = "A" * 90
        self.add_skill("one", base + " first")
        self.add_skill("two", base + " second")
        self.add_skill("three", "Different purpose entirely")
        pairs = [item["skills"] for item in MODULE.scan(self.root)["findings"] if item["code"] == "FM-2"]
        self.assertEqual(pairs, [["one", "two"]])

    def test_invisible_when_to_use_directive_warns(self) -> None:
        self.add_skill("one", "one", extra="whenToUse: 失败时退回 `other-skill`\n")
        self.assertIn("FM-1", self.codes())

    def test_dangling_reference_is_reported_without_writing(self) -> None:
        path = self.add_skill("one", "one", body="请交给 `missing-skill`。\n")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        report = MODULE.scan(self.root)
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertTrue(any(item["code"] == "FM-4" and item["token"] == "missing-skill" for item in report["findings"]))
        self.assertEqual(before, after)

    def test_allowlisted_and_weak_context_tokens_are_ignored(self) -> None:
        self.add_skill("one", "one", body="使用 `python-docx`。CSS 使用 `data-path`。\n")
        self.assertNotIn("FM-4", self.codes())

    def test_fallback_to_another_skill_warns(self) -> None:
        self.add_skill("one", "one", body="若不可用则退回 `other-skill`。\n")
        self.assertIn("FM-3", self.codes())

    def test_json_cli_has_stable_summary_and_nonzero_on_problem(self) -> None:
        self.add_skill("one", "same")
        self.add_skill("two", "same")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--skills-root", str(self.root), "--json"],
            capture_output=True, text=True, encoding="utf-8",
        )
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["skill_count"], 2)
        self.assertIn("blocker", payload["summary"])

    def test_archive_container_not_directly_discovered_is_info(self) -> None:
        archive = self.root / "skills-archive" / "old-skill"
        archive.mkdir(parents=True)
        (archive / "SKILL.md").write_text(
            "---\nname: old-skill\ndescription: old\n---\n", encoding="utf-8"
        )
        item = next(item for item in MODULE.scan(self.root)["findings"] if item["code"] == "FM-5")
        self.assertEqual(item["level"], "info")

    def test_archive_named_skill_still_discovered_is_blocker(self) -> None:
        self.add_skill("legacy-disabled", "disabled skill")
        item = next(item for item in MODULE.scan(self.root)["findings"] if item["code"] == "FM-5")
        self.assertEqual(item["level"], "blocker")


if __name__ == "__main__":
    unittest.main()
