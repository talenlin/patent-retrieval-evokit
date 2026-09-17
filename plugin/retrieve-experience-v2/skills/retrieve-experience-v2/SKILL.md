---
name: retrieve-experience-v2
description: Companion memory layer for existing patent-search skills. Use when a host patent-search skill declares Retrieve Experience hooks, when adding those hooks to a patent-search skill, or when prefetching and validating reusable patent-retrieval experience; do not use as a patent-search backend or for general research.
metadata:
  version: "1.0.0"
---

# Retrieve Experience v2

Act as a companion Module around the active patent-search skill. The host skill owns search strategy, backend calls, claim analysis, and deliverables. This plugin owns only reusable experience state and its audit trail.

Resolve the plugin root two levels above this file. Invoke `<plugin-root>/scripts/expctl.py`; on Windows prefer `py`, elsewhere use Python 3.10+.

Read [TOOL-MAPPING.md](references/TOOL-MAPPING.md) during first installation on each computer. Read [HOST-INTEGRATION.md](references/HOST-INTEGRATION.md) when installing hooks into a host skill. Read [RUN-ACCOUNTING.md](references/RUN-ACCOUNTING.md) before finishing a real retrieval run or admitting/retracting knowledge. Before integrating, removing, or archiving skills, run `<plugin-root>/scripts/check_skill_integrity.py --skills-root <root>`. Read `<plugin-root>/docs/GUIDANCE_SPEC.md` only when changing or evaluating this plugin itself.

## Runtime Hooks

### Hook A: before search

Resolve the experience library from the host's explicit configuration or `EXPCTL_FILE`. Use only abstract capabilities (`search`, `count`, `claims`, `description`, `bibliography`, `family`, `abstract_translated`) in plugin instructions. Resolve concrete tools through the verified machine-local mapping.

Run `validate`, `doctor`, and `tools --catalog <runtime-tools.json> --json`, then `prefetch --domain <actual-domain> --out <case-run.json>`. The `--out` file is the authoritative denominator and section distribution for this run. Never capture machine-readable output with shell redirection. If no verified runtime catalog and `tools.local.json` exist, run `mapping-prompt` and pause plugin activation for mapping; continue the host skill without memory rather than guessing.

Use returned experience as hypotheses, never as current-case facts. If the plugin or library is unavailable, report the degradation and continue the host skill unchanged unless the host requires memory as a hard dependency.

### Hook B: while refining queries

Call `mark-used` only when an experience item actually changes a query, exclusion group, verification path, or judgment. After observable testing, call `mark-outcome` with `confirmed`, `rejected`, or `neutral` and a concrete note. Do not infer outcomes from prior reuse.

### Hook C: after the host deliverable

Add only reusable, evidence-backed mechanisms through `add`; exclude disclosure text, reports, claim charts, credentials, and case-specific conclusions. Sections 1 and 2 are global instructions: require reproducible `--evidence`; a zero/empty/error observation also requires `--falsified-by`. Use `retract` for a disproved rule instead of deleting or editing history into invisibility.

Structured CSV/JSON artifacts must be written by a standard library writer, never hand-concatenated. If this run has an artifact directory, register every file with `finish-run --artifacts-dir ... --artifact ...`; otherwise do not claim the directory was audited. Run `validate`, `finish-run`, then `verify-run`. Copy all reported counts from the run artifact, never from memory or manual arithmetic.

## Invariants

- `expctl.py` is the only library writer.
- Preserve stable IDs, partial-update semantics, revision guards, locking, atomic writes, and managed-file Git allowlisting.
- Rejected experience enters review; do not silently delete, auto-correct, or auto-promote it.
- A correct total never excuses an incorrect per-section distribution; accounting differences block `finish-run`.
- All plugin-written machine files are UTF-8 without BOM. PowerShell `>` is not a run-record interface.
- Concrete tool names never belong in plugin or host Hook instructions. They come from runtime inspection plus machine-local `tools.local.json`.
- Full-text failure requires a confidence downgrade; never imply claims or description were verified.
- Ask before installing software, changing host skills, creating remotes, pushing, migrating real data, or deleting records.
