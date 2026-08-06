"""
Validate a project's spec set before anything is generated or deployed.

Runs as the `spec-validate` gate on every pull request. It is the cheapest
place to catch a mistake: a spec error found here costs seconds, the same error
found after deployment costs a debugging session against a half-built
warehouse.

Checks performed
----------------
  structure     every required spec is present and declares the expected kind
  references    each layer's sources resolve to the previous layer's targets
  rules         every `fn:` in a mapping exists in the cleansing library
  conventions   the switches under 00-platform.yaml `conventions` hold
  coverage      every documented upstream defect is handled by a rule
  quality       every data-quality expectation names a table that exists

The library is read with the `ast` module rather than imported, so this runs in
CI without installing PySpark.

Usage:
    python validate_specs.py --specs ./specs [--lib ../framework/ttfabric] [--strict]

Exit codes:  0 clean (warnings allowed)   1 errors found   2 could not run
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import yaml

REQUIRED_SPECS = {
    "00-platform.yaml": "Platform",
    "01-environments.yaml": "Environments",
    "02-sources.yaml": "SourceRegistry",
    "04-data-quality.yaml": "DataQuality",
    "05-deployment.yaml": "Deployment",
}
REQUIRED_MAPPINGS = ["bronze.yaml", "silver.yaml", "gold.yaml"]


class Report:
    """Collects findings so every problem surfaces in one run."""

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
        for check in self.passed:
            print(f"  PASS   {check}")
        for message in self.warnings:
            print(f"  WARN   {message}")
        for message in self.errors:
            print(f"  ERROR  {message}")

        print()
        print(f"  {len(self.passed)} passed, {len(self.warnings)} warnings, "
              f"{len(self.errors)} errors")

        if self.errors:
            return 1
        if strict and self.warnings:
            print("  --strict: warnings treated as errors")
            return 1
        return 0


def load_specs(spec_dir: Path, report: Report) -> dict:
    """Read every spec, keyed by filename with forward slashes."""
    docs: dict[str, dict] = {}

    for filename, expected_kind in REQUIRED_SPECS.items():
        path = spec_dir / filename
        if not path.exists():
            report.error("structure", f"missing required spec {filename}")
            continue
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            report.error("structure", f"{filename} is not valid YAML -- {exc}")
            continue
        if doc.get("kind") != expected_kind:
            report.error(
                "structure",
                f"{filename} declares kind={doc.get('kind')!r}, expected {expected_kind!r}",
            )
        docs[filename] = doc

    for filename in REQUIRED_MAPPINGS:
        path = spec_dir / "mappings" / filename
        if not path.exists():
            report.error("structure", f"missing required mapping mappings/{filename}")
            continue
        try:
            docs[f"mappings/{filename}"] = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            report.error("structure", f"mappings/{filename} is not valid YAML -- {exc}")

    if not report.errors:
        report.ok("structure: all required specs present with correct kinds")
    return docs


def registry_names(lib_dir: Path, report: Report) -> set[str]:
    """Extract REGISTRY keys from cleansing.py without importing it.

    Parsing rather than importing keeps this runnable wherever CI runs, which
    is the whole point of a fast validation gate.
    """
    path = lib_dir / "cleansing.py"
    if not path.exists():
        report.error("rules", f"cleansing library not found at {path}")
        return set()

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "REGISTRY":
                value = node.value
                if isinstance(value, ast.Dict):
                    return {
                        k.value for k in value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }

    report.error("rules", "REGISTRY not found in cleansing.py")
    return set()


def check_key_types(docs: dict, report: Report) -> None:
    """Every mapping key is a string.

    YAML 1.1 resolves bare `on`, `off`, `yes`, `no`, `y` and `n` to booleans,
    so a key written as `on:` becomes `True` and nothing can look it up. The
    failure is silent at parse time and only surfaces as a KeyError deep in a
    generator, so it is worth catching here.
    """
    def walk(node, path: str) -> list[tuple[str, str]]:
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if not isinstance(key, str):
                    found.append((path, repr(key)))
                found += walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found += walk(value, f"{path}[{index}]")
        return found

    offenders = []
    for filename, doc in docs.items():
        for path, key in walk(doc, filename):
            offenders.append(f"{path} has non-string key {key}")

    if offenders:
        for offender in offenders:
            report.error("key-types", offender)
        report.error(
            "key-types",
            "quote the key or rename it -- bare on/off/yes/no become booleans",
        )
    else:
        report.ok("key-types: no keys coerced to booleans by the YAML parser")


def check_references(docs: dict, report: Report) -> None:
    """Each layer consumes only what the previous layer produces."""
    sources = docs.get("02-sources.yaml", {})
    bronze = docs.get("mappings/bronze.yaml", {})
    silver = docs.get("mappings/silver.yaml", {})
    gold = docs.get("mappings/gold.yaml", {})
    if not all([sources, bronze, silver, gold]):
        return

    entities = {
        f"{s['name']}.{e['name']}"
        for s in sources.get("sources", [])
        for e in s.get("entities", [])
    }

    unregistered = [
        t["source_entity"] for t in bronze.get("tables", [])
        if t.get("source_entity") not in entities
    ]
    if unregistered:
        report.error("references", f"bronze reads unregistered sources: {unregistered}")
    else:
        report.ok("references: bronze sources all registered")

    bronze_targets = {t["target"] for t in bronze.get("tables", [])}
    dangling = [
        t["source"] for t in silver.get("tables", [])
        if t.get("source") not in bronze_targets
    ]
    if dangling:
        report.error("references", f"silver reads non-existent bronze tables: {dangling}")
    else:
        report.ok("references: silver sources all produced by bronze")

    silver_targets = {f"silver.{t['target']}" for t in silver.get("tables", [])}
    bad = [
        d["source"] for d in gold.get("dimensions", [])
        if d.get("source") not in silver_targets and d.get("source") != "generated"
    ]
    bad += [f["source"] for f in gold.get("facts", []) if f.get("source") not in silver_targets]
    if bad:
        report.error("references", f"gold reads non-existent silver tables: {bad}")
    else:
        report.ok("references: gold sources all produced by silver")

    # A bronze table nothing consumes is dead weight -- worth knowing, not fatal.
    consumed = {t.get("source") for t in silver.get("tables", [])}
    orphaned = bronze_targets - consumed
    if orphaned:
        report.warn("references", f"bronze tables no silver mapping consumes: {sorted(orphaned)}")


def check_rules(docs: dict, available: set[str], report: Report) -> None:
    """Every rule a mapping names exists, and every rule shipped is used."""
    silver = docs.get("mappings/silver.yaml", {})
    if not silver or not available:
        return

    referenced = {
        rule["fn"]
        for table in silver.get("tables", [])
        for rule in table.get("rules", [])
        if "fn" in rule
    }

    missing = referenced - available
    if missing:
        report.error(
            "rules",
            f"spec references rules absent from the library: {sorted(missing)}",
        )
    else:
        report.ok(f"rules: all {len(referenced)} referenced rules exist in the library")

    unused = available - referenced
    if unused:
        report.warn("rules", f"library rules no spec uses: {sorted(unused)}")


def check_conventions(docs: dict, report: Report) -> None:
    """Enforce the switches under 00-platform.yaml `conventions`."""
    platform = docs.get("00-platform.yaml", {})
    silver = docs.get("mappings/silver.yaml", {})
    gold = docs.get("mappings/gold.yaml", {})
    sources = docs.get("02-sources.yaml", {})
    conventions = platform.get("conventions", {})

    if conventions.get("require_business_key"):
        offenders = [t["target"] for t in silver.get("tables", []) if not t.get("business_key")]
        if offenders:
            report.error("conventions", f"silver tables without a business_key: {offenders}")
        else:
            report.ok("conventions: every silver table declares a business key")

    if conventions.get("require_declared_grain"):
        offenders = [f["name"] for f in gold.get("facts", []) if not f.get("grain")]
        if offenders:
            report.error("conventions", f"gold facts without a declared grain: {offenders}")
        else:
            report.ok("conventions: every gold fact declares its grain")

    if conventions.get("require_exhaustive_column_mapping"):
        offenders = [
            t["target"] for t in docs.get("mappings/bronze.yaml", {}).get("tables", [])
            if t.get("columns") != "passthrough" and "excluded_columns" not in t
        ]
        if offenders:
            report.error(
                "conventions",
                f"bronze tables mapping a column subset without declaring "
                f"excluded_columns: {offenders}",
            )
        else:
            report.ok("conventions: column mapping is exhaustive")

    if conventions.get("require_pii_classification"):
        # A source column tagged PII must be either mapped with a mask or
        # explicitly absent from silver.
        pii_columns = {
            c["name"]
            for s in sources.get("sources", [])
            for e in s.get("entities", [])
            for c in e.get("columns", [])
            if c.get("pii")
        }
        masked, dropped = set(), set()
        for table in silver.get("tables", []):
            mapped = {c.get("source") for c in table.get("columns", [])}
            for col in table.get("columns", []):
                if col.get("mask"):
                    masked.add(col.get("source"))
            dropped |= (pii_columns - mapped)

        unprotected = pii_columns - masked - dropped
        if unprotected:
            report.warn(
                "conventions",
                f"PII columns reaching silver without a mask policy: {sorted(unprotected)}",
            )
        else:
            report.ok("conventions: every PII column is masked or dropped")


def check_workspace_folders(docs: dict, report: Report) -> None:
    """Every item the generators produce lands in a declared workspace folder.

    Without this the folder taxonomy is documentation rather than a contract: a
    new notebook would be generated, deployed to the workspace root, and nobody
    would notice it never got filed.
    """
    platform = docs.get("00-platform.yaml", {})
    folders = platform.get("workspace_folders")
    if not folders:
        return

    # Folders may declare items directly or split them across `notebook` and
    # `pipeline` subfolders, so both levels are collected.
    patterns: list[tuple[str, str]] = []

    def collect(entries: list[dict], path: str) -> None:
        for entry in entries:
            pattern = entry.get("name_pattern") or entry.get("name")
            if pattern:
                patterns.append((pattern, path))

    for folder in folders:
        collect(folder.get("contains", []), folder["name"])
        for sub in folder.get("subfolders", []):
            collect(sub.get("contains", []), f"{folder['name']}/{sub['name']}")

    naming = platform.get("naming", {})
    template = naming.get("notebook", "nb_{verb}_{entity}")
    verbs = naming.get("verbs", {})

    def name_for(layer: str, entity: str) -> str:
        return template.format(verb=verbs.get(layer, layer), layer=layer, entity=entity)

    expected: list[str] = []
    sources = docs.get("02-sources.yaml", {})
    for source in sources.get("sources", []):
        for entity in source.get("entities", []):
            expected.append(name_for("bronze", entity["name"]))
    for table in docs.get("mappings/silver.yaml", {}).get("tables", []):
        expected.append(name_for("silver", table["target"]))
    gold = docs.get("mappings/gold.yaml", {})
    for item in gold.get("dimensions", []) + gold.get("facts", []):
        expected.append(name_for("gold", item["name"]))

    # Every layer pipeline must also be filed somewhere.
    for layer, pipeline in naming.get("pipelines", {}).items():
        expected.append(pipeline)

    unfiled = []
    for name in expected:
        if not any(
            name == pattern or (pattern.endswith("*") and name.startswith(pattern[:-1]))
            for pattern, _ in patterns
        ):
            unfiled.append(name)

    if unfiled:
        report.error(
            "folders",
            f"generated items matching no workspace folder: {unfiled}",
        )
    else:
        report.ok(f"folders: all {len(expected)} generated items map to a folder")


def check_table_prefixes(docs: dict, report: Report) -> None:
    """Each layer's tables carry that layer's prefix.

    Silver holds staging (stg_), gold holds the dimensional model (dim_/fct_).
    Without this, silver drifts back to dimensional names and produces tables a
    character apart from gold's -- in a different item, with different meaning,
    and no error anywhere.
    """
    platform = docs.get("00-platform.yaml", {})
    prefixes = platform.get("naming", {}).get("table_prefixes")
    if not prefixes:
        return

    offenders: list[str] = []

    allowed = tuple(prefixes.get("silver", []))
    if allowed:
        for table in docs.get("mappings/silver.yaml", {}).get("tables", []):
            if not table["target"].startswith(allowed):
                offenders.append(f"silver.{table['target']} (expected one of {list(allowed)})")

    allowed = tuple(prefixes.get("gold", []))
    if allowed:
        gold = docs.get("mappings/gold.yaml", {})
        for item in gold.get("dimensions", []) + gold.get("facts", []) + gold.get("views", []):
            if not item["name"].startswith(allowed):
                offenders.append(f"gold.{item['name']} (expected one of {list(allowed)})")

    if offenders:
        for offender in offenders:
            report.error("prefixes", offender)
    else:
        report.ok("prefixes: every table carries its layer's prefix")


def check_defect_coverage(docs: dict, report: Report) -> None:
    """Every documented upstream defect is handled by a named rule."""
    sources = docs.get("02-sources.yaml", {})
    silver = docs.get("mappings/silver.yaml", {})
    issues = {i["id"] for i in sources.get("known_issues", [])}
    if not issues:
        return

    handled = {
        rule["handles"]
        for table in silver.get("tables", [])
        for rule in table.get("rules", [])
        if rule.get("handles")
    }

    unhandled = issues - handled
    if unhandled:
        report.error("coverage", f"documented defects no rule handles: {sorted(unhandled)}")
    else:
        report.ok(f"coverage: all {len(issues)} documented defects are handled")

    phantom = handled - issues
    if phantom:
        report.warn("coverage", f"rules handling undocumented defect ids: {sorted(phantom)}")


def check_quality(docs: dict, report: Report) -> None:
    """Data-quality expectations name tables that some mapping produces."""
    dq = docs.get("04-data-quality.yaml", {})
    if not dq:
        return

    known = set()
    for table in docs.get("mappings/bronze.yaml", {}).get("tables", []):
        known.add(table["target"])
    for table in docs.get("mappings/silver.yaml", {}).get("tables", []):
        known.add(table["target"])
    gold = docs.get("mappings/gold.yaml", {})
    for item in gold.get("dimensions", []) + gold.get("facts", []):
        known.add(f"{item.get('schema', 'dbo')}.{item['name']}")
    for view in gold.get("views", []):
        known.add(f"{view.get('schema', 'bi')}.{view['name']}")

    unknown = [
        e["table"] for e in dq.get("expectations", [])
        if e.get("table") not in known
    ]
    if unknown:
        report.error("quality", f"expectations on tables no mapping produces: {unknown}")
    else:
        report.ok("quality: every expectation targets a real table")

    # A silver table with no expectations is a gap in the safety net.
    covered = {e["table"] for e in dq.get("expectations", [])}
    silver_targets = {t["target"] for t in docs.get("mappings/silver.yaml", {}).get("tables", [])}
    uncovered = silver_targets - covered
    if uncovered:
        report.warn("quality", f"silver tables with no expectations: {sorted(uncovered)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True, help="project spec directory")
    parser.add_argument("--lib", default=None, help="framework lib directory")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = parser.parse_args()

    spec_dir = Path(args.specs)
    if not spec_dir.is_dir():
        print(f"  ERROR  spec directory not found: {spec_dir}")
        return 2

    lib_dir = Path(args.lib) if args.lib else Path(__file__).resolve().parent.parent / "ttfabric"

    print(f"Validating specs in {spec_dir}")
    print(f"Against library     {lib_dir}")
    print()

    report = Report()
    docs = load_specs(spec_dir, report)
    if docs:
        check_key_types(docs, report)
        check_references(docs, report)
        check_rules(docs, registry_names(lib_dir, report), report)
        check_conventions(docs, report)
        check_workspace_folders(docs, report)
        check_table_prefixes(docs, report)
        check_defect_coverage(docs, report)
        check_quality(docs, report)

    return report.render(args.strict)


if __name__ == "__main__":
    sys.exit(main())
