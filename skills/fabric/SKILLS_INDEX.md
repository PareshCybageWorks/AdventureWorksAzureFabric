# Fabric Skills Index

Deep knowledge, read on demand. The stage prompts under
`framework/prompts/fabric/` are the procedure; skills here explain the platform
behaviour behind the rules those prompts enforce.

## Available

- `lakehouse-warehouse-topology.md` — how to decide which storage items a
  workspace gets, and what to call them. Medallion versus mesh, when a warehouse
  earns its place beside a lakehouse, and the naming that keeps promotion
  mechanical.

## Not yet written

Nothing else in this folder exists.

An earlier version of this index described fifteen skills in detail —
`provisioning.md`, `capacity-management.md`, `git-integration.md` and others —
under the heading "Complete collection". None of those files were ever present,
and the one file that *is* here was not listed. The claim is removed rather than
left for someone to discover by opening one: an index that overstates itself is
worse than an empty folder, because it stops you looking where the knowledge
actually lives.

## Where that knowledge actually lives

Most of what the old index promised is implemented rather than documented, which
is why it never became a skill:

| Old entry | Where it really is |
|---|---|
| provisioning, workspace-operations | `framework/deploy/create_workspaces.py`, `provision_storage.py` |
| lakehouse-config, data-ingestion | `framework/prompts/fabric/03-bronze.md`, `04-silver.md` |
| notebooks-automation | `framework/generators/generate_notebooks.py` |
| sql-analytics | `framework/deploy/_tsql.py`, `generators/generate_ddl.py` |
| git-integration | `framework/deploy/connect_git.py`, F1 prompt Step 6 |
| monitoring, diagnostics | `framework/prompts/dataops/01-monitoring.md`, `ttfabric/monitoring.py` |
| api-automation | `framework/deploy/` — every script is Fabric REST |
| capacity-management | F1 prompt, and `check_capacity` in `validate.py` |

Read the code before writing a skill about it. A skill that restates what a
script already does goes stale the first time the script changes, and then
misleads with authority.

## Worth writing

When a platform behaviour has cost real debugging time and the explanation does
not belong in a prompt:

- Spark Environment publishing: why a wheel takes minutes to become importable,
  and what a stale environment looks like from a notebook
- OneLake DFS versus the item API: which operations need which, and why a name
  reservation persists into a newly created workspace
- Warehouse connector limits: overwrite-only, and what that forces upstream
