from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "expctl.py"
if not SCRIPT.exists():
    SCRIPT = ROOT / "scripts" / "expctl.py"


class AccountingAndAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "memory"
        self.env = {**os.environ, "PYTHONUTF8": "1"}
        self.run_cli("init", "--repo", str(self.repo), "--no-git")
        self.library = self.repo / "检索经验.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=ROOT, env=self.env,
            capture_output=True, text=True, encoding="utf-8",
        )
        if check and result.returncode != 0:
            self.fail(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def memory_cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run_cli("--file", str(self.library), *args, check=check)

    def add_global(self, key: str = "噪声规则", evidence: str = "TACD:(plate AND rolling) -> 12") -> None:
        self.memory_cli(
            "add", "--section", "1", "--key", key,
            "--cols", "板材|排除轧制|全局|实测记录",
            "--scope", "global", "--evidence", evidence, "--no-log",
        )

    def make_run(self) -> Path:
        self.add_global()
        self.memory_cli(
            "add", "--section", "5", "--key", "TACD:(battery)",
            "--cols", "测试领域|10|有效", "--scope", "domain", "--no-log",
        )
        run_path = Path(self.tmp.name) / "run.json"
        self.memory_cli("prefetch", "--domain", "测试领域", "--out", str(run_path))
        return run_path

    def test_global_entry_requires_reproducible_evidence(self) -> None:
        result = self.memory_cli(
            "add", "--section", "1", "--key", "无证据",
            "--cols", "特征|NOT x|全局|某次观察", "--scope", "global", "--no-log",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--evidence", result.stderr)
        self.assertNotIn("无证据", self.library.read_text(encoding="utf-8"))

    def test_negative_global_evidence_requires_falsification_attempt(self) -> None:
        result = self.memory_cli(
            "add", "--section", "2", "--key", "多层 OR 不可用",
            "--cols", "返回空|0|改用其它字段|全局",
            "--scope", "global", "--evidence", "TACD:((A OR B) AND C) -> 0", "--no-log",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--falsified-by", result.stderr)

    def test_global_and_domain_admission_thresholds(self) -> None:
        self.add_global()
        self.memory_cli(
            "add", "--section", "2", "--key", "个案空结果",
            "--cols", "返回空|0|先复核|全局", "--scope", "global",
            "--evidence", "TACD:((A OR B) AND C) -> 0",
            "--falsified-by", "TACD:((A OR D) AND C) -> 18，已排除字段语法缺陷",
            "--no-log",
        )
        self.memory_cli(
            "add", "--section", "5", "--key", "TACD:(domain only)",
            "--cols", "测试领域|3|待复用", "--scope", "domain", "--no-log",
        )

    def test_retract_preserves_history_and_excludes_prefetch(self) -> None:
        self.add_global()
        before = self.library.read_bytes()
        self.memory_cli(
            "retract", "--section", "1", "--key", "噪声规则",
            "--reason", "同构查询返回 12，原结论被推翻",
        )
        payload = json.loads(self.memory_cli("prefetch", "--domain", "测试领域", "--json").stdout)
        self.assertNotIn("噪声规则", [item["key"] for item in payload["records"]])
        self.assertEqual(self.library.read_bytes(), before)
        status = json.loads(self.memory_cli("status", "--json").stdout)
        self.assertEqual(status["retracted"], 1)

    def test_retract_missing_entry_does_not_modify_state(self) -> None:
        before_library = self.library.read_bytes()
        before_maintenance = (self.repo / "maintenance.json").read_bytes()
        result = self.memory_cli(
            "retract", "--section", "2", "--key", "不存在",
            "--reason", "复核推翻", check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.library.read_bytes(), before_library)
        self.assertEqual((self.repo / "maintenance.json").read_bytes(), before_maintenance)

    def test_doctor_warns_for_negative_global_admission(self) -> None:
        self.memory_cli(
            "add", "--section", "2", "--key", "负向观测",
            "--cols", "返回空|0|先复核|全局", "--scope", "global",
            "--evidence", "TACD:(A AND B) -> 0",
            "--falsified-by", "TACD:(A AND C) -> 9，排除字段故障", "--no-log",
        )
        findings = json.loads(self.memory_cli("doctor", "--json").stdout)
        self.assertTrue(any(item["type"] == "knowledge admission" for item in findings))

    def test_finish_run_rejects_prefetch_count_mismatch(self) -> None:
        run_path = self.make_run()
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["prefetched"] += 1
        run_path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        result = self.memory_cli("finish-run", "--run", str(run_path), check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("预取总数", result.stderr)

    def test_finish_run_rejects_offsetting_section_errors(self) -> None:
        run_path = self.make_run()
        run = json.loads(run_path.read_text(encoding="utf-8"))
        authoritative = run["prefetched_by_section"]
        run["prefetched_by_section"] = {"0": authoritative["1"], "5": authoritative["5"]}
        run_path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        result = self.memory_cli("finish-run", "--run", str(run_path), check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("总数相同但分节不一致", result.stderr)

    def test_finish_run_rejects_duplicate_and_unknown_used_ids(self) -> None:
        run_path = self.make_run()
        run = json.loads(run_path.read_text(encoding="utf-8"))
        known = run["records"][0]["id"]
        run["used_ids"] = [known, known, "exp-missing"]
        run_path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        result = self.memory_cli("finish-run", "--run", str(run_path), check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("重复", result.stderr)
        self.assertIn("未预取", result.stderr)

    def test_valid_run_is_independently_recomputed(self) -> None:
        run_path = self.make_run()
        used_id = json.loads(run_path.read_text(encoding="utf-8"))["records"][0]["id"]
        self.memory_cli("mark-used", "--run", str(run_path), "--id", used_id)
        self.memory_cli("finish-run", "--run", str(run_path))
        result = self.memory_cli("verify-run", "--run", str(run_path), "--json")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["authoritative_metrics"]["reused"], 1)

    def test_artifact_validation_finds_ragged_and_unregistered_files(self) -> None:
        run_path = self.make_run()
        output = Path(self.tmp.name) / "output"
        output.mkdir()
        (output / "matrix.csv").write_text("id,title\n1,ok\n2,bad,extra\n", encoding="utf-8")
        (output / "temp.py").write_text("print('repair')\n", encoding="utf-8")
        result = self.memory_cli(
            "finish-run", "--run", str(run_path), "--artifacts-dir", str(output),
            "--artifact", "candidate_matrix=matrix.csv", check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("列数", result.stderr)
        self.assertIn("未登记", result.stderr)

    def test_verify_run_compares_report_claims_to_artifacts(self) -> None:
        run_path = self.make_run()
        output = Path(self.tmp.name) / "output"
        output.mkdir()
        (output / "matrix.csv").write_text("id,title\n1,a\n2,b\n", encoding="utf-8")
        self.memory_cli(
            "finish-run", "--run", str(run_path), "--artifacts-dir", str(output),
            "--artifact", "candidate_matrix=matrix.csv",
        )
        report = Path(self.tmp.name) / "audit.json"
        report.write_text(json.dumps({"candidate_count": 45}), encoding="utf-8")
        result = self.memory_cli(
            "verify-run", "--run", str(run_path), "--report", str(report), "--json",
            check=False,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any("候选数" in item for item in payload["problems"]))

    def test_utf16_run_record_gets_actionable_error(self) -> None:
        run_path = Path(self.tmp.name) / "utf16.json"
        run_path.write_text("{}", encoding="utf-16")
        result = self.memory_cli("verify-run", "--run", str(run_path), check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("UTF-16", result.stderr)
        self.assertIn("--out", result.stderr)

    def test_plugin_written_run_is_strict_utf8_without_bom(self) -> None:
        run_path = self.make_run()
        raw = run_path.read_bytes()
        self.assertFalse(raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")))
        raw.decode("utf-8", errors="strict")


if __name__ == "__main__":
    unittest.main()
