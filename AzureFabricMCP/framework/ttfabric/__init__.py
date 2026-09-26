"""
ttfabric -- runtime library for the TechTonic Fabric framework.

Imported by generated notebooks running on Fabric Spark. Delivered as a wheel
attached to the workspace Spark Environment, so a notebook simply imports it:

    from ttfabric.cleansing import RuleContext, get_rule

rather than appending a OneLake path to sys.path. That path form depended on
the notebook's default lakehouse binding having propagated, which is not
guaranteed immediately after an item is created -- a freshly created notebook
failed with ModuleNotFoundError and an identical re-run passed.

The package is named `ttfabric` rather than `lib` deliberately. A Spark
environment is shared, `lib` is an inviting name, and a collision there would
resolve to whichever copy landed on sys.path first.

Modules
-------
    cleansing   silver rules; each returns (kept, rejected) so nothing is
                dropped without a recorded reason
    dimensions  SCD2 merge, point-in-time key lookup, unknown member, calendar
    quality     data-quality run log and hard assertions
    warehouse   routes gold IO to a Warehouse or Lakehouse per the spec
"""

__version__ = "0.1.0"

__all__ = ["cleansing", "dimensions", "quality", "warehouse"]
