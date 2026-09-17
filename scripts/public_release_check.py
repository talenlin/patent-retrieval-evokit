#!/usr/bin/env python3
"""Fail a public release when common private artifacts or secrets are present."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


SKIP_PARTS = {".git", "dist", ".ci-extract", "__pycache__", ".pytest_cache"}
FORBIDDEN_FILES = {
    "tools.local.json",
    "runtime-tools.json",
    ".env",
    "cookies.json",
    "storage-state.json",
}
TEXT_SUFFIXES = {
    "", ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".py", ".ps1", ".sh", ".ini", ".cfg", ".csv", ".tsv",
}


def content_rules() -> list[tuple[str, re.Pattern[str]]]:
    legacy_id = "retrieve-experience" + "-v2"
    old_repo = "github.com/talenlin/" + "Retrieve-experience" + "-v2"
    private_key = "-----BEGIN " + "PRIVATE KEY-----"
    return [
        ("legacy-public-identifier", re.compile(re.escape(legacy_id), re.IGNORECASE)),
        ("old-private-repository-url", re.compile(re.escape(old_repo), re.IGNORECASE)),
        ("windows-user-path", re.compile(r"[A-Za-z]:\\Users\\(?!<)[^\\\s]+\\", re.IGNORECASE)),
        ("unix-user-path", re.compile(r"/(?:Users|home)/(?!<)[^/\s]+/")),
        ("github-token", re.compile(r"(?:ghp|github_pat)_[A-Za-z0-9_]{16,}")),
        ("openai-style-token", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
        ("aws-access-key", re.compile(r"AKIA[A-Z0-9]{16}")),
        ("private-key", re.compile(re.escape(private_key))),
        ("authorization-bearer", re.compile(r"Authorization\s*[:=]\s*Bearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE)),
    ]


def iter_files(root: pathlib.Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.relative_to(root).parts):
            continue
        yield path


def scan(root: pathlib.Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in iter_files(root):
        relative = path.relative_to(root).as_posix()
        lower_name = path.name.lower()
        if "retrieve-experience" + "-v2" in relative.lower():
            findings.append({"rule": "legacy-public-path", "path": relative, "line": None})
        runtime_artifact = (
            path.name == "检索经验.md"
            or (lower_name.startswith("case-run") and lower_name.endswith(".json"))
            or (lower_name.startswith("run-") and lower_name.endswith(".json"))
        )
        if lower_name in FORBIDDEN_FILES:
            findings.append({"rule": "forbidden-file", "path": relative, "line": None})
        if runtime_artifact:
            findings.append({"rule": "runtime-or-experience-artifact", "path": relative, "line": None})
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for rule, pattern in content_rules():
                if pattern.search(line):
                    findings.append({"rule": rule, "path": relative, "line": line_number})
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    findings = scan(root)
    result = {"ok": not findings, "root": str(root), "findings": findings}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif findings:
        for item in findings:
            location = item["path"]
            if item["line"] is not None:
                location += f":{item['line']}"
            print(f"BLOCK {item['rule']}: {location}")
    else:
        print("Public release check passed.")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
