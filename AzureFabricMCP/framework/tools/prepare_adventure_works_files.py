"""
Convert Adventure Works TSV files to CSV format and upload to Fabric landing.

The archive contains tab-delimited files with PascalCase headers.
Notebooks expect comma-delimited CSV with lowercase snake_case headers.

Usage:
    python prepare_adventure_works_files.py --archive D:\path\to\archive \
        --workspace ADW_dev --lakehouse lh_bronze
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deploy._tsql import credential  # noqa: E402

FABRIC_API = "https://api.fabric.microsoft.com/v1"
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"

# Entity mapping: filename -> (entity_folder, csv_name)
# Headers are converted from the actual file
ENTITIES = {
    "Product.csv": "product",
    "Sales.csv": "sales",
    "Region.csv": "region",
    "Reseller.csv": "reseller",
    "Salesperson.csv": "salesperson",
    "SalespersonRegion.csv": "salesperson_region",
    "Targets.csv": "targets",
}


def normalize_header(name: str) -> str:
    """Convert header to snake_case, handling spaces and hyphens."""
    # Replace spaces and hyphens with underscores first
    name = name.replace(" ", "_").replace("-", "_")
    # Convert camelCase/PascalCase to snake_case
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    result = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
    # Clean up multiple underscores
    result = re.sub(r"_+", "_", result)
    return result


def convert_tsv_to_csv(tsv_path: Path) -> tuple[str, list[str], list[str]]:
    """Read TSV, convert to CSV, return (csv_text, headers_as_read, headers_normalized)."""
    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if not lines:
        raise ValueError(f"Empty file: {tsv_path}")

    # Parse header
    header_line = lines[0].rstrip("\r\n")
    headers_in_file = header_line.split("\t")

    # Convert to snake_case
    headers_snake = [normalize_header(h) for h in headers_in_file]

    # Convert file to CSV
    csv_lines = [",".join(headers_snake)]
    for line in lines[1:]:
        # Tab-separated → comma-separated
        row = line.rstrip("\r\n").split("\t")
        # Quote fields that contain commas or quotes
        quoted_row = []
        for val in row:
            if "," in val or '"' in val:
                val = val.replace('"', '""')
                quoted_row.append(f'"{val}"')
            else:
                quoted_row.append(val)
        csv_lines.append(",".join(quoted_row))

    csv_text = "\n".join(csv_lines)
    return csv_text, headers_in_file, headers_snake


def get_workspace_id(workspace: str) -> str:
    """Convert workspace name to ID if needed."""
    if workspace.count("-") == 4:  # UUID format
        return workspace
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


def upload_file(workspace_id: str, item_id: str, path: str, content: bytes, storage_token: str) -> bool:
    """Upload a file to OneLake."""
    # Create parent directory first
    parts = path.split("/")
    for i in range(1, len(parts)):
        parent_path = "/".join(parts[:i])
        url = f"{ONELAKE}/{workspace_id}/{item_id}/{parent_path}"
        requests.put(url, headers={"Authorization": f"Bearer {storage_token}"}, timeout=30, verify=False)

    # Upload file
    url = f"{ONELAKE}/{workspace_id}/{item_id}/{path}"
    headers = {"Authorization": f"Bearer {storage_token}"}
    response = requests.put(url, data=content, headers=headers, timeout=60, verify=False)

    if response.status_code in (200, 201):
        print(f"  [OK]     uploaded {path}")
        return True
    else:
        print(f"  [FAIL]   {path} (HTTP {response.status_code})")
        print(f"          {response.text[:200]}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="Path to Adventure Works archive directory")
    parser.add_argument("--workspace", default="ADW_dev", help="Workspace name or ID")
    parser.add_argument("--lakehouse", default="lh_bronze", help="Lakehouse name")
    parser.add_argument("--dry-run", action="store_true", help="Show conversions without uploading")
    args = parser.parse_args()

    archive_path = Path(args.archive).resolve()
    if not archive_path.exists():
        print(f"  ERROR  archive directory not found: {archive_path}")
        return 1

    print(f"Preparing Adventure Works files...")
    print(f"archive: {archive_path}")

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

    print("\nConverting and uploading files...")
    failures = 0

    for src_filename, entity_folder in ENTITIES.items():
        src_path = archive_path / src_filename
        if not src_path.exists():
            print(f"  [SKIP]   {src_filename} (not found in archive)")
            continue

        try:
            print(f"\n  {src_filename}")
            csv_text, headers_in_file, headers_snake = convert_tsv_to_csv(src_path)

            upload_path = f"Files/bronze/adventure_works/{entity_folder}/{src_filename}"

            if args.dry_run:
                lines = csv_text.split("\n")
                print(f"    source: {list(headers_in_file)}")
                print(f"    converted to: {headers_snake}")
                print(f"    rows: {len(lines) - 1}")
                print(f"    would upload to: {upload_path}")
            else:
                if upload_file(workspace_id, item_id, upload_path, csv_text.encode(), storage_token):
                    print(f"    {len(headers_snake)} columns, {len(csv_text.split(chr(10))) - 1} rows")
                else:
                    failures += 1

        except Exception as exc:
            print(f"  [ERROR]  {src_filename}: {exc}")
            failures += 1

    print()
    if args.dry_run:
        print("[DRY RUN] No files were actually uploaded")
    else:
        uploaded = len(ENTITIES) - failures
        print(f"{uploaded} files uploaded, {failures} failures")

    if failures == 0:
        print("\nNext step: Run p_orchestrate_master pipeline")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
