"""
Run a read-only query against a project's Fabric Warehouse and print the result.

For checking what actually landed -- row counts, a reconciliation total, whether
a `bi` view returns anything -- without opening the Fabric portal.

READ-ONLY. Statements that modify anything are refused. Use run_migrations.py to
change the schema and reset_layers.py to clear tables; both record what they did,
which an ad-hoc query would not.

Usage:
    python query_warehouse.py --project ./01_demo-project --sql "SELECT TOP 5 * FROM bi.vw_sales_summary"
    python query_warehouse.py --project ./01_demo-project --file checks.sql
    python query_warehouse.py --project ./01_demo-project --smoke
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _project import get_environment, get_storage_ids, get_workspace_id  # noqa: E402
import _tsql  # noqa: E402

# Refused outright rather than filtered. A query tool that can quietly drop a
# table is a query tool someone eventually drops a table with.
FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|MERGE|GRANT|REVOKE|EXEC)\b",
    re.IGNORECASE)

BODY = '''
    for sql in PAYLOAD:
        log(f"> {sql}")
        rs = stmt.executeQuery(sql)
        meta = rs.getMetaData()
        columns = [meta.getColumnLabel(i + 1) for i in range(meta.getColumnCount())]

        rows = []
        while rs.next() and len(rows) < 200:
            rows.append([rs.getString(i + 1) for i in range(len(columns))])
        rs.close()

        if not rows:
            log("  (no rows)")
            log("")
            continue

        # Size each column to its widest value so the output stays readable
        # when a query returns something unexpected.
        width = [max(len(c), *(len(str(r[i]) if r[i] is not None else "NULL")
                               for r in rows)) for i, c in enumerate(columns)]
        width = [min(w, 40) for w in width]

        def line(values):
            cells = []
            for i, v in enumerate(values):
                text = "NULL" if v is None else str(v)
                if len(text) > width[i]:
                    text = text[:width[i] - 1] + "~"
                cells.append(text.ljust(width[i]))
            return "  " + " | ".join(cells)

        log(line(columns))
        log("  " + "-+-".join("-" * w for w in width))
        for row in rows:
            log(line(row))
        log(f"  ({len(rows)} rows)")
        log("")
'''

SMOKE = [
    "SELECT COUNT(*) AS dim_customer FROM dbo.dim_customer",
    "SELECT COUNT(*) AS dim_product FROM dbo.dim_product",
    "SELECT COUNT(*) AS dim_date FROM dbo.dim_date",
    "SELECT COUNT(*) AS fct_sales FROM dbo.fct_sales",
    "SELECT TOP 5 * FROM bi.vw_sales_summary",
    "SELECT TOP 5 * FROM bi.vw_customer_360",
    "SELECT TOP 5 * FROM bi.vw_product_performance",
    "SELECT TOP 5 * FROM bi.vw_data_quality",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--warehouse", default="wh_gold")
    parser.add_argument("--sql", help="a single statement")
    parser.add_argument("--file", help="a file of statements separated by ';'")
    parser.add_argument("--smoke", action="store_true",
                        help="row counts and a sample from every bi view")
    args = parser.parse_args()

    if args.smoke:
        statements = SMOKE
    elif args.sql:
        statements = [args.sql]
    elif args.file:
        text = Path(args.file).read_text(encoding="utf-8")
        statements = [s.strip() for s in text.split(";") if s.strip()]
    else:
        print("  ERROR  pass one of --sql, --file or --smoke")
        return 2

    for sql in statements:
        # Strip comments first, so a statement is not refused for the word
        # "delete" appearing in a note above it.
        body = re.sub(r"--[^\n]*", "", sql)
        found = FORBIDDEN.search(body)
        if found:
            print(f"  REFUSED  this tool is read-only, and that statement contains "
                  f"{found.group(0).upper()}:")
            print(f"           {sql.strip()[:100]}")
            return 2

    project = Path(args.project).resolve()
    try:
        environment = get_environment(project, args.env)
        workspace = get_workspace_id(project, args.env)
        storage = get_storage_ids(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    scratch = next((i for n, i in storage.items() if n.startswith("lh_")), None)
    if scratch is None:
        print("  ERROR  a lakehouse is needed to host the temporary notebook")
        return 2

    print(f"environment {args.env}  ->  {environment.get('workspace')}  "
          f"({args.warehouse})")
    print(f"{len(statements)} statements")
    print()

    cred = _tsql.credential()
    ok, lines = _tsql.run(
        fabric_token=cred.get_token(_tsql.FABRIC_SCOPE).token,
        storage_token=cred.get_token(_tsql.STORAGE_SCOPE).token,
        workspace=workspace, warehouse=args.warehouse,
        scratch_lakehouse=scratch, body=BODY, payload=statements, label="query")

    for line in lines:
        if line != "OK":
            print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
