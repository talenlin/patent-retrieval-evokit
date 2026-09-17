#!/usr/bin/env python3
"""Read-only integrity checks for a directory of Agent skills."""

from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import re
import sys


HANDOFF_MARKERS = ("交给", "退回", "改用", "并行", "关联 Skill", "协同", "用哪个", "首选", "转交",
                   "hand off", "fallback", "fall back", "prefer", "delegate")
DIRECTIVE_MARKERS = HANDOFF_MARKERS + ("不可用", "if unavailable", "when unavailable")
TOKEN_RE = re.compile(r"`([a-z0-9][a-z0-9-]{1,63})`")
ALLOWLIST = {"python-docx", "file-download", "data-path", "skill-update", "firecrawl-mcp"}


def frontmatter(text: str) -> dict[str, str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}
    lines = normalized[4:end].splitlines()
    values: dict[str, str] = {}
    i = 0
    while i < len(lines):
        match = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", lines[i])
        if not match:
            i += 1
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in {"|", ">"}:
            chunks = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].startswith((" ", "\t"))):
                chunks.append(lines[i].strip())
                i += 1
            values[key] = " ".join(chunk for chunk in chunks if chunk)
            continue
        values[key] = value.strip("\"'")
        i += 1
    return values


def finding(code: str, level: str, message: str, **details) -> dict:
    return {"code": code, "level": level, "message": message, **details}


def common_prefix_ratio(a: str, b: str) -> tuple[int, float]:
    length = 0
    for left, right in zip(a, b):
        if left != right:
            break
        length += 1
    return length, length / max(len(a), len(b), 1)


def discover(skills_root: pathlib.Path) -> list[dict]:
    skills = []
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        meta = frontmatter(text)
        if meta.get("name"):
            skills.append({"name": meta["name"], "description": meta.get("description", ""),
                           "whenToUse": meta.get("whenToUse", ""), "path": skill_md, "text": text})
    return skills


def scan(skills_root: pathlib.Path, target: pathlib.Path | None = None) -> dict:
    skills_root = skills_root.resolve()
    skills = discover(skills_root)
    known = {item["name"] for item in skills}
    findings = []

    for item in skills:
        directive = item.get("whenToUse", "")
        if directive and any(marker.casefold() in directive.casefold() for marker in DIRECTIVE_MARKERS):
            findings.append(finding(
                "FM-1", "warning", "whenToUse contains routing/behavior instructions that may be invisible to the model",
                skill=item["name"], file=str(item["path"]),
            ))

    for left, right in itertools.combinations(skills, 2):
        a, b = left["description"], right["description"]
        if not a or not b:
            continue
        prefix, ratio = common_prefix_ratio(a, b)
        if a == b:
            findings.append(finding(
                "FM-2", "blocker", "two active skills have identical descriptions",
                skills=[left["name"], right["name"]], common_prefix=prefix, ratio=1.0,
            ))
        elif ratio > 0.80:
            findings.append(finding(
                "FM-2", "warning", "two active skill descriptions share more than 80% literal prefix",
                skills=[left["name"], right["name"]], common_prefix=prefix, ratio=round(ratio, 4),
            ))

    scan_files = list(skills_root.glob("*/SKILL.md"))
    scan_files += list(skills_root.glob("*/references/**/*.md"))
    scan_files += list(skills_root.glob("*/agents/*.yaml"))
    scan_files += list(skills_root.glob("*/agents/*.yml"))
    for path in sorted(set(scan_files)):
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            folded = line.casefold()
            if any(marker.casefold() in folded for marker in HANDOFF_MARKERS):
                for token in TOKEN_RE.findall(line):
                    if token not in known and token not in ALLOWLIST:
                        findings.append(finding(
                            "FM-4", "warning", "handoff references an unregistered skill",
                            token=token, file=str(path), line=line_no,
                        ))
            if ("不可用" in line or "unavailable" in folded) and any(
                marker in folded for marker in ("退回", "改用", "fallback", "fall back")
            ):
                targets = TOKEN_RE.findall(line)
                if targets:
                    findings.append(finding(
                        "FM-3", "warning", "fallback to another skill creates persistent host coupling; prefer self-contained degradation",
                        targets=targets, file=str(path), line=line_no,
                    ))

    for archive in sorted(skills_root.glob("*")):
        if not archive.is_dir() or not any(token in archive.name.casefold() for token in ("archive", "disabled")):
            continue
        archived = list(archive.rglob("SKILL.md"))
        if not archived:
            continue
        directly_discovered = archive / "SKILL.md" in {item["path"] for item in skills}
        findings.append(finding(
            "FM-5", "blocker" if directly_discovered else "info",
            "archive is still discoverable as an active skill" if directly_discovered else
            "archive-like directory contains skills; verify it is outside every runtime scan root",
            directory=str(archive), count=len(archived),
        ))

    if target is not None:
        target = target.resolve()
        findings = [item for item in findings if (
            str(target) in str(item.get("file", "")) or
            frontmatter(target.read_text(encoding="utf-8", errors="replace")).get("name") in item.get("skills", []) or
            item["code"] in {"FM-2", "FM-4", "FM-5"}
        )]
    return {"skills_root": str(skills_root), "skill_count": len(skills), "findings": findings,
            "summary": {level: sum(1 for item in findings if item["level"] == level)
                        for level in ("blocker", "warning", "info")}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", required=True, type=pathlib.Path)
    parser.add_argument("--target", type=pathlib.Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = scan(args.skills_root, args.target)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"skills: {report['skill_count']}  findings: {len(report['findings'])}")
        for item in report["findings"]:
            print(f"[{item['level']}] {item['code']}: {item['message']}")
            if item.get("file"):
                print(f"  {item['file']}:{item.get('line', '')}")
    return 1 if report["summary"]["blocker"] or report["summary"]["warning"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
