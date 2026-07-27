"""
Upload source data and the framework library into OneLake.

Why this exists
---------------
The MCP's onelake_upload_file is gated behind an interactive consent prompt that
non-interactive clients cannot render. The OneLake DFS API accepts the same
token and has no such gate, so file staging becomes scriptable.

Uploads two things:

  source data    the CSVs a project's 02-sources.yaml declares, into the bronze
                 lakehouse under the landing path from 00-platform.yaml

The framework library is NO LONGER shipped here. It is packaged as the
ttfabric wheel and attached to the Spark Environment by deploy/push_library.py,
which removes the per-lakehouse duplication and the dependence on a notebook's
default-lakehouse binding being in place.

Authentication mirrors push_items.py: service principal from environment when
present, otherwise the `az login` session. Note the SCOPE DIFFERS -- OneLake is
a storage endpoint and wants https://storage.azure.com/.default, not the Fabric
API scope.

Usage:
    python push_files.py --project ./01_demo-project --env dev
    python push_files.py --project ./01_demo-project --env dev --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests
import yaml

# Sibling import: deploy scripts are run as files, not as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import get_environment, get_storage_ids, get_workspace_id
import _tsql

ONELAKE = "https://onelake.dfs.fabric.microsoft.com"
STORAGE_SCOPE = "https://storage.azure.com/.default"
# OneLake accepts a single append comfortably at this size; larger files would
# need chunking, which nothing in this project currently requires.
MAX_SINGLE_APPEND = 100 * 1024 * 1024


def get_token() -> str:
    from azure.identity import AzureCliCredential, ClientSecretCredential

    client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("FABRIC_CLIENT_ID")
    secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("FABRIC_CLIENT_SECRET")
    tenant = os.getenv("AZURE_TENANT_ID") or os.getenv("FABRIC_TENANT_ID")

    if client_id and secret and tenant:
        print("auth: service principal")
        credential = ClientSecretCredential(tenant, client_id, secret)
    else:
        print("auth: az cli session")
        credential = AzureCliCredential()

    return credential.get_token(STORAGE_SCOPE).token


class OneLake:
    def __init__(self, token: str, workspace: str) -> None:
        self.headers = {"Authorization": f"Bearer {token}"}
        self.workspace = workspace

    def upload(self, item_id: str, path: str, data: bytes) -> bool:
        """Create, append, flush -- the three-step DFS write.

        Overwrites unconditionally: `resource=file` on an existing path
        truncates it, which is what re-running a load should do.
        """
        if len(data) > MAX_SINGLE_APPEND:
            print(f"      {path}: {len(data):,} bytes exceeds the single-append "
                  f"limit; chunking is not implemented")
            return False

        url = f"{ONELAKE}/{self.workspace}/{item_id}/{path}"

        created = _tsql.with_retry("PUT", url, headers=self.headers,
                                   params={"resource": "file"}, timeout=120)
        if created.status_code not in (201, 202):
            print(f"      create failed {created.status_code}: {created.text[:200]}")
            return False

        appended = _tsql.with_retry(
            "PATCH", url,
            headers={**self.headers, "Content-Type": "application/octet-stream"},
            params={"action": "append", "position": "0"}, data=data, timeout=300)
        if appended.status_code not in (200, 202):
            print(f"      append failed {appended.status_code}: {appended.text[:200]}")
            return False

        flushed = _tsql.with_retry("PATCH", url, headers=self.headers,
                                 params={"action": "flush", "position": str(len(data))},
                                 timeout=120)
        if flushed.status_code not in (200, 201):
            print(f"      flush failed {flushed.status_code}: {flushed.text[:200]}")
            return False
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--framework", default=None,
                        help="framework directory (defaults to this file's parent)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    framework = Path(args.framework).resolve() if args.framework \
        else Path(__file__).resolve().parent.parent

    specs = project / "specs"
    platform = yaml.safe_load((specs / "00-platform.yaml").read_text(encoding="utf-8"))
    sources = yaml.safe_load((specs / "02-sources.yaml").read_text(encoding="utf-8"))
    try:
        env = get_environment(project, args.env)
        workspace = get_workspace_id(project, args.env)
        provisioned = get_storage_ids(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2
    if not provisioned:
        print("  ERROR  no provisioned storage ids recorded for this environment")
        return 2
    print(f"environment {args.env}  ->  {env.get('workspace')}  ({workspace})")
    if args.dry_run:
        print("dry run -- nothing will be written")
    print()

    onelake = OneLake(get_token(), workspace)
    uploaded = failed = 0

    # ---- source data into the bronze lakehouse --------------------------
    bronze_item = provisioned.get("lh_bronze")
    source = sources["sources"][0]
    landing_root = platform["storage"]["bronze"]["landing_path"].split("/ingest_date=")[0]
    local_root = project / source["connection"]["location_dev"].lstrip("./")

    print(f"source data -> lh_bronze ({bronze_item})")
    for entity in source["entities"]:
        local = local_root / entity["file_pattern"]
        if not local.exists():
            print(f"  MISS   {local.name} not found at {local}")
            failed += 1
            continue
        remote = landing_root.format(source=source["name"], entity=entity["name"]) \
            + "/" + entity["file_pattern"]
        size = local.stat().st_size
        if args.dry_run:
            print(f"  would  {remote}  ({size:,} bytes)")
            continue
        ok = onelake.upload(bronze_item, remote, local.read_bytes())
        print(f"  {'OK   ' if ok else 'FAIL '} {remote}  ({size:,} bytes)")
        uploaded += ok
        failed += not ok

    # The framework library is delivered as a wheel on the Spark Environment;
    # see deploy/push_library.py. Nothing library-related is uploaded here.

    print()
    print(f"{uploaded} uploaded, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
