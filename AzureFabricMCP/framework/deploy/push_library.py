"""
Build and publish the ttfabric wheel to a workspace Spark Environment.

Why an Environment rather than files in OneLake
-----------------------------------------------
The library used to be uploaded into every lakehouse and imported by appending
`/lakehouse/default/Files/framework` to sys.path. That had three problems:

  * a copy per lakehouse, free to drift
  * OneLake Files are not items, so Fabric git integration cannot version them
  * the path resolves through the notebook's DEFAULT LAKEHOUSE binding, which
    is not reliably in place the moment an item is created -- a freshly created
    notebook failed with ModuleNotFoundError and an identical re-run passed

A wheel on the Environment removes all three. Notebooks import it natively, the
version is pinned per environment, and pytest still runs against the same
package locally.

Publishing is asynchronous and takes minutes, because Fabric rebuilds the Spark
pool image. That is the real cost of this approach: a library change is a
publish, not a file copy.

Usage:
    python push_library.py --project ./01_demo-project --env dev
    python push_library.py --project ./01_demo-project --env dev --no-build
    python push_library.py --project ./01_demo-project --env dev --wait
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import get_environment, get_workspace_id

FABRIC_API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"
ENVIRONMENT_ITEM = "env_spark"


def get_token() -> str:
    from azure.identity import AzureCliCredential, ClientSecretCredential

    client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("FABRIC_CLIENT_ID")
    secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("FABRIC_CLIENT_SECRET")
    tenant = os.getenv("AZURE_TENANT_ID") or os.getenv("FABRIC_TENANT_ID")

    if client_id and secret and tenant:
        print("auth: service principal")
        return ClientSecretCredential(tenant, client_id, secret).get_token(SCOPE).token
    print("auth: az cli session")
    return AzureCliCredential().get_token(SCOPE).token


def build_wheel(framework: Path) -> Path:
    """Rebuild the wheel so what is published matches the working tree.

    Skipping the build and shipping a stale dist/ is the kind of mistake that
    surfaces as a fix that "did not take" -- which cost real time earlier when
    an upload silently timed out and the old code kept running.
    """
    print("building wheel...")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel"],
        cwd=framework, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-2000:])
        print(result.stderr[-2000:])
        raise RuntimeError("wheel build failed")

    wheels = sorted((framework / "dist").glob("ttfabric-*.whl"),
                    key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise FileNotFoundError(f"no wheel produced in {framework / 'dist'}")
    return wheels[-1]


def find_environment(headers: dict, workspace: str, name: str) -> str:
    response = requests.get(f"{FABRIC_API}/workspaces/{workspace}/environments",
                            headers=headers, timeout=60)
    response.raise_for_status()
    for environment in response.json().get("value", []):
        if environment["displayName"] == name:
            return environment["id"]
    raise KeyError(
        f"no Environment named {name!r} in the workspace. It is declared in "
        f"the scaffolding spec under workspace_folders/0_config -- create it "
        f"before publishing a library."
    )


def upload(headers: dict, workspace: str, environment: str, wheel: Path) -> bool:
    """Stage the wheel. Multipart, so Content-Type is set by requests."""
    url = (f"{FABRIC_API}/workspaces/{workspace}/environments/{environment}"
           f"/staging/libraries")
    with wheel.open("rb") as handle:
        response = requests.post(
            url,
            headers={k: v for k, v in headers.items() if k != "Content-Type"},
            files={"file": (wheel.name, handle,
                            "application/octet-stream")},
            timeout=300)

    if response.status_code in (200, 201, 202):
        print(f"  staged {wheel.name} ({wheel.stat().st_size:,} bytes)")
        return True
    print(f"  upload failed {response.status_code}: {response.text[:400]}")
    return False


def publish(headers: dict, workspace: str, environment: str, wait: bool) -> bool:
    """Publish staged libraries into the live environment.

    Asynchronous: Fabric rebuilds the Spark pool image, which takes minutes.
    Until it completes, notebooks still run against the previous version.
    """
    response = requests.post(
        f"{FABRIC_API}/workspaces/{workspace}/environments/{environment}/staging/publish",
        headers=headers, timeout=120)

    if response.status_code not in (200, 202):
        print(f"  publish failed {response.status_code}: {response.text[:400]}")
        return False
    print("  publish started")

    if not wait:
        print("  not waiting -- notebooks keep the previous version until it completes")
        return True

    for attempt in range(60):
        time.sleep(20)
        state = requests.get(
            f"{FABRIC_API}/workspaces/{workspace}/environments/{environment}",
            headers=headers, timeout=60)
        if not state.ok:
            continue
        publish_state = ((state.json().get("properties") or {})
                         .get("publishDetails") or {}).get("state")
        if attempt % 3 == 0:
            print(f"    [{(attempt + 1) * 20}s] {publish_state}")
        if publish_state in ("Success", "Succeeded"):
            print("  published")
            return True
        if publish_state in ("Failed", "Cancelled"):
            print(f"  publish ended in {publish_state}")
            return False

    print("  still publishing after 20 minutes -- check the environment in Fabric")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--environment-item", default=ENVIRONMENT_ITEM)
    parser.add_argument("--no-build", action="store_true",
                        help="publish the existing dist/ wheel without rebuilding")
    parser.add_argument("--wait", action="store_true",
                        help="block until the environment finishes publishing")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    framework = Path(__file__).resolve().parent.parent

    try:
        environment_spec = get_environment(project, args.env)
        workspace = get_workspace_id(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    print(f"environment {args.env}  ->  {environment_spec.get('workspace')}  ({workspace})")

    if args.no_build:
        wheels = sorted((framework / "dist").glob("ttfabric-*.whl"),
                        key=lambda p: p.stat().st_mtime)
        if not wheels:
            print("  ERROR  --no-build given but dist/ holds no wheel")
            return 2
        wheel = wheels[-1]
        print(f"using existing {wheel.name}")
    else:
        wheel = build_wheel(framework)
        print(f"built {wheel.name}")

    headers = {"Authorization": f"Bearer {get_token()}"}

    try:
        environment_id = find_environment(headers, workspace, args.environment_item)
    except KeyError as exc:
        print(f"  ERROR  {exc}")
        return 2
    print(f"target environment {args.environment_item} ({environment_id})")

    if not upload(headers, workspace, environment_id, wheel):
        return 1
    if not publish(headers, workspace, environment_id, args.wait):
        return 1

    print()
    print("Notebooks bound to this environment will import ttfabric natively "
          "once publishing completes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
