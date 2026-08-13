# D1 — DataOps Monitoring

Produce `<project>/dataops/01-monitoring.yaml`: what "healthy" means for each
table, and what happens when it is not.

Contract: `framework/contracts/dataops/01-monitoring.schema.json` — authoritative.
Template: `framework/templates/dataops/01-monitoring.yaml`.
Rules: `framework/ttfabric/monitoring.py` — the evaluators are the authority on
which rules exist.

**Depends on F2–F5.** Upstream tables and expected columns are resolved from the
source registry and the layer mappings, so they are not restated here. A second
copy of a column list is a copy that goes stale, and the failure mode is a
`schema_match` that passes against last year's schema.

---

## The distinction that matters

The pipeline **already** writes a DQ log as it runs — that is F4's job, and it
records what cleansing did to the rows passing through.

This stage is the other half: it reads the tables **afterwards** and asks
whether they are still within their declared limits. That is what catches a
slow upstream drift, which no in-flight check will ever see because each
individual run looks fine.

---

## Ask the user

1. **What does "wrong" look like for this table?** Not "what could we check" —
   what would actually mean something is broken.
2. **Where did each threshold come from?** Put it in `note`. A threshold with no
   stated basis gets loosened the first time it fires, because nobody can argue
   for it.
3. **What should stop a release, per environment?** Blocking dev stops work;
   blocking prod stops the business. They should rarely be the same.
4. **Who is alerted, and how?** Destinations are references — `${ENV}` by
   default, `keyvault://…` where that is set up — never literal webhooks.

---

## Derive

- **Calibrate against real data.** If a defect manifest or profiling run exists,
  set thresholds from it and record the arithmetic in `note` — e.g.
  `29/2048 = 1.42%`, so `warn_above: 0.022` has a visible justification.
- **`measured_on: input` for anything calibrated on raw data.** A rule counting
  a defect is meaningless against the table cleansing already fixed.

  **It also needs the SOURCE column name.** `measured_on: input` runs against
  the upstream table, where silver has not yet renamed anything — so a check on
  silver's `list_price` must say `price`, the name bronze uses. Getting this
  wrong fails at run time with an unresolved-column error that looks like
  missing data. `validate.py` resolves it against the silver mapping, which
  declares both names.
- **Set `fail_above: 0.0` for rules that should never fire at all** —
  referential integrity, accepted values. Zero tolerance is a statement, not
  laziness.

- **Use `reconciliation`, not `arithmetic_consistency`, whenever two tables are
  involved.** `arithmetic_consistency` evaluates a single DataFrame, so an
  expression like `sum(line_revenue) = silver.sum(subtotal)` is not something it
  can do — it fails at run time with an unresolved-name error that reads like a
  data problem.

  ```yaml
  # grand totals: measure is the RELATIVE difference
  - id: GD-SALES-005
    rule: reconciliation
    severity: critical
    fail_above: 0.0            # gold must tie back to silver exactly
    left:  { table: dbo.fct_sales,   expression: sum(line_revenue) }
    right: { table: stg_order_items, expression: sum(subtotal) }

  # per key: measure is the SHARE of keys that disagree
  - id: SL-ORD-006
    rule: reconciliation
    severity: warning
    warn_above: 0.021
    fail_above: 0.04
    tolerance: 0.01
    left:  { table: stg_orders,      expression: sum(order_total), key: order_id }
    right: { table: stg_order_items, expression: sum(subtotal),    key: order_id }
  ```

  A layer-to-layer reconciliation is the check that catches value being lost or
  duplicated in transit, which no single-table rule can see. Both sides must
  agree on whether a key is used — grouping one side and not the other compares
  a per-key total against a grand total.

---

## Units — the one thing to get right

| Field | Unit |
|---|---|
| `warn_above`, `fail_above` | **fraction** — `0.022` is 2.2% |
| `max_increase_pct`, `max_decrease_pct` | **percent** — `50` is 50% |
| `max_age_hours` | hours |

The mixture is unfortunate but deliberate: shares are fractions because that is
what the measurements produce, and count deltas are percent because that is how
people discuss them. Both the contract and `verdict()` reject a share above 1.0,
because a threshold of `2.2` meaning 2.2% can never be exceeded — the check
would sit there looking like coverage while measuring nothing.

---

## Gate — refuse to proceed if

- **A share rule has neither `warn_above` nor `fail_above`.** It measures
  something and compares it to nothing.
- **`warn_above` is above `fail_above`.** It warns only after it has failed.
- **A rule has no evaluator** in `ttfabric/monitoring.py`.
- **`mode: block` with an empty `block_on`.** Nothing ever blocks, and the spec
  reads as though something does.
- **A severity is used that `severity_levels` does not define.**
- **Duplicate check ids** — they identify a row in the results table and a line
  in an alert.

---

## Then

```bash
python AzureFabricMCP/framework/tests/test_monitoring.py                    # verdict logic, no Spark
python AzureFabricMCP/framework/generators/validate.py --project <p> --track dataops
python AzureFabricMCP/framework/generators/generate_monitoring.py --specs <p> --out <p>/generated/notebooks
python AzureFabricMCP/framework/deploy/push_library.py --project <p> --env dev --wait   # if rules changed
python AzureFabricMCP/framework/deploy/push_items.py   --project <p> --env dev --create-missing
```

`push_library.py` is needed whenever `ttfabric/monitoring.py` changes — the
notebook imports it from the wheel on `env_spark`. Publishing takes ~4 minutes
because Fabric rebuilds the pool image.

Then run `nb_dq_monitor` and read the output. A monitor that reports everything
as passing on its first run against real data has usually not measured anything
— check for `skip`.
