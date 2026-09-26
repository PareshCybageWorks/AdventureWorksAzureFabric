"""
Export folder structure from a Fabric workspace.

Why this exists
---------------
Workspace folders cannot be created programmatically -- they are a UI-only feature.
When deploying to a new workspace, you need to manually recreate the folder structure
from an existing workspace. This tool reads the structure from a source workspace and
outputs it in a format you can use as a checklist for manual recreation, or as a
reference for consistency across workspaces.

Usage:
    python export_workspace_structure.py --project ./rahul_demo --env dev --output structure.txt
    python export_workspace_structure.py --workspace ab3f1a80-23e9-4360-a513-20daa0257b10 --format yaml
    python export_workspace_structure.py --workspace ab3f1a80-23e9-4360-a513-20daa0257b10 --format tree

Outputs:
    - tree: Visual folder hierarchy (default)
    - checklist: Markdown checklist for manual folder creation
    - yaml: YAML structure (compatible with workspace_folders in spec)
    - json: JSON structure (for programmatic processing)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _project import get_workspace_id  # noqa: E402

API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"


def get_token() -> str:
    from azure.identity import AzureCliCredential
    return AzureCliCredential().get_token(SCOPE).token


def list_items(workspace: str, headers: dict) -> list[dict]:
    """Fetch all items from a workspace."""
    items = []
    response = requests.get(f"{API}/workspaces/{workspace}/items",
                           headers=headers, timeout=60)
    response.raise_for_status()
    items.extend(response.json().get("value", []))
    return items


def build_folder_tree(items: list[dict]) -> dict:
    """Build hierarchical folder structure from items."""
    # Group items by folder
    by_folder = defaultdict(list)
    folders_seen = set()
    root_items = []

    for item in items:
        folder_path = item.get("path", "")
        item_info = {
            "name": item.get("displayName", item.get("name", "unknown")),
            "type": item.get("type", "Unknown"),
            "id": item.get("id"),
        }

        if folder_path:
            # Path is like "/folder1/subfolder2"
            parts = [p for p in folder_path.split("/") if p]
            folders_seen.add(tuple(parts))
            by_folder[folder_path].append(item_info)
        else:
            root_items.append(item_info)

    return {
        "root_items": root_items,
        "by_folder": dict(by_folder),
        "folders": sorted(list(folders_seen)),
    }


def format_tree(structure: dict) -> str:
    """Output as visual tree."""
    output = []
    output.append("WORKSPACE FOLDER STRUCTURE\n" + "=" * 50)

    # Root items
    if structure["root_items"]:
        output.append("\n[Workspace Root] (items not in folders):")
        for item in sorted(structure["root_items"], key=lambda x: x["name"]):
            output.append(f"   +- {item['name']} [{item['type']}]")

    # Folders with items
    if structure["folders"]:
        output.append("\n[Folder Structure]:")
        for folder_parts in structure["folders"]:
            indent = "   " + "   " * (len(folder_parts) - 1)
            folder_name = folder_parts[-1] if folder_parts else "."
            output.append(f"{indent}+- {folder_name}/")

            # Items in this folder
            folder_path = "/" + "/".join(folder_parts)
            items = structure["by_folder"].get(folder_path, [])
            for item in sorted(items, key=lambda x: x["name"]):
                sub_indent = indent + "   "
                output.append(f"{sub_indent}+- {item['name']} [{item['type']}]")
    else:
        output.append("\nNo folders found. Items are at workspace root.")

    return "\n".join(output)


def format_checklist(structure: dict) -> str:
    """Output as markdown checklist for manual creation."""
    output = []
    output.append("# Workspace Folder Structure Checklist\n")
    output.append("Create these folders manually in Fabric Admin Portal:\n")

    for folder_parts in structure["folders"]:
        folder_path = " / ".join(folder_parts)
        checkbox = "- [ ]"
        output.append(f"{checkbox} `{folder_path}`")

    if structure["folders"]:
        output.append("\n## Items to Organize\n")
        for folder_parts in structure["folders"]:
            folder_path = "/" + "/".join(folder_parts)
            items = structure["by_folder"].get(folder_path, [])
            if items:
                output.append(f"\n### {' / '.join(folder_parts)}\n")
                for item in sorted(items, key=lambda x: x["name"]):
                    output.append(f"- {item['name']} ({item['type']})")

    return "\n".join(output)


def format_yaml(structure: dict) -> str:
    """Output as YAML structure compatible with workspace_folders."""
    output = []
    output.append("# Folder structure (compatible with workspace_folders in spec)")
    output.append("")

    for folder_parts in structure["folders"]:
        if len(folder_parts) == 1:
            # Top-level folder
            folder_name = folder_parts[0]
            output.append(f"- name: \"{folder_name}\"")
            output.append("  layer: null")

            # Find subfolders
            subfolder_items = structure["by_folder"].get("/" + folder_name, [])
            if subfolder_items:
                output.append("  contains:")
                for item in sorted(subfolder_items, key=lambda x: x["name"]):
                    output.append(f"    - {{ item_type: {item['type']}, name: {item['name']} }}")

    return "\n".join(output)


def format_json(structure: dict) -> str:
    """Output as JSON."""
    export_data = {
        "folders": [],
        "root_items": structure["root_items"],
    }

    for folder_parts in structure["folders"]:
        folder_path = "/" + "/".join(folder_parts)
        items = structure["by_folder"].get(folder_path, [])
        export_data["folders"].append({
            "path": folder_path,
            "parts": folder_parts,
            "items": items,
        })

    return json.dumps(export_data, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Project directory (or use --workspace)")
    parser.add_argument("--env", default="dev", help="Environment name")
    parser.add_argument("--workspace", help="Workspace ID (or derive from --project)")
    parser.add_argument("--format", choices=["tree", "checklist", "yaml", "json"],
                       default="tree", help="Output format")
    parser.add_argument("--output", help="Output file (default: stdout)")
    args = parser.parse_args()

    # Get workspace ID
    if args.workspace:
        workspace = args.workspace
    elif args.project:
        try:
            workspace = get_workspace_id(Path(args.project).resolve(), args.env)
        except (FileNotFoundError, KeyError, ValueError) as exc:
            print(f"ERROR  {exc}", file=sys.stderr)
            return 2
    else:
        print("ERROR  Provide --project or --workspace", file=sys.stderr)
        return 2

    # Fetch items
    try:
        headers = {"Authorization": f"Bearer {get_token()}",
                   "Content-Type": "application/json"}
        items = list_items(workspace, headers)
        print(f"INFO   Fetched {len(items)} items from workspace {workspace[:8]}...",
              file=sys.stderr)
    except Exception as exc:
        print(f"ERROR  Failed to fetch items: {exc}", file=sys.stderr)
        return 1

    # Build structure
    structure = build_folder_tree(items)
    print(f"INFO   Found {len(structure['folders'])} folders", file=sys.stderr)

    # Format output
    if args.format == "tree":
        output = format_tree(structure)
    elif args.format == "checklist":
        output = format_checklist(structure)
    elif args.format == "yaml":
        output = format_yaml(structure)
    elif args.format == "json":
        output = format_json(structure)

    # Write output
    if args.output:
        Path(args.output).write_text(output)
        print(f"INFO   Wrote to {args.output}", file=sys.stderr)
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
