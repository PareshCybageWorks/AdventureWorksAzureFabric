"""
Apply warehouse migrations to a Fabric Warehouse.

Neither the Spark connector nor the Fabric REST API issues T-SQL DDL, so the
statements are executed over JDBC from a temporary notebook, which is deleted
afterwards. The notebook writes its own outcome to OneLake, because Fabric
reports only "session failed" for a notebook run and never the statement error.

Migrations are applied in filename order and recorded in a history table, so a
re-run applies only what is new. The DDL itself is idempotent (CREATE OR ALTER),
so re-applying is harmless -- but the history makes it visible which
environment is at which version.

Usage:
    python run_migrations.py --project ./01_demo-project --env dev
    python run_migrations.py --project ./01_demo-project --env dev --dry-run
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import get_environment, get_storage_ids, get_workspace_id

FABRIC_API = "https://api.fabric.microsoft.com/v1"
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"

HISTORY_TABLE = "dbo.schema_migrations"

RUNNER = '''
import traceback
report = []
def log(m):
    print(m); report.append(str(m))

MIGRATIONS = __MIGRATIONS__

try:
    token = mssparkutils.credentials.getToken("https://database.windows.net/")
    jvm = spark._jvm
    props = jvm.java.util.Properties()
    props.setProperty("accessToken", token)
    props.setProperty("encrypt", "true")
    conn = jvm.java.sql.DriverManager.getConnection(
        "jdbc:sqlserver://__ENDPOINT__:1433;database=__DATABASE__", props)
    conn.setAutoCommit(True)
    stmt = conn.createStatement()

    # History first, so the very first run has somewhere to record itself.
    stmt.execute(
        "IF NOT EXISTS (SELECT 1 FROM sys.tables t JOIN sys.schemas s "
        "ON t.schema_id = s.schema_id WHERE s.name = 'dbo' AND t.name = 'schema_migrations') "
        # DATETIME2(6), not bare DATETIME2 -- Fabric Warehouse requires an
        # explicit precision and rejects the unqualified type that SQL Server
        # accepts, with a message that never mentions the column.
        "CREATE TABLE [dbo].[schema_migrations] ("
        "  migration VARCHAR(200) NOT NULL, applied_at DATETIME2(6) NOT NULL)")

    applied = set()
    rs = stmt.executeQuery("SELECT migration FROM [dbo].[schema_migrations]")
    while rs.next():
        applied.add(rs.getString(1))
    rs.close()

    for name, sql in MIGRATIONS:
        if name in applied:
            log(f"skip    {name} (already applied)")
            continue
        # GO is a client batch separator, not T-SQL -- the driver would reject it.
        #
        # Each batch is attempted independently. A migration is a set of
        # objects, not one atomic change, and aborting the whole file on the
        # first failure means one malformed view blocks every healthy one --
        # while reporting a single error that hides how many others were fine.
        batches = [b.strip() for b in sql.split("\\nGO\\n") if b.strip()]
        errors = []
        for batch in batches:
            try:
                stmt.execute(batch)
            except Exception as exc:
                # First line of the batch identifies the object; the rest is body.
                head = next((l for l in batch.splitlines() if l.strip()), "?")
                errors.append(f"{head.strip()[:70]}\\n              {exc}")

        if errors:
            log(f"PARTIAL {name}  ({len(batches) - len(errors)}/{len(batches)} ok)")
            for err in errors:
                log(f"        FAILED  {err}")
            # Not recorded as applied, so a re-run retries once the spec is fixed.
            continue

        stmt.execute(
            f"INSERT INTO [dbo].[schema_migrations] (migration, applied_at) "
            f"VALUES ('{name}', SYSDATETIME())")
        log(f"applied {name}  ({len(batches)} statements)")

    rs = stmt.executeQuery(
        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES "
        "ORDER BY TABLE_TYPE, TABLE_SCHEMA, TABLE_NAME")
    log("")
    log("warehouse now contains:")
    while rs.next():
        log(f"   {rs.getString(3):<10} {rs.getString(1)}.{rs.getString(2)}")
    rs.close()

    stmt.close(); conn.close()
    log("OK")
except Exception:
    log(traceback.format_exc())

mssparkutils.fs.put("Files/_migration_report.txt", "\\n".join(report), True)
'''


def credential():
    from azure.identity import AzureCliCredential, ClientSecretCredential

    client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("FABRIC_CLIENT_ID")
    secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("FABRIC_CLIENT_SECRET")
    tenant = os.getenv("AZURE_TENANT_ID") or os.getenv("FABRIC_TENANT_ID")
    if client_id and secret and tenant:
        return ClientSecretCredential(tenant, client_id, secret)
    return AzureCliCredential()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--migrations", default="generated/migrations")
    parser.add_argument("--warehouse", default="wh_gold")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    try:
        environment = get_environment(project, args.env)
        workspace = get_workspace_id(project, args.env)
        storage = get_storage_ids(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    directory = project / args.migrations
    files = sorted(directory.glob("V*.sql"))
    if not files:
        print(f"  ERROR  no migrations in {directory}. Run generate_ddl.py first.")
        return 2

    print(f"environment {args.env}  ->  {environment.get('workspace')}  ({workspace})")
    print(f"{len(files)} migrations from {directory.name}")
    for path in files:
        print(f"    {path.name}")
    if args.dry_run:
        print("\ndry run -- nothing applied")
        return 0

    cred = credential()
    fabric_token = cred.get_token(FABRIC_SCOPE).token
    storage_token = cred.get_token(STORAGE_SCOPE).token
    headers = {"Authorization": f"Bearer {fabric_token}", "Content-Type": "application/json"}

    warehouses = requests.get(f"{FABRIC_API}/workspaces/{workspace}/warehouses",
                              headers=headers, timeout=60).json().get("value", [])
    warehouse = next((w for w in warehouses if w["displayName"] == args.warehouse), None)
    if warehouse is None:
        print(f"  ERROR  no warehouse named {args.warehouse!r}")
        return 2
    endpoint = (warehouse.get("properties") or {}).get("connectionString")

    scratch = next((i for n, i in storage.items() if n.startswith("lh_")), None)
    if scratch is None:
        print("  ERROR  a lakehouse is needed to host the temporary notebook")
        return 2

    payload = [[path.name, path.read_text(encoding="utf-8")] for path in files]
    code = (RUNNER
            .replace("__MIGRATIONS__", json.dumps(payload))
            .replace("__ENDPOINT__", endpoint)
            .replace("__DATABASE__", args.warehouse))

    notebook = {
        "cells": [{"cell_type": "code", "execution_count": None, "metadata": {},
                   "outputs": [], "source": code.splitlines(keepends=True)}],
        "metadata": {
            "kernelspec": {"display_name": "synapse_pyspark", "name": "synapse_pyspark"},
            "language_info": {"name": "python"},
            "dependencies": {"lakehouse": {
                "default_lakehouse": scratch,
                "default_lakehouse_workspace_id": workspace,
                "known_lakehouses": [{"id": scratch}]}},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }

    b64 = lambda text: base64.b64encode(text.encode()).decode()
    created = requests.post(
        f"{FABRIC_API}/workspaces/{workspace}/items", headers=headers,
        json={"displayName": "_nb_migrate_tmp", "type": "Notebook",
              "description": "Temporary. Applies warehouse migrations, then deletes itself."},
        timeout=120)
    if created.status_code not in (200, 201):
        print(f"  ERROR  could not create the runner notebook: {created.text[:200]}")
        return 1
    notebook_id = created.json()["id"]

    try:
        platform = json.dumps({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": "Notebook", "displayName": "_nb_migrate_tmp"},
            "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"}})
        push = requests.post(
            f"{FABRIC_API}/workspaces/{workspace}/notebooks/{notebook_id}"
            f"/updateDefinition?updateMetadata=false", headers=headers,
            json={"definition": {"format": "ipynb", "parts": [
                {"path": "notebook-content.ipynb", "payload": b64(json.dumps(notebook)),
                 "payloadType": "InlineBase64"},
                {"path": ".platform", "payload": b64(platform),
                 "payloadType": "InlineBase64"}]}}, timeout=180)
        location = push.headers.get("Location")
        while location:
            time.sleep(3)
            if requests.get(location, headers=headers, timeout=60).json().get("status") \
                    in ("Succeeded", "Failed"):
                break

        print()
        print("applying...")
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
            f"{ONELAKE}/{workspace}/{scratch}/Files/_migration_report.txt",
            headers={"Authorization": f"Bearer {storage_token}"}, timeout=90)
        if report.ok:
            for line in report.text.splitlines():
                print(f"  {line}")
        else:
            print(f"  (no report; notebook status was {status})")
        return 0 if status == "Completed" else 1
    finally:
        requests.delete(f"{FABRIC_API}/workspaces/{workspace}/items/{notebook_id}",
                        headers=headers, timeout=60)
        print()
        print("temporary notebook removed")


if __name__ == "__main__":
    sys.exit(main())
