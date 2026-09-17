#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""expctl — 专利检索自进化库的维护工具（公开版 v1.0）

配套文档：专利检索自进化库-SPEC.md
设计约束：**仅用 Python 标准库**，零 pip 依赖，可在 Windows / macOS / Linux 直接运行。

快速开始
--------
  # 1) 新建经验库（建目录 + git + 模板文件；加 --no-git 则只建文件）
  python expctl.py init --repo "/path/to/检索经验库"

  # 2) 检索前精确预取（--domain 传本次真实技术领域；下例为中性示例）
  python expctl.py --file "/path/to/检索经验库/检索经验.md" prefetch --domain "钠离子电池正极材料"

  # 3) 检索后写回 / 校验 / 提交（下例为中性示例，请换成你的真实结果）
  python expctl.py --file "..." add --section 1 --key "金属板材" --cols "判别特征|NOT 组|钠离子电池正极材料|实测证据"
  python expctl.py --file "..." validate
  python expctl.py --file "..." commit -m "检索: 某案 — 新增 1 条"

完整命令列表见 `python expctl.py --help`。
"""
from __future__ import annotations

import argparse
import csv
import contextlib
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import uuid


def _configure_stdio() -> None:
    """Avoid crashes when a legacy Windows console cannot encode status symbols."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


_configure_stdio()

TODAY = _dt.date.today().isoformat()
VER = "1.0.0"

# ============================================================ 路径解析
# 优先级：--file 显式指定 > 环境变量 > 脚本旁的 检索经验库/ > 当前目录
def _default_file() -> pathlib.Path:
    env = os.environ.get("EXPCTL_FILE")
    if env:
        return pathlib.Path(env).expanduser()
    here = pathlib.Path(__file__).resolve().parent
    for cand in (here / "检索经验库" / "检索经验.md",
                 here.parent / "检索经验库" / "检索经验.md",
                 pathlib.Path.cwd() / "检索经验库" / "检索经验.md"):
        if cand.exists():
            return cand
    return here / "检索经验库" / "检索经验.md"


DEFAULT_FILE = _default_file()
DOCTOR_CACHE = ".expctl-doctor.json"

# 会让 skill 名"过时"的后端/工具标记。必须是**独立词**（用词边界匹配），
# 否则 "epo" 会命中 "report"、"qcc" 会命中任意含它的词 —— 假阳性。
# 同一后端的不同叫法都要收录（如 zhihuiya 的商业名与某些部署里的后端别名），
# 否则该部署下"名字绑死后端"的 skill 会漏检。
BACKEND_MARKERS = ["patsnap", "zhihuiya", "himmpat", "epo-ops", "sorftime", "serper",
                   "wipo-pearl", "uspto", "bigquery"]
MANAGED_REPO_FILES = ["检索经验.md", "domains.json", "tools.json", "maintenance.json",
                      ".gitattributes", ".gitignore"]
TOOL_CAPABILITIES = ["search", "count", "claims", "description", "bibliography", "family",
                     "abstract_translated"]
TOOL_MODES = {"direct_tool", "derived_from_search"}
REQUIRED_NEW_FIELDS = {
    1: ["判别特征", "去噪手段（NOT 组）", "领域", "实测证据"],
    2: ["为什么无效", "实测命中", "替代方案", "领域"],
    4: ["申请人", "技术要点", "覆盖特征", "相关度"],
    5: ["领域", "命中量级", "评价"],
}

# ============================================================ 表结构定义
# 逐字节规范见 SPEC §4.1。表头必须与此完全一致（validate 会校验）。
SECTIONS = {
    1: {
        "title": "噪声词典（全局适用）",
        "columns": ["噪声类别", "判别特征", "去噪手段（NOT 组）", "领域",
                    "实测证据", "记录日期", "最后确认", "复用次数"],
        "key": "噪声类别",
        "updatable": ["判别特征", "去噪手段（NOT 组）", "实测证据", "最后确认"],
    },
    2: {
        "title": "检索式禁忌（全局适用）",
        "columns": ["写法", "为什么无效", "实测命中", "替代方案", "领域",
                    "记录日期", "最后确认"],
        "key": "写法",
        "updatable": ["为什么无效", "实测命中", "替代方案", "最后确认"],
    },
    3: {
        "title": "术语对照 / 多语言词表",
        # NOTE: 第 7 列表头按后端命名（zhihuiya / HimmPat / epo-ops / 后端实测）。
        # validate 只校验该列"以『实测』结尾"且顺序正确，不锁定具体品牌名。
        "columns": ["要素（中文）", "标准名词", "领域", "日文", "韩文", "德文",
                    "zhihuiya 实测", "记录日期", "最后确认"],
        "key": "要素（中文）",
        "updatable": ["标准名词", "领域", "日文", "韩文", "德文", "zhihuiya 实测", "最后确认"],
    },
    4: {
        "title": "领域在先技术索引",
        # 领域由图下的 ### 子标题承载，不是每行一列（新增同领域条目时不必重复填标签）
        "columns": ["公开号", "申请人", "技术要点", "覆盖特征", "相关度",
                    "首次发现", "最后确认", "状态"],
        "key": "公开号",
        "updatable": ["申请人", "技术要点", "覆盖特征", "相关度", "最后确认"],
    },
    5: {
        "title": "可复用检索式",
        "columns": ["检索式", "领域", "命中量级", "评价", "记录日期", "最后确认", "复用次数"],
        "key": "检索式",
        "updatable": ["领域", "命中量级", "评价", "最后确认"],
    },
    6: {
        "title": "领域档案",
        "columns": ["领域", "常用同义词", "高频申请人", "有效 IPC/CPC", "检索地形",
                    "已知陷阱", "建档日期"],
        "key": "领域",
        "updatable": ["常用同义词", "高频申请人", "有效 IPC/CPC", "检索地形", "已知陷阱",
                      "建档日期"],
    },
}


class ExpError(Exception):
    pass


# ============================================================ 解析 / 渲染
def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells, current = [], []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] in ("\\", "|"):
            current.append(s[i + 1])
            i += 2
            continue
        if ch == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    cells.append("".join(current).strip())
    return cells


def _render_row(cells: list[str]) -> str:
    def escape(value) -> str:
        return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")

    return "| " + " | ".join(escape(cell) for cell in cells) + " |"


def _record_id(section: int, key: str) -> str:
    normalized = " ".join(key.split()).casefold()
    return "exp-" + hashlib.sha256(f"{section}\0{normalized}".encode("utf-8")).hexdigest()[:12]


def _atomic_write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write_json(path: pathlib.Path, payload: dict) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _file_revision(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_revision(path: pathlib.Path, expected: str | None) -> None:
    if expected and _file_revision(path) != expected:
        raise ExpError("经验库版本已变化；请重新运行 status/prefetch 后再提交写入")


@contextlib.contextmanager
def _library_lock(path: pathlib.Path, timeout_seconds: float = 10.0):
    lock_path = path.parent / f".{path.name}.lock"
    deadline = time.monotonic() + timeout_seconds
    fd = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"pid={os.getpid()} at={_dt.datetime.now().isoformat()}\n".encode("ascii"))
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > 300
            except OSError:
                stale = False
            if stale:
                try:
                    lock_path.unlink()
                    continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                raise ExpError(f"经验库正被其他进程写入，等待超时: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _maintenance_path(library: pathlib.Path) -> pathlib.Path:
    return library.parent / "maintenance.json"


def _load_maintenance(library: pathlib.Path) -> dict:
    path = _maintenance_path(library)
    if not path.exists():
        return {"schema_version": 1, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ExpError(f"维护状态不可读: {path}: {e}") from e
    if not isinstance(data.get("entries"), dict):
        raise ExpError(f"维护状态格式错误: {path}")
    return data


def _read_json_utf8(path: pathlib.Path, label: str = "JSON 文件") -> dict:
    """严格读取机器记录，并对 Windows 重定向产生的 UTF-16 给出可操作提示。"""
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ExpError(f"{label}不可读: {path}: {error}") from error
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ExpError(
            f"{label} {path} 是 UTF-16；不要用 PowerShell > 捕获机器输出，"
            "请改用命令的 --out 生成 UTF-8 记录"
        )
    try:
        text = raw.decode("utf-8-sig", errors="strict")
        value = json.loads(text)
    except (UnicodeDecodeError, ValueError) as error:
        raise ExpError(f"{label}不是有效 UTF-8 JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ExpError(f"{label}顶层必须是 JSON object: {path}")
    return value


def _is_negative_observation(evidence: str) -> bool:
    patterns = (
        r"(?:->|=>|=|:)\s*0\b",
        r"(?:返回|命中|结果).{0,10}(?:0\b|零|空|无结果|未命中)",
        r"(?:报错|异常|失败|\berror\b|\bfailed\b)",
    )
    return any(re.search(pattern, evidence, re.I) for pattern in patterns)


def _validate_admission(section: int, scope: str | None, evidence: str | None,
                        falsified_by: str | None) -> tuple[str, str | None]:
    inferred = "global" if section in (1, 2) else "domain"
    actual_scope = scope or inferred
    if section in (1, 2) and actual_scope != "global":
        raise ExpError(f"## {section} 会被每次预取，--scope 必须为 global")
    if section not in (1, 2) and actual_scope != "domain":
        raise ExpError(f"## {section} 是领域知识，--scope 必须为 domain")
    if actual_scope != "global":
        return actual_scope, None
    evidence = (evidence or "").strip()
    if not evidence:
        raise ExpError("--scope global 必须提供 --evidence，且不能只是单次空结果")
    has_number = bool(re.search(r"\d", evidence))
    has_repro = bool(re.search(r"[:()]|检索式|查询|命令|\bquery\b|\bcmd\b", evidence, re.I))
    if not (has_number and has_repro):
        raise ExpError(
            "全局 --evidence 必须可复现：至少包含具体查询/命令结构和返回数字"
        )
    evidence_type = "negative-observation" if _is_negative_observation(evidence) else "positive-observation"
    if evidence_type == "negative-observation" and not (falsified_by or "").strip():
        raise ExpError(
            "否定性观测（返回 0/空/报错）不得直接升格为全局规则；"
            "必须提供 --falsified-by 记录已排除的其它解释"
        )
    return actual_scope, evidence_type


def _section_counts(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        key = str(record.get("section"))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _audit_run(run: dict) -> tuple[dict, list[str]]:
    problems: list[str] = []
    records = run.get("records", [])
    if not isinstance(records, list):
        return {}, ["运行记录 records 必须是数组"]
    ids = [str(record.get("id", "")) for record in records]
    if any(not record_id for record_id in ids):
        problems.append("预取记录存在空 ID")
    if len(ids) != len(set(ids)):
        problems.append("预取记录存在重复 ID")
    authoritative_by_section = _section_counts(records)
    prefetched = len(records)
    if "prefetched" in run and run.get("prefetched") != prefetched:
        problems.append(f"预取总数不一致：声明 {run.get('prefetched')}，运行记录实际 {prefetched}")
    declared_by_section = run.get("prefetched_by_section")
    if declared_by_section is not None:
        normalized = {str(key): int(value) for key, value in declared_by_section.items()}
        if normalized != authoritative_by_section:
            if sum(normalized.values()) == prefetched:
                problems.append(
                    f"总数相同但分节不一致：声明 {normalized}，实际 {authoritative_by_section}"
                )
            else:
                problems.append(
                    f"分节预取数不一致：声明 {normalized}，实际 {authoritative_by_section}"
                )
    raw_used = run.get("used_ids", [])
    if not isinstance(raw_used, list):
        problems.append("used_ids 必须是数组")
        raw_used = []
    used_ids = [str(value) for value in raw_used]
    if len(used_ids) != len(set(used_ids)):
        problems.append(f"used_ids 存在重复标记：{used_ids}")
    unknown = sorted(set(used_ids) - set(ids))
    if unknown:
        problems.append(f"used_ids 包含未预取 ID：{unknown}")
    outcomes = run.get("outcomes", {})
    if not isinstance(outcomes, dict):
        problems.append("outcomes 必须是 object")
        outcomes = {}
    unknown_outcomes = sorted(set(outcomes) - set(used_ids))
    if unknown_outcomes:
        problems.append(f"outcomes 包含未 mark-used ID：{unknown_outcomes}")

    unique_used = set(used_ids) & set(ids)
    confirmed = sum(outcomes.get(item, {}).get("result") == "confirmed" for item in unique_used)
    rejected = sum(outcomes.get(item, {}).get("result") == "rejected" for item in unique_used)
    neutral = sum(outcomes.get(item, {}).get("result") == "neutral" for item in unique_used)
    reused = len(unique_used)
    rated = confirmed + rejected
    metrics = {
        "prefetched": prefetched,
        "prefetched_by_section": authoritative_by_section,
        "reused": reused,
        "reuse_rate_percent": round(reused * 100 / prefetched, 1) if prefetched else 0.0,
        "confirmed_reuse": confirmed,
        "rejected_reuse": rejected,
        "neutral_reuse": neutral,
        "unrated_reuse": reused - confirmed - rejected - neutral,
        "reuse_confirmation_rate_percent": round(confirmed * 100 / rated, 1) if rated else None,
        "cold_start": prefetched == 0,
    }
    existing = run.get("metrics")
    if isinstance(existing, dict):
        for key, value in metrics.items():
            if key in existing and existing[key] != value:
                problems.append(f"已写入指标 {key} 不一致：声明 {existing[key]!r}，重算 {value!r}")
    return metrics, problems


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv_facts(path: pathlib.Path) -> tuple[dict, list[str]]:
    problems: list[str] = []
    try:
        raw = path.read_bytes()
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return {}, [f"产物 {path.name} 是 UTF-16；结构化产物必须用标准 writer 生成 UTF-8"]
        text = raw.decode("utf-8-sig", errors="strict")
        rows = list(csv.reader(text.splitlines()))
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        return {}, [f"CSV 不可读 {path}: {error}"]
    if not rows:
        return {"header": [], "data_rows": 0, "columns": 0}, []
    width = len(rows[0])
    for line_no, row in enumerate(rows[1:], start=2):
        if len(row) != width:
            problems.append(
                f"{path.name} 首个列数错位行 L{line_no}：{len(row)} != 表头 {width}；"
                f"疑似未正确引用的分隔符字段={row!r}"
            )
            break
    return {"header": rows[0], "data_rows": max(0, len(rows) - 1), "columns": width}, problems


def _build_artifact_manifest(root_arg: str, artifact_args: list[str]) -> tuple[dict, list[str]]:
    root = pathlib.Path(root_arg).expanduser().resolve()
    if not root.is_dir():
        raise ExpError(f"产物目录不存在: {root}")
    declared: dict[str, dict] = {}
    problems: list[str] = []
    for value in artifact_args:
        role, relative = (value.split("=", 1) if "=" in value else ("artifact", value))
        relative_path = pathlib.Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ExpError(f"--artifact 必须是产物目录内的相对路径: {value}")
        normalized = relative_path.as_posix()
        if normalized in declared:
            raise ExpError(f"产物重复登记: {normalized}")
        declared[normalized] = {"role": role.strip() or "artifact", "path": normalized}
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*") if path.is_file()
    }
    missing = sorted(set(declared) - actual)
    unregistered = sorted(actual - set(declared))
    if missing:
        problems.append(f"已登记但缺失的产物: {missing}")
    if unregistered:
        problems.append(f"产物目录存在未登记文件: {unregistered}")
    files = []
    for relative, item in declared.items():
        path = root / pathlib.PurePosixPath(relative)
        if not path.is_file():
            continue
        facts = {
            **item,
            "bytes": path.stat().st_size,
            "sha256": _file_sha256(path),
        }
        if path.suffix.casefold() == ".csv":
            csv_info, csv_problems = _csv_facts(path)
            facts["csv"] = csv_info
            problems.extend(csv_problems)
        files.append(facts)
    return {"root": str(root), "files": files}, problems


def _audit_recorded_artifacts(manifest: dict) -> list[str]:
    root = manifest.get("root")
    files = manifest.get("files")
    if not root or not isinstance(files, list):
        return ["运行记录中的 artifacts 结构无效"]
    specs = [f"{item.get('role', 'artifact')}={item.get('path', '')}" for item in files]
    current, problems = _build_artifact_manifest(str(root), specs)
    current_by_path = {item["path"]: item for item in current["files"]}
    for recorded in files:
        path = recorded.get("path")
        actual = current_by_path.get(path)
        if not actual:
            continue
        if recorded.get("sha256") != actual.get("sha256"):
            problems.append(f"产物校验值已变化: {path}")
        if recorded.get("csv") != actual.get("csv"):
            problems.append(f"CSV 结构已变化: {path}")
    return problems


def _report_claim_problems(report: dict, metrics: dict, artifacts: dict | None) -> list[str]:
    problems: list[str] = []
    declared_metrics = report.get("metrics", report)
    if isinstance(declared_metrics, dict):
        for key in ("prefetched", "reused", "prefetched_by_section"):
            if key in declared_metrics and declared_metrics[key] != metrics[key]:
                problems.append(
                    f"报告指标 {key} 不一致：声明 {declared_metrics[key]!r}，运行记录 {metrics[key]!r}"
                )
    if "candidate_count" in report:
        candidates = [
            item for item in (artifacts or {}).get("files", [])
            if item.get("role") == "candidate_matrix"
        ]
        if len(candidates) != 1:
            problems.append("报告声明了候选数，但运行记录中没有唯一 candidate_matrix 产物")
        else:
            actual = candidates[0].get("csv", {}).get("data_rows")
            if report["candidate_count"] != actual:
                problems.append(f"报告候选数不一致：声明 {report['candidate_count']}，矩阵实际 {actual}")
    return problems


def _is_sep(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= set("|-: ")


def parse(text: str) -> dict:
    """返回 {section_no: {"header_idx", "sep_idx", "tables", "rows": [(idx, cells)]}}

    一节内可有**多张表**（按 `###` 子标题分领域时使用）。每张表形如
    「表头 + 分隔行 + 若干数据行」，重复出现的表头行作为下一张表的起点。
    """
    lines = text.splitlines()
    out: dict[int, dict] = {}
    heads: dict[int, int] = {}
    for i, l in enumerate(lines):
        m = re.match(r"^##\s+([1-6])[.、\s]", l)
        if m:
            heads[int(m.group(1))] = i
    for no, start in heads.items():
        end = len(lines)
        for other in heads.values():
            if other > start:
                end = min(end, other)
        tables = []
        i = start
        while i < end:
            if lines[i].strip().startswith("|") and not _is_sep(lines[i]) \
                    and i + 1 < end and _is_sep(lines[i + 1]):
                rows = []
                j = i + 2
                while j < end and lines[j].strip().startswith("|") \
                        and not _is_sep(lines[j]):
                    if lines[j].strip() == lines[i].strip():
                        break          # 表头重复 = 下一张表
                    rows.append((j, _split_row(lines[j])))
                    j += 1
                tables.append({"header_idx": i, "sep_idx": i + 1, "rows": rows})
                i = j
                continue
            i += 1
        if not tables:
            continue
        first = tables[0]
        out[no] = {"header_idx": first["header_idx"], "sep_idx": first["sep_idx"],
                   "tables": tables,
                   "rows": [r for t in tables for r in t["rows"]]}
    return out


def _names_of(header_line: str) -> list[str]:
    return _split_row(header_line)


def _is_empty_entry(cells: list[str], names: list[str]) -> bool:
    for i, name in enumerate(names):
        v = cells[i] if i < len(cells) else ""
        if v and v != name and v not in ("-", "—"):
            return False
    return True


# ============================================================ 领域别名
def _resolve_aliases(explicit, seed_path: pathlib.Path) -> pathlib.Path:
    """定位 domains.json。

    查找顺序（**库优先**）：
      1) --aliases 显式指定
      2) 环境变量 EXPCTL_ALIASES
      3) **经验库同目录**（最常见：别名表与经验库放在一起、一起进 git）
      4) 脚本所在目录 / 脚本父目录（脚本与库分开部署时）
    顺序很重要：先找脚本目录会读到"另一个库"的别名表，
    导致按错误的同义词过滤 → 保留数异常偏多（本项目实测踩过）。
    """
    if explicit:
        return pathlib.Path(explicit)
    env = os.environ.get("EXPCTL_ALIASES")
    if env:
        return pathlib.Path(env)
    here = pathlib.Path(__file__).resolve().parent
    for cand in (seed_path.parent / "domains.json",
                 here / "domains.json",
                 here.parent / "domains.json",
                 here / "检索经验库" / "domains.json",
                 here.parent / "检索经验库" / "domains.json"):
        if cand.exists():
            return cand
    return seed_path.parent / "domains.json"


def _load_aliases(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        return {k: v for k, v in json.loads(path.read_text(encoding="utf-8"))
                .get("aliases", {}).items() if isinstance(v, list)}
    except (OSError, ValueError) as e:
        print(f"⚠️ 别名表读取失败（忽略）: {e}")
        return {}


def _load_runtime_catalog(path: pathlib.Path | None) -> tuple[dict[str, set[str]] | None, list[str]]:
    if path is None or not path.exists():
        return None, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"运行时工具目录不可读: {e}"]
    servers = raw.get("servers")
    if not isinstance(servers, dict):
        return None, ["运行时工具目录必须包含 servers 对象"]
    catalog: dict[str, set[str]] = {}
    problems = []
    for server, entries in servers.items():
        if not isinstance(server, str) or not server.strip() or not isinstance(entries, list):
            problems.append("servers 必须是 server 名到工具数组的映射")
            continue
        tools = set()
        for entry in entries:
            name = entry if isinstance(entry, str) else entry.get("name") if isinstance(entry, dict) else None
            if isinstance(name, str) and name.strip():
                tools.add(name.strip())
            else:
                problems.append(f"server {server} 包含无效工具条目")
        catalog[server.strip()] = tools
    return catalog, problems


def _descriptor_problems(name: str, value, catalog: dict[str, set[str]] | None) -> tuple[list[str], list[str]]:
    problems, warnings = [], []
    if value is None:
        return problems, warnings
    if isinstance(value, str):
        if not value.strip() or "<" in value:
            problems.append(f"能力 {name} 仍是占位值或为空")
        else:
            warnings.append(f"能力 {name} 使用旧版裸工具名，无法验证 MCP server；建议迁移 schema v2")
        return problems, warnings
    if not isinstance(value, dict):
        return [f"能力 {name} 必须是 descriptor、旧版字符串或 null"], warnings

    mode = value.get("mode", "direct_tool")
    if mode not in TOOL_MODES:
        problems.append(f"能力 {name} 的 mode 无效: {mode}")
    server, tool = value.get("server"), value.get("tool")
    for field, field_value in (("server", server), ("tool", tool)):
        if not isinstance(field_value, str) or not field_value.strip() or "<" in field_value:
            problems.append(f"能力 {name} 缺少有效 {field}")
    if name != "count" and mode == "derived_from_search":
        problems.append(f"只有 count 能力可使用 derived_from_search")
    if name == "count":
        result_path = value.get("result_path")
        if not isinstance(result_path, str) or not result_path.strip() or "<" in result_path:
            problems.append("能力 count 缺少有效 result_path")
        if mode == "derived_from_search" and not isinstance(value.get("fixed_arguments"), dict):
            problems.append("派生 count 必须提供 fixed_arguments 对象")

    if catalog is not None and isinstance(server, str) and isinstance(tool, str):
        if server not in catalog:
            problems.append(f"能力 {name} 配置的 server 不在运行时目录: {server}")
        elif tool not in catalog[server]:
            owners = sorted(s for s, tools in catalog.items() if tool in tools)
            if owners:
                problems.append(
                    f"能力 {name} 的工具 {tool} 存在，但属于 {owners}，不是配置的 {server}"
                )
            else:
                problems.append(f"能力 {name} 的工具不存在于运行时目录: {server}.{tool}")
    return problems, warnings


def _resolve_tool_mapping_path(
    library: pathlib.Path, explicit: pathlib.Path | None = None
) -> pathlib.Path:
    if explicit is not None:
        return explicit
    env = os.environ.get("EXPCTL_TOOLS")
    if env:
        return pathlib.Path(env).expanduser()
    local = library.parent / "tools.local.json"
    return local if local.exists() else library.parent / "tools.json"


def _tool_mapping_diagnostics(
    library: pathlib.Path,
    catalog_path: pathlib.Path | None = None,
    mapping_path: pathlib.Path | None = None,
) -> tuple[dict | None, list[str], list[str]]:
    path = _resolve_tool_mapping_path(library, mapping_path)
    if not path.exists():
        return None, ["缺少 tools.json"], []
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"tools.json 不可读: {e}"], []
    problems, warnings = [], []
    schema_version = config.get("schema_version", 1)
    if schema_version not in (1, 2):
        problems.append(f"不支持的 tools.json schema_version: {schema_version}")
    capabilities = config.get("capabilities")
    if not isinstance(capabilities, dict):
        problems.append("capabilities 必须是对象")
        return config, problems, warnings
    unknown = sorted(set(capabilities) - set(TOOL_CAPABILITIES))
    missing = sorted(set(TOOL_CAPABILITIES) - set(capabilities))
    if unknown:
        problems.append(f"未知能力: {unknown}")
    if missing:
        problems.append(f"缺少能力键: {missing}")
    requested_catalog = catalog_path or library.parent / "runtime-tools.json"
    catalog, catalog_problems = _load_runtime_catalog(requested_catalog)
    problems.extend(catalog_problems)
    for name, value in capabilities.items():
        item_problems, item_warnings = _descriptor_problems(name, value, catalog)
        problems.extend(item_problems)
        warnings.extend(item_warnings)
    search = capabilities.get("search")
    if search is None:
        problems.append("必填能力 search 未配置")
    count = capabilities.get("count")
    if isinstance(count, dict) and count.get("mode") == "derived_from_search":
        if not isinstance(search, dict):
            problems.append("派生 count 要求 search 使用 schema v2 descriptor")
        elif (count.get("server"), count.get("tool")) != (
            search.get("server"), search.get("tool")
        ):
            problems.append("派生 count 的 server+tool 必须与 search 完全一致")
    if schema_version == 1 or any(isinstance(v, str) for v in capabilities.values() if v is not None):
        warnings.append("tools.json 仍含 schema v1 裸工具名；可读取但不能验证 server 命名空间")
    if catalog is None:
        warnings.append("未提供 runtime-tools.json；server+tool 仅做结构校验，尚未完成运行时存在性确认")
    return config, problems, sorted(set(warnings))


def _tool_mapping_problems(library: pathlib.Path) -> tuple[dict | None, list[str]]:
    config, problems, _ = _tool_mapping_diagnostics(library)
    return config, problems


def _load_tool_mapping(
    library: pathlib.Path,
    catalog_path: pathlib.Path | None = None,
    mapping_path: pathlib.Path | None = None,
) -> tuple[dict, list[str], pathlib.Path]:
    config, problems, warnings = _tool_mapping_diagnostics(library, catalog_path, mapping_path)
    if problems:
        raise ExpError("工具映射不可用: " + "；".join(problems))
    assert config is not None
    return config, warnings, _resolve_tool_mapping_path(library, mapping_path)


def _expand(tags, aliases: dict) -> set:
    """标签 → 同义集合。命中条件：标签 t 中含该等价组的某个成员 g（g in t）。"""
    out = set()
    for t in tags:
        t = (t or "").strip()
        if not t:
            continue
        out.add(t)
        for canon, variants in aliases.items():
            group = {canon, *[v for v in variants if isinstance(v, str)]}
            if any(g and g in t for g in group):
                out |= group
    return out


def _tags_for_section(lines: list, info: dict, names: list, no: int, di) -> dict:
    """{行号: 领域标签}。无「领域」列的节（## 4）退回用**每行最近的** ### 子标题。

    逐行解析而非整节取一个，使 §4 可按领域拆成多个 `###` 小节 + 多张表——
    否则一节内各领域的条目会共用第一个子标题，导致按领域预取时取错。
    """
    out = {}
    if not (di is None or no == 4):
        for idx, cells in info["rows"]:
            out[idx] = cells[di] if (di is not None and di < len(cells)) else ""
        return out
    for idx, _ in info["rows"]:
        cur = ""
        for j in range(idx - 1, -1, -1):
            if re.match(r"^###\s", lines[j]):
                cur = re.sub(r"^###\s+[\d.]*\s*", "", lines[j]).strip()
                break
            if re.match(r"^##\s", lines[j]):
                break
        out[idx] = cur or "（未命名小节）"
    return out


def _iter_skill_dirs(lib: pathlib.Path):
    """找 skill 目录。**多候选**，因为 skills/ 未必在经验库的祖先链上
    （经验库与 skill 可以位于不同目录；所有路径均由当前机器显式提供）。

    顺序：环境变量 EXPCTL_SKILLS_DIR → 库的祖先链上的 skills/ → 常见位置
    （~/.dsh/skills、~/.claude/skills、~/.codex/skills、~/.agents/skills）。
    各处按真实路径去重；同名但位于不同宿主的 skill 都要检查。
    """
    cands = []
    env = os.environ.get("EXPCTL_SKILLS_DIR")
    if env:
        cands.append(pathlib.Path(env).expanduser())
    for up in lib.parents:
        cands.append(up / "skills")
    home = pathlib.Path.home()
    cands += [home / ".dsh" / "skills",
              home / ".claude" / "skills",
              home / ".codex" / "skills",
              home / ".agents" / "skills",
              home / ".config" / "dsh" / "skills"]
    seen, out = set(), []
    for c in cands:
        if not c.is_dir():
            continue
        try:
            entries = sorted(c.iterdir())
        except OSError:
            continue
        if not any(e.is_dir() and (e / "SKILL.md").exists() for e in entries):
            continue          # 不像 skill 目录，跳过
        for d in entries:
            identity = str(d.resolve()).casefold()
            if d.is_dir() and identity not in seen:
                seen.add(identity)
                out.append(d)
    return out


def _doctor_cache_path(lib: pathlib.Path) -> pathlib.Path:
    """Use a runtime-neutral user cache outside the experience repository."""
    d = pathlib.Path.home() / ".cache" / "expctl"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d / "doctor.json"
    except OSError:
        return lib.parent / DOCTOR_CACHE


def _frontmatter_name(skill_md: pathlib.Path):
    try:
        head = skill_md.read_text(encoding="utf-8", errors="replace")[:600]
    except OSError:
        return None
    m = re.search(r"^name:\s*(\S+)", head, re.M)
    return m.group(1) if m else None


def doctor_findings(lib: pathlib.Path) -> list:
    """环境体检：只找"需要人决策"且有**确凿依据**的项，不做语义猜测。

    命名检查用**词边界**匹配：否则 "epo" 会命中 "report"（实测踩过这个假阳性）。
    """
    out = []
    pats = [(m, re.compile(rf"(?<!\w){re.escape(m)}(?!\w)", re.I)) for m in BACKEND_MARKERS]
    for d in _iter_skill_dirs(lib):
        hits = [m for m, rx in pats if rx.search(d.name)]
        fm = _frontmatter_name(d / "SKILL.md")
        if fm:
            hits += [m for m, rx in pats if rx.search(fm)]
        if hits:
            out.append({
                "type": "skill naming",
                "skill": d.name,
                "frontmatter_name": fm,
                "hit": sorted(set(hits)),
                "action": "名字里带后端名；换后端后会名不副实。是否改名由你决定（不影响功能）",
            })
    if not (lib.parent / "domains.json").exists() and not (lib / "domains.json").exists():
        out.append({
            "type": "aliases", "skill": "—", "frontmatter_name": None,
            "hit": ["domains.json"],
            "action": "未找到领域同义词表，按领域过滤会**静默失效**（保留数偏多）。"
                      "已自动建最小版，新增领域时往里加等价组即可（SPEC §4.3）",
        })
    _, tool_problems, tool_warnings = _tool_mapping_diagnostics(lib)
    if tool_problems:
        out.append({
            "type": "tool mapping", "skill": "—", "frontmatter_name": None,
            "hit": tool_problems,
            "action": "完成 server-aware tools.json 映射并用运行时工具目录验证后再开始正式检索。",
        })
    if tool_warnings:
        out.append({
            "type": "tool mapping migration", "skill": "—", "frontmatter_name": None,
            "hit": tool_warnings,
            "action": "映射可读取，但命名空间尚未完全验证；运行 mapping-prompt 完成显式映射。",
        })
    if lib.is_file():
        try:
            text = lib.read_text(encoding="utf-8")
            lines = text.splitlines()
            sections = parse(text)
            maintenance = _load_maintenance(lib)
            for no in (1, 2):
                info = sections.get(no)
                if not info:
                    continue
                names = _names_of(lines[info["header_idx"]])
                key_index = names.index(SECTIONS[no]["key"])
                alt_index = names.index("替代方案") if "替代方案" in names else None
                for _, cells in info["rows"]:
                    if _is_empty_entry(cells, names):
                        continue
                    key = cells[key_index] if key_index < len(cells) else ""
                    record_id = _record_id(no, key)
                    state = maintenance["entries"].get(record_id, {})
                    admission = state.get("admission")
                    if not admission:
                        out.append({
                            "type": "knowledge admission", "skill": "—", "frontmatter_name": None,
                            "hit": [record_id, "legacy-global-entry"],
                            "action": f"## {no} / {key} 缺全局入库证据元数据；复核前不应把它当成高置信规则。",
                        })
                    elif admission.get("evidence_type") == "negative-observation":
                        out.append({
                            "type": "knowledge admission", "skill": "—", "frontmatter_name": None,
                            "hit": [record_id, "negative-observation"],
                            "action": f"## {no} / {key} 基于否定性观测；预取时将提示同构查询复核。",
                        })
                    if alt_index is not None and alt_index < len(cells) and re.search(
                        r"(?:改用|换用|切换).{0,8}(?:其他|其它)?(?:字段|工具)", cells[alt_index]
                    ):
                        out.append({
                            "type": "knowledge admission", "skill": "—", "frontmatter_name": None,
                            "hit": [record_id, "capability-avoidance"],
                            "action": f"## {no} / {key} 的替代方案是能力回避；请先隔离测试真实原因。",
                        })
        except (OSError, ExpError, ValueError):
            pass
    return out

def _maybe_run_doctor(seed: pathlib.Path, quiet: bool = True) -> None:
    """在正常使用中顺带体检；结果缓存，仅在环境变化时重跑。

    quiet=True 时**只在发现问题时**打印，不污染检索流程的输出。
    """
    skills = _iter_skill_dirs(seed)
    watched = [seed.parent / "domains.json", seed.parent / "tools.json",
               seed.parent / "tools.local.json", seed.parent / "runtime-tools.json",
               seed.parent / "maintenance.json"]
    sig = f"{len(skills)}:" + ",".join(
        f"{d.resolve()}@{(d / 'SKILL.md').stat().st_mtime_ns if (d / 'SKILL.md').exists() else 0}"
        for d in skills)
    sig += ":" + ",".join(
        f"{p.name}@{p.stat().st_mtime_ns if p.exists() else 0}" for p in watched
    )
    cache = _doctor_cache_path(seed)
    if cache.exists():
        try:
            if json.loads(cache.read_text(encoding="utf-8")).get("sig") == sig:
                return
        except (OSError, ValueError):
            pass
    findings = doctor_findings(seed)
    try:
        _atomic_write_json(cache, {"sig": sig, "at": TODAY, "n": len(findings)})
    except OSError:
        pass
    if not findings and quiet:
        return
    print("\n" + "─" * 62)
    print("🔎 环境自检发现需要你决策的项（不影响检索；环境不变则不再重复提示）：")
    for f in findings:
        if f["type"] == "skill naming":
            extra = f"（frontmatter name: {f['frontmatter_name']}）" if f["frontmatter_name"] else ""
            print(f"  · skill `{f['skill']}`{extra} 命中后端名 {f['hit']} —— 要改名吗？")
        else:
            print(f"  · {f['action']}")
    print("─" * 62 + "\n")


def cmd_doctor(a) -> int:
    lib = pathlib.Path(a.file)
    findings = doctor_findings(lib)
    if getattr(a, "skills_root", None):
        checker_candidates = [
            pathlib.Path(__file__).resolve().with_name("check_skill_integrity.py"),
            pathlib.Path(__file__).resolve().parent / "plugin" / "patent-retrieval-evokit" /
            "scripts" / "check_skill_integrity.py",
        ]
        checker = next((path for path in checker_candidates if path.exists()), None)
        if checker is None:
            raise ExpError("找不到 check_skill_integrity.py，无法执行技能架构检查")
        spec = importlib.util.spec_from_file_location("patent_retrieval_evokit_integrity", checker)
        if spec is None or spec.loader is None:
            raise ExpError("无法加载 check_skill_integrity.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        architecture = module.scan(pathlib.Path(a.skills_root).expanduser())
        for item in architecture["findings"]:
            findings.append({
                "type": "skill architecture",
                "skill": item.get("skill") or item.get("skills") or "—",
                "frontmatter_name": None,
                "hit": [item["code"], item["level"], item["message"]],
                "action": "先修复 blocker；warning 需人工确认。不要在 doctor 中自动改写技能。",
                "details": item,
            })
    if getattr(a, "json", False):
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        return 0
    print(f"环境自检  经验库={lib}")
    if not findings:
        print("  ✅ 未发现需要你决策的项")
        return 0
    print(f"  发现 {len(findings)} 项：\n")
    for f in findings:
        print(f"  [{f['type']}] {f.get('skill', '')}")
        if f.get("frontmatter_name"):
            print(f"      frontmatter name: {f['frontmatter_name']}")
        print(f"      命中: {f['hit']}")
        print(f"      → {f['action']}\n")
    print("  说明：这些项**不影响技能功能**，改名与否由你决定。")
    return 0


def cmd_tools(a) -> int:
    library = pathlib.Path(a.file)
    catalog_path = pathlib.Path(a.catalog).expanduser() if getattr(a, "catalog", None) else None
    mapping_path = pathlib.Path(a.mapping).expanduser() if getattr(a, "mapping", None) else None
    config, warnings, resolved_mapping = _load_tool_mapping(library, catalog_path, mapping_path)
    if a.json:
        payload = dict(config)
        payload["_validation"] = {
            "mapping_file": str(resolved_mapping),
            "runtime_catalog_checked": bool(
                catalog_path or (library.parent / "runtime-tools.json").exists()
            ),
            "warnings": warnings,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"映射文件：{resolved_mapping}")
    print(f"提供方标签：{config.get('provider_label') or config.get('backend') or '（未设置）'}")
    for name in TOOL_CAPABILITIES:
        value = config["capabilities"].get(name)
        shown = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
        print(f"  {name:20s} {shown or '（不可用）'}")
    for warning in warnings:
        print(f"  ⚠️ {warning}")
    return 0


def cmd_mapping_prompt(a) -> int:
    library = pathlib.Path(a.file).expanduser().resolve()
    mapping_file = library.parent / "tools.local.json"
    prompt = f"""请为专利检索记忆插件建立运行时工具映射。不要猜测或照抄任何示例工具名。

目标经验库：{library.parent}

请严格执行：
1. 从当前 Agent 运行时列出所有与专利检索有关的 MCP server 及其真实 tools/list；必须保留 server 和 tool 两层身份。
2. 将工具目录写入 `{library.parent / 'runtime-tools.json'}`，格式为：
   {{"schema_version": 1, "servers": {{"<server>": ["<tool>"]}}}}
3. 把以下抽象能力映射到真实 server+tool：{', '.join(TOOL_CAPABILITIES)}。
4. search 必须映射。其它能力不可用时写 null，不得根据相似名称猜测。
5. count 若有独立工具，使用 mode=direct_tool；若从 search 返回派生，使用 mode=derived_from_search，并明确 fixed_arguments 和整数总命中字段的 result_path。不得把返回记录条数当总命中数。
6. 将结果写入本机专用的 `{mapping_file}`，schema_version 必须为 2。不要覆盖仓库模板 tools.json；provider_label 仅作显示，不参与路由。
7. 执行 `expctl.py --file "{library}" tools --mapping "{mapping_file}" --catalog "{library.parent / 'runtime-tools.json'}" --json`。
8. 报告每个能力的 server、tool、mode、验证结果和未映射原因。在校验成功前不要修改宿主专利检索 Skill，也不要开始正式检索。

边界：工具不存在、网络失败、鉴权失败、返回字段缺失和合法零命中必须分别报告，不能互相降级。
"""
    if getattr(a, "out", None):
        _atomic_write_text(pathlib.Path(a.out).expanduser().resolve(), prompt)
        print(f"工具映射 prompt 已写入: {pathlib.Path(a.out).expanduser().resolve()}")
    else:
        print(prompt)
    return 0


# ============================================================ 命令：prefetch
def cmd_prefetch(a) -> int:
    """检索前精确预取：只输出与本次领域相关的行，不整份读。"""
    p = pathlib.Path(a.file)
    if not p.exists():
        print(f"经验库不存在: {p}")
        print("先运行：python expctl.py init --repo \"<目录>\"")
        return 2
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    secs = parse(text)
    maintenance = _load_maintenance(p)

    req = [t.strip() for t in re.split(r"[—\-/,、\s]+", a.domain) if len(t.strip()) >= 2]
    alias_path = _resolve_aliases(a.aliases, p)
    if not alias_path.exists():
        # 缺别名表会让"按领域过滤"静默失效（保留数偏多且不报错）——自动补最小版
        try:
            _atomic_write_text(alias_path, DOMAINS_TEMPLATE)
            print(f"ℹ️ 已自动补建 {alias_path.name}（原缺失会导致领域过滤静默失效）")
        except OSError as e:
            print(f"⚠️ 无法补建 {alias_path.name}: {e}")
    aliases = _load_aliases(alias_path)
    req_exp = _expand(req, aliases)

    if a.json or a.out:
        records, skipped = [], []
        for no in sorted(secs):
            info = secs[no]
            names = _names_of(lines[info["header_idx"]])
            di = names.index("领域") if "领域" in names else None
            tags = _tags_for_section(lines, info, names, no, di)
            always = no in (1, 2)
            key_name = SECTIONS.get(no, {}).get("key", names[0])
            for idx, cells in info["rows"]:
                if _is_empty_entry(cells, names):
                    continue
                padded = list(cells) + [""] * (len(names) - len(cells))
                fields = dict(zip(names, padded))
                tag = tags.get(idx, "")
                keep = always or not req or tag == "全局" or bool(
                    _expand([tag], aliases) & req_exp
                )
                key = fields.get(key_name, padded[0] if padded else "")
                record_id = _record_id(no, key)
                state = maintenance["entries"].get(record_id, {})
                record = {
                    "id": record_id,
                    "section": no,
                    "section_title": SECTIONS.get(no, {}).get("title", ""),
                    "line": idx + 1,
                    "key": key,
                    "domain": tag,
                    "fields": fields,
                }
                admission = state.get("admission")
                if admission:
                    record["admission"] = admission
                    if admission.get("evidence_type") == "negative-observation":
                        record["warning"] = (
                            "本条基于否定性观测（返回空/0/报错），"
                            "使用前建议用同构查询快速复核"
                        )
                if state.get("status") == "retracted":
                    skipped.append({"id": record_id, "section": no, "key": key,
                                    "domain": tag, "reason": "retracted"})
                elif keep:
                    records.append(record)
                else:
                    skipped.append({"id": record_id, "section": no, "key": key,
                                    "domain": tag, "reason": "domain_mismatch"})
        payload = {
            "schema_version": 2,
            "run_id": "run-" + uuid.uuid4().hex,
            "created_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "active",
            "file": str(p),
            "library_revision": _file_revision(p),
            "domain": a.domain,
            "matched_terms": sorted(req_exp),
            "prefetched": len(records),
            "prefetched_by_section": _section_counts(records),
            "skipped_count": len(skipped),
            "records": records,
            "skipped": skipped,
            "used_ids": [],
        }
        if a.out:
            out_path = pathlib.Path(a.out).expanduser()
            _atomic_write_json(out_path, payload)
        if a.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"已写出运行记录：{a.out}（预取 {len(records)} 条）")
        return 0

    print("# 精确预取（触点 1）")
    print(f"**经验库**: `{p}`（{len(lines)} 行）")
    print(f"**本次领域**: {a.domain}")
    print(f"**匹配词**: {' / '.join(req)}")
    print(f"**同义扩展**（{alias_path.name}）: {' / '.join(sorted(req_exp - set(req))) or '（无）'}")
    print("**规则**: `## 1`+`## 2` 全局预取（已作废条目除外）；"
          "`## 3`-`## 6` 按「领域」匹配（`## 4` 取子标题）\n")

    kept_total = skipped_total = 0
    for no in sorted(secs):
        info = secs[no]
        names = _names_of(lines[info["header_idx"]])
        di = names.index("领域") if "领域" in names else None
        tags = _tags_for_section(lines, info, names, no, di)
        always = no in (1, 2)

        kept, skipped = [], []
        for idx, cells in info["rows"]:
            if _is_empty_entry(cells, names):
                continue
            tag = tags.get(idx, "")
            key_name = SECTIONS.get(no, {}).get("key", names[0])
            padded = list(cells) + [""] * (len(names) - len(cells))
            record_id = _record_id(no, padded[names.index(key_name)])
            if maintenance["entries"].get(record_id, {}).get("status") == "retracted":
                skipped.append((idx, cells, tag))
            elif always or not req or tag == "全局":
                kept.append((idx, cells, tag))
            elif _expand([tag], aliases) & req_exp:
                kept.append((idx, cells, tag))
            else:
                skipped.append((idx, cells, tag))
        kept_total += len(kept)
        skipped_total += len(skipped)

        title = SECTIONS.get(no, {}).get("title", "")
        flag = "无条件全取" if always else f"领域匹配 {len(kept)}/{len(kept) + len(skipped)}"
        print(f"## {no}. {title}  —  {flag}")
        if not kept:
            print("  （本领域无可用条目）")
        for idx, cells, tag in kept:
            print(f"  L{idx + 1}  [{tag}] {_render_row(cells)}")
            key_name = SECTIONS.get(no, {}).get("key", names[0])
            padded = list(cells) + [""] * (len(names) - len(cells))
            state = maintenance["entries"].get(
                _record_id(no, padded[names.index(key_name)]), {}
            )
            if state.get("admission", {}).get("evidence_type") == "negative-observation":
                print("    ⚠ 本条基于否定性观测，使用前请用同构查询快速复核")
        if skipped:
            doms = sorted({c[2] for c in skipped})
            print(f"  ⏭ 跳过 {len(skipped)} 条（领域不在本次范围："
                  f"{'；'.join(d[:22] for d in doms)}）")
        print()

    print(f"---\n**保留 {kept_total} 条 ｜ 跳过 {skipped_total} 条**")
    if skipped_total:
        print("> ⚠️ 请扫一眼上面的跳过列表：若本应相关的条目被跳过，"
              "说明领域标签写错或 domains.json 缺项——先修，再继续检索。")
    # 顺带做一次环境体检（缓存；仅在环境变化时输出，见 SPEC §6.3）
    _maybe_run_doctor(p)
    return 0


# ============================================================ 命令：add
def cmd_add(a) -> int:
    p = pathlib.Path(a.file)
    _require_revision(p, a.expected_revision)
    if a.section not in SECTIONS:
        raise ExpError(f"--section 必须是 1-6，收到 {a.section}")
    if not a.key.strip():
        raise ExpError("--key 不能为空")
    admission_scope, evidence_type = _validate_admission(
        a.section, a.scope, a.evidence, a.falsified_by
    )
    record_id = _record_id(a.section, a.key)
    maintenance = _load_maintenance(p)
    prior_state = maintenance["entries"].get(record_id, {})
    if prior_state.get("status") == "retracted":
        raise ExpError(
            f"经验 {record_id} 已作废，不得通过 add 静默恢复；"
            "请保留原 key 的历史，使用新 key 写入经复核的结论"
        )
    sec = SECTIONS[a.section]
    cols = _split_row(f"|{a.cols}|") if a.cols else []
    writable_columns = [c for c in sec["columns"] if c != sec["key"]]
    if len(cols) > len(writable_columns):
        raise ExpError(
            f"## {a.section} --cols 最多 {len(writable_columns)} 格，收到 {len(cols)} 格；"
            "key 列不要重复传入"
        )
    vals = dict(zip(writable_columns, cols))

    date_col = "记录日期" if "记录日期" in sec["columns"] else (
        "首次发现" if "首次发现" in sec["columns"] else None)

    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    secs = parse(text)
    if a.section not in secs:
        raise ExpError(f"## {a.section} 节不存在（经验库结构不完整？先跑 validate）")
    info = secs[a.section]
    names = _names_of(lines[info["header_idx"]])
    ki = names.index(sec["key"])

    def build_row():
        row = []
        for name in names:
            if name == sec["key"]:
                row.append(a.key)
            elif name == "复用次数":
                row.append("0")
            elif name == date_col and not a.clear:
                row.append(a.date)
            else:
                row.append(vals.get(name, "") or "")
        return row

    target = None
    for idx, cells in info["rows"]:
        if _is_empty_entry(cells, names):
            continue
        if ki < len(cells) and cells[ki].strip() == a.key.strip():
            target = (idx, cells)
            break

    if target is None:
        new_cells = build_row()
        missing = [
            name for name in REQUIRED_NEW_FIELDS.get(a.section, [])
            if name in names and not new_cells[names.index(name)].strip()
        ]
        if missing:
            raise ExpError(f"## {a.section} 新记录缺少必填字段: {missing}")
        tables = info.get("tables") or []
        tbl = None
        if a.section == 4:
            if not a.domain:
                raise ExpError("## 4 新增在先技术时必须提供 --domain，避免写入错误领域")
            for candidate in tables:
                tag = ""
                for j in range(candidate["header_idx"] - 1, info["header_idx"], -1):
                    if re.match(r"^###\s", lines[j]):
                        tag = re.sub(r"^###\s+[\d.]*\s*", "", lines[j]).strip()
                        break
                if tag == a.domain:
                    tbl = candidate
                    break
            if tbl is None:
                section_end = next(
                    (i for i in range(info["header_idx"] + 1, len(lines))
                     if re.match(r"^##\s", lines[i])),
                    len(lines),
                )
                insert_at = section_end
                while insert_at > info["header_idx"] + 1 \
                        and lines[insert_at - 1].strip() in ("", "---"):
                    insert_at -= 1
                lines[insert_at:insert_at] = [
                    "", f"### {a.domain}", "",
                    _render_row(names),
                    _render_row(["---"] * len(names)),
                    _render_row(new_cells), "",
                ]
                print(f"[新增] ## {a.section} / {a.domain}  {a.key}")
                print(f"  {_render_row(new_cells)}")
                action = "新增"
                tbl = None
                insert_at = None
        else:
            tbl = tables[0] if tables else None
        if tbl is not None:
            trows = tbl["rows"]
            if trows:
                insert_at = trows[-1][0] + 1
                if _is_empty_entry(trows[-1][1], names):
                    insert_at = trows[-1][0]
            else:
                insert_at = tbl["sep_idx"] + 1
        elif a.section != 4:
            insert_at = (info["rows"][-1][0] + 1) if info["rows"] else (info["sep_idx"] + 1)
            if info["rows"]:
                last_idx, last_cells = info["rows"][-1]
                if _is_empty_entry(last_cells, names):
                    insert_at = last_idx
        if insert_at is not None:
            lines.insert(insert_at, _render_row(new_cells))
            label = f" / {a.domain}" if a.section == 4 else ""
            print(f"[新增] ## {a.section}{label}  {a.key}")
            print(f"  {_render_row(new_cells)}")
            action = "新增"
    else:
        idx, cells = target
        cells = list(cells) + [""] * (len(names) - len(cells))
        changed = []
        if "最后确认" in names and "最后确认" not in vals and not a.clear:
            vals["最后确认"] = a.date
        for name in sec["updatable"]:
            if name not in names:
                continue
            if name not in vals:
                continue
            v = vals[name] or ""
            j = names.index(name)
            if cells[j] != v:
                changed.append(f"{name}: {cells[j][:14]!r} → {v[:14]!r}")
                cells[j] = v
        lines[idx] = _render_row(cells)
        print(f"[更新（已存在，原地合并）] ## {a.section}  {a.key}")
        print("  " + ("；".join(changed) or "无变化"))
        action = "更新"

    _atomic_write_text(p, "\n".join(lines) + "\n")
    state = dict(prior_state)
    state.update({"section": a.section, "key": a.key, "scope": admission_scope})
    if admission_scope == "global":
        state["admission"] = {
            "evidence": a.evidence.strip(),
            "evidence_type": evidence_type,
            "falsified_by": (a.falsified_by or "").strip(),
            "admitted_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    maintenance["entries"][record_id] = state
    _atomic_write_json(_maintenance_path(p), maintenance)
    if not a.no_log:
        _append_log(p, a.case, action, a.key)
    return 0


def cmd_retract(a) -> int:
    """作废一条经验：保留 Markdown 历史，但从预取中排除。"""
    p = pathlib.Path(a.file)
    _require_revision(p, a.expected_revision)
    if a.section not in SECTIONS:
        raise ExpError(f"--section 必须是 1-6，收到 {a.section}")
    if not a.key.strip() or not a.reason.strip():
        raise ExpError("retract 必须提供非空 --key 和 --reason")
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    sections = parse(text)
    info = sections.get(a.section)
    if not info:
        raise ExpError(f"## {a.section} 节不存在")
    names = _names_of(lines[info["header_idx"]])
    key_index = names.index(SECTIONS[a.section]["key"])
    found = any(
        not _is_empty_entry(cells, names)
        and key_index < len(cells)
        and cells[key_index].strip() == a.key.strip()
        for _, cells in info["rows"]
    )
    if not found:
        raise ExpError(f"未找到 ## {a.section} 中键为 {a.key!r} 的条目")
    record_id = _record_id(a.section, a.key)
    maintenance = _load_maintenance(p)
    entry = dict(maintenance["entries"].get(record_id, {}))
    entry.update({
        "status": "retracted",
        "section": a.section,
        "key": a.key,
        "retracted_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "retraction_reason": a.reason.strip(),
        "superseded_by": (a.superseded_by or "").strip(),
    })
    maintenance["entries"][record_id] = entry
    _atomic_write_json(_maintenance_path(p), maintenance)
    print(f"已作废：{record_id}（## {a.section} / {a.key}）；Markdown 历史已保留")
    return 0


# ============================================================ 命令：mark-used / finish-run
def cmd_mark_used(a) -> int:
    run_path = pathlib.Path(a.run).expanduser()
    run = _read_json_utf8(run_path, "运行记录")
    records = {record.get("id"): record for record in run.get("records", [])}
    if a.id not in records:
        raise ExpError(f"运行记录中没有经验 ID {a.id!r}")
    used_ids = list(dict.fromkeys(run.get("used_ids", [])))
    if a.id in used_ids:
        print(f"已标记过，未重复计数：{a.id}")
        return 0

    record = records[a.id]
    library = pathlib.Path(run.get("file") or a.file)
    maintenance = _load_maintenance(library)
    if maintenance["entries"].get(a.id, {}).get("status") == "retracted":
        raise ExpError(f"经验 {a.id} 已作废，不得标记为本次复用")
    _require_revision(library, run.get("library_revision"))
    text = library.read_text(encoding="utf-8")
    lines = text.splitlines()
    secs = parse(text)
    no = int(record["section"])
    info = secs.get(no)
    if not info:
        raise ExpError(f"经验库缺少 ## {no}，无法标记 {a.id}")
    names = _names_of(lines[info["header_idx"]])
    key_name = SECTIONS[no]["key"]
    ki = names.index(key_name)
    found = False
    for idx, cells in info["rows"]:
        padded = list(cells) + [""] * (len(names) - len(cells))
        key = padded[ki]
        if _record_id(no, key) != a.id:
            continue
        if "最后确认" in names:
            padded[names.index("最后确认")] = a.date
        if "复用次数" in names:
            j = names.index("复用次数")
            padded[j] = str(int(padded[j]) + 1) if padded[j].isdigit() else "1"
        lines[idx] = _render_row(padded)
        found = True
        break
    if not found:
        raise ExpError(f"经验 {a.id} 已不在经验库中；请重新 prefetch")

    _atomic_write_text(library, "\n".join(lines) + "\n")
    state = maintenance["entries"].get(a.id)
    if state and state.get("status") == "prune_candidate":
        state.pop("status", None)
        state.pop("marked_at", None)
        state.pop("reason", None)
        _atomic_write_json(_maintenance_path(library), maintenance)
    used_ids.append(a.id)
    run["used_ids"] = used_ids
    run["library_revision"] = _file_revision(library)
    run["last_updated_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    _atomic_write_json(run_path, run)
    print(f"已标记采用：{a.id}（## {no} / {record['key']}）")
    return 0


def cmd_mark_outcome(a) -> int:
    run_path = pathlib.Path(a.run).expanduser()
    run = _read_json_utf8(run_path, "运行记录")
    if a.id not in set(run.get("used_ids", [])):
        raise ExpError(f"经验 {a.id} 尚未 mark-used，不能记录采用结果")
    if not a.note.strip():
        raise ExpError("--note 必须写明本次验证依据")
    outcomes = run.setdefault("outcomes", {})
    outcomes[a.id] = {
        "result": a.result,
        "note": a.note.strip(),
        "recorded_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _atomic_write_json(run_path, run)
    print(f"已记录采用结果：{a.id} → {a.result}")
    return 0


def cmd_finish_run(a) -> int:
    run_path = pathlib.Path(a.run).expanduser()
    run = _read_json_utf8(run_path, "运行记录")
    metrics, problems = _audit_run(run)
    artifacts = run.get("artifacts")
    if a.artifacts_dir or a.artifact:
        if not a.artifacts_dir:
            raise ExpError("使用 --artifact 时必须同时提供 --artifacts-dir")
        artifacts, artifact_problems = _build_artifact_manifest(a.artifacts_dir, a.artifact)
        problems.extend(artifact_problems)
    elif artifacts:
        problems.extend(_audit_recorded_artifacts(artifacts))
    if problems:
        raise ExpError("运行记账校验失败：\n- " + "\n- ".join(problems))
    run["status"] = "finished"
    run["finished_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    run["metrics"] = {**metrics, "added": a.added}
    if artifacts:
        run["artifacts"] = artifacts
    _atomic_write_json(run_path, run)
    cold = "是" if metrics["cold_start"] else "否"
    confirmation_rate = metrics["reuse_confirmation_rate_percent"]
    quality = f"{confirmation_rate:.1f}%" if confirmation_rate is not None else "暂无"
    print(f"经验库复用率：预取 {metrics['prefetched']} 条 → "
          f"实际复用 {metrics['reused']} 条 ({metrics['reuse_rate_percent']:.1f}%)｜"
          f"确认 {metrics['confirmed_reuse']}｜推翻 {metrics['rejected_reuse']}｜"
          f"中性 {metrics['neutral_reuse']}｜未评价 {metrics['unrated_reuse']}｜"
          f"确认率 {quality}｜本次新增 {a.added} 条｜冷启动：{cold}")
    return 0


def cmd_verify_run(a) -> int:
    """只读重算一次运行的指标，并可选与报告声明和产物比对。"""
    run_path = pathlib.Path(a.run).expanduser()
    run = _read_json_utf8(run_path, "运行记录")
    metrics, problems = _audit_run(run)
    artifacts = run.get("artifacts")
    if artifacts:
        problems.extend(_audit_recorded_artifacts(artifacts))
    if a.report:
        report_path = pathlib.Path(a.report).expanduser()
        report = _read_json_utf8(report_path, "报告审计声明")
        problems.extend(_report_claim_problems(report, metrics, artifacts))
    payload = {
        "run": str(run_path),
        "ok": not problems,
        "authoritative_metrics": metrics,
        "artifacts": artifacts,
        "problems": problems,
    }
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"运行复核: {run_path}")
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        for problem in problems:
            print(f"  FAIL: {problem}")
        print("  => " + ("通过" if not problems else f"{len(problems)} 处差异"))
    return 0 if not problems else 1


def _append_log(p: pathlib.Path, case, action: str, key: str) -> None:
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    log_idx = next((i for i, l in enumerate(lines)
                    if l.strip().startswith("### 更新日志")), None)
    if log_idx is None:
        return
    last = None
    for j in range(log_idx, min(log_idx + 12, len(lines))):
        s = lines[j].strip()
        if s.startswith("| ") and not _is_sep(lines[j]) and "日期" not in s:
            last = j
    if last is None:
        return
    cells = _split_row(lines[last])
    if len(cells) >= 6 and cells[0].strip() == TODAY:
        cells[1] = cells[1] or (case or "")
        col = 3 if action == "新增" else 4
        cur = cells[col].strip()
        parts = [] if cur in ("", "—", "-") else [x.strip() for x in cur.split("；") if x.strip()]
        if key not in parts:
            parts.append(key)
        cells[col] = "；".join(parts)
        lines[last] = _render_row(cells)
    else:
        lines.insert(last + 1, _render_row([
            TODAY, case or "—", "self-evolving-memory",
            key if action == "新增" else "—",
            "—" if action == "新增" else key, "—"]))
    _atomic_write_text(p, "\n".join(lines) + "\n")


# ============================================================ 命令：setcell
def cmd_setcell(a) -> int:
    """按列名精确设置某行单元格 —— 补 `--cols` 覆盖不到 key 列（含「领域」）的缺口。

    ⚠️ 外科手术式：只改指定单元格，不做去重/合并/复用计数。
    """
    p = pathlib.Path(a.file)
    _require_revision(p, a.expected_revision)
    if a.section not in SECTIONS:
        raise ExpError(f"--section 必须是 1-6，收到 {a.section}")
    wants = {}
    for pair in a.col or []:
        if "=" not in pair:
            raise ExpError(f"--col 需为 '列名=值'，收到 {pair!r}")
        k, v = pair.split("=", 1)
        wants[k.strip()] = v

    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    secs = parse(text)
    if a.section not in secs:
        raise ExpError(f"## {a.section} 节不存在")
    info = secs[a.section]
    names = _names_of(lines[info["header_idx"]])
    ki = names.index(SECTIONS[a.section]["key"])
    for name in wants:
        if name not in names:
            raise ExpError(f"## {a.section} 没有列「{name}」。可选：{names}")

    hit = 0
    for idx, cells in info["rows"]:
        cells = list(cells) + [""] * (len(names) - len(cells))
        if ki >= len(cells) or cells[ki].strip() != a.key.strip():
            continue
        for name, val in wants.items():
            j = names.index(name)
            if cells[j] != val:
                print(f"  {name}: {cells[j][:24]!r} → {val[:24]!r}")
                cells[j] = val
        lines[idx] = _render_row(cells)
        hit += 1
    if not hit:
        raise ExpError(f"未找到 ## {a.section} 中键为 {a.key!r} 的行")
    _atomic_write_text(p, "\n".join(lines) + "\n")
    print(f"已更新 {hit} 行（## {a.section} / {a.key}）")
    return 0


# ============================================================ 命令：validate
def cmd_validate(a) -> int:
    p = pathlib.Path(a.file)
    if not p.exists():
        print(f"经验库不存在: {p}")
        return 2
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    secs = parse(text)
    problems, warnings = [], []

    for no in sorted(SECTIONS):
        if no not in secs:
            problems.append(f"缺少 ## {no}「{SECTIONS[no]['title']}」节")
    for no in sorted(secs):
        if no not in SECTIONS:
            continue
        names = _names_of(lines[secs[no]["header_idx"]])
        want = SECTIONS[no]["columns"]
        # 第 3 节第 7 列是"后端实测"列，允许按后端命名（zhihuiya/HimmPat/后端实测…），
        # 但必须：① 以「实测」结尾；② 其余列名与顺序完全一致（防列错位）。
        if no == 3:
            if len(names) != len(want):
                problems.append(
                    f"## 3 列数 {len(names)} != 期望 {len(want)}：{names}")
            else:
                rest_ok = names[:6] == want[:6] and names[7:] == want[7:]
                if not rest_ok:
                    problems.append(
                        f"## 3 表头列名/顺序不匹配（第 7 列可自定义）\n"
                        f"      期望 {want[:6]} + [⟨后端⟩ 实测] + {want[7:]}\n"
                        f"      实际 {names}")
                elif not names[6].endswith("实测"):
                    problems.append(
                        f"## 3 第 7 列应为「⟨后端⟩ 实测」（须以『实测』结尾），实际 {names[6]!r}")
        elif names != want:
            problems.append(f"## {no} 表头不匹配\n      期望 {want}\n      实际 {names}")
        for table_no, table in enumerate(secs[no].get("tables", [])[1:], start=2):
            table_names = _names_of(lines[table["header_idx"]])
            if no == 3:
                header_ok = (
                    len(table_names) == len(want)
                    and table_names[:6] == want[:6]
                    and table_names[7:] == want[7:]
                    and table_names[6].endswith("实测")
                )
            else:
                header_ok = table_names == want
            if not header_ok:
                problems.append(
                    f"## {no} 第 {table_no} 张表表头不匹配\n"
                    f"      期望 {want}\n      实际 {table_names}"
                )
        for idx, cells in secs[no]["rows"]:
            if _is_empty_entry(cells, names):
                continue
            if len(cells) != len(want):
                problems.append(
                    f"L{idx + 1} ## {no} 列数 {len(cells)} != 表头 {len(want)}: {cells[0][:28]!r}")
            for need in ("实测证据", "记录日期"):
                if need in names:
                    v = cells[names.index(need)] if names.index(need) < len(cells) else ""
                    if not v or v in ("-", "—"):
                        warnings.append(f"L{idx + 1} ## {no} 缺「{need}」: {cells[0][:28]!r}")
            for need in ("记录日期", "首次发现"):
                if need in names:
                    j = names.index(need)
                    v = cells[j] if j < len(cells) else ""
                    if v and not re.match(r"^\d{4}-\d{2}-\d{2}$", v.strip()):
                        problems.append(
                            f"L{idx + 1} ## {no} 「{need}」不是 YYYY-MM-DD: {v[:38]!r}")

    # 领域列是否被 key 覆盖（本项目真实发生过的损坏）
    for no in sorted(secs):
        if no not in SECTIONS:
            continue
        names = _names_of(lines[secs[no]["header_idx"]])
        keycol = SECTIONS[no]["key"]
        if "领域" not in names or keycol == "领域":
            continue
        di, ki = names.index("领域"), names.index(keycol)
        for idx, cells in secs[no]["rows"]:
            if _is_empty_entry(cells, names):
                continue
            dom = cells[di] if di < len(cells) else ""
            kv = cells[ki] if ki < len(cells) else ""
            if dom and dom.strip() == kv.strip():
                problems.append(
                    f"L{idx + 1} ## {no} 领域值 == 键值（疑似被 key 覆盖，整行可能右移）")

    out = {"file": str(p), "ok": not problems, "problems": problems, "warnings": warnings}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"结构校验 {p}")
        for w in warnings:
            print("  warn:", w)
        for x in problems:
            print("  FAIL:", x)
        print("  => " + ("通过" if not problems else f"{len(problems)} 处错误"))
    return 0 if not problems else 1


# ============================================================ 命令：prune
def _age_days(s: str):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    if not m:
        return None
    try:
        d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return (_dt.date.today() - d).days


def cmd_prune(a) -> int:
    p = pathlib.Path(a.file)
    _require_revision(p, a.expected_revision)
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    secs = parse(text)
    changes = []
    maintenance = _load_maintenance(p)

    info4 = secs.get(4)
    if info4:
        names = _names_of(lines[info4["header_idx"]])
        di = names.index("首次发现") if "首次发现" in names else None
        ci = names.index("最后确认") if "最后确认" in names else None
        si = names.index("状态") if "状态" in names else None
        if di is not None and si is not None:
            for idx, cells in info4["rows"]:
                if _is_empty_entry(cells, names) or si >= len(cells):
                    continue
                confirmed = cells[ci] if ci is not None and ci < len(cells) else ""
                first_seen = cells[di] if di < len(cells) else ""
                age = _age_days(confirmed or first_seen)
                if age is None:
                    continue
                if age > a.days and "⚠️ 待重验" not in cells[si]:
                    cells[si] = (cells[si] + " ⚠️ 待重验").strip()
                    lines[idx] = _render_row(cells)
                    changes.append(f"L{idx + 1}: {cells[0]} → ⚠️ 待重验（登记 {age} 天）")
                elif age <= a.days and "⚠️ 待重验" in cells[si]:
                    cells[si] = cells[si].replace("⚠️ 待重验", "").strip()
                    lines[idx] = _render_row(cells)
                    changes.append(f"L{idx + 1}: {cells[0]} → 清除待重验（登记 {age} 天）")
    for no in (1, 5):
        info = secs.get(no)
        if not info:
            continue
        names = _names_of(lines[info["header_idx"]])
        if "复用次数" not in names or "记录日期" not in names:
            continue
        ci, di = names.index("复用次数"), names.index("记录日期")
        for idx, cells in info["rows"]:
            if _is_empty_entry(cells, names) or ci >= len(cells):
                continue
            if cells[ci].strip() != "0":
                continue
            age = _age_days(cells[di] if di < len(cells) else "")
            record_id = _record_id(no, cells[0])
            if age is not None and age > 180:
                existing = maintenance["entries"].get(record_id, {})
                if existing.get("status") != "prune_candidate":
                    maintenance["entries"][record_id] = {**existing,
                        "status": "prune_candidate",
                        "section": no,
                        "key": cells[0],
                        "marked_at": TODAY,
                        "reason": f"{age} days without recorded reuse",
                    }
                    changes.append(f"L{idx + 1}: {cells[0]} → ✂️ 剪枝候选（{age} 天从未复用）")

    if not changes:
        print("无需处理：没有过期在先技术，也没有剪枝候选。")
        return 0
    print(f"发现 {len(changes)} 处可自动处理：")
    for c in changes:
        print("  -", c)
    if a.apply:
        _atomic_write_text(p, "\n".join(lines) + "\n")
        _atomic_write_json(_maintenance_path(p), maintenance)
        print(f"\n已写入 {p} 与 {_maintenance_path(p).name}")
    else:
        print("\n（加 --apply 才会写入）")
    return 0


# ============================================================ 命令：reindex
def cmd_reindex(a) -> int:
    p = pathlib.Path(a.file)
    _require_revision(p, a.expected_revision)
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    secs = parse(text)

    by_domain: dict[str, list] = {}
    by_section: dict[str, int] = {}
    for no in sorted(secs):
        info = secs[no]
        names = _names_of(lines[info["header_idx"]])
        di = names.index("领域") if "领域" in names else None
        tags = _tags_for_section(lines, info, names, no, di)
        for idx, cells in info["rows"]:
            if _is_empty_entry(cells, names):
                continue
            by_domain.setdefault(tags.get(idx) or "全局", []).append((no, idx + 1))
            by_section[str(no)] = by_section.get(str(no), 0) + 1

    out = [f"- **## 1** 噪声词典（{by_section.get('1', 0)} 条，全局适用）",
           f"- **## 2** 检索式禁忌（{by_section.get('2', 0)} 条，全局适用）",
           "- **## 3-6** 按领域（域内条目数 / 节-行号）："]
    for dom in sorted(by_domain):
        if dom == "全局":
            continue
        loc = "、".join(f"{no}:L{ln}" for no, ln in by_domain[dom])
        out.append(f"  - `{dom}` — {len(by_domain[dom])} 条（{loc}）")
    out.append("- 条目总览：")
    for no, n in sorted(by_section.items()):
        out.append(f"  - `## {no}` {SECTIONS[int(no)]['title']} — {n} 条")

    block = ("<!-- BEGIN 领域索引（由 expctl.py reindex 生成，勿手工编辑） -->\n"
             "## 领域索引\n\n"
             "> 🔧 本块自动生成。预取请用 "
             "`expctl.py prefetch --domain <本次领域>`，**不必整份读**。\n\n"
             + "\n".join(out) + "\n<!-- END 领域索引 -->\n")

    if "<!-- BEGIN 领域索引" in text:
        text = re.sub(r"<!-- BEGIN 领域索引.*?<!-- END 领域索引 -->\n", block, text, flags=re.S)
    else:
        anchor = "## 0. 维护约定与更新日志"
        if anchor not in text:
            raise ExpError("找不到插入锚点 ## 0")
        text = text.replace(anchor, block + "\n---\n\n" + anchor, 1)
    _atomic_write_text(p, text)
    print(f"领域索引已重建：{len(by_domain)} 个领域，共 {sum(by_section.values())} 条")
    for dom in sorted(by_domain):
        print(f"  {dom:28s} {len(by_domain[dom])} 条")
    return 0


# ============================================================ 命令：commit
def _ensure_git_identity(repo: pathlib.Path) -> str:
    """确保本仓库有提交身份。返回状态说明。

    为什么必须做：缺少 user.email 时 `git commit` 会硬失败
    （init 的首次提交有时能靠主机名自动推断，但后续显式提交不能）——
    这正是"第一次使用就失败"的典型场景，见 SPEC §8 验收测试。
    """
    def get(key):
        r = subprocess.run(["git", "config", key], cwd=repo, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return (r.stdout or "").strip()

    name, email = get("user.name"), get("user.email")
    if name and email:
        return f"已配置（{name} <{email}>）"
    # 仓库级兜底：只影响本仓库，不污染用户全局配置
    if not name:
        subprocess.run(["git", "config", "user.name", "search-memory"], cwd=repo, check=False)
    if not email:
        subprocess.run(["git", "config", "user.email", "search-memory@localhost"],
                       cwd=repo, check=False)
    missing = [k for k, v in (("user.name", name), ("user.email", email)) if not v]
    return (f"⚠️ 原缺 {', '.join(missing)}，已在本仓库设为占位值 "
            f"(search-memory <search-memory@localhost>)。"
            f"建议改成真实身份：git -C \"{repo}\" config user.name/user.email …")


def _has_remote(root: pathlib.Path) -> bool:
    r = subprocess.run(["git", "remote"], cwd=root, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return "origin" in (r.stdout or "")


def cmd_commit(a) -> int:
    root = pathlib.Path(a.file).parent
    if not (root / ".git").exists():
        print(f"不是 git 仓库: {root}（跳过提交）")
        return 0
    _ensure_git_identity(root)
    subprocess.run(["git", "add", "-A", "--", *MANAGED_REPO_FILES], cwd=root, check=False)
    r = subprocess.run(["git", "commit", "-m", a.message], cwd=root,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0:
        print("已提交：", (r.stdout or "").strip().splitlines()[0] if r.stdout else a.message)
    elif "nothing to commit" in out:
        print("无改动，未提交。")
    elif "Author identity unknown" in out or "unable to auto-detect email" in out:
        print("提交失败：git 身份仍不可用。请执行（任选其一）：")
        print("  全局：git config --global user.name \"你的名字\" && "
              "git config --global user.email \"你的邮箱\"")
        print(f"  本库：git -C \"{root}\" config user.name \"…\" && "
              f"git -C \"{root}\" config user.email \"…\"")
        return 1
    else:
        print("提交失败：\n", out)
        return 1

    if not a.no_push and _has_remote(root):
        pr = subprocess.run(["git", "push"], cwd=root, capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        pout = ((pr.stdout or "") + (pr.stderr or "")).strip()
        if pr.returncode == 0:
            print("已推送 origin。")
        else:
            print("⚠️ 推送失败（本地提交已保留，稍后手动 git push 即可）：")
            for line in pout.splitlines()[-4:]:
                print("   ", line)
    elif not a.no_push:
        print("（未配置 origin，跳过推送）")
    if a.status:
        print()
        a.json = False
        cmd_status(a)
    return 0


# ============================================================ 命令：status
def cmd_status(a) -> int:
    p = pathlib.Path(a.file)
    if not p.exists():
        print(f"经验库不存在: {p}")
        return 2
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    secs = parse(text)
    counts, real = {}, 0
    for no, info in sorted(secs.items()):
        names = _names_of(lines[info["header_idx"]])
        n = sum(1 for _, c in info["rows"] if not _is_empty_entry(c, names))
        counts[no] = n
        real += n
    stale = sum(1 for l in lines if "⚠️ 待重验" in l)
    maintenance = _load_maintenance(p)
    prunable = sum(
        1 for item in maintenance["entries"].values()
        if item.get("status") == "prune_candidate"
    )
    retracted = sum(
        1 for item in maintenance["entries"].values()
        if item.get("status") == "retracted"
    )
    negative_global = sum(
        1 for item in maintenance["entries"].values()
        if item.get("scope") == "global"
        and item.get("admission", {}).get("evidence_type") == "negative-observation"
    )
    sizes = ("正常" if len(lines) < 500 else
             "可整份读；建议开始剪枝" if len(lines) <= 1500 else
             "超过 1500 行：必须剪枝或按领域拆分")

    if a.json:
        print(json.dumps({"file": str(p), "revision": _file_revision(p),
                          "lines": len(lines), "entries": real,
                          "by_section": counts, "stale": stale,
                          "prune_candidates": prunable, "retracted": retracted,
                          "negative_observation_global": negative_global,
                          "size_advice": sizes},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"经验库体检  {p}")
    print(f"  行数 {len(lines)} / {p.stat().st_size} 字节  （{sizes}）")
    print(f"  有效条目 {real} 条")
    for no, n in sorted(counts.items()):
        print(f"    ## {no}  {SECTIONS.get(no, {}).get('title', ''):26s} {n:3d} 条")
    print(f"  待重验 {stale} ｜ 剪枝候选 {prunable} ｜ 已作废 {retracted} ｜ "
          f"否定性全局条目 {negative_global}")
    print("\n  add 语法（--cols 顺序如下，「|」分隔，**key 列不用给**）:")
    for no in sorted(SECTIONS):
        s = SECTIONS[no]
        rest = [c for c in s["columns"] if c != s["key"]]
        note = "  ⚠️ 领域即 key，勿在此传领域值" if s["key"] == "领域" else ""
        gate = " --scope global --evidence '<查询/命令+数字>'" if no in (1, 2) else " --scope domain"
        print(f"    ## {no}  --key '{s['key']}' --cols '{'|'.join(rest)}'{gate}{note}")
    return 0


# ============================================================ 命令：init
TEMPLATE = """# 检索经验库

> **这是什么**：跨项目复用的专利检索经验，由检索 skill 在每次检索后写入。
> **你不需要手动维护**：写入 / 去重 / 合并 / 过期标记 / git 提交，全部由 `expctl.py` 自动完成。
> **读取规则**：`## 1` 与 `## 2` 是**全局知识，每次检索无条件预取**；它们是待本次复核的假设，不是自动采用的事实。`## 3`–`## 6` 按「领域」列筛选。
> **写入规则**：只写"编辑过的结论"，不写交底书/说明书/报告/命中矩阵全文。

---

## 0. 维护约定与更新日志

- `## 1`/`## 2` 必须通过 `add --scope global --evidence`入库；否定性观测还必须提供 `--falsified-by`
- 写入前先搜本文件去重：已有且一致 → 不重复写；已有但有新证据 → **原地改那一行**
- 实测推翻旧条目 → 使用 `retract --reason` 保留历史并停止预取；不要删行或靠手改单元格隐藏历史
- `## 4`（在先技术）超过 **90 天**须重新验证后才可写入检索报告
- `最后确认` 列：每次该条被真正用上时更新日期；`复用次数` 累加。长期为 0 的条目可剪枝

### 更新日志

| 日期 | 本次案件 | 触发 skill | 新增 | 修改 | 复用率 |
|------|---------|-----------|------|------|--------|
| ⟨YYYY-MM-DD⟩ | 经验库初始化 | init | — | — | — |

---

## 1. 噪声词典（全局适用）

> 每次检索噪声预探测的产物。**跨领域通用**——同一个关键词在不同领域会撞上同样的噪声。

| 噪声类别 | 判别特征 | 去噪手段（NOT 组） | 领域 | 实测证据 | 记录日期 | 最后确认 | 复用次数 |
|---------|---------|------------------|------|---------|---------|---------|---------|
|  |  |  |  |  |  |  |  |

---

## 2. 检索式禁忌（全局适用）

> 被实测证明无效的写法。**每次建族后必须逐条检查**（触点 2 否决门）。

| 写法 | 为什么无效 | 实测命中 | 替代方案 | 领域 | 记录日期 | 最后确认 |
|------|-----------|---------|---------|------|---------|---------|
|  |  |  |  |  |  |  |

---

## 3. 术语对照 / 多语言词表

> 只在**后端实测有命中**时才写进来。标准术语 ≠ 专利文本实际用词。

| 要素（中文） | 标准名词 | 领域 | 日文 | 韩文 | 德文 | zhihuiya 实测 | 记录日期 | 最后确认 |
|------------|---------|------|------|------|------|-------------|---------|---------|
|  |  |  |  |  |  |  |  |  |

---

## 4. 领域在先技术索引

> ⚠️ **超过 90 天的条目降级为"线索"**，必须本次重新检索验证后才可写入报告。
> 领域由图下的 `### 子标题` 承载——新增同领域条目时不必重复填领域。

### 4.1 ⟨领域名⟩

| 公开号 | 申请人 | 技术要点 | 覆盖特征 | 相关度 | 首次发现 | 最后确认 | 状态 |
|---------|-------|---------|---------|--------|---------|---------|------|
|  |  |  |  |  |  |  |  |

---

## 5. 可复用检索式

> 表现特别好的检索式（高信噪比）。可直接进下一次的检索式族。

| 检索式 | 领域 | 命中量级 | 评价 | 记录日期 | 最后确认 | 复用次数 |
|--------|------|---------|------|---------|---------|---------|
|  |  |  |  |  |  |  |

---

## 6. 领域档案

> 首次做某个领域时建档。记录该领域的检索"地形"。**key 列即「领域」**。

| 领域 | 常用同义词 | 高频申请人 | 有效 IPC/CPC | 检索地形 | 已知陷阱 | 建档日期 |
|------|-----------|-----------|-------------|---------|---------|---------|
|  |  |  |  |  |  |  |
"""

DOMAINS_TEMPLATE = """{
  "_说明": "领域同义词表。prefetch 用它把「本次领域」与经验库里的「领域」标签打通，避免因措辞不同而静默漏读。每个键是一个等价组，组内任意两项视为同一领域。",
  "_维护": "新增领域时把该领域的各种叫法塞进同一个数组；不需要改脚本。",
  "aliases": {
    "引擎层（与领域无关）": [
      "全局",
      "后端接口与故障边界"
    ]
  }
}
"""

TOOLS_TEMPLATE = """{
  "_说明": "抽象能力 → MCP server+tool 映射。先运行 mapping-prompt，按目标电脑的 tools/list 填写；不要照抄示例或猜测命名空间。",
  "schema_version": 2,
  "provider_label": "<仅作显示，不参与路由>",
  "capabilities": {
    "search": {
      "mode": "direct_tool",
      "server": "<运行时 server>",
      "tool": "<运行时 search tool>"
    },
    "count": null,
    "claims": null,
    "description": null,
    "bibliography": null,
    "family": null,
    "abstract_translated": null
  },
  "notes": "count 可配置 direct_tool，或 derived_from_search + fixed_arguments + result_path；不可用时保持 null。"
}
"""

MAINTENANCE_TEMPLATE = """{
  "schema_version": 1,
  "entries": {}
}
"""


def cmd_init(a) -> int:
    repo = pathlib.Path(a.repo).expanduser().resolve()
    repo.mkdir(parents=True, exist_ok=True)
    target = repo / "检索经验.md"

    created = []
    if not target.exists():
        _atomic_write_text(target, TEMPLATE)
        created.append(target.name)
    for name, content in (("domains.json", DOMAINS_TEMPLATE), ("tools.json", TOOLS_TEMPLATE),
                          ("maintenance.json", MAINTENANCE_TEMPLATE)):
        f = repo / name
        if not f.exists():
            _atomic_write_text(f, content)
            created.append(name)
    ga = repo / ".gitattributes"
    if not ga.exists():
        _atomic_write_text(ga, "* text=auto eol=lf\n*.md text eol=lf\n*.json text eol=lf\n")
        created.append(".gitattributes")
    gi = repo / ".gitignore"
    if not gi.exists():
        _atomic_write_text(
            gi,
            ".检索经验.md.lock\ntools.local.json\nruntime-tools.json\n"
            "# 案件材料、运行记录、本机工具映射和凭据不得放进经验库仓库\n",
        )
        created.append(".gitignore")

    print(f"经验库目录: {repo}")
    print("已创建: " + (", ".join(created) if created else "（文件已存在，未覆盖）"))

    if a.no_git:
        print("\n已跳过 git 初始化（--no-git）。经验库仍可正常读写，"
              "但**没有版本历史与异地备份**。\n"
              "  后续想补上：cd 到该目录 → git init → 配远端 → git push")
    elif not (repo / ".git").exists():
        r = subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print("⚠️ git init 失败（git 未安装？见 SPEC §3.1）：",
                  (r.stderr or "").strip()[:120])
            return 1
        # 避免中文 Windows 的行尾转换
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=False)
        subprocess.run(["git", "config", "core.eol", "lf"], cwd=repo, check=False)
        print("git 身份: " + _ensure_git_identity(repo))
        subprocess.run(["git", "add", "-A", "--", *MANAGED_REPO_FILES], cwd=repo, check=False)
        subprocess.run(["git", "commit", "-q", "-m", "初始化检索经验库"], cwd=repo, check=False)
        print("已初始化 git 仓库并完成首次提交。")

    print(f"\n下一步：\n  1) 用 --file \"{target}\" 跑 validate / status 验证")
    if a.no_git:
        print("  2) （已用 --no-git，跳过远程仓库配置）")
    else:
        print(f"  2) 创建**私有**远程仓库后：\n       cd \"{repo}\" && git remote add origin <地址> && git ls-remote origin && git push -u origin <分支>")
    print("  3) 运行 mapping-prompt，让宿主 Agent 按当前 tools/list 填写 server-aware tools.json")
    return 0


# ============================================================ main
def main() -> int:
    ap = argparse.ArgumentParser(prog="expctl", description=f"专利检索自进化库维护工具 v{VER}")
    ap.add_argument("--file", default=str(DEFAULT_FILE),
                    help=f"经验库路径（默认自动探测：{DEFAULT_FILE}）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prefetch", help="检索前精确预取（只输出相关行）")
    p.add_argument("--domain", required=True)
    p.add_argument("--aliases", default=None, help="领域同义词表路径（默认同目录 domains.json）")
    p.add_argument("--json", action="store_true", help="输出完整结构化记录（适合程序消费）")
    p.add_argument("--out", default=None, help="把完整预取结果写成一次运行记录 JSON")
    p.set_defaults(fn=cmd_prefetch)

    p = sub.add_parser("mark-used", help="按运行记录中的稳定 ID 标记一条经验被实际采用")
    p.add_argument("--run", required=True, help="prefetch --out 生成的运行记录 JSON")
    p.add_argument("--id", required=True, help="要标记的经验 ID（exp-...）")
    p.add_argument("--date", default=TODAY)
    p.set_defaults(fn=cmd_mark_used)

    p = sub.add_parser("mark-outcome", help="记录已采用经验在本次检索中被确认或推翻")
    p.add_argument("--run", required=True, help="prefetch --out 生成的运行记录 JSON")
    p.add_argument("--id", required=True, help="已 mark-used 的经验 ID")
    p.add_argument("--result", required=True, choices=("confirmed", "rejected", "neutral"))
    p.add_argument("--note", required=True, help="本次验证依据")
    p.set_defaults(fn=cmd_mark_outcome)

    p = sub.add_parser("finish-run", help="结束一次检索并自动计算实际复用率")
    p.add_argument("--run", required=True, help="prefetch --out 生成的运行记录 JSON")
    p.add_argument("--added", type=int, default=0, help="本次新增经验条数")
    p.add_argument("--artifacts-dir", default=None, help="可选：需要完整登记的产物根目录")
    p.add_argument(
        "--artifact", action="append", default=[],
        help="产物相对路径，可重复；可用 role=path，如 candidate_matrix=matrix.csv",
    )
    p.set_defaults(fn=cmd_finish_run)

    p = sub.add_parser("verify-run", help="只读重算运行指标并可选比对报告/产物")
    p.add_argument("--run", required=True, help="prefetch --out 生成的运行记录 JSON")
    p.add_argument("--report", default=None, help="可选：UTF-8 JSON 报告审计声明")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_verify_run)

    p = sub.add_parser("add", help="写回一条（自动去重/部分更新/入库门禁/日志）")
    p.add_argument("--section", type=int, required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--domain", default=None, help="## 4 新增记录的领域子标题（该节必填）")
    p.add_argument("--scope", choices=("global", "domain"), default=None,
                   help="知识作用域；## 1/2 固定为 global，其它节固定为 domain")
    p.add_argument("--evidence", default=None,
                   help="全局条目必填：可复现的查询/命令及返回数字")
    p.add_argument("--falsified-by", default=None,
                   help="全局否定性观测必填：为排除其它解释做的同构/隔离测试")
    p.add_argument("--cols", default="", help="按 status 打印的顺序，「|」分隔，key 列不用给")
    p.add_argument("--date", default=TODAY)
    p.add_argument("--clear", action="store_true", help="不自动填日期列（用于显式清空）")
    p.add_argument("--case", default=None, help="本次案件名，写入更新日志")
    p.add_argument("--no-log", action="store_true")
    p.add_argument("--expected-revision", default=None,
                   help="可选：status 返回的 revision；不一致时拒绝覆盖")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("retract", help="作废一条经验：保留历史但不再预取")
    p.add_argument("--section", type=int, required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--superseded-by", default=None)
    p.add_argument("--expected-revision", default=None)
    p.set_defaults(fn=cmd_retract)

    p = sub.add_parser("setcell", help="按列名精确改某格（补 --cols 覆盖不到 key/领域 列）")
    p.add_argument("--section", type=int, required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--col", action="append", required=True, help="列名=值，可重复")
    p.add_argument("--expected-revision", default=None)
    p.set_defaults(fn=cmd_setcell)

    p = sub.add_parser("validate", help="结构校验（非 0 退出 = 别提交）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("prune", help="过期在先技术标记 + 剪枝候选")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--expected-revision", default=None)
    p.set_defaults(fn=cmd_prune)

    p = sub.add_parser("reindex", help="重建顶部领域索引块")
    p.add_argument("--expected-revision", default=None)
    p.set_defaults(fn=cmd_reindex)

    p = sub.add_parser("doctor",
                       help="环境自检：找出名字带旧后端名的 skill 等需要你决策的项")
    p.add_argument("--json", action="store_true")
    p.add_argument("--skills-root", default=None, help="可选：对该技能根执行 FM-1..FM-5 架构完整性检查")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("tools", help="校验并输出当前后端的能力到工具名映射")
    p.add_argument("--json", action="store_true")
    p.add_argument("--catalog", default=None, help="运行时工具目录 JSON；默认读取经验库同目录 runtime-tools.json")
    p.add_argument("--mapping", default=None, help="本机工具映射；默认 EXPCTL_TOOLS → tools.local.json → tools.json")
    p.set_defaults(fn=cmd_tools)

    p = sub.add_parser("mapping-prompt", help="生成安装时的运行时工具映射 prompt")
    p.add_argument("--out", default=None, help="可选：把 prompt 写入文件")
    p.set_defaults(fn=cmd_mapping_prompt)

    p = sub.add_parser("commit", help="提交 + 推送 + 体检")
    p.add_argument("-m", "--message", default="更新检索经验库")
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--no-status", dest="status", action="store_false")
    p.set_defaults(fn=cmd_commit, status=True)

    p = sub.add_parser("status", help="体检 + 打印 add 语法")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("init", help="新建经验库：建目录 + 初始化 git + 写出全部文件（模板内联于脚本）")
    p.add_argument("--repo", required=True, help="经验库目录（不存在则创建）")
    p.add_argument("--no-git", action="store_true",
                   help="只建文件、不初始化 git（无版本历史与远程备份；见 SPEC §3.2 路径 C）")
    p.set_defaults(fn=cmd_init)

    a = ap.parse_args()
    if not hasattr(a, "json"):
        a.json = False
    try:
        mutating = {"add", "retract", "setcell", "prune", "reindex", "mark-used",
                    "mark-outcome", "finish-run"}
        if a.cmd in mutating:
            lock_target = pathlib.Path(a.file)
            if a.cmd == "mark-used":
                run = _read_json_utf8(pathlib.Path(a.run), "运行记录")
                lock_target = pathlib.Path(run.get("file") or a.file)
            elif a.cmd in {"mark-outcome", "finish-run"}:
                lock_target = pathlib.Path(a.run)
            with _library_lock(lock_target):
                return a.fn(a)
        return a.fn(a)
    except ExpError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
