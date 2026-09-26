"""
Dimensional modelling helpers for the gold layer.

SCD type 2 and point-in-time key resolution are where a warehouse most often
looks correct and reports wrong numbers, so the invariants are stated
explicitly and asserted rather than assumed:

  * exactly one current row per business key
  * validity windows never overlap and never leave a gap
  * a fact joins the dimension version that was current when the event
    happened, not the version current today

That last one is the difference between "revenue by region" meaning the region
a customer was in when they bought, and meaning the region they are in now.
Both are legitimate questions; silently answering the second when someone asked
the first is not.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.utils import AnalysisException

# Open-ended validity windows use a sentinel rather than NULL so a BETWEEN
# predicate works without a special case for the current row.
END_OF_TIME = "9999-12-31"

# A member's FIRST version must be valid from the beginning of time, not from
# the day it was loaded.
#
# Stamping valid_from = today on an initial load looks harmless and is
# catastrophic: a point-in-time lookup filters
#     event_date BETWEEN valid_from AND valid_to
# so every fact older than the load -- which is all of them, on a historical
# backfill -- matches nothing and falls through to the unknown member. The
# warehouse then reports 100% of revenue against "Unknown Customer" while every
# not-null assertion passes, because -1 is not null.
#
# Only a version opened by an actual tracked-column CHANGE starts today. That
# is a real event with a real date; a first sighting is not.
BEGINNING_OF_TIME = "1900-01-01"


# ---------------------------------------------------------------------------
# Surrogate keys
# ---------------------------------------------------------------------------

def assign_surrogate_key(
    df: DataFrame, surrogate_key: str, business_key: list[str]
) -> DataFrame:
    """Assign a deterministic surrogate key derived from the business key.

    Deterministic rather than monotonically increasing: a rebuild produces the
    same keys, so a full refresh does not silently repoint every fact row at a
    different dimension member. The cost is a hash-shaped key instead of a
    dense sequence, which nothing downstream depends on.
    """
    fingerprint = F.concat_ws("||", *[F.coalesce(F.col(c).cast("string"), F.lit("")) for c in business_key])
    # Positive 63-bit space; -1 stays reserved for the unknown member.
    return df.withColumn(surrogate_key, F.abs(F.xxhash64(fingerprint)))


def ensure_unknown_member(
    spark: SparkSession,
    table: str,
    surrogate_key: str,
    gold,
    key_value: int = -1,
    defaults: dict | None = None,
) -> None:
    """Guarantee the dimension holds an unknown member.

    A fact whose dimension lookup misses is pointed here rather than dropped.
    Without it, an unresolved key silently removes the fact row from every
    report that joins that dimension -- revenue quietly disappears and no
    error is raised anywhere.
    """
    defaults = defaults or {}
    existing = gold.read(table)

    if existing.filter(F.col(surrogate_key) == key_value).limit(1).count():
        return

    # Fill EVERY column, not only the ones the spec names.
    #
    # A warehouse infers NOT NULL from the first write, so a member carrying
    # nulls in the unnamed columns is rejected outright:
    #   "Cannot insert the value NULL into column 'tenure_band'"
    # It is also the right shape regardless of nullability -- an unknown member
    # full of nulls renders as blanks on every report that joins it, which is
    # exactly the silent gap the member exists to make visible.
    placeholder = {
        "string": "Unknown",
        "boolean": False,
        "date": date(1900, 1, 1),
    }

    row = {}
    for field in existing.schema.fields:
        kind = field.dataType.typeName()
        if kind == "decimal":
            # createDataFrame with an explicit schema will not widen an int
            # into a DecimalType column -- it raises CANNOT_ACCEPT_OBJECT_IN_TYPE.
            # The zero has to already be a Decimal.
            row[field.name] = Decimal(0)
        elif kind in ("integer", "long", "short", "byte", "double", "float"):
            row[field.name] = 0
        elif kind == "timestamp":
            row[field.name] = datetime(1900, 1, 1)
        else:
            row[field.name] = placeholder.get(kind, "Unknown")

    row[surrogate_key] = key_value
    row.update({k: v for k, v in defaults.items() if k in row})

    # Real date objects, not strings. createDataFrame with an explicit schema
    # will not coerce a str into a DateType column -- it raises
    # CANNOT_ACCEPT_OBJECT_IN_TYPE, which is the correct behaviour and the
    # reason the sentinels are parsed here rather than passed through.
    for column in ("is_current",):
        if column in row:
            row[column] = True
    if "valid_from" in row:
        row["valid_from"] = date(1900, 1, 1)
    if "valid_to" in row:
        row["valid_to"] = date.fromisoformat(END_OF_TIME)
    if "version" in row:
        row["version"] = 1

    unknown = spark.createDataFrame([row], schema=existing.schema)

    # Union and overwrite rather than append. The Fabric Data Warehouse Spark
    # connector accepts `overwrite` but fails `append` with an opaque
    # "Write orchestration failed" -- so a one-row insert cannot be done as an
    # insert. Rewriting the whole dimension to add a single row is wasteful,
    # but correctness wins and dimensions are small by construction.
    #
    # Guarded by the existence check above, so this runs once per dimension
    # rather than on every load.
    gold.write(existing.unionByName(unknown), table)
    print(f"  inserted unknown member into {table} ({surrogate_key}={key_value})")


# ---------------------------------------------------------------------------
# Slowly changing dimensions
# ---------------------------------------------------------------------------

def merge_scd2(
    spark: SparkSession,
    source: DataFrame,
    target_table: str,
    business_key: list[str],
    tracked_columns: list[str],
    surrogate_key: str,
    load_id: str,
    gold,
    rebuild: bool = False,
    valid_from_column: str = "valid_from",
    valid_to_column: str = "valid_to",
    is_current_column: str = "is_current",
    version_column: str = "version",
) -> None:
    """Merge source rows into a type 2 dimension.

    A change to a TRACKED column closes the current row and opens a new
    version. A change to any other column updates the current row in place --
    correcting a phone number should not fabricate a historical event, and
    treating every edit as a version makes history unreadable within a month.

    First run creates the table. Subsequent runs merge.
    """
    surrogate_source = assign_surrogate_key(source, "_bk_hash", business_key)

    change_fingerprint = F.sha2(
        F.concat_ws("||", *[F.coalesce(F.col(c).cast("string"), F.lit("<null>"))
                            for c in sorted(tracked_columns)]),
        256,
    )
    # First sighting of a member is valid from the beginning of time. See the
    # BEGINNING_OF_TIME note -- using today here silently routes every
    # historical fact to the unknown member.
    incoming = (
        surrogate_source
        .withColumn("_tracked_hash", change_fingerprint)
        .withColumn(valid_from_column, F.lit(BEGINNING_OF_TIME).cast("date"))
        .withColumn(valid_to_column, F.lit(END_OF_TIME).cast("date"))
        .withColumn(is_current_column, F.lit(True))
        .withColumn(version_column, F.lit(1))
        .withColumn("_load_id", F.lit(load_id))
        .withColumn(surrogate_key, F.col("_bk_hash"))
    )

    # `rebuild` discards existing history and reloads from scratch.
    #
    # Needed because SCD2 preserves history by design -- including history that
    # was recorded wrongly. A dimension first built with an incorrect
    # valid_from keeps it forever: unchanged rows are carried through
    # untouched, so a later fix to the load logic never reaches them. The only
    # way to correct an already-materialised dimension is to say so explicitly.
    #
    # Destructive: every existing version is discarded. Genuine historical
    # change that has occurred since the first load is lost, so this is a
    # deliberate operator action, never a default.
    existing = None if rebuild else gold.try_read(target_table)
    if rebuild:
        print(f"  {target_table}: REBUILD -- discarding existing history")

    if existing is None:
        gold.write(incoming.drop("_bk_hash"), target_table)
        print(f"  {target_table}: initial load, {incoming.count():,} rows")
        return

    current = existing.filter(F.col(is_current_column))

    joined = incoming.alias("new").join(
        current.select(
            *[F.col(c).alias(f"cur_{c}") for c in business_key],
            F.col("_tracked_hash").alias("cur_tracked_hash"),
            F.col(version_column).alias("cur_version"),
        ),
        on=[F.col(f"new.{c}") == F.col(f"cur_{c}") for c in business_key],
        how="left",
    )

    unchanged = joined.filter(F.col("cur_tracked_hash") == F.col("_tracked_hash"))
    brand_new = joined.filter(F.col("cur_tracked_hash").isNull())
    changed = joined.filter(
        F.col("cur_tracked_hash").isNotNull()
        & (F.col("cur_tracked_hash") != F.col("_tracked_hash"))
    )

    changed_count = changed.count()
    new_count = brand_new.count()

    # The INCOMING frame defines the schema, not the existing table. Taking it
    # from the table makes the dimension permanently shaped by whatever the
    # first run happened to write: a column the spec later drops keeps being
    # selected (and fails, because the source no longer has it), and a column
    # the spec adds is silently ignored. The spec is the authority.
    target_columns = [c for c in incoming.columns if c != "_bk_hash"]

    def align(df: DataFrame) -> DataFrame:
        """Reshape a frame to the target schema, null-filling new columns."""
        return df.select(*[
            (F.col(c) if c in df.columns else F.lit(None)).alias(c)
            for c in target_columns
        ])

    def shape(df: DataFrame, version) -> DataFrame:
        return align(df.withColumn(version_column, version))

    # Close superseded rows: the previous version stops being current the day
    # the new one starts, leaving no gap and no overlap.
    changed_keys = changed.select(*[F.col(f"new.{c}").alias(c) for c in business_key]).distinct()
    superseded = (
        current.join(changed_keys, on=business_key, how="inner")
        .withColumn(valid_to_column, F.date_sub(F.current_date(), 1))
        .withColumn(is_current_column, F.lit(False))
    )
    superseded = align(superseded)

    untouched = align(current.join(changed_keys, on=business_key, how="left_anti"))
    historical = align(existing.filter(~F.col(is_current_column)))

    # A version opened by a tracked-column change starts TODAY -- that is a real
    # event with a real date. It pairs with the superseded row above, which was
    # closed yesterday, leaving no gap and no overlap.
    #
    # A brand-new member keeps BEGINNING_OF_TIME from `incoming`: its first
    # sighting is not a change, and dating it today would hide every fact that
    # predates the load.
    opened = shape(
        changed.withColumn(valid_from_column, F.current_date()),
        F.col("cur_version") + 1,
    )
    inserted = shape(brand_new, F.lit(1))

    result = (
        historical
        .unionByName(untouched)
        .unionByName(superseded)
        .unionByName(opened)
        .unionByName(inserted)
    )

    gold.write(result, target_table)
    print(f"  {target_table}: {new_count:,} new, {changed_count:,} versioned, "
          f"{unchanged.count():,} unchanged")

    assert_scd2_integrity(gold.read(target_table), business_key,
                          is_current_column, target_table)


def assert_scd2_integrity(
    df: DataFrame, business_key: list[str], is_current_column: str, label: str
) -> None:
    """Assert exactly one current row per business key.

    The classic SCD2 failure. Two current rows silently double every measure
    that joins the dimension, and because both rows look plausible the error
    surfaces as "the numbers are a bit high" weeks later.
    """
    offenders = (
        df.filter(F.col(is_current_column))
        .groupBy(*business_key)
        .count()
        .filter(F.col("count") > 1)
    )
    breaches = offenders.count()
    if breaches:
        sample = [r.asDict() for r in offenders.limit(5).collect()]
        raise AssertionError(
            f"{label}: {breaches:,} business keys have more than one current "
            f"row. Sample: {sample}"
        )


def lookup_surrogate_key(
    fact: DataFrame,
    dimension: DataFrame,
    surrogate_key: str,
    lookup_on: str,
    dimension_key: str | None = None,
    dimension_surrogate_key: str | None = None,
    as_of_column: str | None = None,
    unknown_key: int = -1,
    valid_from_column: str = "valid_from",
    valid_to_column: str = "valid_to",
    is_current_column: str = "is_current",
) -> DataFrame:
    """Resolve a fact's natural key to a dimension surrogate key.

    With `as_of_column`, performs a point-in-time lookup: the fact joins the
    dimension version that was current when the event occurred. Without it,
    joins the current version.

    Unmatched rows receive the unknown member rather than being dropped, so a
    lookup miss shows up as an "Unknown" bar on a chart -- visible and
    questionable -- instead of as revenue that quietly went missing.
    """
    # The fact's column and the dimension's business key are NOT always the
    # same name. fct_sales.order_date_key joins dim_date.full_date; only
    # customer_id and product_id happen to match on both sides. Defaulting
    # dimension_key to lookup_on preserves the matching case without pretending
    # the two are one concept.
    # THREE names are involved and only two coincide in the easy case:
    #   lookup_on               the fact's join column      order_date_key
    #   dimension_key           the dimension's business key full_date
    #   dimension_surrogate_key the dimension's key column   date_sk
    #   surrogate_key           the column written on the fact  order_date_sk
    # For customer and product all four collapse to two names, which is exactly
    # why treating them as one concept survives until a date dimension appears.
    business_key = lookup_on
    dim_key = dimension_key or lookup_on
    dim_sk = dimension_surrogate_key or surrogate_key

    if as_of_column and valid_from_column in dimension.columns:
        candidates = dimension.select(
            F.col(dim_key).alias("_dim_key"),
            F.col(dim_sk).alias("_dim_sk"),
            F.col(valid_from_column).alias("_valid_from"),
            F.col(valid_to_column).alias("_valid_to"),
        )
        condition = (
            (fact[business_key] == F.col("_dim_key"))
            & (F.to_date(fact[as_of_column]) >= F.col("_valid_from"))
            & (F.to_date(fact[as_of_column]) <= F.col("_valid_to"))
        )
    else:
        source = dimension
        if is_current_column in dimension.columns:
            source = dimension.filter(F.col(is_current_column))
        candidates = source.select(
            F.col(dim_key).alias("_dim_key"),
            F.col(dim_sk).alias("_dim_sk"),
        )
        condition = fact[business_key] == F.col("_dim_key")

    joined = fact.join(candidates, on=condition, how="left")

    # Overlapping validity windows would fan the fact out. assert_scd2_integrity
    # guards the dimension, but a fan-out here would silently inflate measures,
    # so it is worth failing loudly rather than trusting the upstream check.
    if joined.count() > fact.count():
        raise AssertionError(
            f"point-in-time lookup on {business_key} fanned out: "
            f"{fact.count():,} fact rows became {joined.count():,}. "
            f"The dimension has overlapping validity windows."
        )

    drop_columns = [c for c in ("_dim_key", "_valid_from", "_valid_to") if c in joined.columns]
    return (
        joined
        .withColumn(surrogate_key, F.coalesce(F.col("_dim_sk"), F.lit(unknown_key)))
        .drop("_dim_sk", *drop_columns)
    )


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

def build_date_dimension(
    spark: SparkSession,
    start_date: str,
    end_date: str,
    fiscal_year_start_month: int = 1,
) -> DataFrame:
    """Build a calendar dimension.

    date_sk is a yyyymmdd integer rather than a hash, because a date key is
    the one surrogate worth being able to read at a glance while debugging.
    """
    days = (
        spark.sql(f"SELECT explode(sequence(to_date('{start_date}'), "
                  f"to_date('{end_date}'), interval 1 day)) AS full_date")
    )

    fiscal_year = F.when(
        F.month("full_date") >= fiscal_year_start_month, F.year("full_date")
    ).otherwise(F.year("full_date") - 1)

    fiscal_month = ((F.month("full_date") - fiscal_year_start_month + 12) % 12) + 1

    return (
        days
        .withColumn("date_sk", F.date_format("full_date", "yyyyMMdd").cast("int"))
        .withColumn("year", F.year("full_date"))
        .withColumn("quarter", F.concat(F.lit("Q"), F.quarter("full_date")))
        .withColumn("month", F.month("full_date"))
        .withColumn("month_name", F.date_format("full_date", "MMMM"))
        .withColumn("year_month", F.date_format("full_date", "yyyy-MM"))
        .withColumn("week_of_year", F.weekofyear("full_date"))
        .withColumn("day_of_month", F.dayofmonth("full_date"))
        .withColumn("day_name", F.date_format("full_date", "EEEE"))
        .withColumn("is_weekend", F.dayofweek("full_date").isin(1, 7))
        .withColumn("fiscal_year", fiscal_year)
        .withColumn("fiscal_quarter", F.concat(F.lit("FQ"), F.ceil(fiscal_month / 3)))
    )
