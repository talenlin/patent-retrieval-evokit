from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "expctl.py"
if not SCRIPT.exists():
    SCRIPT = ROOT / "scripts" / "expctl.py"


class ExpctlCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "memory"
        self.env = {**os.environ, "PYTHONUTF8": "1"}
        self.run_cli("init", "--repo", str(self.repo), "--no-git")
        self.library = self.repo / "检索经验.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(
        self, *args: str, check: bool = True, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=ROOT,
            env=env or self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if check and result.returncode != 0:
            self.fail(
                f"command failed ({result.returncode}): {args}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def memory_cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run_cli("--file", str(self.library), *args, check=check)

    def test_updating_one_field_preserves_unspecified_existing_fields(self) -> None:
        key = "金属加工噪声"
        self.memory_cli(
            "add",
            "--section",
            "1",
            "--key",
            key,
            "--cols",
            "板材与轧制|NOT (轧制 OR 冲压)|钠离子电池|Top20 全为金属加工||2026-09-13",
            "--date",
            "2026-09-12",
            "--scope", "global", "--evidence", "TACD:(metal AND rolling) -> 20",
            "--no-log",
        )

        self.memory_cli(
            "add",
            "--section",
            "1",
            "--key",
            key,
            "--cols",
            "新的判别特征",
            "--date",
            "2026-09-14",
            "--scope", "global", "--evidence", "TACD:(metal AND rolling) -> 20",
            "--no-log",
        )

        row = next(line for line in self.library.read_text(encoding="utf-8").splitlines() if key in line)
        self.assertIn("新的判别特征", row)
        self.assertIn("NOT (轧制 OR 冲压)", row)
        self.assertIn("钠离子电池", row)
        self.assertIn("Top20 全为金属加工", row)
        self.assertIn("2026-09-14", row)

    def test_prefetch_json_returns_complete_records_with_stable_ids(self) -> None:
        query = "TACD:(sodium ion battery AND cathode material AND layered oxide AND surface coating)"
        self.memory_cli(
            "add", "--section", "1", "--key", "金属加工噪声",
            "--cols", "板材与轧制|NOT (轧制 OR 冲压)|钠离子电池|Top20 全为金属加工",
            "--scope", "global", "--evidence", "TACD:(metal AND rolling) -> 20",
            "--no-log",
        )
        self.memory_cli(
            "add", "--section", "5", "--key", query,
            "--cols", "钠离子电池|100-300|高信噪比",
            "--no-log",
        )

        result = self.memory_cli("prefetch", "--domain", "钠离子电池", "--json")
        payload = json.loads(result.stdout)
        records = payload["records"]

        noise = next(record for record in records if record["section"] == 1)
        reusable = next(record for record in records if record["section"] == 5)
        self.assertEqual(noise["fields"]["去噪手段（NOT 组）"], "NOT (轧制 OR 冲压)")
        self.assertEqual(noise["fields"]["实测证据"], "Top20 全为金属加工")
        self.assertEqual(reusable["key"], query)
        self.assertTrue(noise["id"].startswith("exp-"))
        self.assertNotEqual(noise["id"], reusable["id"])

    def test_prune_runs_on_a_cp936_windows_console(self) -> None:
        self.memory_cli(
            "add", "--section", "5", "--key", "TACD:(legacy query)",
            "--cols", "钠离子电池|100-300|高信噪比",
            "--date", "2025-01-01", "--no-log",
        )
        cp936_env = {**self.env, "PYTHONIOENCODING": "cp936"}

        result = subprocess.run(
            [
                sys.executable, str(SCRIPT), "--file", str(self.library),
                "prune", "--days", "90", "--apply",
            ],
            cwd=ROOT, env=cp936_env, capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", errors="replace"))
        status = json.loads(self.memory_cli("status", "--json").stdout)
        self.assertEqual(status["prune_candidates"], 1)

    def test_recent_confirmation_keeps_old_prior_art_active(self) -> None:
        self.memory_cli(
            "add", "--section", "4", "--domain", "钠离子电池", "--key", "CN123456789A",
            "--cols", "申请人|技术要点|A,B|S级||2026-09-14|有效",
            "--date", "2025-01-01", "--no-log",
        )

        self.memory_cli("prune", "--days", "90", "--apply")

        row = next(
            line for line in self.library.read_text(encoding="utf-8").splitlines()
            if "CN123456789A" in line
        )
        self.assertNotIn("待重验", row)

    def test_run_artifact_tracks_actual_reuse_idempotently(self) -> None:
        self.memory_cli(
            "add", "--section", "1", "--key", "金属加工噪声",
            "--cols", "板材与轧制|NOT (轧制 OR 冲压)|钠离子电池|Top20 全为金属加工",
            "--scope", "global", "--evidence", "TACD:(metal AND rolling) -> 20",
            "--no-log",
        )
        self.memory_cli(
            "add", "--section", "5", "--key", "TACD:(sodium ion battery)",
            "--cols", "钠离子电池|100-300|高信噪比", "--no-log",
        )
        run_file = Path(self.tmp.name) / "prior_memory.json"

        self.memory_cli(
            "prefetch", "--domain", "钠离子电池", "--out", str(run_file)
        )
        run = json.loads(run_file.read_text(encoding="utf-8"))
        used_id = run["records"][0]["id"]
        self.memory_cli("mark-used", "--run", str(run_file), "--id", used_id)
        self.memory_cli("mark-used", "--run", str(run_file), "--id", used_id)
        result = self.memory_cli("finish-run", "--run", str(run_file), "--added", "2")

        finished = json.loads(run_file.read_text(encoding="utf-8"))
        self.assertEqual(finished["used_ids"], [used_id])
        self.assertEqual(finished["metrics"]["prefetched"], 2)
        self.assertEqual(finished["metrics"]["reused"], 1)
        self.assertEqual(finished["metrics"]["reuse_rate_percent"], 50.0)
        self.assertIn("50.0%", result.stdout)

        row = next(
            line for line in self.library.read_text(encoding="utf-8").splitlines()
            if "金属加工噪声" in line
        )
        self.assertTrue(row.rstrip().endswith("1 |"))

    def test_prior_art_is_written_to_the_requested_domain_table(self) -> None:
        self.memory_cli(
            "add", "--section", "4", "--domain", "领域A", "--key", "CN-A",
            "--cols", "申请人A|技术A|A|S级", "--no-log",
        )
        self.memory_cli(
            "add", "--section", "4", "--domain", "领域B", "--key", "CN-B",
            "--cols", "申请人B|技术B|B|A级", "--no-log",
        )

        result_a = self.memory_cli("prefetch", "--domain", "领域A", "--json")
        result_b = self.memory_cli("prefetch", "--domain", "领域B", "--json")
        keys_a = [r["key"] for r in json.loads(result_a.stdout)["records"] if r["section"] == 4]
        keys_b = [r["key"] for r in json.loads(result_b.stdout)["records"] if r["section"] == 4]
        self.assertEqual(keys_a, ["CN-A"])
        self.assertEqual(keys_b, ["CN-B"])

    def test_commit_stages_only_managed_library_files(self) -> None:
        secure_repo = Path(self.tmp.name) / "secure-memory"
        self.run_cli("init", "--repo", str(secure_repo))
        library = secure_repo / "检索经验.md"
        untrusted = secure_repo / "case-secret.txt"
        untrusted.write_text("confidential case material", encoding="utf-8")
        (secure_repo / "tools.local.json").write_text("{}", encoding="utf-8")
        (secure_repo / "runtime-tools.json").write_text("{}", encoding="utf-8")
        self.run_cli(
            "--file", str(library), "add", "--section", "1", "--key", "噪声",
            "--cols", "特征|NOT x|领域|证据",
            "--scope", "global", "--evidence", "TACD:(noise) -> 10", "--no-log",
        )

        self.run_cli("--file", str(library), "commit", "--no-push")
        tracked = subprocess.run(
            ["git", "-c", "core.quotepath=false", "-C", str(secure_repo), "ls-files"],
            env=self.env, capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout.splitlines()

        self.assertNotIn(untrusted.name, tracked)
        self.assertNotIn("tools.local.json", tracked)
        self.assertNotIn("runtime-tools.json", tracked)
        self.assertIn("检索经验.md", tracked)

    def test_prune_candidate_state_does_not_change_the_deduplication_key(self) -> None:
        query = "TACD:(stable reusable query)"
        self.memory_cli(
            "add", "--section", "5", "--key", query,
            "--cols", "钠离子电池|100-300|高信噪比",
            "--date", "2025-01-01", "--no-log",
        )

        self.memory_cli("prune", "--days", "90", "--apply")
        after_prune = self.library.read_text(encoding="utf-8")
        self.assertEqual(after_prune.count(query), 1)
        self.assertNotIn(f"{query} ✂️ 剪枝候选", after_prune)
        status = json.loads(self.memory_cli("status", "--json").stdout)
        self.assertEqual(status["prune_candidates"], 1)

        self.memory_cli(
            "add", "--section", "5", "--key", query,
            "--cols", "钠离子电池|50-100|更新后的高信噪比",
            "--no-log",
        )
        final_status = json.loads(self.memory_cli("status", "--json").stdout)
        self.assertEqual(final_status["by_section"]["5"], 1)

    def test_validate_rejects_a_bad_header_in_any_domain_table(self) -> None:
        self.memory_cli(
            "add", "--section", "4", "--domain", "领域B", "--key", "CN-B",
            "--cols", "申请人B|技术B|B|A级", "--no-log",
        )
        text = self.library.read_text(encoding="utf-8")
        heading = "### 领域B"
        before, after = text.split(heading, 1)
        after = after.replace("| 公开号 |", "| 错误公开号 |", 1)
        self.library.write_text(before + heading + after, encoding="utf-8")

        result = self.memory_cli("validate", "--json", check=False)
        payload = json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("第 2 张表" in problem for problem in payload["problems"]))

    def test_tool_mapping_is_validated_and_exposed(self) -> None:
        findings = json.loads(self.memory_cli("doctor", "--json").stdout)
        self.assertTrue(any(item["type"] == "tool mapping" for item in findings))

        tools_file = self.repo / "tools.json"
        tools_file.write_text(
            json.dumps(
                {
                    "backend": "legacy-provider",
                    "capabilities": {
                        "search": "legacy_search",
                        "count": None,
                        "claims": "legacy_claims",
                        "description": None,
                        "bibliography": None,
                        "family": None,
                        "abstract_translated": None,
                    },
                    "notes": "count comes from the search response",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        payload = json.loads(self.memory_cli("tools", "--json").stdout)
        self.assertEqual(payload["backend"], "legacy-provider")
        self.assertEqual(payload["capabilities"]["search"], "legacy_search")
        findings = json.loads(self.memory_cli("doctor", "--json").stdout)
        self.assertFalse(any(item["type"] == "tool mapping" for item in findings))
        self.assertTrue(any(item["type"] == "tool mapping migration" for item in findings))

    def test_server_aware_tool_mapping_is_checked_against_runtime_catalog(self) -> None:
        tools_file = self.repo / "tools.json"
        catalog_file = self.repo / "runtime-tools.json"
        tools_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "provider_label": "local patent provider",
                    "capabilities": {
                        "search": {
                            "mode": "direct_tool",
                            "server": "patent_core",
                            "tool": "query_records",
                        },
                        "count": {
                            "mode": "derived_from_search",
                            "server": "patent_core",
                            "tool": "query_records",
                            "fixed_arguments": {"page_size": 1, "start": 0},
                            "result_path": "data.total",
                        },
                        "claims": None,
                        "description": None,
                        "bibliography": None,
                        "family": None,
                        "abstract_translated": None,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        catalog_file.write_text(
            json.dumps({"schema_version": 1, "servers": {"patent_core": ["query_records"]}}),
            encoding="utf-8",
        )

        result = self.memory_cli("tools", "--catalog", str(catalog_file), "--json")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["_validation"]["runtime_catalog_checked"])
        self.assertEqual(payload["_validation"]["warnings"], [])
        self.assertEqual(payload["capabilities"]["count"]["mode"], "derived_from_search")

    def test_tool_in_another_server_is_a_namespace_error(self) -> None:
        tools_file = self.repo / "tools.json"
        catalog_file = self.repo / "runtime-tools.json"
        capabilities = {name: None for name in (
            "search", "count", "claims", "description", "bibliography", "family",
            "abstract_translated",
        )}
        capabilities["search"] = {
            "mode": "direct_tool", "server": "server_a", "tool": "query_records"
        }
        tools_file.write_text(
            json.dumps({"schema_version": 2, "capabilities": capabilities}), encoding="utf-8"
        )
        catalog_file.write_text(
            json.dumps({"schema_version": 1, "servers": {"server_a": [], "server_b": ["query_records"]}}),
            encoding="utf-8",
        )

        result = self.memory_cli(
            "tools", "--catalog", str(catalog_file), "--json", check=False
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("工具 query_records 存在", result.stderr)
        self.assertIn("不是配置的 server_a", result.stderr)

    def test_derived_count_must_use_the_mapped_search_tool(self) -> None:
        capabilities = {name: None for name in (
            "search", "count", "claims", "description", "bibliography", "family",
            "abstract_translated",
        )}
        capabilities["search"] = {
            "mode": "direct_tool", "server": "server_a", "tool": "query_records"
        }
        capabilities["count"] = {
            "mode": "derived_from_search", "server": "server_a", "tool": "other_query",
            "fixed_arguments": {"page_size": 1}, "result_path": "data.total",
        }
        (self.repo / "tools.json").write_text(
            json.dumps({"schema_version": 2, "capabilities": capabilities}), encoding="utf-8"
        )

        result = self.memory_cli("tools", "--json", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("必须与 search 完全一致", result.stderr)

    def test_mapping_prompt_uses_abstract_capabilities_not_vendor_tool_names(self) -> None:
        result = self.memory_cli("mapping-prompt")
        self.assertIn("server 和 tool 两层身份", result.stdout)
        self.assertIn("derived_from_search", result.stdout)
        self.assertIn("tools.local.json", result.stdout)
        self.assertNotIn("search_" + "patent_count", result.stdout)
        self.assertNotIn("global_core_" + "patent_database", result.stdout)

    def test_machine_local_mapping_overrides_shared_template(self) -> None:
        (self.repo / "tools.local.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "provider_label": "this machine",
                    "capabilities": {
                        "search": {"mode": "direct_tool", "server": "local", "tool": "search"},
                        "count": None,
                        "claims": None,
                        "description": None,
                        "bibliography": None,
                        "family": None,
                        "abstract_translated": None,
                    },
                }
            ),
            encoding="utf-8",
        )

        payload = json.loads(self.memory_cli("tools", "--json").stdout)

        self.assertEqual(payload["provider_label"], "this machine")
        self.assertTrue(payload["_validation"]["mapping_file"].endswith("tools.local.json"))

    def test_markdown_pipe_in_a_value_round_trips_without_column_corruption(self) -> None:
        query = r"TACD:((sodium | natrium) AND C:\\claims)"
        self.memory_cli(
            "add", "--section", "5", "--key", query,
            "--cols", r"钠离子电池|10-20|结果 A\|B 均为高信噪比", "--no-log",
        )

        validation = self.memory_cli("validate", "--json", check=False)
        self.assertEqual(validation.returncode, 0, validation.stdout)
        payload = json.loads(
            self.memory_cli("prefetch", "--domain", "钠离子电池", "--json").stdout
        )
        record = next(r for r in payload["records"] if r["section"] == 5)
        self.assertEqual(record["key"], query)
        self.assertEqual(record["fields"]["评价"], "结果 A|B 均为高信噪比")

    def test_stale_revision_is_rejected_instead_of_overwriting_newer_data(self) -> None:
        status = json.loads(self.memory_cli("status", "--json").stdout)
        revision = status["revision"]
        self.memory_cli(
            "add", "--section", "1", "--key", "先到的写入",
            "--cols", "特征|NOT x|领域|证据",
            "--scope", "global", "--evidence", "TACD:(first) -> 10", "--no-log",
        )

        result = self.memory_cli(
            "add", "--section", "1", "--key", "过期客户端写入",
            "--cols", "特征|NOT y|领域|证据", "--no-log",
            "--scope", "global", "--evidence", "TACD:(stale) -> 10",
            "--expected-revision", revision, check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("版本已变化", result.stderr)
        self.assertNotIn("过期客户端写入", self.library.read_text(encoding="utf-8"))

    def test_doctor_discovers_codex_skills_and_uses_neutral_cache(self) -> None:
        fake_home = Path(self.tmp.name) / "home"
        skill_dir = fake_home / ".codex" / "skills" / "patsnap-search"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: patsnap-search\ndescription: test\n---\n", encoding="utf-8"
        )
        env = {
            **self.env,
            "USERPROFILE": str(fake_home),
            "HOME": str(fake_home),
            "EXPCTL_SKILLS_DIR": "",
        }

        result = self.run_cli(
            "--file", str(self.library), "doctor", "--json", env=env
        )
        findings = json.loads(result.stdout)
        self.assertTrue(any(item.get("skill") == "patsnap-search" for item in findings))

        self.run_cli(
            "--file", str(self.library), "prefetch", "--domain", "测试领域", env=env
        )
        self.assertTrue((fake_home / ".cache" / "expctl" / "doctor.json").exists())
        self.assertFalse((fake_home / ".dsh" / "cache" / "expctl-doctor.json").exists())

    def test_doctor_can_include_skill_architecture_findings(self) -> None:
        skills_root = Path(self.tmp.name) / "skills"
        for name in ("search-one", "search-two"):
            directory = skills_root / name
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: identical search skill\n---\n",
                encoding="utf-8",
            )

        result = self.memory_cli("doctor", "--skills-root", str(skills_root), "--json")
        findings = json.loads(result.stdout)
        architecture = [item for item in findings if item["type"] == "skill architecture"]
        self.assertTrue(any(item["details"]["code"] == "FM-2" for item in architecture))

    def test_add_rejects_incomplete_or_shifted_new_records(self) -> None:
        missing_evidence = self.memory_cli(
            "add", "--section", "1", "--key", "无证据噪声",
            "--cols", "判别特征|NOT x|领域",
            "--scope", "global", "--evidence", "TACD:(noise) -> 10",
            "--no-log", check=False,
        )
        shifted_domain_profile = self.memory_cli(
            "add", "--section", "6", "--key", "钠离子电池",
            "--cols", "误传的领域|同义词|申请人|IPC|地形|陷阱|多余值",
            "--no-log", check=False,
        )

        self.assertNotEqual(missing_evidence.returncode, 0)
        self.assertIn("缺少必填字段", missing_evidence.stderr)
        self.assertNotEqual(shifted_domain_profile.returncode, 0)
        self.assertIn("--cols 最多", shifted_domain_profile.stderr)
        text = self.library.read_text(encoding="utf-8")
        self.assertNotIn("无证据噪声", text)
        self.assertNotIn("误传的领域", text)

    def test_finish_run_reports_whether_reused_experience_was_confirmed(self) -> None:
        self.memory_cli(
            "add", "--section", "1", "--key", "可验证噪声",
            "--cols", "判别特征|NOT x|测试领域|历史证据",
            "--scope", "global", "--evidence", "TACD:(verified noise) -> 20", "--no-log",
        )
        run_file = Path(self.tmp.name) / "quality-run.json"
        self.memory_cli("prefetch", "--domain", "测试领域", "--out", str(run_file))
        run = json.loads(run_file.read_text(encoding="utf-8"))
        used_id = run["records"][0]["id"]
        self.memory_cli("mark-used", "--run", str(run_file), "--id", used_id)

        self.memory_cli(
            "mark-outcome", "--run", str(run_file), "--id", used_id,
            "--result", "confirmed", "--note", "本次 Top20 再次验证",
        )
        result = self.memory_cli("finish-run", "--run", str(run_file))

        finished = json.loads(run_file.read_text(encoding="utf-8"))
        self.assertEqual(finished["metrics"]["confirmed_reuse"], 1)
        self.assertEqual(finished["metrics"]["rejected_reuse"], 0)
        self.assertEqual(finished["metrics"]["reuse_confirmation_rate_percent"], 100.0)
        self.assertIn("确认 1", result.stdout)
        self.assertIn("推翻 0", result.stdout)
        self.assertIn("确认率 100.0%", result.stdout)

    def test_mark_used_rejects_a_run_based_on_an_old_library_revision(self) -> None:
        self.memory_cli(
            "add", "--section", "1", "--key", "原经验",
            "--cols", "特征|NOT x|测试领域|证据",
            "--scope", "global", "--evidence", "TACD:(original) -> 10", "--no-log",
        )
        run_file = Path(self.tmp.name) / "stale-run.json"
        self.memory_cli("prefetch", "--domain", "测试领域", "--out", str(run_file))
        used_id = json.loads(run_file.read_text(encoding="utf-8"))["records"][0]["id"]
        self.memory_cli(
            "add", "--section", "1", "--key", "并发新增经验",
            "--cols", "特征|NOT y|测试领域|证据",
            "--scope", "global", "--evidence", "TACD:(concurrent) -> 11", "--no-log",
        )

        result = self.memory_cli(
            "mark-used", "--run", str(run_file), "--id", used_id, check=False
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("版本已变化", result.stderr)
        self.assertIn("并发新增经验", self.library.read_text(encoding="utf-8"))

    def test_human_prefetch_output_contains_complete_selected_records(self) -> None:
        query = "TACD:(sodium ion battery AND cathode material AND layered oxide AND surface coating)"
        not_group = "NOT (rolling OR stamping OR welding)"
        evidence = "探测式命中 32000 件且 Top20 全为金属加工"
        self.memory_cli(
            "add", "--section", "1", "--key", "金属加工噪声",
            "--cols", f"板材与轧制|{not_group}|测试领域|{evidence}",
            "--scope", "global", "--evidence", "TACD:(metal AND rolling) -> 32000", "--no-log",
        )
        self.memory_cli(
            "add", "--section", "5", "--key", query,
            "--cols", "测试领域|100-300|高信噪比", "--no-log",
        )

        output = self.memory_cli("prefetch", "--domain", "测试领域").stdout

        self.assertIn(query, output)
        self.assertIn(not_group, output)
        self.assertIn(evidence, output)


if __name__ == "__main__":
    unittest.main()
