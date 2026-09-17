#!/usr/bin/env python3
"""Build the portable Retrieve Experience v2 Codex plugin package."""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import tempfile
import zipfile


ROOT = pathlib.Path(__file__).resolve().parent
TEMPLATE = ROOT / "plugin" / "retrieve-experience-v2"
NAME = "retrieve-experience-v2"
VERSION = "1.0.0"
ARCHIVE_BASENAME = "patent-retrieval-evokit"


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    zip_path = dist / f"{ARCHIVE_BASENAME}-v{VERSION}.zip"
    checksum_path = dist / f"{ARCHIVE_BASENAME}-v{VERSION}.zip.sha256"

    with tempfile.TemporaryDirectory(prefix="retrieve-experience-build-") as tmp:
        stage = pathlib.Path(tmp) / NAME
        shutil.copytree(
            TEMPLATE,
            stage,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        copies = {
            ROOT / "expctl.py": stage / "scripts" / "expctl.py",
            ROOT / "tests" / "test_expctl_cli.py": stage / "tests" / "test_expctl_cli.py",
            ROOT / "tests" / "test_accounting_and_admission.py":
                stage / "tests" / "test_accounting_and_admission.py",
            ROOT / "GUIDANCE_SPEC.md": stage / "docs" / "GUIDANCE_SPEC.md",
            ROOT / "README.md": stage / "docs" / "TOOL_REFERENCE.md",
            ROOT / "docs" / "QUICKSTART.zh-CN.md": stage / "docs" / "QUICKSTART.zh-CN.md",
            ROOT / "docs" / "AGENT-START-PROMPT.zh-CN.md":
                stage / "docs" / "AGENT-START-PROMPT.zh-CN.md",
            ROOT / "docs" / "PRIVACY-BOUNDARY.zh-CN.md":
                stage / "docs" / "PRIVACY-BOUNDARY.zh-CN.md",
        }
        for source, target in copies.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        manifest_files = {}
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                manifest_files[path.relative_to(stage).as_posix()] = sha256(path)
        manifest = {"name": NAME, "version": VERSION, "files": manifest_files}
        (stage / "PACKAGE_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(stage.rglob("*")):
                if not path.is_file():
                    continue
                info = zipfile.ZipInfo(
                    path.relative_to(stage.parent).as_posix(),
                    date_time=(2026, 9, 15, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())

    checksum_path.write_text(f"{sha256(zip_path)}  {zip_path.name}\n", encoding="ascii")
    print(zip_path)
    print(checksum_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
