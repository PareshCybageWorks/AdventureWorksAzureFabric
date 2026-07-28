# Power BI Skills Index

Deep knowledge, read on demand. The stage prompts under
`framework/prompts/powerbi/` are the procedure; skills here explain the platform
behaviour behind the rules those prompts enforce.

## Available

- `direct-lake-and-tmdl.md` — why a DirectLake model cannot bind to a view, the
  column/measure naming rule, TMDL's strictness about descriptions and
  references, deterministic lineage tags, and marking the date table. Every
  entry cost a failed deploy to learn.

## Not yet written

Nothing else in this folder exists.

An earlier version of this index listed twelve skills as
"✅ Production Ready with 200+ code examples and 3,000+ lines". None of those
files were ever present. The claim is removed rather than left for someone to
discover by trying to open one — an index that overstates itself is worse than
an empty folder, because it stops you looking elsewhere.

Write a skill when a platform behaviour has cost real debugging time and the
explanation does not belong in a prompt. Candidates, in rough order of value:

- RLS driven by a user-mapping table (dynamic security)
- Composite models and aggregations over a DirectLake base
- Report performance: visual count, cardinality, what actually costs
- Deployment pipelines and rebinding across environments
