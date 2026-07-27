"""
Run DAX against a deployed semantic model and print the result.

Deploying a model proves it parsed, not that it works. A DirectLake model can
publish cleanly and still return nothing -- a relationship pointing at the wrong
column, a measure over an empty partition, a fallback to DirectQuery nobody
noticed. This runs the measures and shows the numbers.

`--smoke` evaluates every measure in the spec at total level, which is the
fastest way to find the one that returns blank.

Uses the Power BI REST executeQueries endpoint, so it needs no gateway and no
XMLA client -- and it queries the model exactly as a report would.

Usage:
    python query_model.py --project ./01_demo-project --smoke
    python query_model.py --project ./01_demo-project --dax "EVALUATE ROW(\\"R\\", [Revenue])"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _project import get_environment, get_workspace_id  # noqa: E402
import _tsql  # noqa: E402

POWERBI_API = "https://api.powerbi.com/v1.0/myorg"
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


def evaluate(token: str, workspace: str, dataset: str, dax: str) -> tuple[bool, object]:
    response = _tsql.with_retry(
        "POST", f"{POWERBI_API}/groups/{workspace}/datasets/{dataset}/executeQueries",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"queries": [{"query": dax}],
              "serializerSettings": {"includeNulls": True}}, timeout=180)

    if not response.ok:
        return False, response.text[:600]
    results = response.json().get("results", [{}])[0]
    if "error" in results:
        return False, results["error"]
    return True, results.get("tables", [{}])[0].get("rows", [])


def show(rows: list) -> None:
    if not rows:
        print("  (no rows)")
        return
    columns = list(rows[0])
    width = [min(40, max(len(c), *(len(str(r.get(c))) for r in rows))) for c in columns]

    def line(values):
        return "  " + " | ".join(str(v)[:w].ljust(w) for v, w in zip(values, width))

    print(line(columns))
    print("  " + "-+-".join("-" * w for w in width))
    for row in rows[:50]:
        print(line([row.get(c) for c in columns]))
    if len(rows) > 50:
        print(f"  ... {len(rows) - 50} more")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--dax")
    parser.add_argument("--smoke", action="store_true",
                        help="evaluate every measure in the spec at total level")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    spec_path = project / "powerbi" / "01-semantic-model.yaml"
    if not spec_path.exists():
        print(f"  ERROR  no semantic model spec at {spec_path}")
        return 2
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    try:
        environment = get_environment(project, args.env)
        workspace = get_workspace_id(project, args.env)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"  ERROR  {exc}")
        return 2

    cred = _tsql.credential()
    token = cred.get_token(POWERBI_SCOPE).token

    datasets = requests.get(f"{POWERBI_API}/groups/{workspace}/datasets",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=60).json().get("value", [])
    model = next((d for d in datasets if d["name"] == spec["model"]["name"]), None)
    if model is None:
        print(f"  ERROR  {spec['model']['name']!r} is not deployed to "
              f"{environment.get('workspace')}. Run push_semantic_model.py first.")
        return 2

    print(f"environment {args.env}  ->  {environment.get('workspace')}")
    print(f"model {spec['model']['name']}  ({model['id']})")
    print()

    if args.smoke:
        measures = [m["name"] for m in spec.get("measures", [])]
        # One row with every measure: a single scan, and blanks line up
        # against the measures that produced them.
        body = ", ".join(f'"{name}", [{name}]' for name in measures)
        ok, result = evaluate(token, workspace, model["id"], f"EVALUATE ROW({body})")
        if not ok:
            print(f"  ERROR  {result}")
            return 1

        row = result[0] if result else {}
        blank = []
        print(f"  {'measure':<26} value")
        print(f"  {'-' * 26} {'-' * 24}")
        for name in measures:
            value = row.get(f"[{name}]")
            print(f"  {name:<26} {'(blank)' if value is None else value}")
            if value is None:
                blank.append(name)

        print()
        if blank:
            # Blank is legitimate for a period comparison with no prior year,
            # so this reports rather than fails.
            print(f"  {len(blank)} of {len(measures)} measures returned blank: "
                  f"{', '.join(blank)}")
            print("  Blank is correct for a comparison with no prior period; "
                  "check the rest.")
        else:
            print(f"  all {len(measures)} measures returned a value")
        return 0

    if not args.dax:
        print("  ERROR  pass --dax or --smoke")
        return 2

    ok, result = evaluate(token, workspace, model["id"], args.dax)
    if not ok:
        print(f"  ERROR  {result}")
        return 1
    show(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
