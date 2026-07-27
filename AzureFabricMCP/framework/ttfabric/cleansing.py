"""
Modular cleansing functions for the silver layer.

Every `fn:` named in a project's mappings/silver.yaml resolves to a function in
the REGISTRY at the bottom of this module. The spec validator checks the
mapping in both directions, so a spec cannot reference a rule that does not
exist and a rule cannot be silently orphaned.

Contract
--------
Every rule has the same shape::

    def rule(df: DataFrame, ctx: RuleContext, **params) -> RuleResult

and returns both the rows it kept and the rows it rejected. That is the whole
design. Because nothing is ever dropped without being handed back, the
reconciliation assertion in silver.yaml holds by construction::

    count(input) == count(kept) + count(rejected)

A rule that filtered rows away internally could not satisfy that, which is why
no rule here returns a bare DataFrame.

Rules fall into three kinds, matching `on_reject` in the spec:

    quarantine          Row is wrong and cannot be repaired. Kept out of
                        silver, written to the quarantine table with the rule
                        that rejected it.

    correct_and_flag    Row is repairable from trustworthy inputs. Corrected in
                        place, original retained in a companion column, and a
                        boolean flag raised so a report can exclude it.

    transform           Reshapes without judging. Rejects nothing.

Requires PySpark, which is present in Fabric notebook runtimes. Install it
locally (`pip install pyspark`) only to run the unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from pyspark.sql import Column, DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType, DateType, DecimalType, IntegerType, LongType,
    StringType, TimestampType,
)

# ---------------------------------------------------------------------------
# Context and result types
# ---------------------------------------------------------------------------

# Columns stamped onto every quarantined row so a rejection is always
# traceable to the rule and the load that produced it.
REJECT_RULE = "_rejected_by"
REJECT_TIME = "_rejected_at"
REJECT_DETAIL = "_rejection_detail"
LOAD_ID = "_load_id"


@dataclass
class RuleContext:
    """Everything a rule needs that does not come from the spec."""

    spark: SparkSession
    load_id: str
    environment: str
    table: str
    # Resolves a table name from the spec to a live DataFrame. Rules that
    # reference another table (referential integrity, rollups) go through this
    # rather than reading a path, so the same rule works against a Lakehouse
    # table in Fabric and a temp view in a unit test.
    resolve_table: Callable[[str], DataFrame]
    # Set from 04-data-quality.yaml `masking.environments[env].apply`.
    apply_masking: bool = False
    masking_policies: dict[str, dict] = field(default_factory=dict)


@dataclass
class RuleResult:
    """Rows kept, rows rejected, and what the rule did."""

    kept: DataFrame
    rejected: DataFrame | None = None
    corrected_count: int | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def rejected_count(self) -> int:
        return 0 if self.rejected is None else self.rejected.count()


def _empty_like(df: DataFrame) -> DataFrame:
    """An empty frame matching df, for rules that reject nothing."""
    return df.limit(0)


def _tag_rejects(df: DataFrame, ctx: RuleContext, rule: str, detail: str) -> DataFrame:
    """Stamp rejection provenance onto rows headed for quarantine."""
    return (
        df.withColumn(REJECT_RULE, F.lit(rule))
        .withColumn(REJECT_TIME, F.lit(datetime.utcnow()).cast(TimestampType()))
        .withColumn(REJECT_DETAIL, F.lit(detail))
        .withColumn(LOAD_ID, F.lit(ctx.load_id))
    )


def _split(
    df: DataFrame, keep_condition: Column, ctx: RuleContext, rule: str, detail: str
) -> RuleResult:
    """Partition a frame on a predicate into kept and quarantined halves.

    Null-safe: a row whose predicate evaluates to NULL is rejected rather than
    silently vanishing, which is what a plain `filter(~cond)` would do.
    """
    safe = keep_condition & keep_condition.isNotNull()
    kept = df.filter(safe)
    rejected = _tag_rejects(df.filter(~safe | safe.isNull()), ctx, rule, detail)
    return RuleResult(kept=kept, rejected=rejected, stats={"rule": rule})


# ---------------------------------------------------------------------------
# Type handling
# ---------------------------------------------------------------------------

_SPARK_TYPES = {
    "string": StringType(),
    "integer": IntegerType(),
    "bigint": LongType(),
    "boolean": BooleanType(),
    "date": DateType(),
    "timestamp": TimestampType(),
}


def _spark_type(declared: dict) -> Any:
    """Map a spec type declaration onto a Spark type."""
    name = declared["type"]
    if name == "decimal":
        return DecimalType(declared.get("precision", 18), declared.get("scale", 2))
    if name not in _SPARK_TYPES:
        raise ValueError(f"Unsupported type in spec: {name!r}")
    return _SPARK_TYPES[name]


def cast_types(df: DataFrame, ctx: RuleContext, columns: list[dict]) -> RuleResult:
    """Cast each mapped column to its declared target type.

    A value that will not cast becomes NULL rather than throwing, so one bad
    row cannot fail an entire load. The nulls it produces are caught by the
    not-null rules that follow, which is where a rejection belongs.
    """
    out = df
    for col in columns:
        source = col.get("source")
        target = col["target"]
        if source is None:          # derived column, built by apply_transforms
            continue
        out = out.withColumn(target, F.col(source).cast(_spark_type(col)))
    return RuleResult(kept=out, rejected=_empty_like(out), stats={"columns": len(columns)})


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

_TRANSFORMS: dict[str, Callable[[Column], Column]] = {
    "trim": F.trim,
    "lower": F.lower,
    "upper": F.upper,
    "title_case": F.initcap,
}


def apply_transforms(df: DataFrame, ctx: RuleContext, columns: list[dict]) -> RuleResult:
    """Apply per-column `transforms` and evaluate derived `expression` columns.

    Derived columns are evaluated after transforms so an expression can rely on
    its inputs already being normalised.
    """
    out = df

    for col in columns:
        target = col["target"]
        for name in col.get("transforms", []):
            if name not in _TRANSFORMS:
                raise ValueError(f"Unknown transform {name!r} on column {target!r}")
            out = out.withColumn(target, _TRANSFORMS[name](F.col(target)))

    for col in columns:
        expression = col.get("expression")
        # Cross-table expressions are handled by dedicated rules, not here.
        if expression and "(" in expression and "where" not in expression:
            out = out.withColumn(col["target"], F.expr(expression))

    return RuleResult(kept=out, rejected=_empty_like(out))


def default_nulls(df: DataFrame, ctx: RuleContext, column: str, value: Any) -> RuleResult:
    """Replace nulls with a declared default. Rejects nothing.

    Used where absence is tolerable and the row still carries meaning -- an
    uncategorised product is still a real product, and dropping it would drop
    its sales too.
    """
    out = df.withColumn(
        column, F.when(F.col(column).isNull() | (F.trim(F.col(column)) == ""), F.lit(value))
                 .otherwise(F.col(column))
    )
    return RuleResult(kept=out, rejected=_empty_like(out))


# ---------------------------------------------------------------------------
# Null handling
# ---------------------------------------------------------------------------

def _reject_nulls(df, ctx, columns, rule) -> RuleResult:
    condition = None
    for name in columns:
        present = F.col(name).isNotNull() & (F.trim(F.col(name).cast("string")) != "")
        condition = present if condition is None else (condition & present)
    return _split(df, condition, ctx, rule, f"null or blank in {columns}")


def drop_null_business_key(df: DataFrame, ctx: RuleContext, columns: list[str]) -> RuleResult:
    """Quarantine rows whose business key is missing.

    A row with no business key cannot be deduplicated or matched on a later
    load, so it cannot participate in the layer at all.
    """
    return _reject_nulls(df, ctx, columns, "drop_null_business_key")


def drop_null_required(df: DataFrame, ctx: RuleContext, columns: list[str]) -> RuleResult:
    """Quarantine rows missing a column the spec declares non-nullable."""
    return _reject_nulls(df, ctx, columns, "drop_null_required")


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _order_columns(order_by: list[str]) -> list[Column]:
    out = []
    for term in order_by:
        parts = term.split()
        col = F.col(parts[0])
        out.append(col.desc() if len(parts) > 1 and parts[1].lower() == "desc" else col.asc())
    return out


def deduplicate(
    df: DataFrame,
    ctx: RuleContext,
    keys: list[str],
    keep: str = "latest",
    order_by: list[str] | None = None,
) -> RuleResult:
    """Collapse duplicates on `keys`, keeping one row per key.

    The losers are quarantined rather than discarded. That matters more than it
    looks: a spike in duplicate volume is an upstream incident, and it is only
    visible if the discarded copies were counted somewhere.

    `order_by` decides which copy survives. Without it the choice would be
    whatever Spark happened to return, so the spec requires it for `latest`.
    """
    if keep == "latest" and not order_by:
        raise ValueError("deduplicate(keep='latest') requires order_by to be deterministic")

    ordering = _order_columns(order_by or keys)
    window = Window.partitionBy(*[F.col(k) for k in keys]).orderBy(*ordering)

    ranked = df.withColumn("_dedup_rank", F.row_number().over(window))
    kept = ranked.filter(F.col("_dedup_rank") == 1).drop("_dedup_rank")
    losers = ranked.filter(F.col("_dedup_rank") > 1).drop("_dedup_rank")

    rejected = _tag_rejects(losers, ctx, "deduplicate", f"duplicate on {keys}")
    return RuleResult(kept=kept, rejected=rejected, stats={"keys": keys})


# ---------------------------------------------------------------------------
# Domain and range validation
# ---------------------------------------------------------------------------

def enforce_allowed_values(
    df: DataFrame, ctx: RuleContext, column: str, values: list[Any] | None = None
) -> RuleResult:
    """Quarantine rows whose value falls outside the declared domain."""
    if not values:
        raise ValueError(f"enforce_allowed_values on {column!r} requires allowed values")
    return _split(
        df, F.col(column).isin(values), ctx,
        "enforce_allowed_values", f"{column} not in {values}",
    )


def _reject_out_of_range(
    df, ctx, columns, rule, min_exclusive=None, min_value=None, max_value=None
) -> RuleResult:
    condition = None
    for name in columns:
        col = F.col(name)
        checks = col.isNotNull()
        if min_exclusive is not None:
            checks = checks & (col > F.lit(min_exclusive))
        if min_value is not None:
            checks = checks & (col >= F.lit(min_value))
        if max_value is not None:
            checks = checks & (col <= F.lit(max_value))
        condition = checks if condition is None else (condition & checks)

    bound = f"> {min_exclusive}" if min_exclusive is not None else f"[{min_value}, {max_value}]"
    return _split(df, condition, ctx, rule, f"{columns} outside {bound}")


def validate_positive(df: DataFrame, ctx: RuleContext, columns: list[str]) -> RuleResult:
    """Quarantine rows where a column that must be positive is not."""
    return _reject_out_of_range(df, ctx, columns, "validate_positive", min_exclusive=0)


def quarantine_invalid_price(
    df: DataFrame, ctx: RuleContext, column: str, min_exclusive: float = 0
) -> RuleResult:
    """Quarantine catalogue rows with a non-positive list price.

    Named separately from validate_positive despite sharing an implementation,
    because the spec traces it to a specific upstream defect (PRICE-001) and
    the quarantine table records which named rule fired.
    """
    return _reject_out_of_range(
        df, ctx, [column], "quarantine_invalid_price", min_exclusive=min_exclusive
    )


def quarantine_negative_total(
    df: DataFrame, ctx: RuleContext, column: str, min_exclusive: float = 0
) -> RuleResult:
    """Quarantine orders with a non-positive total.

    A negative total is a refund recorded in the wrong place. Flipping its sign
    would invent a sale, and admitting it as revenue would understate the true
    figure, so it waits for a proper credit note.
    """
    return _reject_out_of_range(
        df, ctx, [column], "quarantine_negative_total", min_exclusive=min_exclusive
    )


# ---------------------------------------------------------------------------
# Referential integrity
# ---------------------------------------------------------------------------

def enforce_referential_integrity(
    df: DataFrame, ctx: RuleContext, column: str, references: str
) -> RuleResult:
    """Quarantine rows whose foreign key has no parent.

    `references` is "table.column". The parent is fetched through
    ctx.resolve_table so this works identically against a Fabric Lakehouse
    table and a unit-test fixture.

    Deliberately does NOT redirect orphans to an unknown member. Doing so at
    silver would make a genuine upstream deletion look like ordinary data, and
    the count that should have raised an alert would instead sit quietly in a
    dimension nobody inspects. Gold has an unknown member for the different
    problem of a late-arriving dimension.
    """
    parent_table, parent_column = references.rsplit(".", 1)
    parent = ctx.resolve_table(parent_table).select(
        F.col(parent_column).alias("_parent_key")
    ).distinct()

    joined = df.join(parent, df[column] == F.col("_parent_key"), "left")
    kept = joined.filter(F.col("_parent_key").isNotNull()).drop("_parent_key")
    orphans = joined.filter(F.col("_parent_key").isNull()).drop("_parent_key")

    rejected = _tag_rejects(
        orphans, ctx, "enforce_referential_integrity",
        f"{column} has no matching {references}",
    )
    return RuleResult(kept=kept, rejected=rejected, stats={"references": references})


# ---------------------------------------------------------------------------
# Arithmetic correction
# ---------------------------------------------------------------------------

def recompute_subtotal(
    df: DataFrame,
    ctx: RuleContext,
    target: str,
    expression: str,
    tolerance: float = 0.01,
    keep_original_as: str | None = None,
) -> RuleResult:
    """Recompute a derived column from its inputs, preserving what was reported.

    Corrects rather than quarantines. The inputs (quantity, unit_price) are
    trustworthy and only the cached total drifted, so discarding the row would
    throw away real revenue to punish an arithmetic error.

    Sets `{target}_was_corrected` so a report can exclude corrected rows, and
    the count feeds the data-quality log.
    """
    recomputed = F.expr(expression)
    if keep_original_as:
        df = df.withColumn(keep_original_as, F.col(target))

    drifted = F.abs(F.col(target) - recomputed) > F.lit(tolerance)
    out = (
        df.withColumn(f"{target}_was_corrected", drifted & drifted.isNotNull())
        .withColumn(target, F.round(recomputed, 2))
    )
    corrected = out.filter(F.col(f"{target}_was_corrected")).count()

    return RuleResult(
        kept=out, rejected=_empty_like(out), corrected_count=corrected,
        stats={"rule": "recompute_subtotal", "target": target},
    )


def _recompute_from_children(
    df, ctx, target, from_table, join_on, aggregate, tolerance, keep_original_as, rule,
) -> RuleResult:
    """Shared implementation for rolling a child table up onto its parent."""
    children = ctx.resolve_table(from_table)
    rollup = children.groupBy(join_on).agg(F.expr(aggregate).alias("_recomputed"))

    if keep_original_as:
        df = df.withColumn(keep_original_as, F.col(target))

    joined = df.join(rollup, on=join_on, how="left")

    # A parent with no children keeps its reported value -- there is nothing to
    # recompute from, and zeroing it would fabricate a change.
    recomputed = F.coalesce(F.col("_recomputed"), F.col(target))
    drifted = F.abs(F.col(target) - recomputed) > F.lit(tolerance)

    out = (
        joined.withColumn(f"{target}_was_corrected", drifted & drifted.isNotNull())
        .withColumn(target, recomputed)
        .drop("_recomputed")
    )
    corrected = out.filter(F.col(f"{target}_was_corrected")).count()

    return RuleResult(
        kept=out, rejected=_empty_like(out), corrected_count=corrected,
        stats={"rule": rule, "from_table": from_table},
    )


def recompute_total_from_lines(
    df: DataFrame,
    ctx: RuleContext,
    target: str,
    from_table: str,
    join_on: str,
    aggregate: str = "sum(subtotal)",
    tolerance: float = 0.01,
    keep_original_as: str | None = None,
) -> RuleResult:
    """Recompute an order header total from its already-corrected lines.

    Runs after the child table is built -- the spec's `depends_on` enforces the
    ordering. Rolling up uncorrected lines would propagate their error into the
    header and make the reconciliation check pass against wrong numbers.
    """
    return _recompute_from_children(
        df, ctx, target, from_table, join_on, aggregate, tolerance,
        keep_original_as, "recompute_total_from_lines",
    )


def recompute_items_count(
    df: DataFrame,
    ctx: RuleContext,
    target: str,
    from_table: str,
    join_on: str,
    aggregate: str = "count(*)",
    tolerance: float = 0,
    keep_original_as: str | None = None,
) -> RuleResult:
    """Recompute a cached line count from the actual number of child rows."""
    return _recompute_from_children(
        df, ctx, target, from_table, join_on, aggregate, tolerance,
        keep_original_as, "recompute_items_count",
    )


# ---------------------------------------------------------------------------
# PII masking
# ---------------------------------------------------------------------------

def mask_pii(df: DataFrame, ctx: RuleContext, policy_ref: str | None = None) -> RuleResult:
    """Apply masking policies to columns declared as PII.

    A no-op where the environment sets `contains_pii: false`, so the identical
    notebook runs in every environment and masking is a deployment parameter
    rather than a code branch someone can forget to flip.
    """
    if not ctx.apply_masking:
        return RuleResult(kept=df, rejected=_empty_like(df),
                          stats={"masking": "skipped", "environment": ctx.environment})

    out = df
    applied = []
    for name, policy in ctx.masking_policies.items():
        for qualified in policy.get("applies_to", []):
            table, _, column = qualified.rpartition(".")
            if table != ctx.table or column not in out.columns:
                continue
            out = out.withColumn(
                column,
                F.when(F.col(column).isNotNull(), F.expr(policy["expression"]))
                 .otherwise(F.lit(None)),
            )
            applied.append(f"{name}->{column}")

    return RuleResult(kept=out, rejected=_empty_like(out),
                      stats={"masking": "applied", "policies": applied})


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------

def add_record_hash(
    df: DataFrame, ctx: RuleContext, exclude: list[str] | None = None
) -> RuleResult:
    """Add a stable row fingerprint over the business columns.

    Gold's SCD2 build compares this to decide whether a row genuinely changed.
    Audit columns are excluded because they differ on every load, and including
    them would open a new dimension version on every run.
    """
    excluded = set(exclude or []) | {"_processed_at", LOAD_ID, "_record_hash"}
    columns = sorted(c for c in df.columns if c not in excluded)
    fingerprint = F.sha2(
        F.concat_ws("||", *[F.coalesce(F.col(c).cast("string"), F.lit("<null>")) for c in columns]),
        256,
    )
    return RuleResult(
        kept=df.withColumn("_record_hash", fingerprint),
        rejected=_empty_like(df),
        stats={"hashed_columns": len(columns)},
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# The bridge between spec and code. validate_specs.py checks that every `fn:`
# in a mapping appears here, and warns on any rule here that no spec uses --
# so drift is caught at validation time rather than at three in the morning.

REGISTRY: dict[str, Callable[..., RuleResult]] = {
    "cast_types": cast_types,
    "apply_transforms": apply_transforms,
    "default_nulls": default_nulls,
    "drop_null_business_key": drop_null_business_key,
    "drop_null_required": drop_null_required,
    "deduplicate": deduplicate,
    "enforce_allowed_values": enforce_allowed_values,
    "validate_positive": validate_positive,
    "quarantine_invalid_price": quarantine_invalid_price,
    "quarantine_negative_total": quarantine_negative_total,
    "enforce_referential_integrity": enforce_referential_integrity,
    "recompute_subtotal": recompute_subtotal,
    "recompute_total_from_lines": recompute_total_from_lines,
    "recompute_items_count": recompute_items_count,
    "mask_pii": mask_pii,
    "add_record_hash": add_record_hash,
}


def get_rule(name: str) -> Callable[..., RuleResult]:
    """Look up a rule by its spec name."""
    if name not in REGISTRY:
        raise KeyError(
            f"Unknown cleansing rule {name!r}. "
            f"Available: {', '.join(sorted(REGISTRY))}"
        )
    return REGISTRY[name]
