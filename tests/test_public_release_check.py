from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "public_release_check.py"
SPEC = importlib.util.spec_from_file_location("public_release_check", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PublicReleaseCheckTests(unittest.TestCase):
    def test_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "README.md").write_text("fictional public example\n", encoding="utf-8")
            self.assertEqual([], MODULE.scan(root))

    def test_machine_local_mapping_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "tools.local.json").write_text("{}\n", encoding="utf-8")
            self.assertIn("forbidden-file", {item["rule"] for item in MODULE.scan(root)})

    def test_user_path_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            private_path = "C:" + "\\Users\\example-person\\private\\file.txt"
            (root / "note.md").write_text(private_path, encoding="utf-8")
            self.assertIn("windows-user-path", {item["rule"] for item in MODULE.scan(root)})

    def test_real_library_and_run_artifact_names_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "检索经验.md").write_text("private\n", encoding="utf-8")
            (root / "case-run-001.json").write_text("{}\n", encoding="utf-8")
            rules = {item["rule"] for item in MODULE.scan(root)}
            self.assertIn("runtime-or-experience-artifact", rules)

    def test_old_repository_reference_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            old_name = "https://github.com/talenlin/" + "Retrieve-experience" + "-v2.git"
            (root / "note.md").write_text(old_name, encoding="utf-8")
            self.assertIn("old-private-repository-url", {item["rule"] for item in MODULE.scan(root)})

    def test_legacy_public_identifier_and_path_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            legacy = "retrieve-experience" + "-v2"
            folder = root / legacy
            folder.mkdir()
            (folder / "note.md").write_text(f"install {legacy}\n", encoding="utf-8")
            rules = {item["rule"] for item in MODULE.scan(root)}
            self.assertIn("legacy-public-path", rules)
            self.assertIn("legacy-public-identifier", rules)

    def test_dist_and_git_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for folder in ("dist", ".git"):
                target = root / folder
                target.mkdir()
                (target / "tools.local.json").write_text("{}\n", encoding="utf-8")
            self.assertEqual([], MODULE.scan(root))


if __name__ == "__main__":
    unittest.main()
