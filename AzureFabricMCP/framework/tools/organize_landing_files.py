"""
Organize Adventure Works CSV files into landing folder structure.

Creates the bronze/adventure_works/<entity> directory structure and moves
CSV files into their respective entity folders.

Usage:
    python organize_landing_files.py --workspace ADW_dev --lakehouse lh_bronze
    python organize_landing_files.py --workspace bd6a388c-5be5-4c31-8c6f-856513479712 \
        --lakehouse lh_bronze --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deploy._tsql import credential  # noqa: E402

FABRIC_API = "https://api.fabric.microsoft.com/v1"
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"

# Entity -> CSV filename mapping
ENTITIES = {
    "product": "Product.csv",
    "sales": "Sales.csv",
    "region": "Region.csv",
    "reseller": "Reseller.csv",
    "salesperson": "Salesperson.csv",
    "salesperson_region": "SalespersonRegion.csv",
    "targets": "Targets.csv",
}


def get_workspace_id(workspace: str) -> str:
    """Convert workspace name to ID if needed."""
    if workspace.count("-") == 4:  # UUID format
        return workspace
    # Query API to find workspace by name
    cred = credential()
    headers = {"Authorization": f"Bearer {cred.get_token('https://api.fabric.microsoft.com/.default').token}"}
    response = requests.get(
        f"{FABRIC_API}/workspaces", headers=headers, timeout=60, verify=False
    )
    workspaces = response.json().get("value", [])
    for ws in workspaces:
        if ws["displayName"] == workspace:
            return ws["id"]
    raise ValueError(f"Workspace '{workspace}' not found")


def get_item_id(workspace_id: str, item_name: str) -> str:
    """Get lakehouse ID from workspace."""
    cred = credential()
    headers = {"Authorization": f"Bearer {cred.get_token('https://api.fabric.microsoft.com/.default').token}"}
    response = requests.get(
        f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses",
        headers=headers, timeout=60, verify=False
    )
    lakehouses = response.json().get("value", [])
    for lh in lakehouses:
        if lh["displayName"] == item_name:
            return lh["id"]
    raise ValueError(f"Lakehouse '{item_name}' not found in workspace")


def create_directory(
    workspace_id: str, item_id: str, path: str, storage_token: str
) -> bool:
    """Create a directory in OneLake."""
    url = f"{ONELAKE}/{workspace_id}/{item_id}/{path}"
    headers = {"Authorization": f"Bearer {storage_token}"}

    response = requests.put(
        url,
        headers=headers,
        timeout=30,
        verify=False
    )

    if response.status_code == 201:
        print(f"  [OK]     created {path}")
        return True
    elif response.status_code == 409:  # Already exists
        print(f"  [EXISTS] {path}")
        return True
    else:
        print(f"  [FAIL]   {path} (HTTP {response.status_code})")
        print(f"          {response.text[:200]}")
        return False


def list_files(workspace_id: str, item_id: str, path: str, storage_token: str) -> list[str]:
    """List files in a directory."""
    url = f"{ONELAKE}/{workspace_id}/{item_id}/{path}?resource=directory"
    headers = {"Authorization": f"Bearer {storage_token}"}

    response = requests.get(url, headers=headers, timeout=30, verify=False)
    if response.status_code == 200:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.content)
        files = []
        for elem in root.findall(".//Name"):
            if elem.text and not elem.text.endswith("/"):
                files.append(elem.text)
        return files
    return []


def move_file(
    workspace_id: str, item_id: str, src: str, dst: str, storage_token: str
) -> bool:
    """Move a file from src to dst (copy then delete)."""
    # Copy
    copy_url = f"{ONELAKE}/{workspace_id}/{item_id}/{dst}"
    headers = {
        "Authorization": f"Bearer {storage_token}",
        "x-ms-copy-source": f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{item_id}/{src}",
    }

    response = requests.put(copy_url, headers=headers, timeout=60, verify=False)
    if response.status_code not in (200, 201):
        print(f"  [FAIL]   copy failed: {src} -> {dst}")
        return False

    # Wait for copy to complete
    for _ in range(30):
        status = response.headers.get("x-ms-copy-status", "pending")
        if status in ("success", "failed"):
            break
        time.sleep(1)
        response = requests.head(copy_url, headers=headers, timeout=30, verify=False)

    # Delete source
    del_url = f"{ONELAKE}/{workspace_id}/{item_id}/{src}"
    del_headers = {"Authorization": f"Bearer {storage_token}"}
    response = requests.delete(del_url, headers=del_headers, timeout=30, verify=False)

    if response.status_code == 204:
        print(f"  [OK]     moved   {src} -> {dst}")
        return True
    else:
        print(f"  [WARN]   copied (delete failed, source still at {src})")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="ADW_dev",
                        help="Workspace name or ID (default: ADW_dev)")
    parser.add_argument("--lakehouse", default="lh_bronze",
                        help="Lakehouse name (default: lh_bronze)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without doing it")
    args = parser.parse_args()

    print(f"Organizing {args.lakehouse} landing structure...")

    try:
        workspace_id = get_workspace_id(args.workspace)
        print(f"workspace: {args.workspace} ({workspace_id})")
    except ValueError as exc:
        print(f"  ERROR  {exc}")
        return 1

    try:
        item_id = get_item_id(workspace_id, args.lakehouse)
        print(f"lakehouse: {args.lakehouse} ({item_id})")
    except ValueError as exc:
        print(f"  ERROR  {exc}")
        return 1

    cred = credential()
    storage_token = cred.get_token("https://storage.azure.com/.default").token

    # Create parent directories
    print("\nCreating directory structure...")
    paths_to_create = [
        "Files/bronze",
        "Files/bronze/adventure_works",
    ]
    for path in paths_to_create:
        if not args.dry_run:
            create_directory(workspace_id, item_id, path, storage_token)
        else:
            print(f"  > would create {path}")

    # Create entity directories
    for entity in ENTITIES:
        path = f"Files/bronze/adventure_works/{entity}"
        if not args.dry_run:
            create_directory(workspace_id, item_id, path, storage_token)
        else:
            print(f"  > would create {path}")

    # Move CSV files to entity folders
    print("\nMoving CSV files to entity folders...")
    for entity, csv_file in ENTITIES.items():
        src = f"Files/{csv_file}"
        dst = f"Files/bronze/adventure_works/{entity}/{csv_file}"

        if not args.dry_run:
            move_file(workspace_id, item_id, src, dst, storage_token)
        else:
            print(f"  > would move {src} -> {dst}")

    print("\n[OK] Organization complete")
    if args.dry_run:
        print("  (dry run -- no changes made)")

    print("\nNext step: Run p_orchestrate_master pipeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
