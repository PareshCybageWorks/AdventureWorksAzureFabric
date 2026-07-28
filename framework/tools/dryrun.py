"""
Execute a project's spec set against local CSV files, without Spark.

Purpose and limits
------------------
This runs the SPECS -- the rule sequence, the quarantine routing, the
reconciliation invariant and the data-quality thresholds -- against real data,
on a laptop, in seconds. It catches the errors that are expensive to find in
Fabric: a rule ordered wrong, a column name that does not exist, a threshold
calibrated against the wrong denominator, a reconciliation that cannot hold.

It is a SECOND IMPLEMENTATION of the rule semantics in plain Python. It does
not execute framework/ttfabric/cleansing.py and therefore does not prove the PySpark
path runs. Passing here means the design is sound; it does not mean the
notebooks work. Treat it as a fast spec check with real data, not as a
substitute for running in Fabric.

Where the two implementations could drift, the spec is the arbiter: both read
the same mapping file, so a divergence is a bug in whichever one disagrees
with it.

Usage:
    python dryrun.py --project <project-dir> [--verbose]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

Row = dict[str, Any]


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------
# Each returns (kept, rejected, corrected_count), mirroring the RuleResult
# contract in framework/ttfabric/cleansing.py so the reconciliation invariant
# count(in) == count(kept) + count(rejected) holds here too.


def _reject(rows: list[Row], rule: str, detail: str) -> list[Row]:
    for row in rows:
        row["_rejected_by"] = rule
        row["_rejection_detail"] = detail
    return rows


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def cast_value(value: Any, declared: dict) -> Any:
    """Coerce a CSV string to the declared target type; None when impossible."""
    kind = declared.get("type", "string")
    if _is_blank(value):
        return None
    try:
        if kind == "string":
            return str(value)
        if kind == "integer":
            return int(float(value))
        if kind == "decimal":
            return round(float(value), declared.get("scale", 2))
        if kind == "boolean":
            return str(value).strip().lower() in ("true", "1", "yes")
        if kind in ("timestamp", "date"):
            text = str(value).replace("Z", "").split(".")[0]
            parsed = datetime.fromisoformat(text)
            return parsed.date().isoformat() if kind == "date" else parsed.isoformat()
    except (ValueError, TypeError):
        return None
    return value


def rule_cast_types(rows, ctx, columns=None, **_):
    out = []
    for row in rows:
        new = dict(row)
        for col in columns or []:
            source, target = col.get("source"), col["target"]
            if source is None:
                continue
            new[target] = cast_value(row.get(source), col)
        out.append(new)
    return out, [], 0


_TRANSFORMS = {
    "trim": lambda v: v.strip() if isinstance(v, str) else v,
    "lower": lambda v: v.lower() if isinstance(v, str) else v,
    "upper": lambda v: v.upper() if isinstance(v, str) else v,
    "title_case": lambda v: v.title() if isinstance(v, str) else v,
}


def rule_apply_transforms(rows, ctx, columns=None, **_):
    for row in rows:
        for col in columns or []:
            target = col["target"]
            for name in col.get("transforms", []):
                if target in row and row[target] is not None:
                    row[target] = _TRANSFORMS[name](row[target])
        # Derived columns the dry-run understands. Cross-table expressions are
        # handled by their own rules.
        if "full_name" in {c["target"] for c in (columns or [])}:
            row.setdefault("full_name",
                           f"{row.get('first_name', '')} {row.get('last_name', '')}".strip())
        if "order_date_key" in {c["target"] for c in (columns or [])} and row.get("order_date"):
            row["order_date_key"] = str(row["order_date"])[:10]
        if "is_revenue" in {c["target"] for c in (columns or [])}:
            row["is_revenue"] = row.get("status") in ("confirmed", "shipped", "delivered")
    return rows, [], 0


def rule_default_nulls(rows, ctx, column=None, value=None, **_):
    for row in rows:
        if _is_blank(row.get(column)):
            row[column] = value
    return rows, [], 0


def _split_on_blank(rows, columns, rule):
    kept, rejected = [], []
    for row in rows:
        (rejected if any(_is_blank(row.get(c)) for c in columns) else kept).append(row)
    return kept, _reject(rejected, rule, f"null or blank in {columns}"), 0


def rule_drop_null_business_key(rows, ctx, columns=None, **_):
    return _split_on_blank(rows, columns or [], "drop_null_business_key")


def rule_drop_null_required(rows, ctx, columns=None, **_):
    return _split_on_blank(rows, columns or [], "drop_null_required")


def rule_deduplicate(rows, ctx, keys=None, keep="latest", order_by=None, **_):
    groups: dict[tuple, list[Row]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(k) for k in keys)].append(row)

    def sort_key(row: Row):
        out = []
        for term in (order_by or []):
            parts = term.split()
            out.append(str(row.get(parts[0]) or ""))
        return out

    kept, rejected = [], []
    for _, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        descending = bool(order_by) and order_by[0].lower().endswith("desc")
        ordered = sorted(group, key=sort_key, reverse=descending)
        kept.append(ordered[0])
        rejected.extend(ordered[1:])

    return kept, _reject(rejected, "deduplicate", f"duplicate on {keys}"), 0


def rule_enforce_allowed_values(rows, ctx, column=None, values=None, **_):
    kept, rejected = [], []
    for row in rows:
        (kept if row.get(column) in (values or []) else rejected).append(row)
    return kept, _reject(rejected, "enforce_allowed_values",
                         f"{column} not in {values}"), 0


def _split_on_range(rows, columns, rule, min_exclusive=None):
    kept, rejected = [], []
    for row in rows:
        ok = True
        for name in columns:
            value = row.get(name)
            if value is None or (min_exclusive is not None and value <= min_exclusive):
                ok = False
                break
        (kept if ok else rejected).append(row)
    return kept, _reject(rejected, rule, f"{columns} not > {min_exclusive}"), 0


def rule_validate_positive(rows, ctx, columns=None, **_):
    return _split_on_range(rows, columns or [], "validate_positive", 0)


def rule_quarantine_invalid_price(rows, ctx, column=None, min_exclusive=0, **_):
    return _split_on_range(rows, [column], "quarantine_invalid_price", min_exclusive)


def rule_quarantine_negative_total(rows, ctx, column=None, min_exclusive=0, **_):
    return _split_on_range(rows, [column], "quarantine_negative_total", min_exclusive)


def rule_enforce_referential_integrity(rows, ctx, column=None, references=None, **_):
    table, parent_column = references.rsplit(".", 1)
    parents = {r.get(parent_column) for r in ctx["tables"].get(table, [])}
    kept, rejected = [], []
    for row in rows:
        (kept if row.get(column) in parents else rejected).append(row)
    return kept, _reject(rejected, "enforce_referential_integrity",
                         f"{column} has no matching {references}"), 0


def rule_recompute_subtotal(rows, ctx, target=None, tolerance=0.01,
                            keep_original_as=None, **_):
    corrected = 0
    for row in rows:
        if keep_original_as:
            row[keep_original_as] = row.get(target)
        quantity, unit_price = row.get("quantity"), row.get("unit_price")
        if quantity is None or unit_price is None:
            continue
        recomputed = round(quantity * unit_price, 2)
        drifted = abs((row.get(target) or 0) - recomputed) > tolerance
        row[f"{target}_was_corrected"] = drifted
        row[target] = recomputed
        corrected += drifted
    return rows, [], corrected


def _rollup(ctx, from_table, join_on, aggregate):
    totals: dict[Any, float] = defaultdict(float)
    counts: Counter = Counter()
    for row in ctx["tables"].get(from_table, []):
        key = row.get(join_on)
        counts[key] += 1
        if aggregate.startswith("sum("):
            field = aggregate[4:-1]
            totals[key] += row.get(field) or 0
    return totals, counts


def rule_recompute_total_from_lines(rows, ctx, target=None, from_table=None,
                                    join_on=None, aggregate="sum(subtotal)",
                                    tolerance=0.01, keep_original_as=None, **_):
    totals, _ = _rollup(ctx, from_table, join_on, aggregate)
    corrected = 0
    for row in rows:
        if keep_original_as:
            row[keep_original_as] = row.get(target)
        key = row.get(join_on)
        if key not in totals:
            row[f"{target}_was_corrected"] = False
            continue
        recomputed = round(totals[key], 2)
        drifted = abs((row.get(target) or 0) - recomputed) > tolerance
        row[f"{target}_was_corrected"] = drifted
        row[target] = recomputed
        corrected += drifted
    return rows, [], corrected


def rule_recompute_items_count(rows, ctx, target=None, from_table=None,
                               join_on=None, keep_original_as=None, **_):
    _, counts = _rollup(ctx, from_table, join_on, "count(*)")
    corrected = 0
    for row in rows:
        # cast_types has already populated keep_original_as as an integer.
        # Overwriting it from `target` would clobber that with the raw CSV
        # string, and every subsequent comparison would be str != int -- which
        # reports drift on every row and inflates the corrected count past the
        # row count.
        reported = row.get(keep_original_as) if keep_original_as else row.get(target)
        if isinstance(reported, str):
            reported = int(reported) if reported.strip().isdigit() else None

        key = row.get(join_on)
        if key not in counts:
            continue
        actual = counts[key]
        drifted = reported is not None and reported != actual
        row[target] = actual
        corrected += drifted
    return rows, [], corrected


def rule_mask_pii(rows, ctx, **_):
    if not ctx.get("apply_masking"):
        return rows, [], 0
    for row in rows:
        if row.get("email"):
            local, _, domain = str(row["email"]).partition("@")
            row["email"] = f"{local[:2]}***@{domain}"
        if row.get("phone"):
            row["phone"] = f"***-***-{str(row['phone'])[-4:]}"
    return rows, [], 0


def rule_add_record_hash(rows, ctx, exclude=None, **_):
    excluded = set(exclude or []) | {"_processed_at", "_load_id", "_record_hash"}
    for row in rows:
        fields = sorted(k for k in row if k not in excluded)
        payload = "||".join(str(row.get(k, "<null>")) for k in fields)
        row["_record_hash"] = hashlib.sha256(payload.encode()).hexdigest()
    return rows, [], 0


REGISTRY = {
    "cast_types": rule_cast_types,
    "apply_transforms": rule_apply_transforms,
    "default_nulls": rule_default_nulls,
    "drop_null_business_key": rule_drop_null_business_key,
    "drop_null_required": rule_drop_null_required,
    "deduplicate": rule_deduplicate,
    "enforce_allowed_values": rule_enforce_allowed_values,
    "validate_positive": rule_validate_positive,
    "quarantine_invalid_price": rule_quarantine_invalid_price,
    "quarantine_negative_total": rule_quarantine_negative_total,
    "enforce_referential_integrity": rule_enforce_referential_integrity,
    "recompute_subtotal": rule_recompute_subtotal,
    "recompute_total_from_lines": rule_recompute_total_from_lines,
    "recompute_items_count": rule_recompute_items_count,
    "mask_pii": rule_mask_pii,
    "add_record_hash": rule_add_record_hash,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def resolve_params(rule: dict, table: dict) -> dict:
    """Mirror the generator's column-sourced parameter bridge."""
    params = dict(rule.get("params", {}))
    if rule["fn"] == "enforce_allowed_values" and "values" not in params:
        for column in table.get("columns", []):
            if column.get("target") == params.get("column") and "allowed_values" in column:
                params["values"] = column["allowed_values"]
                break
    return params


def run(project: Path, verbose: bool) -> int:
    specs = project / "specs"
    platform = yaml.safe_load((specs / "00-platform.yaml").read_text(encoding="utf-8"))
    sources = yaml.safe_load((specs / "02-sources.yaml").read_text(encoding="utf-8"))
    bronze_spec = yaml.safe_load((specs / "mappings" / "bronze.yaml").read_text(encoding="utf-8"))
    silver_spec = yaml.safe_load((specs / "mappings" / "silver.yaml").read_text(encoding="utf-8"))
    dq_spec = yaml.safe_load((specs / "04-data-quality.yaml").read_text(encoding="utf-8"))

    source = sources["sources"][0]
    landing = (project / source["connection"]["location_dev"].lstrip("./")).resolve()

    print(f"Dry run: {platform['metadata']['name']}")
    print(f"Landing: {landing}")
    print()

    tables: dict[str, list[Row]] = {}
    ctx = {"tables": tables, "apply_masking": False}
    failures = 0

    # ---- bronze: land verbatim ------------------------------------------
    print("BRONZE  (landing -- no transformation)")
    entity_by_name = {e["name"]: e for e in source["entities"]}
    for mapping in bronze_spec["tables"]:
        entity = entity_by_name[mapping["source_entity"].split(".", 1)[1]]
        path = landing / entity["file_pattern"]
        if not path.exists():
            print(f"  MISSING  {path.name}")
            failures += 1
            continue
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            actual_header = list(reader.fieldnames or [])
            rows = list(reader)

        # The bronze notebook applies the registry schema POSITIONALLY, so a
        # column-order mismatch loads values into the wrong columns instead of
        # failing -- and stays invisible whenever the swapped columns share a
        # type. Checking order here catches it before it reaches Fabric.
        expected_header = [c["name"] for c in entity["columns"]]
        if actual_header != expected_header:
            print(f"  HEADER MISMATCH  {entity['file_pattern']}")
            print(f"      registry: {expected_header}")
            print(f"      file:     {actual_header}")
            failures += 1

        for row in rows:
            row["_source"] = source["name"]
            row["_load_id"] = "dryrun"
        tables[mapping["target"]] = rows
        print(f"  {mapping['target']:<32} {len(rows):>8,} rows")

    # ---- silver: cleanse -------------------------------------------------
    print()
    print("SILVER  (cleansing -- rules applied in spec order)")
    summary = []
    for table in silver_spec["tables"]:
        target, source_table = table["target"], table["source"]
        rows = [dict(r) for r in tables.get(source_table, [])]
        rows_in = len(rows)
        quarantine: list[Row] = []
        corrected_total = 0

        for rule in table.get("rules", []):
            fn = REGISTRY.get(rule["fn"])
            if fn is None:
                print(f"  {target}: no dry-run implementation for {rule['fn']}")
                failures += 1
                continue
            params = resolve_params(rule, table)
            if rule["fn"] in ("cast_types", "apply_transforms"):
                params = {"columns": table.get("columns", [])}
            rows, rejected, corrected = fn(rows, ctx, **params)
            quarantine.extend(rejected)
            corrected_total += corrected
            if verbose and (rejected or corrected):
                print(f"      {rule['fn']:<32} "
                      f"rejected {len(rejected):>6,}  corrected {corrected:>6,}")

        tables[target] = rows
        tables[f"{target}_quarantine"] = quarantine

        # Reconciliation: SILVER-RECON-003
        accounted = len(rows) + len(quarantine)
        status = "OK" if accounted == rows_in else "ROW LOSS"
        if accounted != rows_in:
            failures += 1

        print(f"  {target:<24} {rows_in:>8,} in -> {len(rows):>8,} kept  "
              f"{len(quarantine):>6,} quarantined  {corrected_total:>6,} corrected  [{status}]")
        summary.append((target, rows_in, len(rows), len(quarantine), corrected_total))

    # ---- reconciliation --------------------------------------------------
    print()
    print("RECONCILIATION")
    orders = tables.get("fct_orders", [])
    items = tables.get("fct_order_items", [])

    order_ids = {o["order_id"] for o in orders}
    orphan_lines = sum(1 for i in items if i["order_id"] not in order_ids)
    print(f"  SILVER-RECON-001  order lines without a surviving order: {orphan_lines:,}")

    line_totals: dict[str, float] = defaultdict(float)
    for item in items:
        if item["order_id"] in order_ids:
            line_totals[item["order_id"]] += item.get("subtotal") or 0
    mismatched = sum(
        1 for o in orders
        if abs((o.get("order_total") or 0) - round(line_totals.get(o["order_id"], 0), 2)) > 0.01
    )
    print(f"  SILVER-RECON-002  headers disagreeing with their lines: {mismatched:,}")
    if mismatched:
        failures += 1

    unaccounted = sum(1 for t, i, k, q, _ in summary if i != k + q)
    print(f"  SILVER-RECON-003  tables losing rows: {unaccounted:,}")

    # ---- data quality thresholds ----------------------------------------
    print()
    print("DATA QUALITY  (output checks, thresholds from 04-data-quality.yaml)")
    checked = breached = 0
    for expectation in dq_spec.get("expectations", []):
        rows = tables.get(expectation["table"])
        if rows is None:
            continue
        for check in expectation.get("checks", []):
            if check.get("measured_on") != "output":
                continue
            observed = evaluate(check, rows, tables)
            if observed is None:
                continue
            checked += 1
            limit = check.get("fail_above")
            if limit is not None and observed > limit:
                breached += 1
                failures += 1
                print(f"  FAIL  {check['id']:<14} observed {observed:.4f} > {limit}")
            elif verbose:
                print(f"  pass  {check['id']:<14} observed {observed:.4f}")
    print(f"  {checked} output checks evaluated, {breached} breached")

    # ---- gold headline ---------------------------------------------------
    print()
    print("GOLD  (revenue recognition applied)")
    revenue_orders = {o["order_id"] for o in orders if o.get("is_revenue")}
    revenue = sum(i.get("subtotal") or 0 for i in items if i["order_id"] in revenue_orders)
    by_year: dict[str, float] = defaultdict(float)
    order_year = {o["order_id"]: str(o.get("order_date") or "")[:4] for o in orders}
    for item in items:
        if item["order_id"] in revenue_orders:
            by_year[order_year.get(item["order_id"], "?")] += item.get("subtotal") or 0

    print(f"  recognised revenue        {revenue:>18,.2f}")
    print(f"  revenue-bearing orders    {len(revenue_orders):>18,}")
    for year in sorted(by_year):
        print(f"    {year}                    {by_year[year]:>18,.2f}")

    print()
    if failures:
        print(f"DRY RUN FAILED -- {failures} problem(s)")
    else:
        print("DRY RUN PASSED -- specs execute cleanly against the data")
    return 1 if failures else 0


def evaluate(check: dict, rows: list[Row], tables: dict) -> float | None:
    """Measure one expectation against the produced rows."""
    if not rows:
        return None
    total = len(rows)
    rule = check["rule"]

    if rule == "not_null":
        return sum(1 for r in rows if _is_blank(r.get(check["column"]))) / total
    if rule == "unique":
        columns = check["columns"]
        seen = {tuple(r.get(c) for c in columns) for r in rows}
        return 1 - (len(seen) / total)
    if rule == "accepted_values":
        return sum(1 for r in rows if r.get(check["column"]) not in check["values"]) / total
    if rule == "range":
        low, high = check.get("min"), check.get("max")
        bad = 0
        for row in rows:
            value = row.get(check["column"])
            if value is None or (low is not None and value < low) or (high is not None and value > high):
                bad += 1
        return bad / total
    if rule == "referential_integrity":
        table, column = check["references"].rsplit(".", 1)
        parents = {r.get(column) for r in tables.get(table, [])}
        return sum(1 for r in rows if r.get(check["column"]) not in parents) / total
    if rule == "arithmetic_consistency" and "quantity * unit_price" in check.get("expression", ""):
        bad = sum(
            1 for r in rows
            if r.get("quantity") is not None and r.get("unit_price") is not None
            and abs((r.get("subtotal") or 0) - round(r["quantity"] * r["unit_price"], 2)) > 0.01
        )
        return bad / total
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    return run(Path(args.project).resolve(), args.verbose)


if __name__ == "__main__":
    sys.exit(main())
