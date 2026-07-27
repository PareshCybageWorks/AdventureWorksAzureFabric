"""
Tests for the monitoring verdict logic.

Runs with plain `python` -- no pytest, and no Spark. That is the point: the
verdict logic is deliberately separate from measurement so the rules that decide
pass/warn/fail can be tested in a second, rather than only inside a Fabric
session where a wrong threshold is invisible.

    python framework/tests/test_monitoring.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ttfabric.monitoring import FAIL, PASS, SKIP, WARN, verdict  # noqa: E402

SHARE = {"warn_above": 0.022, "fail_above": 0.04}
DELTA = {"max_increase_pct": 50, "max_decrease_pct": 20}

CASES = [
    ("share below both thresholds",   "not_null", 0.010, SHARE, PASS),
    ("share past warn",               "not_null", 0.030, SHARE, WARN),
    ("share past fail",               "not_null", 0.050, SHARE, FAIL),
    # "above" is exclusive: a value exactly on the threshold passes.
    ("share exactly on warn",         "not_null", 0.022, SHARE, PASS),

    ("zero tolerance satisfied",      "accepted_values", 0.0,    {"fail_above": 0.0}, PASS),
    ("zero tolerance broken",         "accepted_values", 0.0001, {"fail_above": 0.0}, FAIL),

    ("fresh",                         "freshness", 12.0, {"max_age_hours": 26}, PASS),
    ("stale",                         "freshness", 30.0, {"max_age_hours": 26}, FAIL),

    ("modest growth",                 "row_count_delta",  10.0, DELTA, PASS),
    ("doubling, likely double load",  "row_count_delta",  80.0, DELTA, FAIL),
    ("collapse, likely partial load", "row_count_delta", -25.0, DELTA, FAIL),
    ("mild shrinkage",                "row_count_delta", -10.0, DELTA, PASS),

    ("schema intact",                 "schema_match", 0.0, {}, PASS),
    ("columns missing",               "schema_match", 2.0, {}, FAIL),

    # Nothing to compare against on a first run. Reporting a breach for that
    # would train people to ignore the check.
    ("not measurable",                "not_null", None, SHARE, SKIP),
]


def main() -> int:
    failures = 0

    for label, rule, measured, check, expected in CASES:
        status, _ = verdict(rule, measured, check)
        if status != expected:
            print(f"  FAIL  {label}: {rule} measured={measured} "
                  f"gave {status}, expected {expected}")
            failures += 1

    # A share threshold above 1.0 is a percentage that was pasted in. Left
    # alone it can never fire, so the check would sit there looking healthy.
    try:
        verdict("not_null", 0.03, {"fail_above": 4.0})
        print("  FAIL  a percentage threshold was accepted silently")
        failures += 1
    except ValueError:
        pass

    total = len(CASES) + 1
    print(f"{total - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
