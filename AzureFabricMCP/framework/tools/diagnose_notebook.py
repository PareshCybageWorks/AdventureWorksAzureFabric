"""
Get the REAL traceback out of a failing generated notebook.

The problem
-----------
Fabric reports a failed notebook run as:

    System cancelled the Spark session due to statement execution failures

and nothing more. Not the exception, not the line, not the column. The Spark
monitoring API returns the same sentence. That single message covers a wrong
column name, a failed assertion, a schema mismatch and a capacity eviction --
four completely different problems with four different fixes.

Diagnosing by guessing from that message does not work. This gets the actual
exception.

How it works
------------
The notebook is generated output, so it can be rewritten temporarily: its whole
body is wrapped in try/except, the traceback is written to OneLake, and the
file is read back. push_items.py restores the original afterwards -- nothing is
lost, because the spec is the source.

TWO THINGS THAT MAKE THIS FAIL IF YOU BUILD IT YOURSELF
-------------------------------------------------------
1. Generated notebooks carry DEPLOY-TIME PLACEHOLDERS -- @@lakehouse:lh_silver@@,
   @@workspace@@, @@environment:env_spark@@ -- which push_items resolves. Upload
   them raw and the notebook has no default lakehouse, so spark.read.table and
   /lakehouse/default both fail and it dies before reaching the code you wanted
   to diagnose. This resolves them.

2. Creating a NEW notebook loses the lakehouse binding entirely. Rewriting the
   EXISTING deployed item keeps it.

Both were learned the hard way; two diagnostic attempts produced nothing before
the placeholders were resolved.

Usage:
    python diagnose_notebook.py --project ./03_live_demo --env dev nb_build_fct_ticket
    # then, to restore:
    python ../deploy/push_items.py --project ./03_live_demo --env dev
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
from _project import get_storage_ids, get_workspace_id      # noqa: E402

API = "https://api.fabric.microsoft.com/v1"
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"


def token_for(scope: str) -> str:
    from azure.identity import AzureCliCredential
    return AzureCliCredential().get_token(scope).token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--scratch-lakehouse", default="lh_silver",
                        help="lakehouse the traceback is written into")
    parser.add_argument("name")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    try:
        workspace = get_workspace_id(project, args.env)
        provisioned = get_storage_ids(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    scratch = provisioned.get(args.scratch_lakehouse)
    if not scratch:
        print(f"  ERROR  no provisioned id for {args.scratch_lakehouse}")
        return 2

    source = project / "generated" / "notebooks" / f"{args.name}.ipynb"
    if not source.exists():
        print(f"  ERROR  {source} not found")
        return 2

    headers = {"Authorization": f"Bearer {token_for(FABRIC_SCOPE)}",
               "Content-Type": "application/json"}
    items = requests.get(f"{API}/workspaces/{workspace}/items?type=Notebook",
                         headers=headers, timeout=60).json().get("value", [])
    item_id = next((i["id"] for i in items if i["displayName"] == args.name), None)
    if not item_id:
        print(f"  ERROR  {args.name} is not deployed to this workspace")
        return 2

    notebook = json.loads(source.read_text(encoding="utf-8"))
    body = "\n".join("".join(c["source"]) for c in notebook["cells"]
                     if c["cell_type"] == "code")
    indented = "\n".join("    " + line for line in body.splitlines())
    out_file = f"diag/{args.name}.txt"

    wrapped = (
        "import traceback, os\n"
        "try:\n"
        f"{indented}\n"
        "    _err = 'NO ERROR -- the notebook completed'\n"
        "except Exception:\n"
        "    _err = traceback.format_exc()\n"
        "print(_err)\n"
        "os.makedirs('/lakehouse/default/Files/diag', exist_ok=True)\n"
        f"open('/lakehouse/default/Files/{out_file}', 'w').write(_err)\n"
    )

    diagnostic = dict(notebook)
    diagnostic["cells"] = [{"cell_type": "code", "metadata": {},
                            "execution_count": None, "outputs": [],
                            "source": wrapped.splitlines(True)}]

    # Resolve the deploy-time placeholders. See point 1 in the docstring.
    text = json.dumps(diagnostic)
    replacements = {"@@workspace@@": workspace}
    for name, item in (provisioned or {}).items():
        replacements[f"@@lakehouse:{name}@@"] = item
        replacements[f"@@warehouse:{name}@@"] = item
    for name, item in ((provisioned or {}).get("environment") or {}).items() \
            if isinstance((provisioned or {}).get("environment"), dict) else []:
        replacements[f"@@environment:{name}@@"] = item
    for placeholder, value in replacements.items():
        if isinstance(value, str):
            text = text.replace(placeholder, value)

    if "@@" in text:
        leftover = {t.split("@@")[1] for t in text.split("@@")[1:2]}
        print(f"  WARNING  unresolved placeholder(s) remain: {leftover}. The "
              f"diagnostic may have no lakehouse and fail before running.")

    response = requests.post(
        f"{API}/workspaces/{workspace}/items/{item_id}/updateDefinition",
        headers=headers, timeout=120,
        json={"definition": {"format": "ipynb", "parts": [
            {"path": "notebook-content.ipynb",
             "payload": base64.b64encode(text.encode()).decode(),
             "payloadType": "InlineBase64"}]}})
    if response.status_code not in (200, 202):
        print(f"  ERROR  updateDefinition HTTP {response.status_code}")
        return 1

    time.sleep(10)
    run = requests.post(
        f"{API}/workspaces/{workspace}/items/{item_id}/jobs/instances"
        f"?jobType=RunNotebook", headers=headers, json={}, timeout=60)
    instance = run.headers.get("Location", "").rsplit("/", 1)[-1]
    print(f"running {args.name} with its body wrapped...")

    for _ in range(75):
        time.sleep(20)
        state = requests.get(
            f"{API}/workspaces/{workspace}/items/{item_id}/jobs/instances/{instance}",
            headers=headers, timeout=60)
        if state.status_code == 200 and \
                state.json().get("status") not in ("InProgress", "NotStarted"):
            print(f"status: {state.json().get('status')}")
            break

    read = requests.get(
        f"{ONELAKE}/{workspace}/{scratch}/Files/{out_file}",
        headers={"Authorization": f"Bearer {token_for(STORAGE_SCOPE)}"},
        timeout=60)

    print("\n" + "=" * 70)
    if read.status_code == 200:
        print(read.text)
    else:
        print(f"no traceback written (HTTP {read.status_code}).")
        print("The session died before the except block could run -- usually a")
        print("capacity eviction, or an unresolved lakehouse placeholder.")
    print("=" * 70)
    print(f"\nRESTORE the real notebook:\n"
          f"  python framework/deploy/push_items.py "
          f"--project {args.project} --env {args.env}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
