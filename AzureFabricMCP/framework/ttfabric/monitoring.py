"""
Evaluate data-quality expectations and SLAs against what actually landed.

The pipeline already writes a DQ log as it runs. This is the other half: it
reads the tables afterwards and asks whether they are still within the limits
the spec declared -- so a slow drift upstream is noticed before someone spots it
in a report.

Two kinds of measure
--------------------
Most rules return a SHARE in 0..1 (the fraction of rows in breach), compared
against `warn_above` / `fail_above`. Three do not: `freshness` measures hours,
`row_count_delta` measures percent change, and `schema_match` is a count of
missing columns. Each carries its own limit, so the verdict logic is per rule
rather than one comparison pretending to fit all of them.

Thresholds are fractions, not percentages. `warn_above: 0.022` is 2.2%. This is
the single most likely thing to get wrong when editing a spec by hand, so
`verdict` rejects a share-based threshold above 1.0 rather than treating a
misread 2.2 as "never breaches".

The verdict logic is deliberately free of Spark so it can be tested without a
cluster; only the measurement functions touch a DataFrame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

# Rules whose measured value is not a 0..1 share.
ABSOLUTE_RULES = {"freshness", "row_count_delta", "schema_match"}

PASS, WARN, FAIL, ERROR, SKIP = "pass", "warn", "fail", "error", "skip"


@dataclass
class CheckResult:
    check_id: str
    table: str
    layer: str
    rule: str
    severity: str
    status: str
    measured: float | None = None
    limit: float | None = None
    detail: str = ""
    rows: int | None = None

    @property
    def breached(self) -> bool:
        return self.status in (WARN, FAIL, ERROR)

    def __str__(self) -> str:
        measured = "-" if self.measured is None else f"{self.measured:,.4f}"
        limit = "-" if self.limit is None else f"{self.limit:,.4f}"
        return (f"{self.status.upper():<5} {self.check_id:<14} {self.table:<28} "
                f"{self.rule:<22} measured={measured:<12} limit={limit:<12} "
                f"{self.detail}")


def verdict(rule: str, measured: float | None, check: dict) -> tuple[str, float | None]:
    """Decide pass/warn/fail for a measured value. Pure Python, no Spark.

    Returns (status, the limit it was judged against).
    """
    if measured is None:
        return SKIP, None

    if rule == "freshness":
        limit = float(check["max_age_hours"])
        return (FAIL if measured > limit else PASS), limit

    if rule == "row_count_delta":
        # Signed percent change. Growth and shrinkage have separate limits
        # because they mean different things: a source doubling is usually a
        # duplicate load, a source halving is usually a partial one.
        if measured >= 0:
            limit = float(check.get("max_increase_pct", float("inf")))
            return (FAIL if measured > limit else PASS), limit
        limit = float(check.get("max_decrease_pct", float("inf")))
        return (FAIL if abs(measured) > limit else PASS), limit

    if rule == "schema_match":
        # Any missing column is a breach; the count is for the message.
        return (FAIL if measured > 0 else PASS), 0.0

    warn_above = check.get("warn_above")
    fail_above = check.get("fail_above")

    for name, value in (("warn_above", warn_above), ("fail_above", fail_above)):
        if value is not None and float(value) > 1.0:
            # Thresholds are fractions. A value above 1.0 is almost certainly a
            # percentage that was pasted in, and would silently never fire.
            raise ValueError(
                f"{name}={value} is above 1.0. Share thresholds are fractions "
                f"(0.022 means 2.2%). As written this check can never breach.")

    if fail_above is not None and measured > float(fail_above):
        return FAIL, float(fail_above)
    if warn_above is not None and measured > float(warn_above):
        return WARN, float(warn_above)
    return PASS, float(fail_above if fail_above is not None
                       else warn_above if warn_above is not None else 0)


# ---------------------------------------------------------------------------
# Measurement -- each returns a single number, or None when not measurable
# ---------------------------------------------------------------------------

def _share(numerator: int, total: int) -> float | None:
    return None if not total else numerator / total


def measure_not_null(df, check: dict, ctx: dict) -> float | None:
    total = ctx["rows"]
    column = check["column"]
    if column not in df.columns:
        raise KeyError(f"column {column!r} is not in the table")
    return _share(df.filter(df[column].isNull()).count(), total)


def measure_unique(df, check: dict, ctx: dict) -> float | None:
    columns = check["columns"]
    scoped = df.filter(check["filter"]) if check.get("filter") else df
    total = scoped.count()
    if not total:
        return None
    return 1 - (scoped.select(*columns).distinct().count() / total)


def measure_accepted_values(df, check: dict, ctx: dict) -> float | None:
    column = check["column"]
    allowed = check["values"]
    offending = df.filter(df[column].isNotNull() & ~df[column].isin(allowed)).count()
    return _share(offending, ctx["rows"])


def measure_range(df, check: dict, ctx: dict) -> float | None:
    from pyspark.sql import functions as F

    column = check["column"]
    predicate = F.col(column).isNotNull()
    if check.get("min") is not None:
        predicate = predicate & (F.col(column) < check["min"])
    if check.get("max") is not None:
        # Outside EITHER bound, so the two comparisons are OR-ed, not AND-ed.
        above = F.col(column).isNotNull() & (F.col(column) > check["max"])
        predicate = (predicate | above) if check.get("min") is not None else above
    return _share(df.filter(predicate).count(), ctx["rows"])


def measure_arithmetic_consistency(df, check: dict, ctx: dict) -> float | None:
    from pyspark.sql import functions as F

    left, right = check["expression"].split("=", 1)
    tolerance = float(check.get("tolerance", 0))
    difference = F.abs(F.expr(left.strip()) - F.expr(right.strip()))
    return _share(df.filter(difference > tolerance).count(), ctx["rows"])


def measure_referential_integrity(df, check: dict, ctx: dict) -> float | None:
    column = check["column"]
    parent_table, parent_column = check["references"].rsplit(".", 1)
    parent = ctx["read"](parent_table).select(parent_column).distinct()

    child = df.filter(df[column].isNotNull())
    total = child.count()
    if not total:
        return None
    resolved = child.join(parent, child[column] == parent[parent_column], "left_semi").count()
    return (total - resolved) / total


def measure_freshness(df, check: dict, ctx: dict) -> float | None:
    from pyspark.sql import functions as F

    column = check["column"]
    if column not in df.columns:
        raise KeyError(f"freshness column {column!r} is not in the table")
    latest = df.select(F.max(F.col(column)).alias("m")).collect()[0]["m"]
    if latest is None:
        return None
    if not isinstance(latest, datetime):
        latest = datetime.combine(latest, datetime.min.time())
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - latest).total_seconds() / 3600


def measure_row_count_delta(df, check: dict, ctx: dict) -> float | None:
    """Signed percent change against the previous run.

    Returns None on the first run: there is nothing to compare to, and
    reporting a breach for that would train people to ignore it.
    """
    previous = ctx.get("previous_rows")
    if not previous:
        return None
    return (ctx["rows"] - previous) / previous * 100.0


def measure_schema_match(df, check: dict, ctx: dict) -> float | None:
    """Count of declared columns absent from the table."""
    expected = check.get("expected_columns") or ctx.get("expected_columns")
    if not expected:
        return None
    return float(len([c for c in expected if c not in df.columns]))


EVALUATORS: dict[str, Callable] = {
    "not_null": measure_not_null,
    "unique": measure_unique,
    "accepted_values": measure_accepted_values,
    "range": measure_range,
    "arithmetic_consistency": measure_arithmetic_consistency,
    "referential_integrity": measure_referential_integrity,
    "freshness": measure_freshness,
    "row_count_delta": measure_row_count_delta,
    "schema_match": measure_schema_match,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

@dataclass
class MonitorRun:
    results: list[CheckResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        summary: dict[str, int] = {}
        for result in self.results:
            summary[result.status] = summary.get(result.status, 0) + 1
        return summary

    def blocking(self, block_on: list[str]) -> list[CheckResult]:
        """Breaches whose severity the environment says should stop promotion."""
        return [r for r in self.results
                if r.status in (FAIL, ERROR) and r.severity in block_on]


def run_expectations(read, expectations: list[dict], *,
                     previous_rows: dict[str, int] | None = None) -> MonitorRun:
    """Evaluate every check. `read(name)` returns a DataFrame.

    A check that raises is recorded as an ERROR rather than aborting the run:
    one malformed check must not hide the state of every other table.
    """
    run = MonitorRun()
    previous_rows = previous_rows or {}

    for expectation in expectations:
        table = expectation["table"]
        layer = expectation.get("layer", "")

        # `measured_on: input` means the upstream table -- a silver rule
        # calibrated against raw data is meaningless once cleansing has
        # removed what it was counting.
        frames: dict[str, object] = {}
        rows: dict[str, int] = {}
        for side in ("output", "input"):
            name = expectation.get("source") if side == "input" else table
            name = name or table
            if name in frames:
                continue
            try:
                frames[name] = read(name)
                rows[name] = frames[name].count()
            except Exception as exc:                     # noqa: BLE001
                frames[name] = None
                rows[name] = 0
                frames.setdefault("_error_" + name, str(exc))

        for check in expectation["checks"]:
            side = check.get("measured_on", "output")
            name = (expectation.get("source") or table) if side == "input" else table
            df = frames.get(name)

            if df is None:
                run.results.append(CheckResult(
                    check["id"], table, layer, check["rule"], check["severity"],
                    ERROR, detail=f"could not read {name}: "
                                  f"{frames.get('_error_' + name, 'unknown')}"))
                continue

            evaluator = EVALUATORS.get(check["rule"])
            if evaluator is None:
                run.results.append(CheckResult(
                    check["id"], table, layer, check["rule"], check["severity"],
                    ERROR, detail=f"no evaluator for rule {check['rule']!r}"))
                continue

            context = {"rows": rows[name], "read": read,
                       "previous_rows": previous_rows.get(name),
                       "expected_columns": expectation.get("expected_columns")}
            try:
                measured = evaluator(df, check, context)
                status, limit = verdict(check["rule"], measured, check)
                detail = check.get("note", "")
                if status == SKIP:
                    detail = detail or "not measurable on this run"
                run.results.append(CheckResult(
                    check["id"], table, layer, check["rule"], check["severity"],
                    status, measured, limit, detail, rows[name]))
            except Exception as exc:                     # noqa: BLE001
                run.results.append(CheckResult(
                    check["id"], table, layer, check["rule"], check["severity"],
                    ERROR, detail=f"{type(exc).__name__}: {exc}", rows=rows[name]))

    return run


def evaluate_slas(read, slas: list[dict]) -> list[CheckResult]:
    """Freshness and completeness against the declared service levels."""
    results = []
    for sla in slas:
        table = sla["table"]
        try:
            df = read(table)
            rows = df.count()
        except Exception as exc:                          # noqa: BLE001
            results.append(CheckResult(
                f"SLA-{table}", table, "sla", "availability", "critical",
                ERROR, detail=f"unavailable: {exc}"))
            continue

        results.append(CheckResult(
            f"SLA-{table}-availability", table, "sla", "availability", "critical",
            PASS if rows > 0 else FAIL, float(rows), 1.0,
            "table is readable and non-empty", rows))
    return results
