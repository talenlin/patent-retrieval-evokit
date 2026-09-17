# Host Skill Integration

Use this guide only when adding Patent Retrieval EvoKit to an existing patent-search skill.

## Boundary

The host skill remains authoritative for retrieval tools, query syntax, search iterations, patent interpretation, and outputs. This plugin adds three lifecycle Hooks and must not rename the host, copy its full instructions, reorder its workflow, or change its deliverables.

## Platform visibility

Routing decisions must be stated early in frontmatter `description`. Behavior required after loading must be stated in the Markdown body. Do not put required routing or behavior only in provider metadata such as `whenToUse`, because some runtimes omit it from both discovery and loaded instructions.

Descriptions of sibling skills must be distinguishable near the beginning. Do not copy the same description into an enhanced variant.

Degradation must be self-contained: if this companion is unavailable, continue the same host's original workflow without memory and report that limitation. Never add “fall back to another skill” to this Adapter; that creates permanent coupling and dangling references after uninstall.

## Minimal adapter block

Insert the following block once, preferably after the host skill's general workflow or before its completion criteria:

```markdown
<!-- patent-retrieval-evokit:begin -->
## Retrieval experience companion

When this skill performs patent retrieval and `$patent-retrieval-evokit` is available, use it as a companion memory layer without changing this skill's search logic or deliverables.

- Before retrieval: run its Hook A with this task's actual technical domain and a case-local run artifact.
- During query refinement: run Hook B only for experience that actually changes the search or verification path.
- After this skill's normal deliverable is complete: run Hook C, then append the memory metrics to the handoff.
- Create the run artifact with `prefetch --out`; never save machine-readable output with shell redirection.
- Derive all counts from `finish-run` / `verify-run`, and generate CSV/JSON through a standard writer rather than manual delimiter concatenation.
- If the companion is unavailable, state that memory was skipped and continue this skill's original workflow.
<!-- patent-retrieval-evokit:end -->
```

First run `<plugin-root>/scripts/check_skill_integrity.py --skills-root <skills-root> --json`. Then use `<plugin-root>/scripts/integrate_host_skill.py --skills-root <skills-root> --catalog <runtime-tools.json> --json` for a dry-run diff, namespace check, architecture diagnostics, and idempotent append. Inspect every target before applying; capability detection is a human or Agent judgment, not a filename match. A blocker must be fixed separately before adding memory Hooks—the integration tool must not rewrite host retrieval logic.

## Acceptance checks

1. The original `SKILL.md` remains a byte-for-byte prefix except for an intentionally normalized final newline.
2. Exactly one begin marker and one end marker exist.
3. YAML frontmatter remains the first block and is unchanged.
4. The host's original tests, steps, tool mapping, outputs, and authorization boundaries remain unchanged.
5. A cold-start test can complete when the plugin or library is unavailable.
6. A memory-enabled test produces one run artifact and reports measured metrics.
7. The Adapter block contains only abstract Hook names and no concrete MCP server or tool names.
8. `description` remains routing-distinct; required behavior is in the body, not only `whenToUse`.
9. No fallback to another Skill or dangling handoff reference is introduced.
10. Global additions use reproducible evidence; negative observations also record an isolation/falsification attempt.
11. `finish-run` rejects altered totals, per-section offset errors, duplicate reuse IDs, unknown IDs, and undeclared/ragged artifacts.
