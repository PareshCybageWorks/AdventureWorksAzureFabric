# dbt Skills Index

Deep knowledge for dbt-based transformation, if a project chooses it, read on demand. The stage prompts under
`framework/prompts/` are the procedure; skills explain the platform
behaviour behind the rules those prompts enforce.

## Available

**Nothing yet.** This folder is empty.

An earlier version of this index advertised a "complete skill
collection" of eight to ten production-ready skills. None of those files
ever existed. The claim is removed rather than left for someone to find
by trying to open one -- an index that overstates itself is worse than an
empty folder, because it stops you looking elsewhere.

## Worth writing, when one is earned

- This framework does not use dbt. Silver transformation is PySpark via ttfabric.cleansing, and gold is PySpark plus the warehouse connector.
- Kept as a folder because a future project may adopt dbt for the silver layer, at which point F4 would need a second generator.

Write a skill when a platform behaviour has cost real debugging time and
the explanation does not belong in a prompt. The two that exist
(`skills/fabric/`, `skills/powerbi/`) were written that way.
