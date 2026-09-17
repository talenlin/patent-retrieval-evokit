#!/usr/bin/env python3
"""Safely append Patent Retrieval EvoKit hooks to one host SKILL.md."""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import tempfile


BEGIN = "<!-- patent-retrieval-evokit:begin -->"
END = "<!-- patent-retrieval-evokit:end -->"
BLOCK = f"""{BEGIN}
## Retrieval experience companion

When this skill performs patent retrieval and `$patent-retrieval-evokit` is available, use it as a companion memory layer without changing this skill's search logic or deliverables.

- Before retrieval: run its Hook A with this task's actual technical domain and a case-local run artifact.
- During query refinement: run Hook B only for experience that actually changes the search or verification path.
- After this skill's normal deliverable is complete: run Hook C, then append the memory metrics to the handoff.
- Create the run artifact with `prefetch --out`; never save machine-readable output with shell redirection.
- Derive all counts from `finish-run` / `verify-run`, and generate CSV/JSON through a standard writer rather than manual delimiter concatenation.
- If the companion is unavailable, state that memory was skipped and continue this skill's original workflow.
{END}
"""


def render_updated(original: str) -> tuple[str, bool]:
    has_begin, has_end = BEGIN in original, END in original
    if has_begin != has_end:
        raise ValueError("found only one integration marker; repair the host skill manually")
    if has_begin:
        if original.count(BEGIN) != 1 or original.count(END) != 1:
            raise ValueError("duplicate integration markers; repair the host skill manually")
        return original, False
    if not original.startswith("---\n") and not original.startswith("---\r\n"):
        raise ValueError("SKILL.md does not start with YAML frontmatter")
    return original.rstrip() + "\n\n" + BLOCK, True


def atomic_write(path: pathlib.Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        os.replace(tmp_name, path)
    finally:
        pathlib.Path(tmp_name).unlink(missing_ok=True)


def load_catalog(path: pathlib.Path) -> dict[str, set[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    servers = raw.get("servers")
    if not isinstance(servers, dict):
        raise ValueError("runtime catalog must contain a servers object")
    catalog = {}
    for server, entries in servers.items():
        if not isinstance(server, str) or not isinstance(entries, list):
            raise ValueError("servers must map server names to tool arrays")
        catalog[server] = {
            entry if isinstance(entry, str) else entry.get("name")
            for entry in entries
            if isinstance(entry, str) or isinstance(entry, dict)
        }
        catalog[server].discard(None)
    return catalog


def namespace_blockers(text: str, catalog: dict[str, set[str]]) -> list[str]:
    blockers = []
    for server, tool in re.findall(r"(?<![\w-])([A-Za-z_][\w-]*)\.([A-Za-z_][\w-]*)(?![\w-])", text):
        if server not in catalog or tool in catalog[server]:
            continue
        owners = sorted(name for name, tools in catalog.items() if tool in tools)
        if owners:
            blockers.append(f"{server}.{tool}: tool belongs to {owners}, not {server}")
        else:
            blockers.append(f"{server}.{tool}: tool is absent from the runtime catalog")
    return sorted(set(blockers))


def architecture_report(path: pathlib.Path, skills_root: pathlib.Path) -> dict:
    checker = pathlib.Path(__file__).with_name("check_skill_integrity.py")
    spec = importlib.util.spec_from_file_location("patent_retrieval_evokit_integrity", checker)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load check_skill_integrity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.scan(skills_root, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", required=True, type=pathlib.Path, help="host SKILL.md")
    parser.add_argument("--apply", action="store_true", help="apply after showing validation")
    parser.add_argument("--backup-dir", type=pathlib.Path, default=None)
    parser.add_argument("--catalog", type=pathlib.Path, default=None,
                        help="runtime-tools.json used for read-only namespace checks")
    parser.add_argument("--skills-root", type=pathlib.Path, default=None,
                        help="sibling skill root for architecture checks; defaults to host parent")
    parser.add_argument("--json", action="store_true", help="machine-readable dry-run report")
    args = parser.parse_args()

    path = args.skill.expanduser().resolve()
    if not path.is_file() or path.name != "SKILL.md":
        raise SystemExit(f"not a SKILL.md file: {path}")
    original = path.read_text(encoding="utf-8")
    skills_root = (args.skills_root or path.parent.parent).expanduser().resolve()
    try:
        architecture = architecture_report(path, skills_root)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"skill architecture check failed: {error}") from error
    architecture_blockers = [item for item in architecture["findings"] if item["level"] == "blocker"]
    ns_blockers = []
    if args.catalog:
        try:
            ns_blockers = namespace_blockers(original, load_catalog(args.catalog.expanduser().resolve()))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f"runtime catalog is invalid: {error}") from error
        if ns_blockers and not args.json:
            print("namespace blockers found; repair the host skill before adding memory hooks:")
            for blocker in ns_blockers:
                print(f"- {blocker}")
        elif not args.json:
            print("runtime namespace check passed")
    elif not args.json:
        print("runtime catalog not supplied; namespace check skipped")
    try:
        updated, changed = render_updated(original)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    diff = difflib.unified_diff(
        original.splitlines(), updated.splitlines(),
        fromfile=str(path), tofile=str(path), lineterm="",
    )
    diff_text = "\n".join(diff)
    if args.json:
        print(json.dumps({
            "skill": str(path), "skills_root": str(skills_root), "changed": changed,
            "architecture": architecture, "namespace_blockers": ns_blockers,
            "diff": diff_text, "apply_requested": args.apply,
        }, ensure_ascii=False, indent=2))
    else:
        for item in architecture["findings"]:
            print(f"[{item['level']}] {item['code']}: {item['message']}")
        print(diff_text)
    if ns_blockers or architecture_blockers:
        if not args.json:
            print("integration blocked; repair blockers before adding memory hooks")
        return 2
    if not changed:
        if not args.json:
            print(f"already integrated: {path}")
        return 0
    if not args.apply:
        if not args.json:
            print("dry run only; rerun with --apply after reviewing this diff")
        return 0

    backup_dir = (args.backup_dir or path.parent / ".patent-retrieval-evokit-backups").resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = backup_dir / f"{path.parent.name}-{stamp}-SKILL.md"
    shutil.copy2(path, backup)
    atomic_write(path, updated)
    if not path.read_text(encoding="utf-8").startswith(original.rstrip()):
        raise SystemExit("post-write prefix verification failed; restore the backup")
    print(f"integrated: {path}")
    print(f"backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
