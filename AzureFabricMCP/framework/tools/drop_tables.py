"""
Drop NAMED tables from one storage item, so they can be rebuilt.

Why not reset_layers.py
-----------------------
reset_layers drops every table in bronze, silver AND gold, which means
re-running the entire pipeline. That is right for "start from empty" and far
too heavy for the common case: one table whose schema changed.

When you need this
------------------
1. A GOLD SCHEMA CHANGE. The Fabric warehouse Spark connector refuses to
   overwrite a table whose schema differs --

       FabricSparkTDSWriteError: Schemas are not equal

   -- and it cannot ALTER. So adding or removing a single gold column means
   dropping the table before the next build. Nothing else clears it.

2. A BRONZE TABLE THAT DOUBLED. Bronze appends. A table loaded twice cannot be
   corrected by re-landing -- that makes three copies. The table has to go
   first, and the load pattern has to be fixed, or it will double again.

Warehouse vs lakehouse
----------------------
Lakehouse tables drop with Spark SQL. Warehouse tables need T-SQL, which
neither the Spark connector nor the Fabric REST API exposes, so a temporary
notebook issues the DROPs over JDBC and is deleted afterwards. Both paths are
handled here; the item name decides which.

REFUSES to touch an environment marked is_production without --allow-production.

Usage:
    python drop_tables.py --project ./03_live_demo --env dev \
        --item lh_bronze bronze_netmon_synthetic_network_traffic_daily
    python drop_tables.py --project ./03_live_demo --env dev \
        --item wh_gold fct_time_entry --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _project import (get_environment, get_storage_ids,      # noqa: E402
                      get_workspace_id, load_scaffolding)

API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"


def get_token() -> str:
    from azure.identity import AzureCliCredential
    return AzureCliCredential().get_token(SCOPE).token


def lakehouse_code(tables: list[str]) -> str:
    body = "\n".join(
        f'spark.sql("DROP TABLE IF EXISTS {t}")\nprint("dropped {t}")'
        for t in tables)
    return "# Targeted drop -- see framework/tools/drop_tables.py\n" + body + "\n"


def warehouse_code(tables: list[str], endpoint: str, database: str) -> str:
    drops = "\n".join(
        f'    stmt.execute("DROP TABLE IF EXISTS [dbo].[{t}]")\n'
        f'    report.append("dropped dbo.{t}")' for t in tables)
    return f'''import traceback
report = []
try:
    token = mssparkutils.credentials.getToken("https://database.windows.net/")
    jvm = spark._jvm
    props = jvm.java.util.Properties()
    props.setProperty("accessToken", token)
    props.setProperty("encrypt", "true")
    conn = jvm.java.sql.DriverManager.getConnection(
        "jdbc:sqlserver://{endpoint}:1433;database={database}", props)
    stmt = conn.createStatement()
{drops}
    stmt.close(); conn.close()
    report.append("OK")
except Exception:
    report.append(traceback.format_exc())
print("\\n".join(report))
'''


def run_temp_notebook(workspace: str, headers: dict, code: str,
                      lakehouse_id: str, lakehouse_name: str,
                      environment_id: str | None) -> bool:
    dependencies = {"lakehouse": {
        "default_lakehouse": lakehouse_id,
        "default_lakehouse_name": lakehouse_name,
        "default_lakehouse_workspace_id": workspace,
        "known_lakehouses": [{"id": lakehouse_id}]}}
    if environment_id:
        dependencies["environment"] = {"environmentId": environment_id,
                                       "workspaceId": workspace}

    notebook = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "synapse_pyspark",
                           "name": "synapse_pyspark"},
            "language_info": {"name": "python"},
            "dependencies": dependencies,
        },
        "cells": [{"cell_type": "code", "metadata": {}, "execution_count": None,
                   "outputs": [], "source": code.splitlines(True)}],
    }

    # A deleted display name stays reserved for minutes, so never reuse one.
    name = f"nb_drop_tables_{time.strftime('%H%M%S')}"
    requests.post(f"{API}/workspaces/{workspace}/items", headers=headers,
                  timeout=120,
                  json={"displayName": name, "type": "Notebook",
                        "definition": {"format": "ipynb", "parts": [
                            {"path": "notebook-content.ipynb",
                             "payload": base64.b64encode(
                                 json.dumps(notebook).encode()).decode(),
                             "payloadType": "InlineBase64"}]}})

    item_id = None
    for _ in range(20):
        if item_id:
            break
        time.sleep(5)
        items = requests.get(f"{API}/workspaces/{workspace}/items?type=Notebook",
                             headers=headers, timeout=60).json().get("value", [])
        item_id = next((i["id"] for i in items if i["displayName"] == name), None)
    if not item_id:
        print("  ERROR  could not create the temporary notebook")
        return False

    run = requests.post(
        f"{API}/workspaces/{workspace}/items/{item_id}/jobs/instances"
        f"?jobType=RunNotebook", headers=headers, json={}, timeout=60)
    instance = run.headers.get("Location", "").rsplit("/", 1)[-1]

    ok = False
    for _ in range(60):
        time.sleep(20)
        state = requests.get(
            f"{API}/workspaces/{workspace}/items/{item_id}/jobs/instances/{instance}",
            headers=headers, timeout=60)
        if state.status_code == 200 and \
                state.json().get("status") not in ("InProgress", "NotStarted"):
            status = state.json().get("status")
            print(f"  {status}")
            ok = status == "Completed"
            break

    requests.delete(f"{API}/workspaces/{workspace}/items/{item_id}",
                    headers=headers, timeout=60)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--item", required=True,
                        help="storage item name, e.g. lh_bronze or wh_gold")
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("tables", nargs="+")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    try:
        workspace = get_workspace_id(project, args.env)
        provisioned = get_storage_ids(project, args.env)
        environment = get_environment(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    if environment.get("is_production") and not args.allow_production:
        print(f"  REFUSED  {args.env} is marked is_production. Pass "
              f"--allow-production if you really mean it.")
        return 2

    item_id = (provisioned or {}).get(args.item)
    if not item_id:
        print(f"  ERROR  no provisioned id for {args.item}. Known: "
              f"{sorted(k for k, v in (provisioned or {}).items() if isinstance(v, str))}")
        return 2

    print(f"environment {args.env}  ->  {environment.get('workspace')}")
    print(f"item        {args.item}  ({item_id})")
    for table in args.tables:
        print(f"  would drop {table}" if args.dry_run else f"  dropping {table}")
    if args.dry_run:
        print("\ndry run -- nothing dropped")
        return 0

    scaffolding, _ = load_scaffolding(project)
    if args.item.startswith("wh_"):
        endpoint = scaffolding.get("tenant", {}).get("warehouse_endpoint")
        if not endpoint:
            print("  ERROR  warehouse drops need the SQL endpoint. Add\n"
                  "         tenant.warehouse_endpoint to 01-scaffolding.yaml, or\n"
                  "         read it from the warehouse's connection string.")
            return 2
        code = warehouse_code(args.tables, endpoint, args.item)
        scratch = (provisioned or {}).get("lh_silver") or \
            (provisioned or {}).get("lh_bronze")
        scratch_name = "lh_silver" if (provisioned or {}).get("lh_silver") else "lh_bronze"
    else:
        code = lakehouse_code(args.tables)
        scratch, scratch_name = item_id, args.item

    env_id = ((provisioned or {}).get("environment") or {}).get("env_spark") \
        if isinstance((provisioned or {}).get("environment"), dict) else None

    ok = run_temp_notebook(workspace, {"Authorization": f"Bearer {get_token()}",
                                       "Content-Type": "application/json"},
                           code, scratch, scratch_name, env_id)
    print("done" if ok else "  the drop notebook did not complete")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
