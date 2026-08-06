"""
Pull a REST source into landing CSVs, driven entirely by 02-sources.yaml.

Why this exists
---------------
The framework's bronze path is file-based end to end: generate_notebooks,
dryrun and push_files all read a landing folder. `connection.kind: rest` is in
the contract's enum but no generator implements it, so a REST source has a gap
between "the API" and "a file bronze can read". This is that step, and until
now it was the one piece of a pipeline that had to be written by hand per
project.

It is spec-driven rather than project-specific: any source declaring
`connection.options.extract.protocol: rest` is extracted, using the endpoint,
pagination and query template that source already declares. Nothing about
ServiceNow is hard-coded except the offset-pagination convention its Table API
uses, which is named in the spec as `pagination: offset`.

The three things it gets right on purpose
-----------------------------------------
1. COLUMN ORDER. The bronze notebook applies the declared schema POSITIONALLY.
   Two same-typed columns swapped is silent, total corruption, so the header
   and every row are written in the exact order 02-sources declares -- never in
   whatever order the API returned.

2. IT WRITES A FULL SNAPSHOT, AND THAT IS DELIBERATE.

   The generated bronze notebook does the incremental work itself: it reads the
   whole landing folder, reads the high-water mark back out of the existing
   bronze table, filters `watermark > high_water` and appends only what is new.

   So an extract that ALSO filters would be doing the same job twice -- and
   worse, it would overwrite the landing file with just the delta, destroying
   the rows bronze still needs to see on a rebuild. An earlier version of this
   tool did exactly that: a second run replaced a 427-row file with 243 rows,
   because ServiceNow's `>=` is inclusive and most demo rows share a timestamp.

   Full snapshot here, incremental in bronze. One place owns it.

   `--since` is available for a genuinely huge table where a full pull is not
   viable, but it changes the landing file into a delta and the operator has to
   understand that before using it.

3. MISSING TABLES ARE REPORTED, NOT GUESSED AT. A table absent from the
   instance is skipped with a clear line, not written as an empty CSV -- an
   empty file looks like "the source had no rows today", which is a completely
   different fact.

Nothing here writes to the source system, and no credential value is printed.

Usage:
    python extract_rest.py --project 03_live_demo
    python extract_rest.py --project 03_live_demo --entity incident
    python extract_rest.py --project 03_live_demo --since 2026-01-01
    python extract_rest.py --project 03_live_demo --dry-run
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _secrets import load_project_env, resolve  # noqa: E402

PAGE_SIZE_DEFAULT = 1000


def _env(reference: str | None) -> str | None:
    """Resolve a ${VAR} reference to its value, never logging it."""
    if reference is None:
        return None
    return resolve(str(reference), required=False)


def _request(url: str, user: str, password: str, timeout: int = 120):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(url, headers={
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def fetch(endpoint: str, entity: str, extract: dict, user: str, password: str,
          watermark_column: str | None, since: str | None,
          page_size: int) -> list[dict] | None:
    """Page an entity out of the Table API. None means the table is absent."""
    base = endpoint.replace("{entity}", entity)
    rows: list[dict] = []
    offset = 0

    while True:
        params = {
            "sysparm_limit": str(page_size),
            "sysparm_offset": str(offset),
            # A display value returns a LABEL instead of a sys_id, and every
            # downstream join then matches nothing -- or worse, matches on a
            # label two records share.
            "sysparm_display_value": str(extract.get("sysparm_display_value", "false")),
            "sysparm_exclude_reference_link":
                str(extract.get("sysparm_exclude_reference_link", "true")),
        }
        if watermark_column:
            # Ordering is not optional. Offset pagination over an unordered
            # result set can return the same row twice and skip another, and
            # nothing reports it.
            clause = f"ORDERBY{watermark_column}"
            if since:
                clause = f"{watermark_column}>={since}^{clause}"
            params["sysparm_query"] = clause

        url = f"{base}?" + urllib.parse.urlencode(params)
        try:
            body = _request(url, user, password)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):
                return None          # not in the dictionary on this instance
            raise SystemExit(f"  HTTP {exc.code} on {entity} ({exc.reason})")
        except urllib.error.URLError as exc:
            raise SystemExit(f"  cannot reach instance: {exc.reason}")

        page = body.get("result", [])
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def flatten(value):
    """The API returns {'value': x} for a reference when links are included."""
    if isinstance(value, dict):
        return value.get("value", "")
    return "" if value is None else value


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Write in DECLARED column order. See point 1 in the module docstring."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([flatten(row.get(c, "")) for c in columns])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--entity", help="extract one entity rather than all")
    parser.add_argument("--since", metavar="TIMESTAMP",
                        help="fetch only rows whose watermark is >= this. NOT "
                             "the default: it turns the landing file into a "
                             "delta, and bronze expects a full snapshot")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    load_project_env(project)

    sources_path = project / "fabric" / "02-sources.yaml"
    if not sources_path.exists():
        print(f"  ERROR  no source registry at {sources_path}")
        return 2
    registry = yaml.safe_load(sources_path.read_text(encoding="utf-8"))


    extracted = skipped = missing = 0

    for source in registry.get("sources", []):
        options = (source.get("connection") or {}).get("options") or {}
        extract = options.get("extract") or {}
        if extract.get("protocol") != "rest":
            continue

        endpoint = _env(extract.get("endpoint")) or extract.get("endpoint", "")
        # The endpoint template itself carries ${SERVICENOW_INSTANCE_URL}.
        for match in set(__import__("re").findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", endpoint)):
            endpoint = endpoint.replace("${" + match + "}", os.environ.get(match, ""))

        user = _env(extract.get("auth_user_ref"))
        password = _env(extract.get("auth_secret_ref"))
        if not user or not password:
            print(f"  ERROR  {source['name']}: credentials did not resolve. "
                  f"Check .config/.env")
            return 2

        landing = project / (source["connection"].get("location_dev") or "./data").lstrip("./")
        page_size = int(extract.get("sysparm_limit") or PAGE_SIZE_DEFAULT)
        host = urllib.parse.urlparse(endpoint).netloc

        print(f"\n{source['name']}  ->  {host}")
        print(f"  landing {landing}")

        for entity in source.get("entities", []):
            name = entity["name"]
            if args.entity and name != args.entity:
                continue

            watermark_column = entity.get("watermark_column")
            # FULL SNAPSHOT by default -- see point 2 in the module docstring.
            # `since` stays None unless --since was passed explicitly, because
            # bronze owns incrementality and a filtered landing file would
            # delete rows bronze still needs.
            #
            # The watermark column is still used for ORDER BY: offset
            # pagination over an unordered result set can return the same row
            # twice and skip another, and nothing reports it.
            since = args.since or None
            columns = [c["name"] for c in entity["columns"]]
            target = landing / (entity.get("file_pattern") or f"{name}.csv")

            if args.dry_run:
                window = f" since {since}" if since else " (full)"
                print(f"  would fetch {name}{window} -> {target.name}")
                continue

            rows = fetch(endpoint, name, extract, user, password,
                         watermark_column, since, page_size)

            if rows is None:
                # Deliberately NOT an empty CSV. See point 3 in the docstring.
                print(f"  MISSING  {name:<24} not in this instance's dictionary")
                missing += 1
                continue

            if not rows:
                print(f"  no change {name:<23} 0 rows since {since}")
                skipped += 1
                continue

            write_csv(target, columns, rows)
            print(f"  OK       {name:<24} {len(rows):>7,} rows -> {target.name}")
            extracted += 1



    print(f"\n{extracted} extracted, {skipped} unchanged, {missing} missing")
    if missing:
        print("\nA MISSING table is not an empty one. Nothing was written for it,")
        print("so bronze will not mistake an absent source for a quiet day.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

