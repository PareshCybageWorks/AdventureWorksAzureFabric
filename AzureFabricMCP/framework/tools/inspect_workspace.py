"""
Read the code already in a Fabric workspace, and report its conventions.

Why this exists
---------------
Writing a notebook for a workspace someone else built means matching what is
already there: how tables are addressed, which layer names are in use, how
parameters are passed, whether writes append or overwrite. Guessing any of
those produces a notebook that runs and quietly disagrees with its neighbours
-- a second naming scheme, a second way of reading the same lakehouse.

The portal shows one item at a time, which makes the CONVENTION invisible: you
can read ten notebooks and still not notice that nine of them use
`spark.read.table` and the tenth uses an abfss path. This reads every item's
definition and reports the patterns across all of them, so the convention is a
fact rather than an impression.

Read-only. It fetches definitions and writes them locally; it changes nothing
in the workspace.

Usage:
    python inspect_workspace.py --project ./01_demo-project --env dev
    python inspect_workspace.py --workspace <guid> --out ./inspected
    python inspect_workspace.py --project ./01_demo-project --env dev --item nb_load_orders
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "deploy"))
import _tsql                                   # noqa: E402
from _project import get_workspace_id          # noqa: E402

API = "https://api.fabric.microsoft.com/v1"

# Item types whose definition is code worth reading.
CODE_TYPES = {"Notebook", "DataPipeline", "SparkJobDefinition"}

PATTERNS = {
    "spark.read.table":     re.compile(r"spark\.read\.table\(", re.I),
    "spark.sql":            re.compile(r"spark\.sql\(", re.I),
    "abfss path":           re.compile(r"abfss://", re.I),
    "Files/ path":          re.compile(r"[\"']Files/", re.I),
    "Tables/ path":         re.compile(r"[\"']Tables/", re.I),
    "delta write":          re.compile(r"\.format\(\s*[\"']delta", re.I),
    "mode overwrite":       re.compile(r"mode\(\s*[\"']overwrite", re.I),
    "mode append":          re.compile(r"mode\(\s*[\"']append", re.I),
    "merge / upsert":       re.compile(r"\bMERGE\b|\.merge\(", re.I),
    "warehouse connector":  re.compile(r"synapsesql", re.I),
    "mssparkutils":         re.compile(r"mssparkutils|notebookutils", re.I),
    "parameters cell":      re.compile(r"#\s*PARAMETERS|\"parameters\"\s*:\s*true", re.I),
}

READ_TABLE = re.compile(r"spark\.read\.table\(\s*[\"']([\w.]+)[\"']", re.I)
SAVE_TABLE = re.compile(r"saveAsTable\(\s*[\"']([\w.]+)[\"']", re.I)
SQL_FROM = re.compile(r"\bFROM\s+(\[?\w+\]?\.\[?\w+\]?)", re.I)
IMPORTS = re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.M)

# Python's `from pyspark.sql import ...` is indistinguishable from SQL's
# `FROM schema.table` to a regex. Left in, the report lists `pyspark.sql` and
# `ttfabric.cleansing` as tables -- and someone goes looking for a table that
# was never a table. Import lines are removed before the SQL scan.
IMPORT_LINE = re.compile(r"^\s*(?:from|import)\s+.*$", re.M)


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_tsql.credential().get_token(_tsql.FABRIC_SCOPE).token}"}


def list_items(workspace: str, h: dict) -> list[dict]:
    r = _tsql.with_retry("GET", f"{API}/workspaces/{workspace}/items", headers=h)
    r.raise_for_status()
    return r.json().get("value", [])


def get_definition(workspace: str, item: dict, h: dict) -> str:
    """Definition text for one item, following the long-running operation.

    getDefinition returns 202 with only a Location header for anything
    non-trivial. Treating that as 'no definition' silently reports every large
    notebook as empty, which looks like a workspace of stubs.
    """
    url = f"{API}/workspaces/{workspace}/items/{item['id']}/getDefinition"
    r = _tsql.with_retry("POST", url, headers=h, timeout=180)

    payload = None
    if r.status_code == 202:
        location = r.headers.get("Location")
        for _ in range(30):
            time.sleep(2)
            got = _tsql.with_retry("GET", location, headers=h)
            if got.ok and got.content:
                body = got.json()
                if body.get("status") in ("Succeeded", None) or "definition" in body:
                    result = _tsql.with_retry("GET", location.rstrip("/") + "/result", headers=h)
                    payload = result.json() if result.ok and result.content else body
                    break
                if body.get("status") == "Failed":
                    return ""
    elif r.ok and r.content:
        payload = r.json()

    if not payload:
        return ""
    text = ""
    for part in (payload.get("definition") or {}).get("parts", []):
        if part.get("payloadType") == "InlineBase64":
            try:
                text += base64.b64decode(part["payload"]).decode("utf-8", "replace") + "\n"
            except Exception:
                continue
    return text


def notebook_source(text: str) -> str:
    """Strip .ipynb JSON down to its source lines, so regexes see code."""
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    out = []
    for cell in doc.get("cells", []):
        src = cell.get("source")
        out.append("".join(src) if isinstance(src, list) else str(src or ""))
    return "\n".join(out)


def analyse(name: str, kind: str, text: str) -> dict:
    code = notebook_source(text) if kind == "Notebook" else text
    found = [label for label, rx in PATTERNS.items() if rx.search(code)]
    sql_only = IMPORT_LINE.sub("", code)
    reads = set(READ_TABLE.findall(code)) | {
        m for m in SQL_FROM.findall(sql_only) if "." in m}
    writes = set(SAVE_TABLE.findall(code))
    mods = Counter(i.split(".")[0] for i in IMPORTS.findall(code))
    return {"name": name, "type": kind, "chars": len(code),
            "patterns": found, "reads": sorted(reads), "writes": sorted(writes),
            "imports": [m for m, _ in mods.most_common(8)]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project")
    ap.add_argument("--env", default="dev")
    ap.add_argument("--workspace", help="workspace id, instead of --project/--env")
    ap.add_argument("--item", help="only this item, by display name")
    ap.add_argument("--out", help="write decoded definitions into this directory")
    args = ap.parse_args()

    if args.workspace:
        workspace = args.workspace
    elif args.project:
        workspace = get_workspace_id(Path(args.project).resolve(), args.env)
    else:
        print("  need --project (with --env) or --workspace")
        return 2

    h = headers()
    items = list_items(workspace, h)
    print(f"  workspace {workspace}")
    print(f"  {len(items)} item(s)\n")

    by_type = Counter(i["type"] for i in items)
    for t, n in sorted(by_type.items()):
        print(f"    {t:<22}{n}")

    targets = [i for i in items if i["type"] in CODE_TYPES]
    if args.item:
        targets = [i for i in targets if i["displayName"] == args.item]
        if not targets:
            print(f"\n  no code item named {args.item!r}")
            return 1

    print(f"\n  reading {len(targets)} code item(s)...\n")
    reports, pattern_tally = [], Counter()
    outdir = Path(args.out) if args.out else None
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)

    for item in targets:
        text = get_definition(workspace, item, h)
        if not text:
            print(f"    {item['displayName']:<34} (no definition returned)")
            continue
        rep = analyse(item["displayName"], item["type"], text)
        reports.append(rep)
        pattern_tally.update(rep["patterns"])
        if outdir:
            ext = ".ipynb" if item["type"] == "Notebook" else ".json"
            (outdir / f"{item['displayName']}{ext}").write_text(text, encoding="utf-8")
        print(f"    {rep['name']:<34}{rep['chars']:>7} chars  "
              f"reads {len(rep['reads'])}  writes {len(rep['writes'])}")

    if not reports:
        print("\n  nothing readable. Either the workspace has no code items, or the "
              "\n  identity lacks permission to read their definitions.")
        return 1

    # ---- the part the portal cannot show you -----------------------------
    print("\n  CONVENTIONS IN USE  (count of items using each)\n")
    for label, n in pattern_tally.most_common():
        share = f"{n}/{len(reports)}"
        note = "  <-- minority, check before copying" if 0 < n < len(reports) * 0.34 else ""
        print(f"    {label:<24}{share:>8}{note}")

    tables = Counter()
    for r in reports:
        tables.update(r["reads"]); tables.update(r["writes"])
    if tables:
        print("\n  TABLES REFERENCED\n")
        for t, n in tables.most_common(15):
            print(f"    {t:<40}{n}")

    prefixes = Counter(t.split("_")[0] for t in tables if "_" in t)
    if prefixes:
        print("\n  NAMING PREFIXES\n")
        for p, n in prefixes.most_common(8):
            print(f"    {p+'_':<12}{n}")

    if outdir:
        (outdir / "_inventory.json").write_text(
            json.dumps({"workspace": workspace, "items": reports}, indent=2),
            encoding="utf-8")
        print(f"\n  definitions written to {outdir}")

    print("\n  Match the MAJORITY convention above. A minority pattern is either "
          "\n  a deliberate exception with a reason, or the bug you are about to copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
