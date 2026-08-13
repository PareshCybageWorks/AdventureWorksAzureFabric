"""
Sync the silver contract's rule enum with the cleansing library.

The F4 contract lists the permitted `fn:` values as a JSON Schema enum. That
gives editors autocomplete and inline errors, and makes a typo fail at schema
validation rather than at run time in Spark.

But an enum is a second copy of something the library already defines, and a
second copy drifts. So it is GENERATED from ttfabric/cleansing.py `REGISTRY`,
and `validate.py` refuses to pass if the generated enum no longer matches the
library. Adding a rule is then: write the function, register it, run this.

REGISTRY is read with `ast` rather than imported, so this works without PySpark
installed -- the same reason validate_specs reads it that way.

Usage:
    python sync_contract_rules.py                 # write the enum
    python sync_contract_rules.py --check         # fail if stale, write nothing
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parent.parent
LIBRARY = FRAMEWORK / "ttfabric" / "cleansing.py"
CONTRACT = FRAMEWORK / "contracts" / "fabric" / "04-silver.schema.json"


def registry_names(library: Path) -> list[str]:
    """Extract REGISTRY keys without importing the module."""
    tree = ast.parse(library.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "REGISTRY":
                if isinstance(node.value, ast.Dict):
                    return sorted(
                        key.value for key in node.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    )

    raise ValueError(f"REGISTRY not found in {library}")


def load_contract(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def current_enum(contract: dict) -> list[str]:
    return contract.get("$defs", {}).get("ruleName", {}).get("enum", [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail if the contract is stale; write nothing")
    parser.add_argument("--library", default=str(LIBRARY))
    parser.add_argument("--contract", default=str(CONTRACT))
    args = parser.parse_args()

    library = Path(args.library)
    contract_path = Path(args.contract)

    if not library.exists():
        print(f"  ERROR  library not found: {library}")
        return 2
    if not contract_path.exists():
        print(f"  ERROR  contract not found: {contract_path}")
        return 2

    rules = registry_names(library)
    contract = load_contract(contract_path)
    existing = current_enum(contract)

    if existing == rules:
        print(f"  contract enum matches the library ({len(rules)} rules)")
        return 0

    added = [r for r in rules if r not in existing]
    removed = [r for r in existing if r not in rules]

    if args.check:
        print("  ERROR  the silver contract's rule enum is stale")
        if added:
            print(f"           in the library but not the contract: {added}")
        if removed:
            print(f"           in the contract but not the library: {removed}")
        print("         run: python AzureFabricMCP/framework/generators/sync_contract_rules.py")
        return 1

    contract.setdefault("$defs", {}).setdefault("ruleName", {})
    contract["$defs"]["ruleName"] = {
        "description": (
            "GENERATED from ttfabric/cleansing.py REGISTRY by "
            "generators/sync_contract_rules.py. Do not edit by hand -- add the "
            "function to the library, register it, and re-run the sync. "
            "validate.py fails if this list drifts from the library."
        ),
        "enum": rules,
    }
    contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")

    print(f"  updated {contract_path.name} ({len(rules)} rules)")
    if added:
        print(f"    added:   {added}")
    if removed:
        print(f"    removed: {removed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
