"""
Run the DQ monitor in an environment and enforce the quality gate.

This is what makes `dq-gate` a control rather than documentation. Without it the
spec declares that no error or critical breach may reach uat or prod, and
nothing checks.

What it does
------------
1. Runs `nb_dq_monitor` in the target workspace, passing the environment so the
   notebook applies that environment's enforcement policy.
2. Reads the results of THAT run back out of the results table.
3. Exits non-zero if any breach has a severity the environment blocks on.

Step 2 is the point. A failed Fabric notebook reports "session failed" and
nothing else, so a CI log would say a gate failed without saying which check,
on which table, measured what. This prints the breaches.

A monitor that cannot be read is a monitor nobody trusts.

Usage:
    python run_monitor.py --project ./01_demo-project --env qa
    python run_monitor.py --project ./01_demo-project --env qa --report-only
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _project import get_environment, get_storage_ids, get_workspace_id  # noqa: E402
import _tsql  # noqa: E402

NOTEBOOK = "nb_dq_monitor"
RESULTS_TABLE = "dq_run_log_monitor"

# `rule_id`, not `rule`: RULE is reserved in T-SQL and the column is
# unqueryable through the SQL endpoint without brackets.
QUERY = """
    for sql in PAYLOAD:
        rs = stmt.executeQuery(sql)
        m = rs.getMetaData(); n = m.getColumnCount()
        while rs.next():
            log("\\t".join(
                "" if rs.getString(i + 1) is None else rs.getString(i + 1)
                for i in range(n)))
        rs.close()
        log("--")
"""


def read_results(headers, storage_token, workspace, lakehouse_id,
                 lakehouse_name, attempts=4):
    """Latest run's rows, as a list of dicts.

    Retried: the lakehouse SQL endpoint lags behind the Spark write by a minute
    or two, so a table that certainly exists is briefly not queryable. Failing
    the gate for that would be a false negative on data quality, which is the
    worst possible direction for this check to be wrong in.
    """
    statements = [
        f"SELECT MAX(run_id) FROM {RESULTS_TABLE}",
        f"""SELECT check_id, table_name, [rule_id], severity, status,
                   CAST(measured AS VARCHAR(40)), CAST(limit_value AS VARCHAR(40)),
                   LEFT(ISNULL(detail, ''), 160)
            FROM {RESULTS_TABLE}
            WHERE run_id = (SELECT MAX(run_id) FROM {RESULTS_TABLE})""",
    ]

    delay = 20
    for attempt in range(1, attempts + 1):
        ok, lines = _tsql.run(
            fabric_token=headers["Authorization"].split(" ", 1)[1],
            storage_token=storage_token, workspace=workspace,
            warehouse=lakehouse_name, scratch_lakehouse=lakehouse_id,
            body=QUERY, payload=statements, label="dqgate")

        if ok:
            blocks, current = [], []
            for line in lines:
                if line == "--":
                    blocks.append(current)
                    current = []
                elif line != "OK":
                    current.append(line)

            rows = []
            for line in (blocks[1] if len(blocks) > 1 else []):
                parts = line.split("\t")
                if len(parts) < 8:
                    continue
                rows.append(dict(zip(
                    ("check_id", "table", "rule", "severity", "status",
                     "measured", "limit", "detail"), parts)))
            return rows, (blocks[0][0] if blocks and blocks[0] else "")

        if attempt < attempts:
            print(f"  results not readable yet; retrying in {delay}s "
                  f"(the SQL endpoint lags the write)")
            time.sleep(delay)
            delay = int(delay * 1.5)

    return None, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--report-only", action="store_true",
                        help="print breaches but always exit 0")
    parser.add_argument("--skip-run", action="store_true",
                        help="evaluate the last recorded run without a new one")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    spec_path = project / "dataops" / "01-monitoring.yaml"
    if not spec_path.exists():
        print(f"  ERROR  no monitoring spec at {spec_path}")
        return 2
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    try:
        environment = get_environment(project, args.env)
        workspace = get_workspace_id(project, args.env)
        storage = get_storage_ids(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    policy = (spec.get("enforcement") or {}).get(args.env) or {}
    block_on = set(policy.get("block_on") or [])

    print(f"environment {args.env}  ->  {environment.get('workspace')}")
    print(f"policy: mode={policy.get('mode', 'warn')}  "
          f"block_on={sorted(block_on) or 'nothing'}")
    print()

    cred = _tsql.credential()
    fabric_token = cred.get_token(_tsql.FABRIC_SCOPE).token
    storage_token = cred.get_token(_tsql.STORAGE_SCOPE).token
    headers = {"Authorization": f"Bearer {fabric_token}",
               "Content-Type": "application/json"}

    if not args.skip_run:
        print(f"running {NOTEBOOK}...")
        status, _ = _tsql.run_notebook(headers, workspace, NOTEBOOK,
                                       parameters={"env": args.env})
        print(f"  notebook run: {status}")
        if status == "NotFound":
            print(f"  ERROR  {NOTEBOOK} is not in this workspace. Deploy it first.")
            return 2
        # A Failed run is expected when the notebook's own enforcement raises.
        # The breaches below are the useful output either way, so this
        # continues rather than returning here.

    lakehouse = next((n for n in storage if n.startswith("lh_silver")), None) \
        or next((n for n in storage if n.startswith("lh_")), None)
    if lakehouse is None:
        print("  ERROR  no lakehouse to read results from")
        return 2

    print()
    rows, run_id = read_results(headers, storage_token, workspace,
                                storage[lakehouse], lakehouse)
    if rows is None:
        print("  ERROR  could not read the monitoring results. The gate cannot "
              "confirm data quality, so it does not pass.")
        return 1
    if not rows:
        print("  ERROR  the monitor recorded no results. Treating an empty "
              "result as a pass would make this gate meaningless.")
        return 1

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(f"run {run_id}: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    breaches = [r for r in rows if r["status"] in ("fail", "error", "warn")]
    blocking = [r for r in rows
                if r["status"] in ("fail", "error") and r["severity"] in block_on]

    if breaches:
        print()
        print(f"  {'check':<16} {'table':<26} {'severity':<9} {'status':<6} "
              f"{'measured':<12} limit")
        print(f"  {'-'*16} {'-'*26} {'-'*9} {'-'*6} {'-'*12} {'-'*10}")
        for row in sorted(breaches, key=lambda r: (r["status"], r["check_id"])):
            print(f"  {row['check_id']:<16} {row['table'][:26]:<26} "
                  f"{row['severity']:<9} {row['status']:<6} "
                  f"{row['measured'][:12]:<12} {row['limit'][:10]}")
            if row["status"] == "error" and row["detail"]:
                print(f"      {row['detail'][:110]}")

    print()
    if blocking and not args.report_only:
        print(f"GATE FAILED: {len(blocking)} breach(es) at a severity {args.env} "
              f"blocks on ({', '.join(sorted(block_on))}):")
        for row in blocking:
            print(f"  {row['check_id']}  {row['table']}  {row['severity']}")
        return 1

    if blocking:
        print(f"{len(blocking)} blocking breach(es), but --report-only was given.")
        return 0

    if breaches:
        print(f"GATE PASSED: {len(breaches)} breach(es) recorded, none at a "
              f"severity {args.env} blocks on.")
    else:
        print("GATE PASSED: no breaches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
