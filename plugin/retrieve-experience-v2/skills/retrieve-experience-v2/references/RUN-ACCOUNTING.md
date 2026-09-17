# Run Accounting and Knowledge Admission

Read this guide when closing a real patent-search run, adding a global rule, retracting an old rule, or auditing host artifacts.

## Authoritative run record

Create exactly one case-local record with `prefetch --out <run.json>`. This JSON is the authority for the prefetched denominator, per-section distribution, stable IDs, source lines, reuse set, outcomes, and final metrics.

Do not use PowerShell `>` or another shell redirect to save `prefetch`, `status`, or other machine-readable output. Windows redirection may produce UTF-16. Plugin-written files use UTF-8 without BOM; `verify-run` detects UTF-16 and tells the operator to use `--out`.

Use `mark-used` only for an ID present in this run. Do not manually add IDs or counts to the JSON. `finish-run` rejects duplicate IDs, unknown IDs, a changed prefetched total, and a per-section distribution that differs even when the grand total happens to match.

## Global knowledge admission

Sections 1 and 2 are injected into every later search, so their admission threshold is higher than domain sections.

```text
expctl.py --file <library> add --section 1 --key <key> \
  --cols <values> --scope global \
  --evidence "<reproducible query or command and numeric result>"
```

If the supporting observation is zero results, empty output, an error, or a failure, first test competing explanations and record that isolation test:

```text
expctl.py --file <library> add --section 2 --key <key> \
  --cols <values> --scope global \
  --evidence "<query that returned 0/empty/error>" \
  --falsified-by "<isomorphic or isolation query and result>"
```

A single empty result does not prove a field, syntax, or tool is broken. First suspect the tested terms, grouping, spelling, coverage, or current data. Negative-observation global entries remain visibly flagged during prefetch and in `doctor`.

Domain sections use `--scope domain`; they do not require the global evidence gate, although evidence remains recommended.

## Retraction

When a stored rule is disproved, retain its historical Markdown row and make the retraction explicit:

```text
expctl.py --file <library> retract --section <n> --key <key> \
  --reason "<reproducible contrary evidence>" [--superseded-by <new-key>]
```

Retracted entries remain traceable in `maintenance.json`, no longer appear in prefetch, and cannot be silently reactivated through `add`.

## Structured artifacts

Generate CSV with a standard CSV writer and JSON with a serializer. Never concatenate rows or fields manually. Values containing commas, quotes, line breaks, or delimiters must be escaped by the writer.

If an output directory is audited, register every file. Use `role=path` for artifacts whose counts are cited in a report:

```text
expctl.py --file <library> finish-run --run <run.json> \
  --artifacts-dir <output-dir> \
  --artifact candidate_matrix=matrix.csv \
  --artifact audit=audit.json
```

The command fails on a missing or unregistered file, a changed artifact, a UTF-16 CSV, or the first ragged CSV row. Temporary scripts and raw diagnostics must either be registered or kept outside the audited output directory.

Keep the mutable run JSON outside the audited artifact directory because `finish-run` updates that file after artifact inspection.

## Independent verification

Run this after `finish-run` and before copying numbers into the narrative report:

```text
expctl.py --file <library> verify-run --run <run.json> --json
```

For report comparison, provide a UTF-8 JSON audit statement rather than asking the verifier to infer numbers from prose:

```json
{
  "metrics": {
    "prefetched": 33,
    "prefetched_by_section": {"1": 7, "2": 13, "3": 1, "4": 6, "5": 3, "6": 3},
    "reused": 12
  },
  "candidate_count": 58
}
```

```text
expctl.py --file <library> verify-run --run <run.json> \
  --report <audit-statement.json> --json
```

The narrative report must copy mechanically derived values from the verified record. A report sentence, manual tally, or apparently correct grand total is never authoritative.

`verify-run` does not invent tool-call counts from prose. A claim such as “claims called 9 times” is auditable only when the host also supplies an authoritative machine event log; until such a log is registered, label that count unverified rather than presenting it as plugin-verified.
