"""
Sample data generator for the commerce domain.

Framework tool. Emits a synthetic OLTP export that a project can land in its
bronze layer. The schema matches the commerce source system exactly:

    customers    customer_id, first_name, last_name, email, phone, region,
                 created_at, updated_at
    products     product_id, product_name, category, price, stock,
                 created_at, updated_at
    orders       order_id, customer_id, order_date, total_price, status,
                 items_count, created_at, updated_at
    order_items  order_item_id, order_id, product_id, quantity, unit_price,
                 subtotal, created_at

Two properties make this useful for testing a data platform rather than just
filling tables:

1. The clean baseline is arithmetically correct.
       subtotal    == quantity * unit_price
       total_price == sum(subtotal) over the order's items
       items_count == number of items on the order
   Defects are injected *after* this baseline is computed, so every row that
   violates a rule violates it because it was deliberately corrupted.

2. Every injected defect is counted and written to defect_manifest.json.
   Data-quality thresholds in the project spec are calibrated from that
   manifest instead of being guessed, and a regression in the cleansing logic
   shows up as a count mismatch rather than a vague drift.

Usage:
    python generate_sample_data.py --out <dir> [--seed 42] [--scale 1.0]

Stdlib only -- no install step.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------
# Volume and window
# --------------------------------------------------------------------------

BASE_CUSTOMERS = 2_000
BASE_PRODUCTS = 300
BASE_ORDERS = 25_000

# Two full years so year-over-year measures have a prior-year comparison,
# ending mid-year so year-to-date is visibly in flight.
DATE_START = date(2024, 8, 1)
DATE_END = date(2026, 7, 25)

# --------------------------------------------------------------------------
# Defect injection rates
# --------------------------------------------------------------------------
# Deliberately low. A rule that fails on every row proves nothing -- it reads
# as broken data rather than as governance working. These rates put each rule
# in the range where a threshold is a real decision.

DEFECTS = {
    "customer_duplicate":      0.030,   # retried load re-delivered records
    "customer_null_email":     0.015,   # upstream omitted the field
    "customer_null_phone":     0.020,
    "product_invalid_price":   0.020,   # zero or negative list price
    "product_null_category":   0.030,
    "order_duplicate":         0.025,   # at-least-once event delivery
    "order_orphan_customer":   0.010,   # customer purged upstream
    "order_null_date":         0.008,   # malformed timestamp
    "order_negative_total":    0.005,   # refund written as a raw negative
    "item_subtotal_mismatch":  0.020,   # unit price changed after the write
    "order_total_mismatch":    0.015,   # header not recomputed after edit
}

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

# Regions are deliberately unequal. A flat regional split looks synthetic and
# gives a reader nothing to react to.
REGIONS = ["North", "South", "East", "West", "Central"]
REGION_MIX = [0.30, 0.25, 0.18, 0.15, 0.12]     # share of customer base
REGION_ORDER_WEIGHT = {                          # relative order frequency
    "North": 3.2, "South": 2.1, "East": 1.4, "West": 1.1, "Central": 1.0,
}
REGION_BASKET = {                                # units per line (min, max)
    "North": (3, 9), "South": (2, 6), "East": (1, 4),
    "West": (1, 3), "Central": (1, 3),
}

CATEGORIES = [
    "Electronics", "Computers", "Networking", "Storage",
    "Audio", "Peripherals", "Accessories", "Software",
]

PRODUCT_NOUNS = [
    "UltraBook", "ProDock", "VisionPanel", "SwiftDrive", "EdgeRouter",
    "ClearAudio", "MeshCam", "PowerHub", "QuietKey", "RapidSSD",
    "CoreSwitch", "FlexStand", "SecureVault", "StreamDeck", "NanoMouse",
]
PRODUCT_QUALIFIERS = ["14", "16", "Pro", "Max", "Lite", "X2", "S", "Elite", "Air"]

FIRST_NAMES = [
    "Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Meera", "Arjun", "Divya",
    "James", "Sarah", "Michael", "Emily", "David", "Laura", "Daniel", "Nina",
    "Lukas", "Sofia", "Chen", "Mei", "Omar", "Fatima", "Tom", "Grace",
    "Lisa", "Emma", "Noah", "Olivia", "Liam", "Ava",
]
LAST_NAMES = [
    "Sharma", "Patel", "Reddy", "Nair", "Iyer", "Kulkarni", "Desai", "Rao",
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis",
    "Weber", "Schmidt", "Rossi", "Tan", "Lim", "Khan", "Ahmed", "Clark",
]

# Only these statuses represent recognised revenue. `cancelled` and `pending`
# are excluded by the silver layer -- the split is what makes that rule
# meaningful rather than cosmetic.
STATUS_MIX = {
    "delivered": 0.38,
    "shipped": 0.22,
    "confirmed": 0.18,
    "pending": 0.13,
    "cancelled": 0.09,
}
REVENUE_STATUSES = {"delivered", "shipped", "confirmed"}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def ts(d: date, rng: random.Random) -> str:
    """Timestamp in the source system's format: ISO-8601 microseconds, Z."""
    moment = datetime(
        d.year, d.month, d.day,
        rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59),
        rng.randint(0, 999_999),
    )
    return moment.isoformat() + "Z"


def write_csv(path: Path, rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


class DefectLog:
    """Counts every corruption applied, so thresholds can be derived."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = defaultdict(int)

    def record(self, kind: str, n: int = 1) -> None:
        self.counts[kind] += n

    def hit(self, rng: random.Random, kind: str) -> bool:
        """True when this row should receive the named defect."""
        if rng.random() < DEFECTS[kind]:
            self.record(kind)
            return True
        return False


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def make_customers(
    rng: random.Random, log: DefectLog, n: int
) -> tuple[list[dict], dict[str, str]]:
    rows: list[dict] = []
    region_of: dict[str, str] = {}
    span = (DATE_END - DATE_START).days

    for i in range(1, n + 1):
        cid = f"CUST_{i:06d}"
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        region = rng.choices(REGIONS, weights=REGION_MIX, k=1)[0]
        region_of[cid] = region

        created = DATE_START + timedelta(days=rng.randint(0, span))
        updated = created + timedelta(days=rng.randint(0, max(1, (DATE_END - created).days)))

        email = f"{first.lower()}.{last.lower()}{i}@example.com"
        phone = f"{rng.randint(200, 999)}-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"

        rows.append({
            "customer_id": cid,
            "first_name": first,
            "last_name": last,
            "email": "" if log.hit(rng, "customer_null_email") else email,
            "phone": "" if log.hit(rng, "customer_null_phone") else phone,
            "region": region,
            "created_at": ts(created, rng),
            "updated_at": ts(updated, rng),
        })

    # A retried load re-delivered a slice of records verbatim.
    dupes = [dict(r) for r in rows if rng.random() < DEFECTS["customer_duplicate"]]
    log.record("customer_duplicate", len(dupes))
    rows.extend(dupes)
    rng.shuffle(rows)

    return rows, region_of


def make_products(rng: random.Random, log: DefectLog, n: int) -> tuple[list[dict], dict[str, float]]:
    rows: list[dict] = []
    price_of: dict[str, float] = {}
    span = (DATE_END - DATE_START).days

    for i in range(1, n + 1):
        pid = f"PROD_{i:06d}"
        true_price = round(rng.uniform(25, 2400), 2)
        price_of[pid] = true_price

        created = DATE_START + timedelta(days=rng.randint(0, span))
        updated = created + timedelta(days=rng.randint(0, max(1, (DATE_END - created).days)))

        # A corrupted list price does not change what the line actually sold
        # for -- order_items keeps the true price, which is exactly why the
        # silver layer drops the product row rather than the sales.
        listed = true_price
        if log.hit(rng, "product_invalid_price"):
            listed = round(rng.choice([0.0, -true_price]), 2)

        rows.append({
            "product_id": pid,
            "product_name": f"{rng.choice(PRODUCT_NOUNS)} {rng.choice(PRODUCT_QUALIFIERS)}",
            "category": "" if log.hit(rng, "product_null_category") else rng.choice(CATEGORIES),
            "price": listed,
            "stock": rng.randint(0, 900),
            "created_at": ts(created, rng),
            "updated_at": ts(updated, rng),
        })

    return rows, price_of


def make_orders_and_items(
    rng: random.Random,
    log: DefectLog,
    n_orders: int,
    region_of: dict[str, str],
    price_of: dict[str, float],
) -> tuple[list[dict], list[dict]]:
    orders: list[dict] = []
    items: list[dict] = []
    span = (DATE_END - DATE_START).days

    customer_ids = list(region_of)
    weights = [REGION_ORDER_WEIGHT[region_of[c]] for c in customer_ids]
    product_ids = list(price_of)

    statuses = list(STATUS_MIX)
    status_weights = list(STATUS_MIX.values())

    item_seq = 0

    for i in range(1, n_orders + 1):
        oid = f"ORD_{i:08d}"
        customer_id = rng.choices(customer_ids, weights=weights, k=1)[0]
        region = region_of[customer_id]

        # Weight recent months more heavily so the trend slopes upward.
        order_day = DATE_START + timedelta(days=int((rng.random() ** 0.7) * span))

        # ---- build the lines, arithmetically correct ---------------------
        n_lines = rng.randint(1, 5)
        lo, hi = REGION_BASKET[region]
        order_lines: list[dict] = []

        for _ in range(n_lines):
            item_seq += 1
            product_id = rng.choice(product_ids)
            quantity = rng.randint(lo, hi)
            unit_price = price_of[product_id]
            subtotal = round(quantity * unit_price, 2)

            line = {
                "order_item_id": f"ITEM_{item_seq:010d}",
                "order_id": oid,
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": unit_price,
                "subtotal": subtotal,
                "created_at": ts(order_day, rng),
            }

            # Corrupt the line total only. quantity and unit_price stay
            # truthful, so the reconciliation rule has something to detect.
            if log.hit(rng, "item_subtotal_mismatch"):
                line["subtotal"] = round(subtotal * rng.uniform(0.4, 1.9), 2)

            order_lines.append(line)

        items.extend(order_lines)

        # ---- header, derived from the lines ------------------------------
        total_price = round(sum(l["subtotal"] for l in order_lines), 2)
        items_count = len(order_lines)

        if log.hit(rng, "order_total_mismatch"):
            total_price = round(total_price * rng.uniform(0.5, 1.6), 2)
        if log.hit(rng, "order_negative_total"):
            total_price = -abs(total_price)

        if log.hit(rng, "order_orphan_customer"):
            # Customer purged upstream; the order row survived.
            customer_id = f"CUST_{rng.randint(900_001, 999_999):06d}"

        created = order_day
        updated = created + timedelta(days=rng.randint(0, 30))

        orders.append({
            "order_id": oid,
            "customer_id": customer_id,
            "order_date": "" if log.hit(rng, "order_null_date") else ts(order_day, rng),
            "total_price": total_price,
            "status": rng.choices(statuses, weights=status_weights, k=1)[0],
            "items_count": items_count,
            "created_at": ts(created, rng),
            "updated_at": ts(updated, rng),
        })

    # At-least-once delivery from the event stream.
    dupes = [dict(o) for o in orders if rng.random() < DEFECTS["order_duplicate"]]
    log.record("order_duplicate", len(dupes))
    orders.extend(dupes)
    rng.shuffle(orders)

    return orders, items


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG seed; fixed by default so runs are reproducible")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="multiplier on row volumes (0.1 = small, 4.0 = large)")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    log = DefectLog()
    out = Path(args.out)

    n_customers = int(BASE_CUSTOMERS * args.scale)
    n_products = int(BASE_PRODUCTS * args.scale)
    n_orders = int(BASE_ORDERS * args.scale)

    customers, region_of = make_customers(rng, log, n_customers)
    products, price_of = make_products(rng, log, n_products)
    orders, items = make_orders_and_items(rng, log, n_orders, region_of, price_of)

    write_csv(out / "customers.csv", customers)
    write_csv(out / "products.csv", products)
    write_csv(out / "orders.csv", orders)
    write_csv(out / "order_items.csv", items)

    # ---- defect manifest -------------------------------------------------
    # The project's data-quality spec calibrates its thresholds from this.
    manifest = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "seed": args.seed,
        "scale": args.scale,
        "date_range": {"start": DATE_START.isoformat(), "end": DATE_END.isoformat()},
        "row_counts": {
            "customers": len(customers),
            "products": len(products),
            "orders": len(orders),
            "order_items": len(items),
        },
        "configured_rates": DEFECTS,
        "injected_defects": dict(sorted(log.counts.items())),
        "revenue_statuses": sorted(REVENUE_STATUSES),
        "notes": (
            "Every row not listed under injected_defects is arithmetically "
            "consistent: subtotal == quantity * unit_price, total_price == "
            "sum(subtotal), items_count == line count."
        ),
    }
    (out / "defect_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    # ---- summary ---------------------------------------------------------
    print(f"Wrote commerce sample data to {out}")
    print()
    print(f"  customers    {len(customers):>8,}")
    print(f"  products     {len(products):>8,}")
    print(f"  orders       {len(orders):>8,}")
    print(f"  order_items  {len(items):>8,}")
    print()
    print("  Injected defects (thresholds calibrate from these):")
    for kind in sorted(log.counts):
        print(f"    {kind:<26} {log.counts[kind]:>7,}")


if __name__ == "__main__":
    main()
