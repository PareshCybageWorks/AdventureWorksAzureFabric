"""
Read and write gold tables, in a Warehouse or a Lakehouse.

Why this exists
---------------
A Fabric notebook's default lakehouse must be a Lakehouse -- a Warehouse cannot
be one. Gold notebooks still need a default (the framework library is imported
from /lakehouse/default/Files/framework), so their default is the lakehouse they
READ from.

That makes every unqualified `saveAsTable` in a gold notebook land in the silver
lakehouse. Gold tables end up beside the silver tables they were derived from,
under names that differ by a single letter (dim_customer vs dim_customers), and
nothing raises an error.

So gold writes must never be unqualified. They go through this module, which
routes to the Fabric Data Warehouse Spark connector when a warehouse is
configured, and falls back to Delta in the default lakehouse when it is not.

The routing follows `storage.gold.write_mode` in 00-platform.yaml:

    warehouse_connector   -> synapsesql against wh_gold.dbo.*
    lakehouse_staging     -> Delta in the default lakehouse

Keeping both behind one interface means switching is a spec change, and the
generated notebooks do not care which is in force.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession

# Raised by Spark when a table does not exist. Imported lazily inside functions
# so this module can be imported for reference without a live Spark session.
_NOT_FOUND = ("TABLE_OR_VIEW_NOT_FOUND", "PATH_NOT_FOUND", "cannot be found",
              "Invalid object name")

# The warehouse connector reports a missing table and an unauthorised one with
# the SAME message, so absence cannot be distinguished from denial by the error
# alone. Treated as absence, because a first run legitimately has no table --
# but never silently: a permissions fault would otherwise look like a first run
# on every execution, quietly rebuilding the dimension and destroying its
# history.
_AMBIGUOUS_NOT_FOUND = "Either source is invalid or user doesn't have read access"

_connector_ready = False


def _ensure_connector() -> None:
    """Register the Fabric Data Warehouse Spark connector.

    `synapsesql` is an extension method that does not exist on DataFrameReader
    or DataFrameWriter until this package is imported -- without it the call
    fails with AttributeError rather than anything that hints at a missing
    import.

    Imported lazily, and only on the warehouse path, because the package exists
    only inside a Fabric Spark runtime. A module-level import would make this
    file unimportable anywhere else, including the spec validator.
    """
    global _connector_ready
    if _connector_ready:
        return
    try:
        import com.microsoft.spark.fabric  # noqa: F401
        from com.microsoft.spark.fabric.Constants import Constants  # noqa: F401
    except ImportError as exc:                      # pragma: no cover
        raise RuntimeError(
            "The Fabric Data Warehouse Spark connector is unavailable in this "
            "runtime, so gold cannot be written to a Warehouse. Either run this "
            "notebook in Fabric, or set storage.gold.write_mode to "
            "'lakehouse_staging' in 00-platform.yaml."
        ) from exc
    _connector_ready = True


class GoldTarget:
    """Where the gold layer physically lives, and how to reach it.

    Constructed once per notebook from the spec-derived parameters, then passed
    to every read and write so no call site decides for itself.
    """

    def __init__(self, spark: SparkSession, warehouse: str | None,
                 schema: str = "dbo", write_mode: str = "warehouse_connector") -> None:
        self.spark = spark
        self.schema = schema
        # A warehouse name is only honoured when the spec actually asks for the
        # connector. Setting one under lakehouse_staging would silently ignore
        # the configured mode.
        self.warehouse = warehouse if write_mode == "warehouse_connector" else None
        self.write_mode = write_mode

    # -- naming ------------------------------------------------------------

    def qualified(self, table: str) -> str:
        if self.warehouse:
            return f"{self.warehouse}.{self.schema}.{table}"
        return table

    def __repr__(self) -> str:
        return (f"GoldTarget(mode={self.write_mode}, "
                f"target={self.qualified('<table>')})")

    # -- io ----------------------------------------------------------------

    def read(self, table: str) -> DataFrame:
        """Read a gold table from wherever gold lives."""
        if self.warehouse:
            _ensure_connector()
            return self.spark.read.synapsesql(self.qualified(table))
        return self.spark.read.table(table)

    def try_read(self, table: str) -> DataFrame | None:
        """Read a gold table, or None when it does not exist yet.

        First-run logic needs to distinguish "no table" from "read failed", and
        the two paths raise different exception types with different messages,
        so the check is centralised here rather than repeated at each call site.
        """
        try:
            df = self.read(table)
            # synapsesql is lazy enough that a missing table can survive until
            # first action; force it now so first-run detection is reliable.
            df.take(1)
            return df
        except Exception as exc:                     # noqa: BLE001
            message = str(exc)
            if any(marker in message for marker in _NOT_FOUND):
                return None
            if _AMBIGUOUS_NOT_FOUND in message:
                print(f"  {self.qualified(table)} is unreadable -- treating as a "
                      f"first run. If this table SHOULD exist, this is a "
                      f"permissions fault and the dimension is about to be "
                      f"rebuilt from scratch, losing its history.")
                return None
            raise

    def write(self, df: DataFrame, table: str, mode: str = "overwrite") -> None:
        """Write a gold table to wherever gold lives.

        Never falls back silently. If the connector is configured and fails,
        the failure surfaces -- writing to the lakehouse instead would put gold
        back in the silver item, which is the exact problem this module exists
        to prevent.
        """
        target = self.qualified(table)

        if self.warehouse:
            _ensure_connector()
            if mode != "overwrite":
                # The connector accepts overwrite and fails append with an
                # opaque "Write orchestration failed". Refusing here names the
                # real constraint at the call site, instead of surfacing a
                # generic orchestration error several frames away.
                raise ValueError(
                    f"the warehouse connector supports mode='overwrite' only; "
                    f"got {mode!r} for {target}. Read the existing table, union "
                    f"the new rows, and overwrite."
                )
            df.write.mode(mode).synapsesql(target)
            print(f"  wrote {df.count():,} rows to {target} (warehouse connector)")
            return

        (df.write.mode(mode)
            .option("overwriteSchema", "true")
            .format("delta").saveAsTable(target))
        print(f"  wrote {df.count():,} rows to {target} (lakehouse delta)")


def gold_target(spark: SparkSession, warehouse: str | None, schema: str = "dbo",
                write_mode: str = "warehouse_connector") -> GoldTarget:
    """Build the gold target for a notebook. Called once, in the preamble."""
    target = GoldTarget(spark, warehouse, schema, write_mode)
    print(f"gold target: {target!r}")
    return target
