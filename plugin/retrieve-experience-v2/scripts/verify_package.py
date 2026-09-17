#!/usr/bin/env python3
"""Verify files and hashes in an extracted Retrieve Experience package."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib


FORBIDDEN_PARTS = {".git", "__pycache__", ".pytest_cache", "检索经验库"}


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=pathlib.Path, default=pathlib.Path(__file__).parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = root / "PACKAGE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    for relative, expected in manifest.get("files", {}).items():
        path = root / relative
        if not path.is_file():
            problems.append(f"missing: {relative}")
        elif sha256(path) != expected:
            problems.append(f"hash mismatch: {relative}")
    for path in root.rglob("*"):
        if any(part in FORBIDDEN_PARTS for part in path.relative_to(root).parts):
            problems.append(f"forbidden path: {path.relative_to(root)}")
    if manifest.get("name") != "retrieve-experience-v2" or manifest.get("version") != "2.3.0":
        problems.append("manifest identity/version mismatch")
    if problems:
        print("package verification failed")
        for problem in sorted(set(problems)):
            print(f"- {problem}")
        return 1
    print(f"package verified: {len(manifest['files'])} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
