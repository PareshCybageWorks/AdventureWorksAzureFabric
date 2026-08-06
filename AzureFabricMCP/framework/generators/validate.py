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
import re
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
            # Match on the LEAF key only, not the whole path.
            #
            # Matching the path meant any value nested anywhere beneath a key
            # whose name contains "key" was tested as a credential -- and
            # `foreign_keys` contains "key". A foreign key reference longer
            # than 24 characters was therefore reported as a literal secret,
            # while a shorter one passed, so the check fired on entity-name
            # length rather than on anything about the value.
            leaf = path.rsplit(".", 1)[-1].split("[", 1)[0].lower()

            # Structural fields that merely CONTAIN a keyword. These hold
            # column names, never credentials.
            if leaf in ("primary_key", "foreign_keys", "references",
                        "references_columns", "watermark_column",
                        "surrogate_key", "business_key", "natural_key"):
                return

            if any(w in leaf for w in ("secret", "password", "key", "token")):
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
    # A cascade can be the ONLY place a defect is handled. A polymorphic
    # foreign key cannot be checked by enforce_referential_integrity against
    # any single parent without quarantining every row belonging to the
    # others, so the post-pass -- which removes exactly the rows whose real
    # parent was rejected -- is the handler. Counting only table rules here
    # reported those defects as unhandled and pushed the author towards
    # mislabelling them.
    handled |= {
        cascade["handles"]
        for cascade in silver.get("cascade_quarantine", [])
        if cascade.get("handles")
    }

    unhandled = sorted(documented - handled)
    phantom = sorted(handled - documented)

    if unhandled:
        report.error("coverage", f"documented defects no rule handles: {unhandled}")
    if phantom:
        report.warn("coverage", f"rules handling undocumented defect ids: {phantom}")
    if not unhandled and not phantom:
        report.ok(f"coverage: all {len(documented)} documented defects are handled")


def check_dimension_business_keys(specs: dict, report: Report) -> None:
    """A dimension's business_key survives its own projection.

    The generated gold notebook projects source -> target BEFORE calling
    assign_surrogate_key, so a business_key naming a SOURCE column that the
    projection renames refers to a column that no longer exists.

    This is the "four names" trap from the F5 prompt, landing in the spec
    rather than in a join: the fact's lookup column, the dimension's business
    key, its surrogate key and the column written on the fact are four separate
    names that only coincide in the easy case.

    It fails at run time inside Spark, which Fabric surfaces only as "session
    failed" -- five dimensions failed that way here, while the one dimension
    whose key was not renamed succeeded, making it look like a data problem
    rather than a naming one.
    """
    gold = specs.get("fabric/05-gold", {}).get("doc")
    if not gold:
        return

    problems = []
    for dim in gold.get("dimensions", []):
        columns = dim.get("columns") or []
        renames = {c["source"]: c["target"] for c in columns
                   if c.get("source") and c.get("target")}
        targets = {c["target"] for c in columns if c.get("target")}

        for key in dim.get("business_key", []):
            # Not projected at all: passes through untouched, which is fine.
            if key not in renames and key not in targets:
                continue
            if key in targets:
                continue
            problems.append(
                f"{dim['name']}: business_key {key!r} is a SOURCE column that "
                f"the projection renames to {renames[key]!r}, so it does not "
                f"exist when the surrogate key is assigned. Use the target name."
            )

    if problems:
        for problem in problems:
            report.error("dimension-keys", problem)
    elif gold.get("dimensions"):
        report.ok(f"dimension-keys: all {len(gold['dimensions'])} business "
                  f"key(s) survive their projection")


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


def check_cascade_quarantine(specs: dict, report: Report) -> None:
    """Declared cascades resolve, and a fact that can orphan rows has one.

    The failure this prevents is indirect and therefore slow to find: a parent
    rejected in silver leaves its children behind, no rule fires because a
    child is valid on its own, and the loss only appears when a join two layers
    later discards them.
    """
    silver = specs.get("fabric/04-silver", {}).get("doc")
    if not silver:
        return

    targets = {t["target"] for t in silver.get("tables") or []}
    cascades = silver.get("cascade_quarantine") or []
    errors: list[str] = []

    for cascade in cascades:
        where = f"{cascade['child']} <- {cascade['parent']}"
        for side in ("child", "parent"):
            if cascade[side] not in targets:
                errors.append(f"cascade {where}: {side} {cascade[side]!r} is not "
                              f"a silver table")
        if cascade["child"] == cascade["parent"]:
            errors.append(f"cascade {where}: a table cannot cascade to itself")

    # A gold join that quarantines orphans is compensating for something silver
    # let through. Better to catch it at the layer that made the decision.
    gold = specs.get("fabric/05-gold", {}).get("doc") or {}
    covered = {(c["child"], c["parent"]) for c in cascades}
    for fact in gold.get("facts") or []:
        for join in fact.get("joins") or []:
            if join.get("on_unmatched") != "quarantine":
                continue
            child = (fact.get("source") or "").rsplit(".", 1)[-1]
            parent = (join.get("table") or "").rsplit(".", 1)[-1]
            if child and parent and (child, parent) not in covered:
                report.warn(
                    "cascade",
                    f"{fact['name']} quarantines rows unmatched against "
                    f"{parent}, but no cascade_quarantine propagates "
                    f"{parent} rejections to {child}. Gold is compensating for "
                    f"a loss silver could attribute to its cause.")

    if errors:
        for error in errors:
            report.error("cascade", error)
    elif cascades:
        report.ok(f"cascade: {len(cascades)} parent/child rejection rule(s) resolve")


def check_git_integration(specs: dict, report: Report) -> None:
    """Workspace git mirroring cannot overwrite specs or generated output.

    Fabric owns everything beneath its sync directory: it rewrites that subtree
    to match the workspace. Pointed at `generated/`, an item edited in the UI
    would land on top of a generated artefact -- and the no-drift gate would
    then fail on a file nobody edited, which is a genuinely hard morning.

    The mirror is also one-directional on purpose. Syncing git -> workspace
    would compete with push_items.py over the same items, and both report
    success, so the loser is silent.
    """
    scaffolding = specs.get("fabric/01-scaffolding", {}).get("doc")
    if not scaffolding:
        return

    block = scaffolding.get("git_integration")
    if not block:
        return

    errors: list[str] = []
    directory = block.get("directory", "")
    reserved = ("/generated", "/fabric", "/powerbi", "/dataops", "/cicd", "/data")
    if any(directory == r or directory.startswith(r + "/") for r in reserved):
        errors.append(
            f"directory {directory!r} overlaps spec or generated output. Fabric "
            f"rewrites everything beneath it, so a UI edit would overwrite a "
            f"generated artefact and no-drift would fail on a file nobody touched")

    # The providers take different FIELDS, not merely different credentials.
    # Azure DevOps is addressed by organisation, project and repository; GitHub
    # by owner and repository. Getting it wrong is rejected by the API as a
    # missing field, which reads like a malformed request rather than the wrong
    # shape for the provider.
    provider = block.get("provider")
    parts = [p for p in block.get("repository", "").split("/") if p]

    if provider == "AzureDevOps":
        if len(parts) != 3:
            errors.append(
                f"provider is AzureDevOps, so repository must be "
                f"<organisation>/<project>/<repo>; got {block.get('repository')!r} "
                f"({len(parts)} part(s)). The project is a separate level and "
                f"cannot be inferred from the other two")
        if block.get("connection_id"):
            report.warn("git-integration",
                        "connection_id is set but Azure DevOps authenticates as the "
                        "calling user; it will be ignored")
    elif provider == "GitHub":
        if len(parts) != 2:
            errors.append(
                f"provider is GitHub, so repository must be <owner>/<repo>; got "
                f"{block.get('repository')!r} ({len(parts)} part(s))")
        if not block.get("connection_id"):
            errors.append(
                "provider is GitHub but no connection_id is declared. GitHub needs "
                "a Fabric connection holding a PAT; only Azure DevOps can "
                "authenticate as the calling user")

    declared = {e["name"] for e in scaffolding.get("environments") or []}
    for name in block.get("environments") or []:
        if name not in declared:
            errors.append(f"environments lists {name!r}, which is not an environment "
                          f"in this spec")
            continue
        environment = next(e for e in scaffolding["environments"] if e["name"] == name)
        if not environment.get("branch"):
            errors.append(f"{name} is mirrored but declares no branch, and the "
                          f"branch is read from the environment rather than restated")
        if environment.get("is_production"):
            report.warn("git-integration",
                        f"{name} is production. Mirroring it is only useful if items "
                        f"are edited there by hand, which is a larger problem")

    for error in errors:
        report.error("git-integration", error)
    if not errors:
        report.ok("git-integration")


def check_promotion_path(specs: dict, report: Report) -> None:
    """Every declared environment can actually be deployed to.

    uat had a workspace, provisioned storage, a deployment_pipeline stage and
    four gates blocking it -- and no workflow that deployed to it. The chain
    broke at exactly the step a business owner signs off, and nothing said so,
    because the branch/environment mapping existed only as an implication of
    whichever workflow triggers happened to exist.

    Declaring `branch` per environment makes the mapping a fact the specs hold,
    which is what lets this be checked at all.
    """
    scaffolding = specs.get("fabric/01-scaffolding", {}).get("doc")
    cicd = specs.get("cicd/01-pipeline", {}).get("doc")
    if not scaffolding or not cicd:
        return

    environments = scaffolding.get("environments") or []
    deployed = {j.get("environment")
                for w in cicd.get("workflows") or []
                for j in w.get("jobs") or []
                if j.get("environment")}

    # branch -> the environments claiming it
    claims: dict[str, list[str]] = {}
    errors: list[str] = []

    for environment in environments:
        name = environment["name"]
        branch = environment.get("branch")

        if not branch:
            report.warn("promotion-path",
                        f"{name} declares no branch, so nothing can check it "
                        f"has a deploy path")
            continue
        claims.setdefault(branch, []).append(name)

        if name not in deployed:
            errors.append(
                f"{name} is declared (branch {branch!r}, workspace "
                f"{environment.get('workspace')}) but no workflow job deploys "
                f"to it. It is unreachable however many gates guard it.")

    for branch, owners in claims.items():
        if len(owners) > 1:
            errors.append(f"branch {branch!r} is claimed by more than one "
                          f"environment: {owners}. A push would deploy to both.")

    # A workflow triggered by a branch push should target the environment that
    # branch belongs to -- otherwise merging to qa deploys somewhere else.
    by_branch = {e.get("branch"): e["name"] for e in environments if e.get("branch")}
    for workflow in cicd.get("workflows") or []:
        pushed = ((workflow.get("triggers") or {}).get("push") or {}).get("branches") or []
        targets = {j.get("environment") for j in workflow.get("jobs") or []
                   if j.get("environment")}
        for branch in pushed:
            expected = by_branch.get(branch)
            if expected and targets and expected not in targets:
                errors.append(
                    f"{workflow['name']} triggers on push to {branch!r}, which "
                    f"belongs to {expected}, but deploys to {sorted(targets)}")

    if errors:
        for error in errors:
            report.error("promotion-path", error)
    else:
        chain = " -> ".join(e["name"] for e in environments)
        report.ok(f"promotion-path: every environment is reachable ({chain})")


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


def check_view_columns(specs: dict, report: Report) -> None:
    """Every column a `bi` view names exists on the gold table it reads.

    check_view_dialect confirms the SQL is T-SQL and stops there, so a view
    naming a column that does not exist is caught only when the warehouse tries
    to create it -- and a view is validated at CREATE, not at query, so it
    fails during deployment with nothing local having objected.

    Three of six views failed that way here, all for the same reason: a fact
    carries the MEASURE target name, and the view was written against the
    business rule's. `created_ticket` becomes `created_ticket_count` on the way
    into the fact, so a view reading `created_ticket` finds nothing.

    Deliberately conservative. It only checks `alias.column` references whose
    alias it can tie to a known gold table, and only flags a column absent from
    that table's full column set. Anything it cannot resolve is left alone --
    a false error here would be worse than the gap, because the fix would be to
    stop trusting the check.
    """
    gold = specs.get("fabric/05-gold", {}).get("doc")
    if not gold:
        return

    # Every column each gold table actually carries.
    columns_by_table: dict[str, set[str]] = {}
    for dimension in gold.get("dimensions", []):
        names = {c["target"] for c in dimension.get("columns", []) or []}
        names.add(dimension["surrogate_key"])
        names.update(dimension.get("business_key", []))
        for rule in dimension.get("business_rules", []) or []:
            names.add(rule["target"])
        # SCD2 validity columns are INJECTED by merge_scd2, not declared in
        # `columns`. A type2 dimension really does carry is_current at runtime,
        # so a view filtering on it is correct -- flagging that would be a
        # false error, and a check that cries wolf gets switched off.
        if dimension.get("scd") == "type2":
            scd2 = dimension.get("scd2_columns") or {}
            names |= {
                scd2.get("valid_from", "valid_from"),
                scd2.get("valid_to", "valid_to"),
                scd2.get("is_current", "is_current"),
                scd2.get("version", "version"),
            }

        # A generated calendar's columns come from the library, not the spec.
        if dimension.get("source") == "generated":
            names |= {"full_date", "date_sk", "year", "quarter", "month",
                      "month_name", "year_month", "week_of_year", "day_of_month",
                      "day_name", "is_weekend", "fiscal_year", "fiscal_quarter"}
        columns_by_table[dimension["name"]] = names

    for fact in gold.get("facts", []):
        names = {d["target"] for d in fact.get("degenerate_dimensions", []) or []}
        names |= {k["target"] for k in fact.get("dimension_keys", []) or []}
        names |= {m["target"] for m in fact.get("measures", []) or []}
        names |= {b["target"] for b in fact.get("business_rules", []) or []}
        names |= {q["target"] for q in fact.get("quality_flags", []) or []}

        # A business rule's target is RENAMED AWAY when a measure reads it.
        # `created_ticket` is computed by a rule, then renamed to the measure's
        # target `created_ticket_count` before the write, so the rule's own
        # name is not a column on the finished fact.
        #
        # This is the exact trap the views fell into: they were written against
        # the rule names, which read naturally and do not exist.
        renamed_away = {m["source"] for m in fact.get("measures", []) or []
                        if m.get("source") and m["source"] != m["target"]}
        renamed_away |= {d["source"] for d in fact.get("degenerate_dimensions", []) or []
                         if d.get("source") and d["source"] != d["target"]}
        names -= renamed_away

        columns_by_table[fact["name"]] = names

    from_join = re.compile(r"(?:FROM|JOIN)\s+(?:dbo|bi)\.(\w+)\s+(?:AS\s+)?(\w+)",
                           re.IGNORECASE)
    qualified = re.compile(r"\b(\w+)\.(\w+)\b")
    keywords = {"as", "on", "and", "or", "select", "from", "join", "where",
                "group", "by", "inner", "left", "outer", "cross", "apply"}

    problems: list[str] = []
    for view in gold.get("views", []) or []:
        sql = view.get("sql", "")
        aliases = {alias: table for table, alias in from_join.findall(sql)}
        if not aliases:
            continue
        for alias, column in qualified.findall(sql):
            table = aliases.get(alias)
            if not table or table not in columns_by_table:
                continue
            if column.lower() in keywords:
                continue
            if column in columns_by_table[table]:
                continue
            problems.append(
                f"{view['name']}: reads {alias}.{column}, but {table} has no "
                f"column {column!r}. A view is validated when CREATED, so this "
                f"fails at deploy.")

    if problems:
        for problem in dict.fromkeys(problems):
            report.error("view-columns", problem)
    elif gold.get("views"):
        report.ok(f"view-columns: all {len(gold['views'])} bi view(s) reference "
                  f"real gold columns")


def check_model_names(specs: dict, report: Report) -> None:
    """Names in the semantic model are unique CASE-INSENSITIVELY, and every
    DAX column reference resolves to a DISPLAY name.

    Both of these deployed cleanly and failed afterwards:

    1. Power BI treats names case-insensitively. `Month` (from month_name) and
       a hidden sort key named `month` are the SAME name, and the model refuses
       to import with "Item 'month' already exists in the collection".
       check_semantic_model compares case-sensitively, so it passed.

    2. DAX resolves DISPLAY names. Renaming a raw fact column to `Line Direct
       Hours` -- which the model does deliberately, so nobody can build an
       unreviewed total -- and then writing SUM('Time Entry'[direct_hours])
       gives a model that deploys perfectly and fails on every query. Eighteen
       measures were wrong that way here, and only query_model.py --smoke
       found them, which needs a deployed model and a populated warehouse.
    """
    model = specs.get("powerbi/01-semantic-model", {}).get("doc")
    if not model:
        return

    measures_by_table: dict[str, list[str]] = {}
    for measure in model.get("measures", []):
        measures_by_table.setdefault(measure["table"], []).append(measure["name"])

    problems: list[str] = []
    display_by_table: dict[str, set[str]] = {}

    for table in model.get("tables", []):
        name = table["name"]
        display = [c.get("name", c["source"]) for c in table.get("columns", [])]
        display_by_table[name] = {d.lower() for d in display}

        seen: dict[str, str] = {}
        for item in display:
            key = item.lower()
            if key in seen and seen[key] != item:
                problems.append(
                    f"{name}: columns {seen[key]!r} and {item!r} differ only by "
                    f"case. Power BI treats them as one name and refuses to "
                    f"load the model.")
            seen[key] = item

        for measure in measures_by_table.get(name, []):
            if measure.lower() in seen:
                problems.append(
                    f"{name}: measure {measure!r} collides with column "
                    f"{seen[measure.lower()]!r}. Only the first collision is "
                    f"reported at deploy, so they surface one at a time.")

        # sort_by names a DISPLAY name on the same table. A source name loads
        # fine and silently drops the sort -- months read April, August, December.
        for column in table.get("columns", []):
            sort_by = column.get("sort_by")
            if sort_by and sort_by.lower() not in display_by_table[name]:
                problems.append(
                    f"{name}: column {column.get('name', column['source'])!r} "
                    f"sorts by {sort_by!r}, which is not a display name on this "
                    f"table.")

    for hierarchy in model.get("hierarchies", []):
        known = display_by_table.get(hierarchy["table"], set())
        for level in hierarchy["levels"]:
            if level.lower() not in known:
                problems.append(
                    f"hierarchy {hierarchy['name']!r}: level {level!r} is not a "
                    f"display name on {hierarchy['table']!r}.")

    # DAX column references.
    reference = re.compile(r"'([^']+)'\[([^\]]+)\]")
    for measure in model.get("measures", []):
        for table_name, column in reference.findall(measure.get("expression", "")):
            if table_name not in display_by_table:
                problems.append(
                    f"measure {measure['name']!r} references table "
                    f"{table_name!r}, which the model does not declare.")
                continue
            known = display_by_table[table_name]
            known |= {m.lower() for m in measures_by_table.get(table_name, [])}
            # Surrogate keys are injected by the generator, not declared.
            if column.lower() in known or column.endswith("_sk"):
                continue
            problems.append(
                f"measure {measure['name']!r} references "
                f"'{table_name}'[{column}], which is not a display name on that "
                f"table. DAX resolves display names, so this deploys cleanly "
                f"and returns an error on every query.")

    if problems:
        for problem in problems:
            report.error("model-names", problem)
    else:
        report.ok(f"model-names: {len(model.get('tables', []))} table(s) have no "
                  f"case-insensitive collisions and all DAX references resolve")


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


def check_cicd_scripts(specs: dict, report: Report, project: Path) -> None:
    """Every `python <path>` in a workflow step names a script that exists.

    The C1 prompt lists this as a gate and says it earned its place -- the
    first version of that spec referenced sync_workspace.py, which never
    existed. But the check lived only in generate_workflows.py, which resolves
    against its --out directory and is not run by validate. So a project could
    validate cleanly with step paths that resolve to nothing and fail on its
    first CI run, which is exactly the failure the gate was described as
    preventing.

    Paths are relative to the REPOSITORY ROOT. Projects are siblings of the
    framework inside it, so the project's parent is that root.
    """
    spec = specs.get("cicd/01-pipeline", {}).get("doc")
    if not spec:
        return

    repo_root = project.parent
    pattern = re.compile(r"python\s+(\S+\.py)")
    missing: list[str] = []
    checked = 0

    for workflow in spec.get("workflows", []):
        for job in workflow.get("jobs", []):
            for step in job.get("steps", []):
                for path in pattern.findall(step):
                    checked += 1
                    if not (repo_root / path).exists():
                        missing.append(f"{workflow['name']}/{job['id']}: {path}")

    if missing:
        for item in dict.fromkeys(missing):
            report.error("cicd-scripts", f"step names a script that does not "
                                         f"exist: {item}")
        report.error("cicd-scripts",
                     f"resolved against {repo_root}. A workflow generated from "
                     f"this fails only once CI is already running.")
    elif checked:
        report.ok(f"cicd-scripts: all {checked} step script path(s) resolve")


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
    check_dimension_business_keys,
    check_view_columns,
    check_model_names,
    check_discarded_rows,
    check_cascade_quarantine,
    check_git_integration,
    check_promotion_path,
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
    # Needs the project path to resolve step paths against the repository root.
    check_cicd_scripts(specs, report, project)

    return report.render(args.strict)


if __name__ == "__main__":
    sys.exit(main())
