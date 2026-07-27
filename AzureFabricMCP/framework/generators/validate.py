"""
Validate a project's specs against the framework contracts.

Two layers of checking, deliberately separated:

  SCHEMA     Each spec is validated against its JSON Schema in
             framework/contracts/<track>/<stage>.schema.json. Shape, types,
             required fields, enums and patterns. Declarative -- adding a new
             spec kind means adding a schema, not editing this file.

  SEMANTIC   Everything a schema cannot express: cross-spec references, layer
             prefix rules, defect coverage, YAML boolean keys. These are
             registered functions, and each one exists because the mistake it
             catches actually happened.

Discovery is by convention. A project spec at

    <project>/fabric/01-scaffolding.yaml

is validated against

    framework/contracts/fabric/01-scaffolding.schema.json

so a new stage needs no wiring here at all.

Usage:
    python validate.py --project ./01_demo-project
    python validate.py --project ./01_demo-project --track fabric
    python validate.py --project ./01_demo-project --strict
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

try:
    from jsonschema import Draft202012Validator
except ImportError:                                  # pragma: no cover
    Draft202012Validator = None

TRACKS = ("fabric", "powerbi", "dataops", "cicd")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

class Report:
    """Collects findings so one run surfaces every problem, not just the first."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.passed: list[str] = []

    def error(self, check: str, message: str) -> None:
        self.errors.append(f"{check}: {message}")

    def warn(self, check: str, message: str) -> None:
        self.warnings.append(f"{check}: {message}")

    def ok(self, check: str) -> None:
        self.passed.append(check)

    def render(self, strict: bool) -> int:
        for item in self.passed:
            print(f"  PASS   {item}")
        for item in self.warnings:
            print(f"  WARN   {item}")
        for item in self.errors:
            print(f"  ERROR  {item}")

        print()
        print(f"  {len(self.passed)} passed, {len(self.warnings)} warnings, "
              f"{len(self.errors)} errors")

        if self.errors:
            return 1
        if strict and self.warnings:
            print("  --strict: warnings treated as errors")
            return 1
        return 0


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_project(project: Path, tracks: tuple[str, ...]) -> dict[str, dict]:
    """Load every spec under the requested tracks, keyed 'track/stem'."""
    specs: dict[str, dict] = {}
    for track in tracks:
        directory = project / track
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            specs[f"{track}/{path.stem}"] = {
                "path": path,
                "doc": yaml.safe_load(path.read_text(encoding="utf-8")),
            }
    return specs


# ---------------------------------------------------------------------------
# Schema layer
# ---------------------------------------------------------------------------

def check_schemas(specs: dict, contracts: Path, report: Report) -> None:
    """Validate each spec against its contract, matched by path convention."""
    if Draft202012Validator is None:
        report.warn("schema", "jsonschema is not installed; schema checks skipped")
        return

    for key, entry in specs.items():
        schema_path = contracts / f"{key}.schema.json"
        if not schema_path.exists():
            # Absent contract is a warning, not an error: tracks are built out
            # one stage at a time, and an unwritten contract should not block a
            # project that is ahead of the framework.
            report.warn("schema", f"{key}: no contract at {schema_path.name}")
            continue

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        failures = sorted(validator.iter_errors(entry["doc"]), key=lambda e: e.path)

        if not failures:
            report.ok(f"schema: {key} conforms to its contract")
            continue

        for failure in failures[:12]:
            location = "/".join(str(p) for p in failure.absolute_path) or "<root>"
            report.error("schema", f"{key} at {location}: {failure.message}")
        if len(failures) > 12:
            report.error("schema", f"{key}: {len(failures) - 12} further violations")


# ---------------------------------------------------------------------------
# Semantic layer
# ---------------------------------------------------------------------------
# Registered checks. Each takes (specs, report) and each exists because the
# mistake it catches actually occurred.

def check_key_types(specs: dict, report: Report) -> None:
    """Every mapping key is a string.

    YAML 1.1 resolves bare `on`, `off`, `yes`, `no` to booleans, so a key
    written `on:` becomes True and nothing can look it up. Silent at parse
    time; surfaces as a KeyError deep in a generator.
    """
    def walk(node, path):
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if not isinstance(key, str):
                    found.append(f"{path} has non-string key {key!r}")
                found += walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found += walk(value, f"{path}[{index}]")
        return found

    offenders = [o for key, entry in specs.items() for o in walk(entry["doc"], key)]
    if offenders:
        for offender in offenders:
            report.error("key-types", offender)
        report.error("key-types",
                     "quote the key or rename it -- bare on/off/yes/no become booleans")
    else:
        report.ok("key-types: no keys coerced to booleans by the YAML parser")


def check_environment_ids(specs: dict, report: Report) -> None:
    """Provisioned environments carry a workspace id, and prod is marked."""
    scaffolding = specs.get("fabric/01-scaffolding", {}).get("doc")
    if not scaffolding:
        return

    environments = scaffolding.get("environments", [])
    unprovisioned = [e["name"] for e in environments if not e.get("workspace_id")]
    if unprovisioned:
        report.warn("environments",
                    f"no workspace_id recorded for: {unprovisioned} (not yet provisioned)")

    production = [e for e in environments if e.get("is_production")]
    if not production:
        report.error(
            "environments",
            "no environment marked is_production. Production is commonly the "
            "shortest, most innocuous name of the set, which is exactly why it "
            "must be flagged so destructive operations resolve it by id.",
        )
    elif len(production) > 1:
        report.error("environments",
                     f"more than one environment marked is_production: "
                     f"{[e['name'] for e in production]}")
    else:
        report.ok(f"environments: production identified ({production[0]['workspace']})")


def check_storage_topology(specs: dict, report: Report) -> None:
    """The active topology has storage items declared for it."""
    scaffolding = specs.get("fabric/01-scaffolding", {}).get("doc")
    if not scaffolding:
        return

    pattern = scaffolding["topology"]["pattern"]
    items = scaffolding["storage"]["items"]

    if pattern not in items:
        report.error("storage", f"topology is {pattern!r} but storage.items has no "
                                f"{pattern!r} block")
        return

    if pattern == "medallion":
        names = {layer: spec["name"] for layer, spec in items["medallion"].items()}
        if len(set(names.values())) != len(names):
            report.error(
                "storage",
                f"medallion topology maps layers to the same item: {names}. "
                f"Per-layer separation is the point -- retention, permissions "
                f"and blast radius differ per layer.",
            )
            return
        report.ok(f"storage: medallion items distinct per layer ({', '.join(sorted(names.values()))})")
    else:
        report.ok(f"storage: mesh item declared ({items['mesh']['per_domain']['name']})")


def check_capacity(specs: dict, report: Report) -> None:
    """Capacity is sized for Spark, and a trial is declared as such."""
    scaffolding = specs.get("fabric/01-scaffolding", {}).get("doc")
    if not scaffolding:
        return

    capacity = scaffolding["tenant"]["capacity"]

    # F2/F4 carry 2 and 4 capacity units. Spark does not refuse to run on them
    # -- it allocates a minimal pool and crawls, or dies on memory, with
    # nothing in the logs pointing at capacity. Naming the threshold is the
    # only way that failure becomes diagnosable in advance.
    TOO_SMALL = {"F2", "F4"}

    def assess(sku: str, label: str, active: bool) -> None:
        """Severity follows consequence: an unviable ACTIVE capacity breaks the
        pipeline now; an unviable declared fallback breaks it later.

        Both are worth saying, but only one should stop a build. A check that
        stays red for a future concern gets ignored, and takes the checks
        around it down with it.
        """
        sku = str(sku).upper()
        if sku not in TOO_SMALL:
            report.ok(f"capacity: {label} ({sku}) is viable for Spark")
            return

        message = (
            f"{label} is {sku} -- too few capacity units to run Spark usefully. "
            f"Notebooks crawl or fail on memory with no error explaining why. "
            f"F8 is the realistic floor for this pipeline."
        )
        if active:
            report.error("capacity", message)
        else:
            report.warn("capacity", message + " Becomes an error the moment it "
                                              "is promoted to default_sku.")

    assess(capacity.get("default_sku", ""), "primary capacity", active=True)

    fallback = capacity.get("fallback")
    if fallback:
        assess(fallback.get("sku", ""), "declared fallback", active=False)
    elif capacity.get("is_trial"):
        report.warn(
            "capacity",
            f"{capacity.get('default_sku')} is a trial with no `fallback` "
            f"declared. It expires and takes every environment's compute at "
            f"once -- decide the successor now, not on the day.",
        )


def check_secrets(specs: dict, report: Report) -> None:
    """No spec carries a literal secret."""
    suspicious = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            lowered = path.lower()
            if any(w in lowered for w in ("secret", "password", "key", "token")):
                # A reference or an env placeholder is exactly what we want.
                if node.startswith(("keyvault://", "${", "env:")) or node.endswith("_ref"):
                    return
                if len(node) > 24 and " " not in node:
                    suspicious.append(f"{path} looks like a literal secret")

    for key, entry in specs.items():
        walk(entry["doc"], key)

    if suspicious:
        for item in suspicious:
            report.error("secrets", item)
    else:
        report.ok("secrets: no literal credentials in any spec")


def check_source_domains(specs: dict, report: Report) -> None:
    """Every source names a domain the scaffolding declares.

    A schema validates one file at a time and cannot see across specs, so this
    is the layer that catches a source pointing at a domain that does not
    exist -- which would generate items into a workspace layout with no home
    for them.
    """
    scaffolding = specs.get("fabric/01-scaffolding", {}).get("doc")
    sources = specs.get("fabric/02-sources", {}).get("doc")
    if not scaffolding or not sources:
        return

    declared = {d["name"] for d in scaffolding.get("domains", [])}
    referenced = {s["domain"] for s in sources.get("sources", [])}

    unknown = referenced - declared
    if unknown:
        report.error("cross-ref",
                     f"sources reference domains absent from 01-scaffolding: "
                     f"{sorted(unknown)}. Declared: {sorted(declared)}")
    else:
        report.ok(f"cross-ref: all {len(referenced)} source domains are declared")

    # A domain listing a source that does not exist is the mirror error.
    source_names = {s["name"] for s in sources.get("sources", [])}
    for domain in scaffolding.get("domains", []):
        missing = set(domain.get("sources", [])) - source_names
        if missing:
            report.error("cross-ref",
                         f"domain {domain['name']!r} lists unregistered sources: "
                         f"{sorted(missing)}")


def check_watermarks(specs: dict, report: Report) -> None:
    """Incremental entities declare a usable watermark.

    The schema enforces presence; this checks the column actually exists on the
    entity. A watermark naming a column that is not there fails at run time,
    inside Spark, with a message about an unresolved column rather than about
    the load pattern.
    """
    sources = specs.get("fabric/02-sources", {}).get("doc")
    if not sources:
        return

    offenders = []
    incremental = 0
    for source in sources.get("sources", []):
        for entity in source.get("entities", []):
            if entity.get("load_pattern") != "incremental":
                continue
            incremental += 1
            watermark = entity.get("watermark_column")
            columns = {c["name"] for c in entity.get("columns", [])}
            if watermark not in columns:
                offenders.append(
                    f"{source['name']}.{entity['name']} watermark "
                    f"{watermark!r} is not one of its columns")

    if offenders:
        for offender in offenders:
            report.error("watermarks", offender)
    elif incremental:
        report.ok(f"watermarks: all {incremental} incremental entities resolve theirs")


def check_defect_handlers(specs: dict, report: Report) -> None:
    """Every known defect names a handler, and ids are unique.

    A defect recorded without a handler is a defect nobody deals with; a
    duplicate id makes coverage checking meaningless.
    """
    sources = specs.get("fabric/02-sources", {}).get("doc")
    if not sources:
        return

    issues = sources.get("known_issues", [])
    if not issues:
        report.warn("defects", "no known_issues declared -- upstream data is "
                               "rarely clean, so this usually means nobody asked")
        return

    ids = [i["id"] for i in issues]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        report.error("defects", f"duplicate known_issue ids: {sorted(duplicates)}")

    entities = {e["name"] for s in sources.get("sources", []) for e in s.get("entities", [])}
    unknown = [i["id"] for i in issues if i["entity"] not in entities]
    if unknown:
        report.error("defects", f"known_issues naming unregistered entities: {unknown}")

    if not duplicates and not unknown:
        report.ok(f"defects: {len(issues)} documented, each with a handler")


def check_rule_enum_fresh(specs: dict, report: Report, contracts: Path) -> None:
    """The silver contract's rule enum still matches the cleansing library.

    The enum is a generated copy of ttfabric REGISTRY, kept so editors can
    autocomplete and a typo fails at schema validation rather than inside
    Spark. A copy drifts, so this proves it has not: add a rule to the library
    without re-syncing and the build stops.
    """
    contract_path = contracts / "fabric" / "04-silver.schema.json"
    library = contracts.parent / "ttfabric" / "cleansing.py"
    if not contract_path.exists() or not library.exists():
        return

    import ast as _ast

    declared = json.loads(contract_path.read_text(encoding="utf-8"))
    enum = declared.get("$defs", {}).get("ruleName", {}).get("enum", [])

    registry: set[str] = set()
    tree = _ast.parse(library.read_text(encoding="utf-8"))
    for node in _ast.walk(tree):
        targets = node.targets if isinstance(node, _ast.Assign) else []
        for target in targets:
            if isinstance(target, _ast.Name) and target.id == "REGISTRY":
                if isinstance(node.value, _ast.Dict):
                    registry = {
                        k.value for k in node.value.keys
                        if isinstance(k, _ast.Constant) and isinstance(k.value, str)
                    }

    if not registry:
        return

    missing = sorted(registry - set(enum))
    extra = sorted(set(enum) - registry)
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"in the library but not the contract: {missing}")
        if extra:
            detail.append(f"in the contract but not the library: {extra}")
        report.error(
            "rule-enum",
            "the silver contract has drifted from ttfabric REGISTRY -- "
            + "; ".join(detail)
            + ". Run: python framework/generators/sync_contract_rules.py",
        )
    else:
        report.ok(f"rule-enum: contract matches the library ({len(registry)} rules)")


def check_view_dialect(specs: dict, report: Report) -> None:
    """Reporting views are T-SQL, not Spark SQL.

    The `bi` views execute inside the Warehouse. Spark constructs are rejected
    outright, and the error names the symbol rather than the dialect -- so
    `is_current = TRUE` surfaces as "Invalid column name 'TRUE'", which sends
    you looking for a column that was never the problem.

    Every entry here cost a deploy round-trip to discover.
    """
    import re as _re

    gold = specs.get("fabric/05-gold", {}).get("doc")
    if not gold:
        return

    SPARKISMS = [
        (r"CURRENT_DATE(?!\s*\()", "CURRENT_DATE", "CAST(GETDATE() AS DATE)"),
        (r"current_date\s*\(\)", "current_date()", "GETDATE()"),
        (r"=\s*(TRUE|FALSE)", "TRUE/FALSE literal", "1 / 0 -- T-SQL has no boolean literals"),
        (r"months_between\s*\(", "months_between()", "DATEDIFF(month, ...)"),
        (r"PERCENTILE_CONT[^)]*\)\s*WITHIN GROUP[^)]*\)\s*OVER\s*\(\s*\)",
         "PERCENTILE_CONT ... OVER ()", "NTILE(n) OVER (ORDER BY ...)"),
        (r"concat_ws\s*\(", "concat_ws()", "CONCAT_WS(...) -- check availability"),
        (r"F\.", "PySpark F. reference", "plain SQL"),
    ]

    offenders = []
    for view in gold.get("views", []):
        sql = view.get("sql", "")
        # Strip comments so guidance mentioning a construct is not flagged.
        body = _re.sub(r"--[^\n]*", "", sql)
        for pattern, found, instead in SPARKISMS:
            if _re.search(pattern, body, _re.IGNORECASE):
                offenders.append(f"{view['name']}: {found} is Spark SQL; use {instead}")

    if offenders:
        for offender in offenders:
            report.error("view-dialect", offender)
    elif gold.get("views"):
        report.ok(f"view-dialect: all {len(gold['views'])} bi views are T-SQL")


def check_rule_coverage(specs: dict, report: Report) -> None:
    """Every documented upstream defect is handled by a silver rule.

    A defect recorded in 02-sources but handled nowhere is a defect that
    reaches gold. The schema cannot see across the two files, so this does.
    """
    sources = specs.get("fabric/02-sources", {}).get("doc")
    silver = specs.get("fabric/04-silver", {}).get("doc")
    if not sources or not silver:
        return

    documented = {i["id"] for i in sources.get("known_issues", [])}
    handled = {
        rule["handles"]
        for table in silver.get("tables", [])
        for rule in table.get("rules", [])
        if rule.get("handles")
    }

    unhandled = sorted(documented - handled)
    phantom = sorted(handled - documented)

    if unhandled:
        report.error("coverage", f"documented defects no rule handles: {unhandled}")
    if phantom:
        report.warn("coverage", f"rules handling undocumented defect ids: {phantom}")
    if not unhandled and not phantom:
        report.ok(f"coverage: all {len(documented)} documented defects are handled")


def check_layer_chain(specs: dict, report: Report) -> None:
    """Each layer consumes only what the previous layer produces."""
    sources = specs.get("fabric/02-sources", {}).get("doc")
    bronze = specs.get("fabric/03-bronze", {}).get("doc")
    silver = specs.get("fabric/04-silver", {}).get("doc")
    gold = specs.get("fabric/05-gold", {}).get("doc")
    if not all([sources, bronze, silver, gold]):
        return

    entities = {
        f"{s['name']}.{e['name']}"
        for s in sources.get("sources", [])
        for e in s.get("entities", [])
    }
    bad = [t["source_entity"] for t in bronze.get("tables", [])
           if t["source_entity"] not in entities]
    if bad:
        report.error("chain", f"bronze reads unregistered sources: {bad}")

    bronze_targets = {t["target"] for t in bronze.get("tables", [])}
    bad = [t["source"] for t in silver.get("tables", []) if t["source"] not in bronze_targets]
    if bad:
        report.error("chain", f"silver reads non-existent bronze tables: {bad}")

    silver_targets = {f"silver.{t['target']}" for t in silver.get("tables", [])}
    bad = [d["source"] for d in gold.get("dimensions", [])
           if d["source"] not in silver_targets and d["source"] != "generated"]
    bad += [f["source"] for f in gold.get("facts", []) if f["source"] not in silver_targets]
    if bad:
        report.error("chain", f"gold reads non-existent silver tables: {bad}")

    if not report.errors or all("chain" not in e for e in report.errors):
        report.ok("chain: bronze -> silver -> gold references all resolve")


def check_discarded_rows(specs: dict, report: Report) -> None:
    """Every join that can discard rows says what happens to them.

    A join in a layer transition removes rows and the value they carry. Nothing
    reports it: the fact simply has fewer rows than its source, and the loss
    surfaces only as a reconciliation gap, much later, if anyone happens to be
    measuring one.

    This is exactly what happened here. An inner join dropped 2,945 order lines
    worth 15.8M, and every report built on the fact was short by 4.15% with no
    indication anywhere. `on_unmatched` forces the choice to be stated rather
    than inherited from the join type -- and when the choice is `quarantine`,
    the rows stay countable.

    Generalises to any future transition: a spec cannot silently lose rows.
    """
    gold = specs.get("fabric/05-gold", {}).get("doc")
    if not gold:
        return

    monitoring = specs.get("dataops/01-monitoring", {}).get("doc") or {}
    reconciled: set[str] = set()
    for expectation in monitoring.get("expectations") or []:
        for check in expectation.get("checks") or []:
            if check.get("rule") == "reconciliation":
                for side in ("left", "right"):
                    table = (check.get(side) or {}).get("table")
                    if table:
                        reconciled.add(table.rsplit(".", 1)[-1])

    errors: list[str] = []
    warnings: list[str] = []
    quarantining = 0

    for fact in gold.get("facts") or []:
        for join in fact.get("joins") or []:
            where = f"{fact['name']} join {join.get('table')}"
            policy = join.get("on_unmatched")

            if not policy:
                errors.append(
                    f"{where}: no on_unmatched. A {join.get('type')} join "
                    f"discards source rows and the value they carry, and "
                    f"nothing reports it. State quarantine, drop or fail.")
                continue

            if policy == "quarantine":
                quarantining += 1
                if not join.get("quarantine_table"):
                    errors.append(f"{where}: on_unmatched is quarantine but no "
                                  f"quarantine_table is named")
            elif policy == "drop" and not join.get("description"):
                # Dropping is legitimate, but only as a decision someone made.
                errors.append(f"{where}: on_unmatched is drop with no "
                              f"description saying why the rows are not needed")

        # A fact that can lose rows needs something measuring whether it did.
        loses_rows = any(j.get("on_unmatched") in ("quarantine", "drop")
                         for j in fact.get("joins") or [])
        if loses_rows and monitoring and fact["name"] not in reconciled:
            warnings.append(
                f"{fact['name']} can discard rows but no reconciliation check "
                f"covers it, so the loss would not be measured")

    if errors:
        for error in errors:
            report.error("discarded-rows", error)
    else:
        total = sum(len(f.get("joins") or []) for f in gold.get("facts") or [])
        report.ok(f"discarded-rows: all {total} join(s) declare what happens to "
                  f"rows they discard ({quarantining} quarantining)")
    for warning in warnings:
        report.warn("discarded-rows", warning)


def check_promotion(specs: dict, report: Report) -> None:
    """The declared promotion mechanism matches the rest of the spec.

    Two blocks can contradict each other silently: `deployment_pipeline`
    describes promotion through Fabric, while the workflows deploy by running
    scripts. Both being present reads as though the pipeline is in use when
    nothing creates or triggers it.

    The framework supports either -- promotion is a per-project decision, since
    the framework itself is never deployed to Fabric. It just has to be stated.
    """
    spec = specs.get("cicd/01-pipeline", {}).get("doc")
    if not spec:
        return

    promotion = spec.get("promotion") or {}
    mechanism = promotion.get("mechanism")

    if not mechanism:
        report.warn("promotion",
                    "no promotion.mechanism declared. The spec has both a "
                    "deployment_pipeline block and script-based deploy "
                    "workflows, and nothing says which one actually promotes.")
        return

    if mechanism == "deployment_pipeline":
        # Refused rather than half-supported: an untested promotion path that
        # looks supported is worse than one that says it is not.
        report.error("promotion",
                     "mechanism is deployment_pipeline, which the framework "
                     "does not implement -- nothing creates or triggers a "
                     "Fabric deployment pipeline. Use scripts, or build the "
                     "path first. A promotion route that looks supported and "
                     "is not will be discovered in production.")
        return

    strategy = (spec.get("parameterisation") or {}).get("strategy")
    contradicts = strategy == "deployment_rules"
    if contradicts:
        report.error("promotion",
                     f"mechanism is {mechanism} but parameterisation.strategy "
                     f"is deployment_rules, which only a Fabric deployment "
                     f"pipeline applies. Script promotion resolves "
                     f"placeholders.")

    if spec.get("deployment_pipeline") and not promotion.get("rationale"):
        report.warn("promotion",
                    "a deployment_pipeline block is present but the mechanism "
                    "is scripts. Record why in promotion.rationale, or a reader "
                    "will assume the pipeline is what promotes.")

    if not contradicts:
        report.ok(f"promotion: mechanism is {mechanism}, consistent with "
                  f"parameterisation.strategy={strategy}")


def check_semantic_model(specs: dict, report: Report) -> None:
    """The P1 model resolves, and nothing collides.

    Every rule here was learned from a deploy that failed, and Fabric's message
    named the symptom rather than the cause:

      column/measure collision -> "measure cannot be created because a column
                                   with the same name already exists"
      unresolved reference     -> "refers to an object which cannot be found",
                                   naming the RELATIONSHIP, not the column
      DirectLake over a view   -> no error at all; it falls back to DirectQuery

    A round trip to Fabric costs minutes. These cost milliseconds.
    """
    spec = specs.get("powerbi/01-semantic-model", {}).get("doc")
    if not spec:
        return

    model = spec.get("model", {})
    tables = spec.get("tables", [])
    errors: list[str] = []

    # Display names per table, plus the source names a reference may use.
    display: dict[str, set[str]] = {}
    references: dict[str, set[str]] = {}
    for table in tables:
        names = set()
        refs = set()
        for column in table.get("columns") or []:
            name = column.get("name", column["source"])
            if name in names:
                errors.append(f"{table['name']}: two columns named {name!r}")
            names.add(name)
            refs.add(name)
            refs.add(column["source"])
        display[table["name"]] = names
        references[table["name"]] = refs

    # A measure and a column cannot share a name. The model refuses to load,
    # and only the first collision is reported -- so they are found one deploy
    # at a time unless caught here.
    for measure in spec.get("measures") or []:
        owner = measure["table"]
        if owner not in display:
            errors.append(f"measure {measure['name']!r} is on table {owner!r}, "
                          f"which is not declared")
        elif measure["name"] in display[owner]:
            errors.append(f"{owner}: measure {measure['name']!r} collides with a "
                          f"column of the same name -- rename one "
                          f"(columns on a fact are conventionally 'Line ...')")

    seen_measures: set[str] = set()
    for measure in spec.get("measures") or []:
        if measure["name"] in seen_measures:
            errors.append(f"two measures named {measure['name']!r}")
        seen_measures.add(measure["name"])

    # Relationship endpoints must exist. The generator injects missing key
    # columns, so only an unknown table or a genuinely absent column fails.
    for rel in spec.get("relationships") or []:
        for side in ("from", "to"):
            table = rel[f"{side}_table"]
            if table not in references:
                errors.append(f"relationship {side}_table {table!r} is not declared")

    for hierarchy in spec.get("hierarchies") or []:
        owner = hierarchy["table"]
        if owner not in display:
            errors.append(f"hierarchy {hierarchy['name']!r} is on undeclared "
                          f"table {owner!r}")
            continue
        for level in hierarchy["levels"]:
            if level not in display[owner]:
                errors.append(f"hierarchy {hierarchy['name']!r} level {level!r} "
                              f"is not a column of {owner} -- levels use DISPLAY "
                              f"names")

    for table in tables:
        for column in table.get("columns") or []:
            sort_by = column.get("sort_by")
            if sort_by and sort_by not in references[table["name"]]:
                errors.append(f"{table['name']}.{column.get('name', column['source'])}: "
                              f"sort_by {sort_by!r} is not a column of this table")

    # DirectLake reads delta files. Binding it to a view does not fail -- it
    # silently becomes DirectQuery, and every report is slower for a reason
    # that never appears anywhere.
    if model.get("storage_mode") == "direct_lake":
        physical = (spec.get("defaults", {}).get("physical_schema")
                    or "dbo")
        for table in tables:
            schema = table["source_table"].split(".")[0]
            if schema != physical:
                errors.append(f"{table['name']}: storage_mode is direct_lake but "
                              f"source_table is {table['source_table']} -- "
                              f"DirectLake cannot read a view and will fall back "
                              f"to DirectQuery without reporting it")

    date_tables = [t for t in tables if t.get("kind") == "date"]
    if len(date_tables) > 1:
        errors.append("more than one table has kind: date; time intelligence "
                      "needs exactly one")
    for table in date_tables:
        sources = {c["source"] for c in table.get("columns") or []}
        if not sources & {"full_date", "date"}:
            errors.append(f"{table['name']} is kind: date but has no full_date "
                          f"column to mark -- time intelligence returns wrong "
                          f"answers rather than failing")

    for table in tables:
        if table.get("kind") == "dimension" and not table.get("key"):
            errors.append(f"{table['name']} is a dimension without a key")

    if errors:
        for error in errors:
            report.error("semantic-model", error)
    else:
        report.ok(f"semantic-model: {len(tables)} tables, "
                  f"{len(spec.get('relationships') or [])} relationships, "
                  f"{len(spec.get('measures') or [])} measures resolve")


def check_report_fields(specs: dict, report: Report) -> None:
    """Every report field exists in the semantic model it binds to.

    This is the failure that makes reports untrustworthy: rename a measure in
    P1 and the visuals using it do not break, they render EMPTY. Nothing logs
    it, and it is usually found by someone in a meeting.

    Checked across specs because neither one can see it alone.
    """
    spec = specs.get("powerbi/02-reports", {}).get("doc")
    model = specs.get("powerbi/01-semantic-model", {}).get("doc")
    if not spec:
        return
    if not model:
        report.warn("report-fields", "no semantic model spec, so report fields "
                                     "cannot be resolved")
        return

    known: set[str] = set()
    for table in model.get("tables", []):
        for column in table.get("columns") or []:
            known.add(f"{table['name']}.{column.get('name', column['source'])}")
    for measure in model.get("measures") or []:
        known.add(f"{measure['table']}.{measure['name']}")

    errors: list[str] = []
    total = 0
    for definition in spec.get("reports", []):
        if definition["model"] != model.get("model", {}).get("name"):
            errors.append(f"{definition['name']} binds to "
                          f"{definition['model']!r}, but the project's model is "
                          f"{model.get('model', {}).get('name')!r}")

        for page in definition.get("pages", []):
            for index, visual in enumerate(page.get("visuals", [])):
                where = f"{definition['name']}/{page['name']}/{visual['type']}[{index}]"

                references = []
                for role_fields in (visual.get("fields") or {}).values():
                    references += list(role_fields)
                if visual.get("sort"):
                    references.append(visual["sort"]["field"])
                for filt in visual.get("filters") or []:
                    references.append(filt["field"])

                total += len(references)
                for reference in references:
                    if reference not in known:
                        errors.append(f"{where}: {reference!r} is not in "
                                      f"{definition['model']}")

                # A visual with no fields renders as an empty box.
                if not visual.get("fields"):
                    errors.append(f"{where}: no fields")

    if errors:
        for error in errors:
            report.error("report-fields", error)
    else:
        report.ok(f"report-fields: all {total} field references resolve against "
                  f"{model['model']['name']}")


def check_monitoring(specs: dict, report: Report) -> None:
    """Expectations are enforceable, and the thresholds can actually fire.

    The failure this exists for is quiet: a check whose threshold can never be
    exceeded, or whose severity nothing acts on, sits in the spec looking like
    coverage while measuring nothing.
    """
    spec = specs.get("dataops/01-monitoring", {}).get("doc")
    if not spec:
        return

    # Evaluators are the authority on which rules exist -- read from the
    # library rather than restated, so adding a rule cannot leave this behind.
    library = (Path(__file__).resolve().parent.parent
               / "ttfabric" / "monitoring.py")
    implemented: set[str] = set()
    if library.exists():
        import ast as _ast
        tree = _ast.parse(library.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, _ast.AnnAssign) and getattr(node.target, "id", "") == "EVALUATORS":
                for key in (node.value.keys if node.value else []):
                    if isinstance(key, _ast.Constant):
                        implemented.add(key.value)

    severities = set(spec.get("severity_levels") or {})
    errors: list[str] = []
    warnings: list[str] = []

    SHARE_RULES = {"not_null", "unique", "referential_integrity",
                   "accepted_values", "range", "arithmetic_consistency",
                   "reconciliation"}

    # `measured_on: input` runs against the UPSTREAM table, where silver has not
    # yet renamed anything. A check naming the silver column fails at run time
    # with an unresolved-column error that looks like a data problem.
    #
    # The silver mapping declares both names, so the correct one is knowable
    # here. This is exactly the mistake SL-PROD-001 made: `list_price` measured
    # on input, where the column is still `price`.
    silver = specs.get("fabric/04-silver", {}).get("doc") or {}
    upstream_columns: dict[str, set[str]] = {}
    for table in silver.get("tables") or []:
        target = table.get("target")
        if not target:
            continue
        upstream_columns[target] = {
            c["source"] for c in table.get("columns") or []
            if isinstance(c, dict) and c.get("source")}
    seen_ids: set[str] = set()
    uncalibrated: list[str] = []
    total = 0

    for expectation in spec.get("expectations", []):
        table = expectation["table"]
        for check in expectation.get("checks", []):
            total += 1
            where = f"{table}/{check['id']}"

            if check["id"] in seen_ids:
                errors.append(f"duplicate check id {check['id']!r} -- ids appear "
                              f"in the results table and in alerts")
            seen_ids.add(check["id"])

            if implemented and check["rule"] not in implemented:
                errors.append(f"{where}: rule {check['rule']!r} has no evaluator "
                              f"in ttfabric/monitoring.py")

            if check["severity"] not in severities:
                errors.append(f"{where}: severity {check['severity']!r} is not in "
                              f"severity_levels")

            # A share rule with no threshold measures something and compares it
            # to nothing.
            if check["rule"] in SHARE_RULES:
                if check.get("warn_above") is None and check.get("fail_above") is None:
                    errors.append(f"{where}: {check['rule']} has neither "
                                  f"warn_above nor fail_above, so it can never "
                                  f"report a breach")
                for name in ("warn_above", "fail_above"):
                    value = check.get(name)
                    if value is not None and value > 1:
                        errors.append(f"{where}: {name}={value} is above 1.0. "
                                      f"Thresholds are fractions (0.022 = 2.2%); "
                                      f"as written this can never fire")
                warn, fail = check.get("warn_above"), check.get("fail_above")
                if warn is not None and fail is not None and warn > fail:
                    errors.append(f"{where}: warn_above ({warn}) is above "
                                  f"fail_above ({fail}), so it warns only after "
                                  f"it has already failed")

            # `measured_on: input` without a source is NOT flagged here.
            # generate_monitoring.py resolves it from the layer specs and
            # errors if it cannot, so warning about it twice is noise.

            if check.get("measured_on") == "input" and check.get("column"):
                known = upstream_columns.get(table)
                if known and check["column"] not in known:
                    errors.append(
                        f"{where}: measured_on is input, so this runs against "
                        f"the upstream table, but {check['column']!r} is a "
                        f"{table} column name. Use the source name the silver "
                        f"mapping declares.")

            if check["rule"] == "reconciliation":
                left = check.get("left") or {}
                right = check.get("right") or {}
                if not left or not right:
                    errors.append(f"{where}: reconciliation needs both left "
                                  f"and right")
                elif bool(left.get("key")) != bool(right.get("key")):
                    # One side grouped and the other not compares a per-key
                    # total against a grand total, which is never meaningful.
                    errors.append(f"{where}: one side declares a key and the "
                                  f"other does not; either both group or "
                                  f"neither does")

            if not check.get("note") and check["rule"] in SHARE_RULES:
                uncalibrated.append(check["id"])

    declared_tables = {e["table"] for e in spec.get("expectations", [])}
    for sla in spec.get("slas") or []:
        if sla["table"].rsplit(".", 1)[-1] not in {t.rsplit(".", 1)[-1]
                                                   for t in declared_tables}:
            warnings.append(f"SLA on {sla['table']} has no expectations; its "
                            f"freshness and completeness are asserted but never "
                            f"measured")

    for environment, policy in (spec.get("enforcement") or {}).items():
        if policy.get("mode") == "block" and not policy.get("block_on"):
            errors.append(f"enforcement.{environment}: mode is 'block' but "
                          f"block_on is empty, so nothing ever blocks")
        blocking = set(policy.get("block_on") or [])
        unknown = blocking - severities
        if unknown:
            errors.append(f"enforcement.{environment}: block_on names unknown "
                          f"severities {sorted(unknown)}")

    if errors:
        for error in errors:
            report.error("monitoring", error)
    else:
        report.ok(f"monitoring: {total} checks across {len(declared_tables)} "
                  f"tables are enforceable")
    for warning in warnings:
        report.warn("monitoring", warning)

    # Aggregated: one line about calibration, not one per check. The point is
    # the proportion, and per-check warnings would bury everything else.
    if uncalibrated:
        shown = ", ".join(uncalibrated[:5])
        more = f" and {len(uncalibrated) - 5} more" if len(uncalibrated) > 5 else ""
        report.warn("monitoring",
                    f"{len(uncalibrated)} of {total} checks record no note saying "
                    f"where the threshold came from ({shown}{more}). An "
                    f"uncalibrated threshold gets loosened the first time it fires.")


def check_cicd(specs: dict, report: Report) -> None:
    """Workflows reference real scripts, real environments, and real gates.

    A workflow is the one artefact whose mistakes surface on someone else's
    pull request rather than on yours. This spec's original steps named
    sync_workspace.py, which never existed, and framework/lib/tests, which had
    moved -- both would have failed only once CI was already running.
    """
    spec = specs.get("cicd/01-pipeline", {}).get("doc")
    if not spec:
        return

    scaffolding = specs.get("fabric/01-scaffolding", {}).get("doc") or {}
    environments = {e["name"] for e in scaffolding.get("environments", [])}

    errors: list[str] = []
    warnings: list[str] = []

    workflow_names = [w["name"] for w in spec.get("workflows", [])]
    if len(workflow_names) != len(set(workflow_names)):
        errors.append("two workflows share a name")

    files = [w["file"] for w in spec.get("workflows", [])]
    if len(files) != len(set(files)):
        errors.append("two workflows write to the same file; one would overwrite "
                      "the other silently")

    for workflow in spec.get("workflows", []):
        job_ids = [j["id"] for j in workflow["jobs"]]
        for job in workflow["jobs"]:
            where = f"{workflow['name']}/{job['id']}"

            if environments and job.get("environment") \
                    and job["environment"] not in environments:
                errors.append(f"{where}: environment {job['environment']!r} is not "
                              f"declared in fabric/01-scaffolding.yaml")

            for need in job.get("needs") or []:
                if need not in job_ids:
                    errors.append(f"{where}: needs {need!r}, which is not a job "
                                  f"in this workflow")

            # A job that deploys but declares no GitHub Environment has no
            # approval gate and no scoped secrets, however many gates the spec
            # lists further down.
            deploys = any("deploy/push" in s or "run_migrations" in s
                          for s in job["steps"])
            if deploys and not job.get("environment"):
                errors.append(f"{where}: deploys but declares no environment, so "
                              f"it has no approval gate and no scoped secrets")

    for gate in spec.get("gates") or []:
        unknown = set(gate["blocks"]) - environments if environments else set()
        if unknown:
            errors.append(f"gate {gate['id']}: blocks unknown environments "
                          f"{sorted(unknown)}")
        if not gate.get("automated") and not gate.get("approvers"):
            errors.append(f"gate {gate['id']}: manual but names no approver, "
                          f"which is not a gate")

        # An automated gate should correspond to a job that can enforce it.
        if gate.get("automated"):
            known = {j["id"] for w in spec.get("workflows", []) for j in w["jobs"]}
            if gate["id"] not in known:
                warnings.append(f"gate {gate['id']} is automated but no job has "
                                f"that id, so nothing enforces it")

    # Manual gates are enforced by GitHub Environment reviewers, which live
    # outside this repository -- worth stating rather than assuming.
    manual = [g for g in spec.get("gates") or [] if not g.get("automated")]
    if manual:
        guarded = sorted({e for g in manual for e in g["blocks"]})
        warnings.append(
            f"{len(manual)} manual gate(s) guard {', '.join(guarded)}. These are "
            f"enforced by required reviewers on the GitHub Environment, not by "
            f"anything generated here -- confirm they are configured.")

    secrets = (spec.get("secrets") or {}).get("required") or []
    if any("deploy" in w["name"] for w in spec.get("workflows", [])) and not secrets:
        warnings.append("deploy workflows exist but secrets.required is empty; "
                        "the generated jobs would have no credentials")

    if errors:
        for error in errors:
            report.error("cicd", error)
    else:
        report.ok(f"cicd: {len(spec.get('workflows', []))} workflows, "
                  f"{len(spec.get('gates') or [])} gates resolve")
    for warning in warnings:
        report.warn("cicd", warning)


SEMANTIC_CHECKS = (
    check_key_types,
    check_environment_ids,
    check_storage_topology,
    check_capacity,
    check_secrets,
    check_source_domains,
    check_watermarks,
    check_defect_handlers,
    check_view_dialect,
    check_discarded_rows,
    check_promotion,
    check_rule_coverage,
    check_layer_chain,
    check_semantic_model,
    check_report_fields,
    check_monitoring,
    check_cicd,
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--track", action="append", choices=TRACKS,
                        help="limit to one or more tracks (default: all)")
    parser.add_argument("--contracts", default=None)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"  ERROR  project not found: {project}")
        return 2

    contracts = (Path(args.contracts) if args.contracts
                 else Path(__file__).resolve().parent.parent / "contracts")
    tracks = tuple(args.track) if args.track else TRACKS

    print(f"Project   {project.name}")
    print(f"Tracks    {', '.join(tracks)}")
    print(f"Contracts {contracts}")
    print()

    specs = load_project(project, tracks)
    if not specs:
        print(f"  ERROR  no specs found under {project} for tracks {tracks}")
        return 2

    report = Report()
    check_schemas(specs, contracts, report)
    check_rule_enum_fresh(specs, report, contracts)
    for check in SEMANTIC_CHECKS:
        check(specs, report)

    return report.render(args.strict)


if __name__ == "__main__":
    sys.exit(main())
