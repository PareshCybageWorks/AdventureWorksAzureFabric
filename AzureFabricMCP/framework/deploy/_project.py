"""
Resolve project settings from whichever spec layout is in force.

The spec set is migrating from a flat `<project>/specs/` collection to
track-based folders (`fabric/`, `powerbi/`, `dataops/`, `cicd/`). The migration
is additive -- both layouts coexist while stages move across one at a time.

Every deploy script goes through here rather than reading a path directly, so
the migration is a change in one file instead of five, and a project on either
layout deploys with the same commands.

Precedence is new-first: `fabric/01-scaffolding.yaml` wins when present, and
the legacy `specs/01-environments.yaml` is the fallback. That way migrating a
project is simply adding the new file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

NEW_SCAFFOLDING = Path("fabric") / "01-scaffolding.yaml"
LEGACY_ENVIRONMENTS = Path("specs") / "01-environments.yaml"
LEGACY_PLATFORM = Path("specs") / "00-platform.yaml"


def _read(path: Path) -> dict | None:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_scaffolding(project: Path) -> tuple[dict, str]:
    """Return (scaffolding-ish doc, which layout it came from)."""
    new = _read(project / NEW_SCAFFOLDING)
    if new:
        return new, "fabric/01-scaffolding.yaml"

    legacy = _read(project / LEGACY_ENVIRONMENTS)
    if legacy:
        # Fold the legacy platform spec in, so callers see one shape whichever
        # layout produced it.
        platform = _read(project / LEGACY_PLATFORM) or {}
        merged = dict(legacy)
        for key in ("naming", "storage", "topology", "domains", "tenant"):
            if key in platform and key not in merged:
                merged[key] = platform[key]
        return merged, "specs/01-environments.yaml (legacy)"

    raise FileNotFoundError(
        f"no scaffolding spec found in {project}. Expected "
        f"{NEW_SCAFFOLDING} or {LEGACY_ENVIRONMENTS}."
    )


def get_environment(project: Path, name: str) -> dict:
    """Resolve one environment, failing loudly rather than defaulting.

    A missing environment must never fall through to another one -- that is how
    a dev deployment reaches production.
    """
    doc, source = load_scaffolding(project)
    for environment in doc.get("environments", []):
        if environment.get("name") == name:
            environment = dict(environment)
            environment["_source"] = source
            return environment

    available = [e.get("name") for e in doc.get("environments", [])]
    raise KeyError(
        f"environment {name!r} is not defined in {source}. Available: {available}"
    )


def get_workspace_id(project: Path, name: str) -> str:
    """Workspace id for an environment, or a clear error if unprovisioned."""
    environment = get_environment(project, name)
    workspace_id = environment.get("workspace_id")
    if not workspace_id:
        raise ValueError(
            f"environment {name!r} has no workspace_id recorded in "
            f"{environment['_source']}. Provision it first, then record the id "
            f"so the spec and the tenant agree."
        )
    return workspace_id


def get_storage_ids(project: Path, name: str) -> dict[str, str]:
    """Storage item ids for an environment, keyed by item name.

    Prefers what the spec records, and falls back to asking the workspace.

    The fallback is not a nicety. `provisioned_items` is written when an
    environment is first built, and for a long time only dev had it -- qa, uat
    and prod were provisioned but never recorded. Every caller of this function
    would then have got an empty map and gone looking for a lakehouse id that
    was sitting in the workspace all along: OneLake uploads, migrations, the DQ
    monitor and reset would each have failed on the first promotion, for a
    reason that reads like a missing item rather than a missing note about one.

    Item ids are a property of the workspace, so the workspace is the
    authority. The recorded map stays as a fast path and an audit record.
    """
    environment = get_environment(project, name)
    recorded = (environment.get("provisioned_items") or {}).get("storage") or {}
    if recorded:
        return recorded

    workspace = environment.get("workspace_id")
    if not workspace:
        return {}

    # Imported here so reading a spec does not require network access or an
    # auth library -- only the fallback does.
    import requests

    import _tsql

    token = _tsql.credential().get_token(_tsql.FABRIC_SCOPE).token
    response = requests.get(
        f"https://api.fabric.microsoft.com/v1/workspaces/{workspace}/items",
        headers={"Authorization": f"Bearer {token}"}, timeout=90)
    if not response.ok:
        return {}

    return {item["displayName"]: item["id"]
            for item in response.json().get("value", [])
            if item["type"] in ("Lakehouse", "Warehouse")}


def is_production(project: Path, name: str) -> bool:
    """Whether this environment is production.

    Callers use it to refuse destructive operations, or to demand an explicit
    override. Production commonly carries the shortest, most innocuous name of
    the set, so the flag is the only reliable signal.
    """
    return bool(get_environment(project, name).get("is_production"))
