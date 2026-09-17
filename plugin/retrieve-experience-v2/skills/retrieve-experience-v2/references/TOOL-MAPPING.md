# Machine-local Tool Mapping

Read this once on each computer before enabling memory Hooks. The mapping is local because MCP server namespaces and tool names may differ even when they reach the same commercial provider.

## Files

- `tools.json`: shared abstract template; safe to version with the experience library.
- `tools.local.json`: concrete server-aware mapping for this computer; Git-ignored.
- `runtime-tools.json`: captured runtime `tools/list`; Git-ignored.

Never infer that tools exposed by two servers are interchangeable. The identity of a callable tool is the pair `(server, tool)`.

## Mapping step

Generate a path-specific prompt:

```text
expctl.py --file <library>/检索经验.md mapping-prompt --out <case-output>/tool-mapping-prompt.txt
```

Give that prompt to the Agent which can inspect the current runtime. It must map only these abstract capabilities:

```text
search, count, claims, description, bibliography, family, abstract_translated
```

For `count`, distinguish a direct tool from a value derived from search output. A derived mapping must record fixed arguments and the result path. Missing tools, call errors, authentication failures, missing fields, and a legitimate zero count are separate states.

Validate before enabling the plugin:

```text
expctl.py --file <library>/检索经验.md tools \
  --mapping <library>/tools.local.json \
  --catalog <library>/runtime-tools.json --json
```

Success requires `_validation.runtime_catalog_checked=true` and no warnings. The plugin never calls tools on its own; the host skill consumes this verified Adapter.
