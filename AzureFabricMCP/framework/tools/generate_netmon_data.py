"""
Generate the synthetic network-telemetry feed a project declares.

Why this is generated at all
---------------------------
ServiceNow's CMDB records that a switch EXISTS; it does not record traffic
through it. Byte Volume therefore has no source in an ITSM extract, and the
project registers `netmon_synthetic` as its own source precisely so the
fabricated data cannot be mistaken for measured data: separate owner, separate
bronze table, and an `is_synthetic` column that travels all the way to the
report.

This writes that feed. It is FABRICATED. Nothing here is a measurement.

Correlation is real even though the traffic is not
--------------------------------------------------
The CI names come from the ALREADY-EXTRACTED cmdb_ci.csv, filtered to the
network classes, rather than being invented. That matters: the fact joins to
the CMDB on name, so inventing names would produce a feed that correlates with
nothing and a Byte Volume that cannot be sliced by CI at all.

Columns and their order are read from 02-sources.yaml, so this cannot drift
from the schema bronze applies positionally.

Usage:
    python generate_netmon_data.py --project 03_live_demo
    python generate_netmon_data.py --project 03_live_demo --days 90
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

SOURCE_NAME = "netmon_synthetic"
ENTITY_NAME = "network_traffic_daily"
INTERFACES = ["GigabitEthernet0/1", "GigabitEthernet0/2"]


def network_ci_names(project: Path, registry: dict) -> list[str]:
    """Read the network CI population out of the extracted CMDB."""
    cmdb_source = next(
        (s for s in registry["sources"]
         if any(e["name"] == "cmdb_ci" for e in s.get("entities", []))),
        None,
    )
    if not cmdb_source:
        return []

    location = (cmdb_source["connection"].get("location_dev") or "./data").lstrip("./")
    path = project / location / "cmdb_ci.csv"
    if not path.exists():
        print(f"  WARNING  {path} not found -- run extract_rest.py first, or the")
        print(f"           generated feed will correlate with nothing")
        return []

    # The same class list stg_cmdb_ci uses for is_network_ci. Kept here rather
    # than re-derived so the two cannot disagree about what "network" means.
    classes = {
        "cmdb_ci_netgear", "cmdb_ci_ip_switch", "cmdb_ci_ip_router",
        "cmdb_ci_ip_firewall", "cmdb_ci_lb", "cmdb_ci_wap_network",
    }
    names: list[str] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("sys_class_name") in classes and row.get("name"):
                names.append(row["name"])
    return sorted(set(names))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--days", type=int, default=730,
                        help="days of history to generate (default 730 = 24 months)")
    parser.add_argument("--seed", type=int, default=20260728,
                        help="fixed so a re-run reproduces the same feed")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    registry = yaml.safe_load(
        (project / "fabric" / "02-sources.yaml").read_text(encoding="utf-8"))

    source = next((s for s in registry["sources"] if s["name"] == SOURCE_NAME), None)
    if not source:
        print(f"  ERROR  no source named {SOURCE_NAME} in 02-sources.yaml")
        return 2
    entity = next((e for e in source["entities"] if e["name"] == ENTITY_NAME), None)
    if not entity:
        print(f"  ERROR  no entity named {ENTITY_NAME}")
        return 2

    # Column ORDER comes from the spec, not from this file.
    columns = [c["name"] for c in entity["columns"]]

    cis = network_ci_names(project, registry)
    if not cis:
        print("  ERROR  no network CIs found; nothing to generate against")
        return 2

    landing = project / (source["connection"].get("location_dev") or "./data").lstrip("./")
    pattern = entity.get("file_pattern") or f"{ENTITY_NAME}.csv"
    # file_pattern is a glob for the reader; write one concrete file.
    filename = pattern.replace("*", "all")
    target = landing / filename

    end = date.today()
    start = end - timedelta(days=args.days - 1)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    rows = []
    for ci in cis:
        # Per-CI baseline, derived from the name so it is stable across runs
        # without needing to be stored.
        digest = int(hashlib.sha256(ci.encode()).hexdigest()[:8], 16)
        rng = random.Random(args.seed ^ digest)
        baseline = rng.uniform(2e9, 4e10)

        for offset in range(args.days):
            day = start + timedelta(days=offset)
            # Weekday traffic exceeds weekend; a slow upward trend over the
            # window. Plausible shape, entirely invented magnitudes.
            weekday_factor = 0.35 if day.weekday() >= 5 else 1.0
            seasonal = 1.0 + 0.12 * math.sin(offset / 30.0)
            trend = 1.0 + (offset / max(args.days, 1)) * 0.25
            noise = rng.uniform(0.85, 1.15)

            volume = baseline * weekday_factor * seasonal * trend * noise
            bytes_in = int(volume * rng.uniform(0.55, 0.7))
            bytes_out = int(volume - bytes_in)

            for interface in INTERFACES:
                split = rng.uniform(0.4, 0.6)
                rows.append({
                    "reading_date": day.isoformat(),
                    "ci_correlation_id": ci,
                    "interface_name": interface,
                    "bytes_in": int(bytes_in * split),
                    "bytes_out": int(bytes_out * split),
                    # A low sample count means an unreliable day; a few are
                    # seeded so the quality column is not uniformly perfect.
                    "sample_count": 288 if rng.random() > 0.02 else rng.randint(20, 120),
                    # Always true. This is the column P2 labels the visual from.
                    "is_synthetic": "true",
                    "generated_at": generated_at,
                })

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c, "") for c in columns])

    print(f"SYNTHETIC network telemetry -- FABRICATED, not measured")
    print(f"  network CIs   {len(cis)}  (from the extracted CMDB, real names)")
    print(f"  interfaces    {len(INTERFACES)} per CI")
    print(f"  days          {args.days}  ({start} .. {end})")
    print(f"  rows          {len(rows):,}")
    print(f"  -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
