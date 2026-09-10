"""
Generate semantic model documentation from the P1 spec.

Why generated rather than written
---------------------------------
Model documentation is the first artefact to go stale. A measure gets renamed,
a relationship is re-pointed, a column changes type -- and the document keeps
saying what used to be true, with nothing to signal that it no longer is. A
wrong document is worse than none: people stop checking the model because they
believe the page.

So the page is generated from the same spec the model is generated from, and
`--check` fails the build when the committed page no longer matches. The
document cannot drift from the model, because both come from one source.

What it documents that a model browser cannot
---------------------------------------------
Tables, columns, measures and relationships are all visible in Power BI. What
is not visible is WHY -- why this grain, why SCD2 here, why this measure is a
DISTINCTCOUNT rather than a COUNT, why the audit table is deliberately related
to nothing. Those live in the spec's `description` fields, and they are the
half of the documentation that a person actually needs.

Lineage is the other half: which gold table each model table reads, so a
question about a number can be traced back to the layer that produced it.

Usage:
    python generate_model_docs.py --specs ./01_demo-project --out ./01_demo-project/docs
    python generate_model_docs.py --specs ./01_demo-project --out ./01_demo-project/docs --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specs import load as load_spec

FILENAME = "SEMANTIC-MODEL.md"


def rule(title: str, level: int = 2) -> list[str]:
    return ["", "#" * level + " " + title, ""]


def table_block(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["_None declared._", ""]
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        cells = [str(c).replace("|", "\\|").replace("\n", " ") for c in row]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return out


def bare(name: str) -> str:
    """Drop a schema prefix: `dbo.fct_sales` -> `fct_sales`.

    The model names its source with a schema, F5 names the object without one.
    Comparing them raw matches nothing, and the lineage column then reads as
    'this table came from nowhere' for every row.
    """
    return str(name).split(".")[-1].strip().lower()


def gold_objects(project: Path) -> set[str]:
    """Every object F5 builds, when F5 is available.

    Optional on purpose: a model can be documented before gold is written, and
    refusing to document it then would make this useless exactly when someone
    is trying to understand a half-built project.
    """
    try:
        gold = load_spec(project, "gold")
    except (FileNotFoundError, KeyError, Exception):
        return set()
    names: set[str] = set()
    for key in ("dimensions", "facts", "views", "entities", "tables"):
        for entity in (gold.get(key) or []):
            if isinstance(entity, dict):
                value = entity.get("name") or entity.get("table")
            else:
                value = entity
            if value:
                names.add(bare(value))
    return names


def describe_source(source) -> str:
    """`{'item': 'wh_gold', 'kind': 'warehouse'}` reads badly on a page."""
    if isinstance(source, dict):
        item = source.get("item") or source.get("name") or "?"
        kind = source.get("kind")
        return f"`{item}`" + (f" ({kind})" if kind else "")
    return f"`{source}`" if source else "not declared"


def render(spec: dict, project: Path) -> str:
    model = spec.get("model", {})
    tables = spec.get("tables") or []
    measures = spec.get("measures") or []
    relationships = spec.get("relationships") or []
    hierarchies = spec.get("hierarchies") or []
    gold = gold_objects(project)

    lines: list[str] = [
        f"# {model.get('name', 'Semantic model')}",
        "",
        "GENERATED from `powerbi/01-semantic-model.yaml` by",
        "`framework/generators/generate_model_docs.py` — do not edit by hand.",
        "Change the spec and regenerate; `--check` fails the build on drift.",
        "",
    ]
    if model.get("description"):
        lines += [model["description"], ""]

    lines += rule("At a glance")
    lines += table_block(["", ""], [
        ["Storage mode", f"`{model.get('storage_mode', 'not declared')}`"],
        ["Source", describe_source(model.get("source"))],
        ["Culture", f"`{model.get('culture', 'not declared')}`"],
        ["Tables", str(len(tables))],
        ["Measures", str(len(measures))],
        ["Relationships", str(len(relationships))],
    ])

    # `direct_lake`, `directlake`, `DirectLake` all mean the same thing, and a
    # literal comparison silently drops the warning that matters most here.
    mode = str(model.get("storage_mode", "")).lower().replace("_", "").replace("-", "")
    if mode == "directlake":
        lines += [
            "> **DirectLake binds to tables, never to views.** Pointing a table at a",
            "> reporting view does not fail — the model silently falls back to",
            "> DirectQuery and gets slower. Validation refuses it for that reason.",
            "",
        ]

    # ---------------------------------------------------------------- tables
    lines += rule("Tables")
    rows = []
    for t in tables:
        src = t.get("source_table", "")
        built_by_gold = bare(src) in gold if gold else None
        rows.append([
            f"**{t.get('name','')}**",
            f"`{src}`" if src else "—",
            t.get("kind", "—"),
            str(len(t.get("columns") or [])),
            "—" if built_by_gold is None else ("F5 gold" if built_by_gold else "elsewhere"),
        ])
    lines += table_block(["Table", "Reads from", "Kind", "Columns", "Built by"], rows)

    if gold and any(bare(t.get("source_table", "")) not in gold for t in tables):
        lines += [
            "A table marked _elsewhere_ is not built by the gold stage — an audit or",
            "operational table, for instance. That is not a fault, but it does mean the",
            "star-schema lineage below does not describe it.",
            "",
        ]

    for t in tables:
        lines += rule(f"{t.get('name','')}", 3)
        if t.get("description"):
            lines += [t["description"], ""]
        cols = t.get("columns") or []
        # Columns declare source, format and hidden; TYPE is derived from gold at
        # generation time and is deliberately not restated here, because a type
        # written in two places is a type that will eventually disagree.
        crows = [[
            f"`{c.get('name','')}`",
            f"`{c.get('source_column', c.get('source', ''))}`" if (c.get("source_column") or c.get("source")) else "—",
            f"`{c['format']}`" if c.get("format") else "—",
            "hidden" if c.get("hidden") else "",
            c.get("description", "") or "",
        ] for c in cols]
        lines += table_block(["Column", "Source", "Format", "", "Notes"], crows)

    # -------------------------------------------------------------- measures
    lines += rule("Measures")
    folders: dict[str, list[dict]] = {}
    for m in measures:
        folders.setdefault(m.get("display_folder") or "General", []).append(m)

    lines += [f"{len(measures)} measures in {len(folders)} folder(s).", ""]
    for folder in sorted(folders):
        lines += rule(folder, 3)
        for m in sorted(folders[folder], key=lambda x: x.get("name", "")):
            lines += [f"**{m.get('name','')}**  ·  `{m.get('table','')}`"
                      + (f"  ·  format `{m['format']}`" if m.get("format") else "")]
            if m.get("description"):
                lines += ["", m["description"]]
            lines += ["", "```dax", str(m.get("expression", "")).strip(), "```", ""]

    # --------------------------------------------------------- relationships
    lines += rule("Relationships")
    rrows = [[
        f"`{r.get('from_table','')}[{r.get('from_column','')}]`",
        f"`{r.get('to_table','')}[{r.get('to_column','')}]`",
        r.get("cardinality", "—"),
        r.get("cross_filter", "—"),
    ] for r in relationships]
    lines += table_block(["From", "To", "Cardinality", "Cross filter"], rrows)

    related = {r.get("from_table") for r in relationships} | {r.get("to_table") for r in relationships}
    orphans = [t.get("name") for t in tables if t.get("name") not in related]
    if orphans:
        lines += [
            "**Deliberately unrelated:** " + ", ".join(f"`{o}`" for o in orphans) + ".",
            "",
            "A table related to nothing is usually a defect. Where it is intentional —",
            "an audit table describes the pipeline, not the business — joining it to a",
            "dimension would let someone slice it by a filter that had already been",
            "applied, and read the subset as the whole.",
            "",
        ]

    if hierarchies:
        lines += rule("Hierarchies")
        lines += table_block(
            ["Hierarchy", "Table", "Levels"],
            [[h.get("name", ""), h.get("table", ""),
              " → ".join(h.get("levels") or [])] for h in hierarchies])

    # --------------------------------------------------------------- lineage
    lines += rule("Where the numbers come from")
    lines += [
        "Each table reads a gold object built by the F5 stage, which reads silver,",
        "which reads bronze. A question about a number is answered by walking back",
        "through those layers rather than by inspecting the model alone.",
        "",
        "```",
        "sources  →  bronze  →  silver  →  gold  →  this model  →  reports",
        "```",
        "",
        "`dataops/02-audit.yaml` records row and value flow at every layer, so the",
        "arithmetic of a drop is available rather than reconstructed.",
        "",
    ]

    lines += rule("Verifying this model actually works")
    lines += [
        "Publishing proves the model parsed, not that it returns anything. A",
        "relationship on the wrong column publishes cleanly and returns blank.",
        "",
        "```bash",
        "python framework/tools/query_model.py --project <project> --smoke",
        "python framework/tools/verify_bindings.py --project <project> --env dev",
        "```",
        "",
        "`--smoke` evaluates every measure above at total level, which is the",
        "fastest way to find the one that returns blank.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True)
    parser.add_argument("--out", required=True, help="directory to write SEMANTIC-MODEL.md into")
    parser.add_argument("--check", action="store_true",
                        help="fail if the committed page differs from the spec")
    args = parser.parse_args()

    project = Path(args.specs)
    spec = load_spec(project, "semantic-model", track="powerbi")
    text = render(spec, project)
    target = Path(args.out) / FILENAME

    if args.check:
        if not target.exists():
            print(f"  ERROR  {target} does not exist. Run without --check to create it.")
            return 1
        if target.read_text(encoding="utf-8") != text:
            print(f"  ERROR  {target} is out of date with "
                  f"powerbi/01-semantic-model.yaml.")
            print(f"         Regenerate it rather than editing it by hand.")
            return 1
        print(f"  {target} matches the spec.")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

    model = spec.get("model", {})
    print(f"  wrote {target}")
    print(f"    model         {model.get('name','?')} ({model.get('storage_mode','?')})")
    print(f"    tables        {len(spec.get('tables') or [])}")
    print(f"    measures      {len(spec.get('measures') or [])}")
    print(f"    relationships {len(spec.get('relationships') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
