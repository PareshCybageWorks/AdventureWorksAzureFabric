# Lakehouse & Warehouse Topology

How to decide which storage items a Fabric workspace gets, and what to call them.

This is the one structural decision that differs between medallion and data mesh.
Get it right at the start and switching topology later is a one-line spec change;
get it wrong and it is a migration.

---

## The rule

**Split storage items along the axis that owns them.**

| Topology | Unit of ownership | Items | Names |
|---|---|---|---|
| **Medallion** | the *layer* | one per layer | `lh_bronze`, `lh_silver`, `wh_gold` |
| **Data mesh** | the *domain* | one per domain | `wh_sales`, `wh_scm`, `wh_finance` |

Everything below is the reasoning. If you only remember one thing, remember that
the split follows ownership, not convenience.

---

## Medallion — split by layer

```
AgenticAIDemo_dev/
├── lh_bronze     Files/ + Tables/   raw landing, immutable
├── lh_silver     Tables/            cleansed, conformed
└── wh_gold       dbo.* + bi.*       star schema, reporting views
```

### Why per layer rather than one lakehouse for all three

The three layers have genuinely different operational needs, and an item is the
smallest unit most Fabric settings apply to:

- **Retention.** Bronze keeps two years of immutable history; silver is fully
  rebuildable and keeps one. One item forces one retention policy onto both.
- **Permissions.** Plenty of people should read gold. Almost nobody should read
  bronze, which holds unmasked PII and every upstream defect. Item-level access
  makes that a configuration rather than a convention.
- **Blast radius.** A bad silver rebuild that targets the wrong table cannot
  touch bronze if bronze is a different item.
- **Cost attribution.** Storage growth per layer is visible without tagging.

The cost is that a notebook reading across layers must qualify the table with
its item — `spark.read.table("lh_bronze.bronze_commerce_orders")` — and both
lakehouses must be attached to the notebook. Set the item the notebook *writes*
to as the default, so an unqualified write cannot land in the wrong place.

### Why gold is a Warehouse, not a Lakehouse

Choose `wh_gold` when gold needs:
- real T-SQL schemas (`dbo` for physical tables, `bi` for reporting views)
- views, which are how a report gets reshaped without migrating a table
- a SQL surface for tools that speak T-SQL rather than Spark

Choose `lh_gold` instead only when gold is consumed exclusively by Spark and no
SQL surface is required. That is rarer than it sounds — Power BI, Excel and most
BI tools want SQL.

Writing to a Warehouse from Spark needs the Fabric DW Spark connector. **Validate
that on your capacity before committing the design.** If it proves unreliable,
the fallback is to materialise gold as Delta in a lakehouse and have the
warehouse expose `dbo` over it — set `storage.gold.write_mode` accordingly.

---

## Data mesh — split by domain

```
SalesDemo_dev/       wh_sales     bronze + silver + gold for sales
SCMDemo_dev/         wh_scm       bronze + silver + gold for supply chain
FinanceDemo_dev/     wh_finance   bronze + silver + gold for finance
```

Under mesh the domain team owns its pipeline end to end and publishes a data
product. Splitting by layer here would cut *across* team boundaries and put
three teams inside every item — the opposite of what mesh is for.

Domains consume each other's gold through **OneLake shortcuts**, never by
reaching into another domain's silver. A shortcut to gold is a published
contract; a read from someone else's silver is a hidden coupling that breaks the
first time they refactor.

### When mesh is actually warranted

Mesh pays for its overhead only when **all** of these hold:

- more than one team, each owning a domain end to end
- independent release cadences — one domain shipping should not wait on another
- a platform team bottleneck that decentralised ownership would relieve

One team building a first framework meets none of them. Start medallion.

---

## Making the switch cheap

Keep `domain` a first-class field in every spec from day one, even with a single
domain. Then switching is one line:

```yaml
topology:
  pattern: medallion    # -> mesh
```

The generators fan the same domain definitions across more workspaces. Without
that field, switching means rewriting every mapping.

---

## Spec shape

```yaml
storage:
  items:
    medallion:
      bronze: { item: lakehouse, name: "lh_bronze" }
      silver: { item: lakehouse, name: "lh_silver" }
      gold:   { item: warehouse, name: "wh_gold" }
    mesh:
      per_domain: { item: warehouse, name: "wh_{domain}" }
```

Generators resolve names through this block — never by string literal. A
notebook that hard-codes `lh_bronze` cannot be promoted to an environment that
named it differently.

---

## Naming

| Item | Pattern | Example |
|---|---|---|
| Lakehouse | `lh_{layer}` or `lh_{domain}` | `lh_bronze`, `lh_sales` |
| Warehouse | `wh_{layer}` or `wh_{domain}` | `wh_gold`, `wh_scm` |
| Notebook | `nb_{verb}_{entity}` | `nb_load_customers` |
| Pipeline | `p_{verb}_{layer}` | `p_load_bronze` |

Use a verb per layer — `load` / `clean` / `build` — so an item name says what it
*does*. The layer is already carried by the storage item and the workspace
folder, so repeating it gives `nb_bronze_bronze_commerce_orders`.

---

## Workspace folders

Fabric workspace folders organise *items*; OneLake paths organise *files inside
a lakehouse*. Both exist and conflating them is the usual source of confusion.

```
1_bronze     nb_load_*     p_load_bronze
2_silver     nb_clean_*    p_clean_silver
3_gold       nb_build_*    p_build_gold
4_master                   p_orchestrate_master
0_config     env_spark
5_datastore  lh_bronze, lh_silver, wh_gold
```

Numeric prefixes force medallion order in a UI that sorts alphabetically, so the
workspace reads top-to-bottom in the direction data flows.

**Automation limit:** the Fabric REST surface has no parameter for creating an
item *into* a folder, and no folder-management endpoint. Items are created at the
workspace root and moved in the UI. Treat the spec's folder block as declared
intent a human applies, and a mismatch as drift to correct.

---

## Checklist

- [ ] Topology chosen deliberately, with the reasoning written down
- [ ] `domain` present in every spec, even with one domain
- [ ] Storage items named from the spec, never hard-coded in a notebook
- [ ] Gold's Warehouse-vs-Lakehouse choice justified by whether SQL is needed
- [ ] Spark→Warehouse write path validated on the target capacity
- [ ] Cross-item reads qualified; the write target is the notebook's default
- [ ] Workspace folders declared, with the manual-move limitation noted

## Related

- `provisioning.md` — creating workspaces and items
- `lakehouse-config.md` — medallion folder layout inside a lakehouse
- `sql-analytics.md` — SQL endpoint and T-SQL access to gold
- `../../framework/generators/validate_specs.py` — enforces these rules in CI
