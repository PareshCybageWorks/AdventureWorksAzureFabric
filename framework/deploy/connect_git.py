"""
Mirror a workspace's items into the project repository via Fabric Git integration.

What this is for
----------------
Deployed items are otherwise only readable in the Fabric UI, one item at a time.
Mirroring them into the repo makes the deployed state diffable: what changed
between Tuesday and Friday becomes a commit range rather than a memory.

What this is NOT
----------------
A deployment path. Items reach a workspace one way only:

    specs -> generators -> generated/ -> push_items.py -> workspace -> git

This script only ever commits OUTWARD. There is no update-from-git here and
adding one would be a mistake: git -> workspace would compete with
push_items.py over the same items, and the winner would be whichever ran last.
That is not a race worth having, because both sides look successful.

The consequence worth stating plainly: an item edited in the Fabric UI is
committed here, and will then sit in the repository looking authoritative while
disagreeing with the spec that generated it. The `no-drift` gate compares specs
against `generated/` and cannot see this. Treat the mirrored directory as
evidence of what is deployed, never as a source to edit.

Prerequisites, both of which are one-time and neither of which this script can
do for you:

  1. Tenant setting -- Admin portal > Tenant settings >
     "Users can synchronize workspace items with their GitHub repositories".
     Without it every call returns FeatureNotAvailable.
  2. A Fabric connection holding a GitHub PAT (repo scope). Create it in
     Manage connections and gateways > Connections > New > GitHub source
     control. The token is entered there, once, and never appears in a spec.
     Azure DevOps needs neither -- it authenticates as the calling user.

Usage:
    python connect_git.py --project . --env dev --status
    python connect_git.py --project . --env dev
    python connect_git.py --project . --env dev --disconnect
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _secrets
import _tsql
from _project import get_environment, is_production, load_scaffolding

API = "https://api.fabric.microsoft.com/v1"


def headers() -> dict[str, str]:
    token = _tsql.credential().get_token(_tsql.FABRIC_SCOPE).token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def explain(response: requests.Response) -> str:
    """Turn a Fabric error into the action that resolves it.

    The two failures anyone hits here are both configuration a caller cannot
    infer from the error code alone, so they are translated rather than echoed.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]

    code = body.get("errorCode", "")
    message = body.get("message", response.text[:200])

    if code == "FeatureNotAvailable":
        return (f"{message}\n"
                f"         A tenant admin must enable Admin portal > Tenant settings >\n"
                f"         'Users can synchronize workspace items with their GitHub\n"
                f"         repositories'. Nothing in this project can work around it.")
    if code == "GitProviderResourceNotFound":
        return (f"{message}\n"
                f"         Fabric RESOLVES the sync directory as an existing resource --\n"
                f"         it does not create it. The usual cause is that `directory`\n"
                f"         does not exist yet on that branch. Create it (a README or\n"
                f"         .gitkeep is enough) and retry.\n"
                f"         The organisation, project, repository and branch must also\n"
                f"         all exist and be spelled exactly as the provider stores them.\n"
                f"         Connecting with directory '/' would succeed and hand Fabric\n"
                f"         the whole repository root -- do not use it as a workaround.")
    if code in ("InvalidCredentials", "GitCredentialsConfigurationFailed",
                "UnauthorizedGitProviderAccess"):
        return (f"{message}\n"
                f"         The GitHub connection's PAT is missing, expired, or lacks\n"
                f"         `repo` scope. Recreate it under Manage connections and\n"
                f"         gateways > Connections.")
    return f"[{code}] {message}" if code else message


def poll(response: requests.Response, label: str, timeout: int = 900) -> bool:
    """Follow a long-running operation to its end.

    connect returns immediately; initialize and commit do not. Treating a 202 as
    success reports a commit that has not happened yet -- and on the first run
    the repository is empty, so nothing looks wrong.
    """
    if response.status_code != 202:
        return response.ok

    location = response.headers.get("Location")
    if not location:
        return True

    deadline = time.time() + timeout
    delay = 5
    while time.time() < deadline:
        time.sleep(delay)
        delay = min(delay * 1.5, 30)
        poll_response = _tsql.with_retry("GET", location, headers=headers())
        if not poll_response.ok:
            print(f"  ERROR  polling {label}: {explain(poll_response)}")
            return False
        state = poll_response.json().get("status", "")
        if state == "Succeeded":
            return True
        if state in ("Failed", "Undetermined"):
            error = poll_response.json().get("error", {})
            print(f"  ERROR  {label} failed: {error.get('message', state)}")
            return False

    print(f"  ERROR  {label} did not finish within {timeout}s")
    return False


def config(project: Path) -> dict:
    doc, source = load_scaffolding(project)
    block = doc.get("git_integration")
    if not block:
        raise SystemExit(
            f"  no `git_integration` block in {source}.\n"
            f"  Mirroring is opt-in; declare it there rather than passing it here,\n"
            f"  so what a workspace is connected to is reviewable.")
    return block


def resolve_connection(block: dict) -> str | None:
    """The Fabric connection id, which is a GUID and not a credential.

    Resolved through the secrets helper anyway, so `${VAR}` works the same way
    it does everywhere else in the framework.
    """
    value = block.get("connection_id")
    if not value:
        return None
    if value.startswith("${") or value.startswith("keyvault://"):
        return _secrets.resolve(value)
    return value


def status(workspace: str) -> dict | None:
    response = _tsql.with_retry(
        "GET", f"{API}/workspaces/{workspace}/git/connection", headers=headers())
    if not response.ok:
        print(f"  ERROR  {explain(response)}")
        return None
    return response.json()


def report(workspace: str) -> int:
    state = status(workspace)
    if state is None:
        return 1

    connection_state = state.get("gitConnectionState", "Unknown")
    print(f"  connection   {connection_state}")

    details = state.get("gitProviderDetails") or {}
    if details:
        print(f"  provider     {details.get('gitProviderType')}")
        # Azure DevOps reports organisation/project, GitHub reports an owner.
        scope = "/".join(p for p in (details.get("organizationName"),
                                     details.get("projectName"),
                                     details.get("ownerName"),
                                     details.get("repositoryName")) if p)
        print(f"  repository   {scope}")
        print(f"  branch       {details.get('branchName')}")
        print(f"  directory    {details.get('directoryName')}")

    if connection_state != "ConnectedAndInitialized":
        return 0

    changes = _tsql.with_retry(
        "GET", f"{API}/workspaces/{workspace}/git/status", headers=headers())
    if changes.ok:
        body = changes.json()
        pending = body.get("changes") or []
        print(f"  workspace    {(body.get('workspaceHead') or '')[:8]}")
        print(f"  remote       {(body.get('remoteCommitHash') or '')[:8]}")
        print(f"  uncommitted  {len(pending)}")
        for change in pending[:10]:
            item = change.get("itemMetadata", {})
            kind = change.get("workspaceChange") or change.get("remoteChange") or "?"
            print(f"                 {kind:<8} {item.get('itemType','')}"
                  f" {item.get('displayName','')}")
        if len(pending) > 10:
            print(f"                 ... and {len(pending) - 10} more")
    return 0


def connect(project: Path, env: str, message: str) -> int:
    block = config(project)
    environment = get_environment(project, env)
    workspace = environment.get("workspace_id")
    if not workspace:
        print(f"  ERROR  environment {env!r} has no workspace_id.")
        return 1

    # The branch is not restated in git_integration -- it comes from the
    # environment, so branch/workspace pairing lives in exactly one place and
    # cannot drift into a workspace tracking someone else's branch.
    branch = environment.get("branch")
    if not branch:
        print(f"  ERROR  environment {env!r} has no `branch`. Git integration needs\n"
              f"         one, and the deployment model already depends on it.")
        return 1

    wanted = block.get("environments") or ["dev"]
    if env not in wanted:
        print(f"  REFUSED  {env!r} is not in git_integration.environments "
              f"({', '.join(wanted)}).")
        return 2

    # Production is excluded by default rather than by intent: a prod workspace
    # that anyone edits by hand has a bigger problem than an unmirrored one.
    if is_production(project, env) and env not in (block.get("environments") or []):
        print(f"  REFUSED  {env!r} is production.")
        return 2

    parts = [p for p in block["repository"].split("/") if p]
    directory = block["directory"]

    # An overlap silently hands Fabric write access over generated artefacts:
    # it rewrites everything beneath its directory, so a UI edit could land on
    # top of a generated notebook and the no-drift gate would then fail on a
    # file nobody edited.
    reserved = ("/generated", "/fabric", "/powerbi", "/dataops", "/cicd", "/data")
    if any(directory == r or directory.startswith(r + "/") for r in reserved):
        print(f"  REFUSED  directory {directory} overlaps spec or generated output.\n"
              f"           Fabric owns everything beneath it and would overwrite them.")
        return 2

    # The two providers do not merely differ in credentials -- they take
    # different FIELDS. Azure DevOps is addressed by organisation, project and
    # repository; GitHub by owner and repository. Sending GitHub's shape to
    # Azure DevOps is rejected for a missing field, which reads like a bad
    # request rather than the wrong provider.
    if block["provider"] == "AzureDevOps":
        if len(parts) != 3:
            print(f"  ERROR  Azure DevOps needs `repository` as "
                  f"<organisation>/<project>/<repo>.\n"
                  f"         Got {block['repository']!r} ({len(parts)} part(s)). The "
                  f"project is a\n         separate level and cannot be inferred "
                  f"from the other two.")
            return 1
        organisation, adoproject, repository = parts
        details = {"gitProviderType": "AzureDevOps",
                   "organizationName": organisation,
                   "projectName": adoproject,
                   "repositoryName": repository}
        shown = f"{organisation}/{adoproject}/{repository}"
    else:
        if len(parts) != 2:
            print(f"  ERROR  GitHub needs `repository` as <owner>/<repo>.\n"
                  f"         Got {block['repository']!r} ({len(parts)} part(s)).")
            return 1
        owner, repository = parts
        details = {"gitProviderType": "GitHub",
                   "ownerName": owner,
                   "repositoryName": repository}
        shown = f"{owner}/{repository}"

    print(f"  workspace    {environment.get('workspace')} ({workspace})")
    print(f"  repository   {shown}")
    print(f"  branch       {branch}")
    print(f"  directory    {directory}")
    print()

    current = status(workspace)
    if current is None:
        return 1

    if current.get("gitConnectionState") == "NotConnected":
        payload = {"gitProviderDetails": {
            **details,
            "branchName": branch,
            "directoryName": directory}}

        if block["provider"] == "GitHub":
            connection = resolve_connection(block)
            if not connection:
                print("  ERROR  GitHub needs `connection_id` -- a Fabric connection\n"
                      "         holding a PAT. Create it under Manage connections and\n"
                      "         gateways > Connections > New > GitHub source control,\n"
                      "         then record its id in the spec. The token stays there.")
                return 1
            payload["myGitCredentials"] = {"source": "ConfiguredConnection",
                                           "connectionId": connection}
        else:
            payload["myGitCredentials"] = {"source": "Automatic"}

        response = _tsql.with_retry(
            "POST", f"{API}/workspaces/{workspace}/git/connect",
            headers=headers(), json=payload)
        if not response.ok:
            print(f"  ERROR  connect: {explain(response)}")
            return 1
        print("  connected")
    else:
        print(f"  already {current.get('gitConnectionState')}")

    if status(workspace).get("gitConnectionState") != "ConnectedAndInitialized":
        # PreferWorkspace: the workspace holds the real items and the directory
        # is empty. PreferRemote would empty the workspace to match, which on a
        # first connect means deleting everything that was deployed.
        response = _tsql.with_retry(
            "POST", f"{API}/workspaces/{workspace}/git/initializeConnection",
            headers=headers(), json={"initializationStrategy": "PreferWorkspace"})
        if not poll(response, "initialize"):
            return 1
        print("  initialised  (workspace wins -- it holds the deployed items)")

    pending = _tsql.with_retry(
        "GET", f"{API}/workspaces/{workspace}/git/status", headers=headers())
    if not pending.ok:
        print(f"  ERROR  status: {explain(pending)}")
        return 1

    body = pending.json()
    changes = body.get("changes") or []
    if not changes:
        print("  nothing to commit -- git already matches the workspace")
        return 0

    print(f"  committing   {len(changes)} item(s) to {branch}:{directory}")
    response = _tsql.with_retry(
        "POST", f"{API}/workspaces/{workspace}/git/commitToGit",
        headers=headers(),
        json={"mode": "All", "comment": message,
              "workspaceHead": body.get("workspaceHead")})
    if not poll(response, "commit"):
        return 1

    print(f"  committed    {len(changes)} item(s)")
    print()
    print(f"  This directory is a MIRROR. Editing it does not deploy, and editing")
    print(f"  an item in the Fabric UI puts a change here that no spec produced.")
    return 0


def disconnect(project: Path, env: str) -> int:
    workspace = get_environment(project, env).get("workspace_id")
    response = _tsql.with_retry(
        "POST", f"{API}/workspaces/{workspace}/git/disconnect", headers=headers())
    if not response.ok:
        print(f"  ERROR  {explain(response)}")
        return 1
    # Disconnecting is not destructive in either direction: the workspace keeps
    # its items and the repository keeps the mirrored files.
    print("  disconnected -- workspace items and mirrored files both retained")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--status", action="store_true",
                        help="report the connection and pending changes, change nothing")
    parser.add_argument("--disconnect", action="store_true")
    parser.add_argument("--message", default="Mirror workspace items",
                        help="commit message")
    args = parser.parse_args()

    project = Path(args.project).resolve()

    if args.status:
        workspace = get_environment(project, args.env).get("workspace_id")
        if not workspace:
            print(f"  ERROR  environment {args.env!r} has no workspace_id.")
            return 1
        return report(workspace)

    if args.disconnect:
        return disconnect(project, args.env)

    return connect(project, args.env, args.message)


if __name__ == "__main__":
    sys.exit(main())
