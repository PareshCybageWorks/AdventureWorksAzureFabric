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
        if not expression:
            continue

        # Cross-table expressions are handled by dedicated rules, not here.
        # `count(order_items where order_id = this.order_id)` names another
        # table, which F.expr cannot resolve against a single DataFrame.
        if "where" in expression:
            continue

        # The previous guard ALSO required "(" in the expression, as a proxy
        # for "looks like a function call". It silently skipped every valid
        # expression without parentheses -- `bytes_in + bytes_out`,
        # `span_type = 'exclude'`, a bare CASE WHEN -- and the column simply
        # never appeared.
        #
        # That failure is invisible at the point it happens. It surfaces later
        # either as a rule referencing a column that does not exist, or worse,
        # as a measure quietly reading nothing. A declared expression is a
        # statement that the column should exist; there is no reason a
        # parenthesis should decide it.
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
# Structural defects
# ---------------------------------------------------------------------------
# The rules above all repair or reject VALUES. These five deal with defects in
# the SHAPE of a table -- a grain that is not what everyone assumes, an array
# stuffed into a text column, a calendar too short for time intelligence, a
# flag computed once and never again, a reference table missing a side. None
# of them could be expressed with the value rules without lying about what
# they do: pointing `deduplicate` at a legitimate re-entry would destroy real
# events to make a count look tidy.

def assert_grain(
    df: DataFrame, ctx: RuleContext, columns: list[str], allow_duplicates: bool = False
) -> RuleResult:
    """Assert the table's grain, WITHOUT deduplicating it.

    The distinction this exists for: a repeated combination is sometimes a
    duplicate record and sometimes a real repeated event. Badge reads are the
    second kind -- one person entering one building twice in a day is two
    genuine arrivals, and deduplicating them would delete real entries to make
    a headcount look tidy.

    With `allow_duplicates` false the rule behaves like a constraint and
    quarantines the repeats. With it true the rule rejects nothing and only
    RECORDS how far the data is from one row per combination, so the number is
    visible in the run stats instead of being discovered by a measure that
    quietly double-counts.
    """
    key = [F.col(c) for c in columns]
    counts = df.groupBy(*key).agg(F.count(F.lit(1)).alias("_grain_n"))
    repeated = counts.filter(F.col("_grain_n") > 1)
    repeated_groups = repeated.count()
    excess = repeated.agg(
        F.coalesce(F.sum(F.col("_grain_n") - F.lit(1)), F.lit(0))
    ).collect()[0][0]

    stats = {
        "grain": ", ".join(columns),
        "repeated_groups": repeated_groups,
        "rows_above_grain": int(excess or 0),
        "enforced": not allow_duplicates,
    }

    if allow_duplicates:
        return RuleResult(kept=df, rejected=_empty_like(df), stats=stats)

    marked = df.join(repeated.select(*key, F.lit(True).alias("_over_grain")), columns, "left")
    kept = marked.filter(F.col("_over_grain").isNull()).drop("_over_grain")
    over = marked.filter(F.col("_over_grain").isNotNull()).drop("_over_grain")
    rejected = _tag_rejects(
        over, ctx, "assert_grain",
        f"more than one row per ({', '.join(columns)})",
    )
    return RuleResult(kept=kept, rejected=rejected, stats=stats)


def explode_json_array(
    df: DataFrame, ctx: RuleContext, column: str, into: str,
    element_type: str = "string", drop_source: bool = False,
) -> RuleResult:
    """Expand a JSON array held in a text column into one row per element.

    A star schema cannot join on an array. Left as text, "[1, 2, 3]" compares
    as a string and matches nothing -- and because a failed join yields null
    rather than an error, the result looks like missing data rather than a
    type mistake.

    Rejects nothing, but note this rule CHANGES THE ROW COUNT upward, which is
    the one place the layer identity `count(in) == count(kept) + count(rejected)`
    does not hold. That is deliberate and has to be: a persona scoped to five
    buildings genuinely is five facts. Declare it after any rule that counts
    rows, and reconcile the exploded table against its parent by distinct
    parent key rather than by row count.

    A null or empty array yields ONE row with a null element, not zero rows.
    Dropping the row would silently delete a persona whose scope is
    unpopulated -- exactly the under-scoping that makes an RLS bug invisible.
    """
    caster = {
        "string": StringType(), "integer": IntegerType(), "long": LongType(),
    }.get(element_type)
    if caster is None:
        raise ValueError(
            f"Unknown element_type {element_type!r} for explode_json_array on "
            f"{column!r}. Use string, integer or long."
        )

    parsed = F.from_json(F.col(column), f"array<{element_type}>")
    out = df.withColumn("_json_elements", parsed)
    out = out.withColumn(
        "_json_elements",
        F.when(
            F.col("_json_elements").isNull() | (F.size(F.col("_json_elements")) == 0),
            F.array(F.lit(None).cast(caster)),
        ).otherwise(F.col("_json_elements")),
    )
    out = out.withColumn(into, F.explode(F.col("_json_elements"))).drop("_json_elements")
    if drop_source:
        out = out.drop(column)

    return RuleResult(
        kept=out, rejected=_empty_like(out),
        stats={"exploded": column, "into": into, "rows_out": None},
    )


def extend_calendar(
    df: DataFrame, ctx: RuleContext, date_column: str, key_column: str,
    to_full_years: bool = True, start: str | None = None, end: str | None = None,
) -> RuleResult:
    """Extend a date dimension to cover whole calendar years.

    Power BI time intelligence -- SAMEPERIODLASTYEAR, DATEADD, TOTALYTD --
    requires a contiguous date table spanning full years. Given a partial one
    those functions return BLANK rather than raising, so every year-over-year
    tile renders empty and reads as a data gap rather than a modelling fault.
    That is the most expensive kind of defect: it looks like someone else's
    problem.

    Generated rows carry `_is_generated = true` so a report can tell a real
    calendar day from padding, and every non-key attribute is left null rather
    than guessed -- inventing is_work_day for a day nobody classified would
    put a fabricated working-day count into a denominator.
    """
    bounds = df.agg(F.min(date_column).alias("lo"), F.max(date_column).alias("hi")).collect()[0]
    lo = start or f"{bounds['lo'].year}-01-01"
    hi = end or (f"{bounds['hi'].year}-12-31" if to_full_years else str(bounds["hi"]))

    spine = (
        ctx.spark.sql(f"SELECT explode(sequence(DATE'{lo}', DATE'{hi}', INTERVAL 1 DAY)) AS {date_column}")
        .withColumn(key_column, F.date_format(F.col(date_column), "yyyyMMdd").cast(IntegerType()))
    )

    existing = df.withColumn("_is_generated", F.lit(False))
    missing = (
        spine.join(df.select(key_column), key_column, "left_anti")
        .withColumn("_is_generated", F.lit(True))
    )
    # Every column the real table has, nulled on generated rows, so the two
    # frames union by name rather than by position.
    for column in existing.columns:
        if column not in missing.columns:
            missing = missing.withColumn(column, F.lit(None).cast(existing.schema[column].dataType))

    out = existing.unionByName(missing.select(existing.columns))
    return RuleResult(
        kept=out, rejected=_empty_like(out),
        stats={"calendar_from": lo, "calendar_to": hi, "generated_rows": missing.count()},
    )


def recompute_activity_flags(
    df: DataFrame, ctx: RuleContext, key: str, event_table: str, event_key: str,
    event_date_column: str, windows: dict[str, int], as_of: str | None = None,
) -> RuleResult:
    """Recompute "active in the last N days" flags from the event table.

    A flag of this shape is computed once, at extract time, and is then wrong
    every day afterwards. The failure is quiet in a particular way: some of the
    windows keep varying while others saturate to a single value, so three of
    four options behave and the fourth silently reports everyone as active.

    `windows` maps target column to day count, e.g.
    {"is_active_in_last_30_days": 30}. The original value is retained beside it
    as `<column>_source` so a disagreement between the source's answer and ours
    stays visible rather than being overwritten.
    """
    events = ctx.resolve_table(event_table)
    anchor = F.lit(as_of).cast(DateType()) if as_of else F.lit(None).cast(DateType())
    if as_of is None:
        anchor = events.agg(F.max(F.col(event_date_column))).collect()[0][0]
        anchor = F.lit(anchor).cast(DateType())

    out = df
    for column, days in windows.items():
        recent = (
            events
            .filter(F.col(event_date_column) > F.date_sub(anchor, days))
            .select(F.col(event_key).alias("_event_key")).distinct()
            .withColumn("_active", F.lit(1))
        )
        out = out.join(recent, out[key] == F.col("_event_key"), "left").drop("_event_key")
        if column in df.columns:
            out = out.withColumnRenamed(column, f"{column}_source")
        out = out.withColumn(column, F.coalesce(F.col("_active"), F.lit(0))).drop("_active")

    return RuleResult(
        kept=out, rejected=_empty_like(out),
        stats={"recomputed": sorted(windows), "against": event_table},
    )


def flag_incomplete_reference(
    df: DataFrame, ctx: RuleContext, required_columns: list[str], flag: str = "_reference_incomplete",
) -> RuleResult:
    """Mark a reference table that cannot do the job its name claims.

    A bridge with only one side, or a dimension with a key and no attributes,
    is not wrong row by row -- every row is fine. It is structurally unable to
    resolve what it exists to resolve, and that has to be recorded somewhere a
    later modeller will actually look. Without it, a many-to-many is designed
    against a bridge that cannot express one, and the discovery happens after
    the relationship is drawn.

    Rejects nothing. Quarantining every row would be wrong: the rows are the
    only thing the table does have.
    """
    missing = [c for c in required_columns if c not in df.columns]
    out = df.withColumn(flag, F.lit(bool(missing)))
    return RuleResult(
        kept=out, rejected=_empty_like(out),
        stats={"missing_columns": missing, "complete": not missing},
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
    # Structural defects -- shape rather than values. See the section above for
    # why none of these could be folded into an existing rule.
    "assert_grain": assert_grain,
    "explode_json_array": explode_json_array,
    "extend_calendar": extend_calendar,
    "recompute_activity_flags": recompute_activity_flags,
    "flag_incomplete_reference": flag_incomplete_reference,
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


# ---------------------------------------------------------------------------
# Cascading quarantine
# ---------------------------------------------------------------------------

def cascade_quarantine(
    spark,
    child: str,
    parent: str,
    join_on: str,
    reason: str,
    load_id: str,
    quarantine_suffix: str = "_quarantine",
) -> dict:
    """Quarantine child rows whose parent did not survive cleansing.

    A row rejected at one table orphans its children at every table below it.
    Nothing notices: the child rows are still valid in isolation, so no rule
    fires, and they travel on until a join in a later layer silently discards
    them -- which is how 2,945 order lines worth 15.8M ended up outside every
    report with the only trace being a reconciliation gap.

    Quarantining them HERE attributes the loss to the decision that caused it
    ("parent order failed cleansing") rather than to its symptom two layers
    later ("no matching order_id"), and puts the record in the same place as
    every other rejection from this run.

    Runs as a POST-PASS over the whole layer, not inside either table's build.
    It cannot be part of the child's own build: the parent is often cleansed
    after the child -- an order header is corrected FROM its lines -- so at the
    time the child is written, which parents survive is not yet known.

    Appends to the child's existing quarantine table rather than replacing it,
    because cleansing has already written this run's row-level rejections there.
    """
    from pyspark.sql import functions as F

    child_df = spark.read.table(child)
    parent_keys = spark.read.table(parent).select(join_on).distinct()

    orphaned = child_df.join(parent_keys, on=join_on, how="left_anti")
    count = orphaned.count()
    if not count:
        return {"child": child, "parent": parent, "quarantined": 0}

    (orphaned
        .withColumn("_rejected_by", F.lit("cascade_quarantine"))
        .withColumn("_rejected_at", F.current_timestamp())
        .withColumn("_load_id", F.lit(load_id))
        .withColumn("_reason", F.lit(reason))
        .write.mode("append").option("mergeSchema", "true")
        .saveAsTable(f"{child}{quarantine_suffix}"))

    # The child is rewritten without them, so every table below this layer sees
    # a set with no dangling references and nothing further has to compensate.
    kept = child_df.join(parent_keys, on=join_on, how="left_semi")
    kept.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(child)

    return {"child": child, "parent": parent, "quarantined": count}
