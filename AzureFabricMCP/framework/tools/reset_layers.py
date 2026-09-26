"""
Drop every table in bronze, silver and gold so a run starts from empty.

For demos, benchmarking, and reproducing a defect from a known state.

Why gold needs explicit dropping
--------------------------------
Bronze appends and silver overwrites, so those are straightforward. Gold is
not: `merge_scd2` PRESERVES history by design, so a dimension carries its
existing rows and validity windows into the next run. Without dropping, a
"fresh" run produces a dimension that is anything but -- and, worse, one that
still holds whatever the previous load got wrong.

Lakehouse tables are removed through the OneLake DFS API. Warehouse tables need
T-SQL, which neither the Spark connector nor the Fabric REST API provides, so a
temporary notebook issues the DROPs over JDBC and is deleted afterwards.

SOURCE FILES ARE NEVER TOUCHED. Only `Tables/` is cleared; `Files/` keeps the
landed CSVs, so the next run re-lands from the same source.

Refuses to run against an environment marked `is_production`.

Usage:
    python reset_layers.py --project ./01_demo-project --env dev
    python reset_layers.py --project ./01_demo-project --env dev --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _project import get_environment, get_storage_ids, get_workspace_id  # noqa: E402

FABRIC_API = "https://api.fabric.microsoft.com/v1"
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"


def with_retry(method, url, *, attempts: int = 4, **kwargs):
    """Issue a request, retrying transient network failures.

    OneLake intermittently drops a connection mid-operation -- observed on both
    an upload and a delete in the same session. Without a retry the whole run
    aborts partway, leaving the layers half-cleared, which is a worse state
    than either fully cleared or untouched.
    """
    delay = 5
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(method, url, **kwargs)
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as exc:
            if attempt == attempts:
                raise
            print(f"      transient {type(exc).__name__}; retrying in {delay}s")
            time.sleep(delay)
            delay *= 2
            continue

        # 5xx and throttling are worth another go; 4xx is not.
        if response.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
            print(f"      HTTP {response.status_code}; retrying in {delay}s")
            time.sleep(delay)
            delay *= 2
            continue
        return response

    raise RuntimeError(f"{method} {url} failed after {attempts} attempts")


def credential():
    from azure.identity import AzureCliCredential, ClientSecretCredential

    client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("FABRIC_CLIENT_ID")
    secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("FABRIC_CLIENT_SECRET")
    tenant = os.getenv("AZURE_TENANT_ID") or os.getenv("FABRIC_TENANT_ID")
    if client_id and secret and tenant:
        return ClientSecretCredential(tenant, client_id, secret)
    return AzureCliCredential()


# ---------------------------------------------------------------------------
# Lakehouse
# ---------------------------------------------------------------------------

def clear_lakehouse(token: str, workspace: str, item_id: str,
                    label: str, dry_run: bool) -> tuple[int, int]:
    headers = {"Authorization": f"Bearer {token}"}
    listing = with_retry(
        "GET", f"{ONELAKE}/{workspace}", headers=headers,
        params={"resource": "filesystem", "recursive": "false",
                "directory": f"{item_id}/Tables"}, timeout=60)

    if not listing.ok:
        print(f"  {label}: no Tables directory")
        return 0, 0

    tables = sorted(p["name"].split("/")[-1] for p in listing.json().get("paths", []))
    if not tables:
        print(f"  {label}: already empty")
        return 0, 0

    dropped = failed = 0
    for table in tables:
        if dry_run:
            print(f"  {label}: would drop {table}")
            continue
        response = with_retry("DELETE", f"{ONELAKE}/{workspace}/{item_id}/Tables/{table}",
                              headers=headers, params={"recursive": "true"},
                              timeout=120)
        ok = response.status_code in (200, 202, 204)
        print(f"  {label}: {'dropped' if ok else 'FAILED'} {table}")
        dropped += ok
        failed += not ok
    return dropped, failed


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------

DROP_NOTEBOOK = '''
import traceback
report = []
def log(m):
    print(m); report.append(str(m))

try:
    token = mssparkutils.credentials.getToken("https://database.windows.net/")
    jvm = spark._jvm
    props = jvm.java.util.Properties()
    props.setProperty("accessToken", token)
    props.setProperty("encrypt", "true")
    conn = jvm.java.sql.DriverManager.getConnection(
        "jdbc:sqlserver://__ENDPOINT__:1433;database=__DATABASE__", props)
    stmt = conn.createStatement()

    rs = stmt.executeQuery(
        "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_TYPE = 'BASE TABLE'")
    targets = []
    while rs.next():
        targets.append((rs.getString(1), rs.getString(2)))
    rs.close()

    for schema, table in targets:
        stmt.execute(f"DROP TABLE IF EXISTS [{schema}].[{table}]")
        log(f"dropped {schema}.{table}")
    if not targets:
        log("warehouse already empty")

    stmt.close(); conn.close()
    log("OK")
except Exception:
    log(traceback.format_exc())

mssparkutils.fs.put("Files/_reset_report.txt", "\\n".join(report), True)
'''


def clear_warehouse(fabric_token: str, storage_token: str, workspace: str,
                    warehouse_name: str, scratch_lakehouse: str,
                    dry_run: bool) -> bool:
    """Drop warehouse tables via a temporary notebook issuing T-SQL over JDBC."""
    headers = {"Authorization": f"Bearer {fabric_token}", "Content-Type": "application/json"}

    warehouses = requests.get(f"{FABRIC_API}/workspaces/{workspace}/warehouses",
                              headers=headers, timeout=60).json().get("value", [])
    warehouse = next((w for w in warehouses if w["displayName"] == warehouse_name), None)
    if warehouse is None:
        print(f"  {warehouse_name}: not found")
        return True

    endpoint = (warehouse.get("properties") or {}).get("connectionString")
    if not endpoint:
        print(f"  {warehouse_name}: no connection string exposed")
        return False

    if dry_run:
        print(f"  {warehouse_name}: would drop every base table via {endpoint}")
        return True

    code = DROP_NOTEBOOK.replace("__ENDPOINT__", endpoint).replace("__DATABASE__", warehouse_name)
    notebook = {
        "cells": [{"cell_type": "code", "execution_count": None, "metadata": {},
                   "outputs": [], "source": code.splitlines(keepends=True)}],
        "metadata": {
            "kernelspec": {"display_name": "synapse_pyspark", "name": "synapse_pyspark"},
            "language_info": {"name": "python"},
            "dependencies": {"lakehouse": {
                "default_lakehouse": scratch_lakehouse,
                "default_lakehouse_workspace_id": workspace,
                "known_lakehouses": [{"id": scratch_lakehouse}]}},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }

    b64 = lambda text: base64.b64encode(text.encode()).decode()
    created = requests.post(
        f"{FABRIC_API}/workspaces/{workspace}/items", headers=headers,
        json={"displayName": "_nb_reset_tmp", "type": "Notebook",
              "description": "Temporary. Drops warehouse tables, then deletes itself."},
        timeout=120)
    if created.status_code not in (200, 201):
        print(f"  could not create the temporary notebook: {created.status_code}")
        return False
    notebook_id = created.json()["id"]

    try:
        platform = json.dumps({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": "Notebook", "displayName": "_nb_reset_tmp"},
            "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"}})
        push = requests.post(
            f"{FABRIC_API}/workspaces/{workspace}/notebooks/{notebook_id}"
            f"/updateDefinition?updateMetadata=false",
            headers=headers,
            json={"definition": {"format": "ipynb", "parts": [
                {"path": "notebook-content.ipynb", "payload": b64(json.dumps(notebook)),
                 "payloadType": "InlineBase64"},
                {"path": ".platform", "payload": b64(platform),
                 "payloadType": "InlineBase64"}]}},
            timeout=180)
        location = push.headers.get("Location")
        while location:
            time.sleep(3)
            if requests.get(location, headers=headers, timeout=60).json().get("status") \
                    in ("Succeeded", "Failed"):
                break

        run = requests.post(
            f"{FABRIC_API}/workspaces/{workspace}/items/{notebook_id}"
            f"/jobs/instances?jobType=RunNotebook", headers=headers, json={}, timeout=60)
        location = run.headers.get("Location")
        status = None
        for _ in range(40):
            time.sleep(15)
            status = requests.get(location, headers=headers, timeout=60).json().get("status")
            if status in ("Completed", "Failed", "Cancelled"):
                break

        report = requests.get(
            f"{ONELAKE}/{workspace}/{scratch_lakehouse}/Files/_reset_report.txt",
            headers={"Authorization": f"Bearer {storage_token}"}, timeout=60)
        if report.ok:
            for line in report.text.splitlines():
                print(f"  {warehouse_name}: {line}")
        return status == "Completed"
    finally:
        requests.delete(f"{FABRIC_API}/workspaces/{workspace}/items/{notebook_id}",
                        headers=headers, timeout=60)
        print("  temporary notebook removed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-production", action="store_true",
                        help="required to reset an environment marked is_production")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    try:
        environment = get_environment(project, args.env)
        workspace = get_workspace_id(project, args.env)
        storage = get_storage_ids(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    if environment.get("is_production") and not args.allow_production:
        print(f"  REFUSED  {args.env!r} is marked is_production. This drops every "
              f"table in every layer. Pass --allow-production if that is genuinely "
              f"what you want.")
        return 2

    print(f"environment {args.env}  ->  {environment.get('workspace')}  ({workspace})")
    print("clearing Tables/ only -- landed source files are untouched")
    if args.dry_run:
        print("dry run -- nothing will be dropped")
    print()

    cred = credential()
    fabric_token = cred.get_token(FABRIC_SCOPE).token
    storage_token = cred.get_token(STORAGE_SCOPE).token

    dropped = failed = 0
    for name, item_id in sorted(storage.items()):
        if name.startswith("lh_"):
            d, f = clear_lakehouse(storage_token, workspace, item_id, name, args.dry_run)
            dropped += d
            failed += f

    for name in sorted(storage):
        if name.startswith("wh_"):
            scratch = next((i for n, i in storage.items() if n.startswith("lh_")), None)
            if scratch is None:
                print(f"  {name}: needs a lakehouse to host the temporary notebook")
                failed += 1
                continue
            if not clear_warehouse(fabric_token, storage_token, workspace,
                                   name, scratch, args.dry_run):
                failed += 1

    print()
    print(f"{dropped} lakehouse tables dropped, {failed} failures")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
