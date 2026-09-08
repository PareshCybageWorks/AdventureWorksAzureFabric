"""
Run T-SQL against a Fabric Warehouse.

Fabric exposes no API for this. The Spark connector reads and writes tables but
issues no DDL, and the REST API manages items rather than their contents. The
only route is a SQL connection from inside the tenant, so this uploads a
temporary notebook, runs it, collects the result, and deletes it.

The notebook writes its outcome to OneLake as a file rather than relying on the
run's output, because a failed Fabric notebook reports only "session failed" --
never the statement that failed or why. Without the file, every error looks
identical.

Callers pass T-SQL and get back the printed report. Used by run_migrations,
reset_layers, and query_warehouse; the pattern is identical in all three and
subtly wrong in slightly different ways when it is copied.

This is a private module: no stable API, no backwards-compatibility promise.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid

import requests

FABRIC_API = "https://api.fabric.microsoft.com/v1"
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"

# The notebook body. __BODY__ is the caller's code, which runs with `stmt`
# (a live JDBC Statement) and `log(...)` already in scope.
TEMPLATE = '''
import traceback
report = []
def log(m):
    print(m); report.append(str(m))

PAYLOAD = __PAYLOAD__

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

__BODY__

    stmt.close(); conn.close()
    log("OK")
except Exception:
    log(traceback.format_exc())

mssparkutils.fs.put("Files/__REPORT__", "\\n".join(report), True)
'''


def with_retry(method: str, url: str, *, attempts: int = 4, **kwargs):
    """Issue a request, retrying transient failures with exponential backoff.

    OneLake intermittently drops or stalls a connection -- frequently enough to
    abort a run that had already succeeded, since the work happens inside Fabric
    and only the report fetch fails. Retrying turns a lost result back into a
    readable one.
    """
    delay = 5
    for attempt in range(1, attempts + 1):
        try:
            kwargs.setdefault('verify', False)
            response = requests.request(method, url, **kwargs)
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as exc:
            if attempt == attempts:
                raise
            print(f"    transient {type(exc).__name__}; retrying in {delay}s")
            time.sleep(delay)
            delay *= 2
            continue

        # Throttling and 5xx are worth another attempt; a 4xx will not change.
        if response.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
            wait = int(response.headers.get("Retry-After", delay))
            print(f"    HTTP {response.status_code}; retrying in {wait}s")
            time.sleep(wait)
            delay *= 2
            continue
        return response

    raise RuntimeError(f"{method} {url} failed after {attempts} attempts")


def credential(secret_ref: str | None = None):
    """Service principal when one is available, else the CLI login.

    `secret_ref` is a spec reference such as
    `keyvault://techtonic-kv/fabric-sp-secret`. It is resolved only when the
    environment does not already carry the secret, so CI keeps using the
    variables GitHub injects and never makes a round trip to Key Vault.

    Falling back to the CLI login is what lets a developer run any of this
    without a service principal at all.
    """
    from azure.identity import AzureCliCredential, ClientSecretCredential

    client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("FABRIC_CLIENT_ID")
    secret = os.getenv("AZURE_CLIENT_SECRET") or os.getenv("FABRIC_CLIENT_SECRET")
    tenant = os.getenv("AZURE_TENANT_ID") or os.getenv("FABRIC_TENANT_ID")

    if client_id and tenant and not secret and secret_ref:
        # The id and tenant are known but the secret is not in the environment,
        # which is the case a vault exists for.
        import _secrets
        secret = _secrets.resolve(secret_ref, required=False)

    if client_id and secret and tenant:
        return ClientSecretCredential(tenant, client_id, secret)
    return AzureCliCredential()


def warehouse_endpoint(headers: dict, workspace: str, name: str) -> str | None:
    """SQL endpoint for a warehouse OR a lakehouse of this name.

    A lakehouse exposes a read-only SQL endpoint over its Delta tables, and it
    is the only way to query silver from outside Spark -- which is where the DQ
    results land. Falling back to it here means callers name an item without
    having to know which kind it is.

    Note the endpoint lags: a table written by Spark appears in OneLake at once
    but takes a minute or two to become visible through SQL. An "Invalid object
    name" on a table you just wrote is usually that, not a failed write.
    """
    response = requests.get(f"{FABRIC_API}/workspaces/{workspace}/warehouses",
                            headers=headers, timeout=60, verify=False)
    warehouse = next((w for w in response.json().get("value", [])
                      if w["displayName"] == name), None)
    if warehouse is not None:
        return (warehouse.get("properties") or {}).get("connectionString")

    response = requests.get(f"{FABRIC_API}/workspaces/{workspace}/lakehouses",
                            headers=headers, timeout=60, verify=False)
    lakehouse = next((l for l in response.json().get("value", [])
                      if l["displayName"] == name), None)
    if lakehouse is None:
        return None
    return (((lakehouse.get("properties") or {})
             .get("sqlEndpointProperties") or {}).get("connectionString"))


def run_notebook(headers: dict, workspace: str, name: str,
                 parameters: dict | None = None,
                 timeout_minutes: int = 25) -> tuple[str, str]:
    """Run a notebook by display name. Returns (status, notebook id).

    Fabric reports only a coarse status for a notebook run -- never the
    statement that failed. Anything needing a diagnosis has the notebook write
    its own outcome somewhere readable.
    """
    items = requests.get(f"{FABRIC_API}/workspaces/{workspace}/items?type=Notebook",
                         headers=headers, timeout=60).json().get("value", [])
    notebook = next((i for i in items if i["displayName"] == name), None)
    if notebook is None:
        return "NotFound", ""

    body: dict = {}
    if parameters:
        body["executionData"] = {
            "parameters": {k: {"value": v, "type": "string"}
                           for k, v in parameters.items()}}

    started = with_retry(
        "POST", f"{FABRIC_API}/workspaces/{workspace}/items/{notebook['id']}"
                f"/jobs/instances?jobType=RunNotebook",
        headers=headers, json=body, timeout=60)
    location = started.headers.get("Location")
    if not location:
        return f"NotStarted (HTTP {started.status_code})", notebook["id"]

    status = None
    for _ in range(timeout_minutes * 4):
        time.sleep(15)
        status = with_retry("GET", location, headers=headers,
                            timeout=60).json().get("status")
        if status in ("Completed", "Failed", "Cancelled"):
            return status, notebook["id"]
    return f"TimedOut after {timeout_minutes}m", notebook["id"]


def run(*, fabric_token: str, storage_token: str, workspace: str,
        warehouse: str, scratch_lakehouse: str, body: str,
        payload=None, label: str = "tsql", timeout_minutes: int = 10) -> tuple[bool, list[str]]:
    """Execute `body` against the warehouse. Returns (succeeded, report lines).

    `body` is Python source, indented four spaces to sit inside the template's
    try block, with `stmt` and `log` in scope. `payload` is any JSON-serialisable
    value, available to the body as PAYLOAD -- passing data this way avoids
    interpolating it into source, where a quote in the data becomes a syntax
    error.
    """
    headers = {"Authorization": f"Bearer {fabric_token}", "Content-Type": "application/json"}

    endpoint = warehouse_endpoint(headers, workspace, warehouse)
    if endpoint is None:
        return False, [f"no warehouse named {warehouse!r} in this workspace"]

    report_file = f"_{label}_report.txt"
    code = (TEMPLATE
            .replace("__PAYLOAD__", json.dumps(payload))
            .replace("__BODY__", body)
            .replace("__ENDPOINT__", endpoint)
            .replace("__DATABASE__", warehouse)
            .replace("__REPORT__", report_file))

    # Unique per run. A fixed name is deleted at the end of each run and Fabric
    # then reserves it, so two runs in quick succession collide with
    # ItemDisplayNameNotAvailableYet -- the second one failing for a reason
    # that has nothing to do with what it was asked to do.
    name = f"_nb_{label}_{uuid.uuid4().hex[:8]}"
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

    def b64(text: str) -> str:
        return base64.b64encode(text.encode()).decode()

    created = requests.post(
        f"{FABRIC_API}/workspaces/{workspace}/items", headers=headers,
        json={"displayName": name, "type": "Notebook",
              "description": "Temporary. Runs T-SQL, then deletes itself."}, timeout=120)
    if created.status_code not in (200, 201):
        return False, [f"could not create the runner notebook: {created.text[:200]}"]
    notebook_id = created.json()["id"]

    try:
        platform = json.dumps({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": "Notebook", "displayName": name},
            "config": {"version": "2.0",
                       "logicalId": "00000000-0000-0000-0000-000000000000"}})
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

        run_response = requests.post(
            f"{FABRIC_API}/workspaces/{workspace}/items/{notebook_id}"
            f"/jobs/instances?jobType=RunNotebook", headers=headers, json={}, timeout=60)
        location = run_response.headers.get("Location")

        status = None
        for _ in range(timeout_minutes * 4):
            time.sleep(15)
            status = requests.get(location, headers=headers, timeout=60).json().get("status")
            if status in ("Completed", "Failed", "Cancelled"):
                break

        # Retried: the work is already done inside Fabric by this point, so
        # losing the report to a flaky read would discard a successful run.
        report = with_retry(
            "GET", f"{ONELAKE}/{workspace}/{scratch_lakehouse}/Files/{report_file}",
            headers={"Authorization": f"Bearer {storage_token}"}, timeout=90)
        if report.ok:
            lines = report.text.splitlines()
            # The body traps its own errors and logs the traceback, so a
            # Completed run can still represent a failure. "OK" is the only
            # positive signal.
            return ("OK" in lines), lines
        return False, [f"no report was written; the notebook run was {status}"]
    finally:
        requests.delete(f"{FABRIC_API}/workspaces/{workspace}/items/{notebook_id}",
                        headers=headers, timeout=60)
