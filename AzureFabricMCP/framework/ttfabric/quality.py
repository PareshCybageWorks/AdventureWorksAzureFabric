"""
Data-quality logging and assertions for generated notebooks.

Two responsibilities:

  DQRunLog    accumulates what happened during a notebook run and writes one
              row per check to dbo.dq_run_log, which bi.vw_data_quality reads.
              Pipeline health then appears in the same dashboard as the
              business numbers rather than in a log only the platform team
              sees.

  assert_*    hard gates inside a notebook. They raise, which fails the Fabric
              activity, which fails the pipeline. Used for invariants that
              must never be violated -- a breach means a framework bug, not a
              data problem, and continuing would write a corrupt table.

Thresholds live in the project's 04-data-quality.yaml. Nothing here hard-codes
a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType, IntegerType, LongType, StringType, StructField, StructType,
    TimestampType,
)

DQ_TABLE = "dq_run_log"

# Declared explicitly rather than inferred. A run with no threshold breaches
# has observed_value and threshold_value null on EVERY row, and Spark cannot
# infer a type from all-nulls -- createDataFrame raises CANNOT_DETERMINE_TYPE
# and fails the notebook. The irony of the quality logger being the thing that
# breaks the load is worth avoiding.
#
# Mirrors `results.schema` in the project's 04-data-quality.yaml.
DQ_SCHEMA = StructType([
    StructField("run_id",           StringType(),    True),
    StructField("load_id",          StringType(),    True),
    StructField("layer",            StringType(),    True),
    StructField("table_name",       StringType(),    True),
    StructField("check_id",         StringType(),    True),
    StructField("check_type",       StringType(),    True),
    StructField("severity",         StringType(),    True),
    StructField("status",           StringType(),    True),
    StructField("observed_value",   DoubleType(),    True),
    StructField("threshold_value",  DoubleType(),    True),
    StructField("rows_in",          LongType(),      True),
    StructField("rows_out",         LongType(),      True),
    StructField("rows_quarantined", LongType(),      True),
    StructField("rows_corrected",   LongType(),      True),
    StructField("checks_passed",    IntegerType(),   True),
    StructField("checks_failed",    IntegerType(),   True),
    StructField("processed_at",     TimestampType(), True),
])


@dataclass
class CheckOutcome:
    check_id: str
    check_type: str
    severity: str
    status: str                 # passed | warned | failed
    observed_value: float | None = None
    threshold_value: float | None = None


@dataclass
class DQRunLog:
    """Collects one notebook run's quality signal, then flushes it in one write."""

    spark: SparkSession
    load_id: str
    layer: str
    table_name: str

    rows_in: int = 0
    rows_out: int = 0
    rows_quarantined: int = 0
    rows_corrected: int = 0
    outcomes: list[CheckOutcome] = field(default_factory=list)
    rule_stats: dict[str, Any] = field(default_factory=dict)

    # -- accumulation ------------------------------------------------------

    def record_input(self, count: int) -> None:
        self.rows_in = count

    def record_output(self, count: int) -> None:
        self.rows_out = count

    def record_quarantined(self, count: int) -> None:
        self.rows_quarantined = count

    def record_corrected(self, count: int) -> None:
        # Accumulates: several rules may correct rows in the same run.
        self.rows_corrected += count

    def record_rule(self, name: str, result) -> None:
        """Record what a cleansing rule did.

        Takes the RuleResult rather than raw numbers so a rule cannot report
        a rejection count that disagrees with the frame it returned.
        """
        rejected = result.rejected_count if result.rejected is not None else 0
        self.rule_stats[name] = {
            "rejected": rejected,
            "corrected": result.corrected_count or 0,
            **(result.stats or {}),
        }
        if rejected:
            print(f"    {name}: rejected {rejected:,} rows")

    def record_check(
        self,
        check_id: str,
        check_type: str,
        severity: str,
        observed: float,
        warn_above: float | None = None,
        fail_above: float | None = None,
    ) -> CheckOutcome:
        """Evaluate one expectation from 04-data-quality.yaml."""
        status = "passed"
        threshold = None

        if fail_above is not None and observed > fail_above:
            status, threshold = "failed", fail_above
        elif warn_above is not None and observed > warn_above:
            status, threshold = "warned", warn_above

        outcome = CheckOutcome(check_id, check_type, severity, status, observed, threshold)
        self.outcomes.append(outcome)

        if status != "passed":
            print(f"    {check_id} {status.upper()}: observed {observed:.4f} "
                  f"exceeds {threshold}")
        return outcome

    # -- reporting ---------------------------------------------------------

    @property
    def checks_passed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "passed")

    @property
    def checks_failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "failed")

    def blocking_failures(self, block_on: tuple[str, ...] = ("error", "critical")) -> list[CheckOutcome]:
        """Failures severe enough to stop a promotion in this environment."""
        return [o for o in self.outcomes if o.status == "failed" and o.severity in block_on]

    def flush(self) -> None:
        """Write one row per check. Called at the end of every notebook."""
        now = datetime.utcnow()
        run_id = f"{self.load_id}_{self.table_name}"

        rows = [
            {
                "run_id": run_id,
                "load_id": self.load_id,
                "layer": self.layer,
                "table_name": self.table_name,
                "check_id": o.check_id,
                "check_type": o.check_type,
                "severity": o.severity,
                "status": o.status,
                "observed_value": float(o.observed_value) if o.observed_value is not None else None,
                "threshold_value": float(o.threshold_value) if o.threshold_value is not None else None,
                "rows_in": self.rows_in,
                "rows_out": self.rows_out,
                "rows_quarantined": self.rows_quarantined,
                "rows_corrected": self.rows_corrected,
                "checks_passed": self.checks_passed,
                "checks_failed": self.checks_failed,
                "processed_at": now,
            }
            for o in self.outcomes
        ]

        # A run with no expectations still records its row accounting, so the
        # absence of checks on a table is visible rather than indistinguishable
        # from the table not having run.
        if not rows:
            rows = [{
                "run_id": run_id, "load_id": self.load_id, "layer": self.layer,
                "table_name": self.table_name, "check_id": "NONE",
                "check_type": "row_accounting", "severity": "info", "status": "passed",
                "observed_value": None, "threshold_value": None,
                "rows_in": self.rows_in, "rows_out": self.rows_out,
                "rows_quarantined": self.rows_quarantined,
                "rows_corrected": self.rows_corrected,
                "checks_passed": 0, "checks_failed": 0, "processed_at": now,
            }]

        # Column order must match DQ_SCHEMA, so the dicts are projected into
        # tuples rather than trusting insertion order.
        ordered = [tuple(row[field.name] for field in DQ_SCHEMA.fields) for row in rows]

        (self.spark.createDataFrame(ordered, schema=DQ_SCHEMA)
            .write.mode("append").format("delta")
            .option("mergeSchema", "true").saveAsTable(DQ_TABLE))

        print(f"  dq: {self.rows_in:,} in, {self.rows_out:,} out, "
              f"{self.rows_quarantined:,} quarantined, {self.rows_corrected:,} corrected, "
              f"{self.checks_passed} checks passed, {self.checks_failed} failed")


# ---------------------------------------------------------------------------
# Hard assertions
# ---------------------------------------------------------------------------

def assert_unique(df: DataFrame, columns: list[str]) -> None:
    """Raise if `columns` are not unique.

    Used to enforce a fact's declared grain. A duplicate here means the grain
    statement in the spec is wrong or a join fanned out, and either way every
    additive measure downstream is now overstated.
    """
    if not columns:
        return
    total = df.count()
    distinct = df.select(*columns).distinct().count()
    if total != distinct:
        raise AssertionError(
            f"uniqueness violated on {columns}: {total:,} rows, "
            f"{distinct:,} distinct ({total - distinct:,} duplicates). "
            f"The declared grain does not hold -- check for a fan-out join."
        )


def assert_not_null(df: DataFrame, columns: list[str]) -> None:
    """Raise if any of `columns` contains a null.

    Applied to dimension keys on a fact. A null surrogate key means a lookup
    missed and the unknown member did not catch it, so those rows would vanish
    from any report that joins the dimension.
    """
    if not columns:
        return
    present = [c for c in columns if c in df.columns]
    if not present:
        return

    condition = None
    for name in present:
        is_null = F.col(name).isNull()
        condition = is_null if condition is None else (condition | is_null)

    offending = df.filter(condition).count()
    if offending:
        raise AssertionError(
            f"{offending:,} rows have a null in {present}. "
            f"A dimension lookup failed and the unknown member did not absorb it."
        )


def assert_keys_resolve(df: DataFrame, columns: list[str], unknown_key: int = -1,
                        max_unknown_share: float = 0.01) -> None:
    """Raise when too many facts fell through to the unknown member.

    assert_not_null is NOT sufficient here, and the difference is the whole
    point of this function. A failed dimension lookup does not produce a null --
    it produces the unknown-member key, deliberately, so the row survives. So a
    fact table where EVERY key resolved to -1 passes a not-null check while
    reporting 100% of revenue against "Unknown".

    That is how a warehouse looks correct and reports wrong numbers. A handful
    of unknowns is normal (late-arriving dimension rows); a large share means
    the lookup is broken -- commonly an SCD2 validity window that starts after
    the facts occurred.
    """
    total = df.count()
    if not total:
        return

    breaches = []
    for column in columns:
        if column not in df.columns:
            continue
        unknown = df.filter(F.col(column) == unknown_key).count()
        share = unknown / total
        if share > max_unknown_share:
            breaches.append(
                f"{column}: {unknown:,}/{total:,} ({share:.1%}) resolved to the "
                f"unknown member, above the {max_unknown_share:.1%} tolerance"
            )

    if breaches:
        raise AssertionError(
            "dimension lookups are failing at scale:\n  " + "\n  ".join(breaches)
            + "\nA near-total unknown share usually means the dimension's "
              "valid_from post-dates the facts. Check the SCD2 validity windows."
        )


def assert_reconciles(actual: float, expected: float, tolerance: float, label: str) -> None:
    """Raise if two totals disagree beyond tolerance.

    The end-to-end check: gold must tie back to silver. Passing this is what
    lets someone state that the dashboard equals the source.
    """
    difference = abs(actual - expected)
    if difference > tolerance:
        raise AssertionError(
            f"{label} does not reconcile: actual {actual:,.2f} vs "
            f"expected {expected:,.2f} (difference {difference:,.2f}, "
            f"tolerance {tolerance})"
        )
