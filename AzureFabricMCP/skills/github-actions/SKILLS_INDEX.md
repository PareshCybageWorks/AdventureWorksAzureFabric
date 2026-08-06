# GitHub Actions Skills Index

Deep knowledge for CI/CD for Fabric deployment, read on demand. The stage prompts under
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

- Why a deploy job needs a GitHub Environment (approval gates and scoped secrets live there, not in the workflow)
- Gating deploys on a repository variable so an unconfigured repo skips rather than fails
- Running a CI job locally before it runs publicly on someone's pull request

Write a skill when a platform behaviour has cost real debugging time and
the explanation does not belong in a prompt. The two that exist
(`skills/fabric/`, `skills/powerbi/`) were written that way.
