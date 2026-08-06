"""
Generate GitHub Actions workflows from the C1 spec.

Writing workflows by hand means the gates in the spec and the gates actually
enforced drift apart, and the spec is the one people read. Generating them makes
the spec the only place either exists.

Authentication
--------------
Every deploying job authenticates with a service principal from repository
secrets. The framework's credential resolution already prefers
AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID over an interactive CLI
login, so nothing in the deploy scripts needs a CI-specific branch.

Approvals are NOT emitted as steps. A manual gate is enforced by a GitHub
Environment's required reviewers -- a step cannot pause for a human. The spec's
`gates` are rendered into the workflow header as a comment stating which
environment must carry which reviewers, so the connection is visible where
someone would look for it.

Usage:
    python generate_workflows.py --specs ./01_demo-project --out .
    python generate_workflows.py --specs ./01_demo-project --out . --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _specs import load as load_spec

PYTHON_VERSION = "3.12"


def quote(value: str) -> str:
    """Quote a YAML scalar when it would otherwise be misread.

    `on`, `no`, `y` and friends resolve to booleans under YAML 1.1, which is
    how a branch named `on` would silently become a workflow that never runs.
    """
    risky = {"on", "off", "yes", "no", "y", "n", "true", "false", "null", "~"}
    if (value.lower() in risky or any(c in value for c in ":#{}[],&*?|<>=!%@`")
            or value.strip() != value or not value):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def wrap(text: str, width: int = 74, prefix: str = "# ") -> list[str]:
    words = " ".join(text.split()).split(" ")
    lines, current = [], ""
    for word in words:
        if current and len(current) + len(word) + 1 > width:
            lines.append(prefix + current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(prefix + current)
    return lines


def triggers_yaml(triggers: dict) -> list[str]:
    # Quoted: bare `on:` is the canonical YAML 1.1 trap, and it lands precisely
    # here -- an unquoted key would parse as True and the workflow would never
    # trigger, with no error anywhere.
    lines = ['"on":']

    for event in ("pull_request", "push"):
        if event not in triggers:
            continue
        body = triggers[event] or {}
        lines.append(f"  {event}:")
        for key in ("branches", "tags"):
            if body.get(key):
                lines.append(f"    {key}:")
                lines += [f"      - {quote(v)}" for v in body[key]]

    if "schedule" in triggers:
        lines.append("  schedule:")
        for entry in triggers["schedule"]:
            lines.append(f"    - cron: {quote(entry['cron'])}")

    if "workflow_dispatch" in triggers:
        lines.append("  workflow_dispatch:")

    return lines


def gates_for(gates: list[dict], environments: set[str]) -> list[str]:
    """Comment block naming the manual gates that guard these environments."""
    manual = [g for g in gates
              if not g.get("automated") and set(g["blocks"]) & environments]
    if not manual:
        return []

    lines = ["#", "# Manual gates guarding this workflow:"]
    for gate in manual:
        approvers = ", ".join(gate.get("approvers") or []) or "UNNAMED"
        lines.append(f"#   {gate['id']} -- approvers: {approvers}")
        if gate.get("criteria"):
            lines += wrap(gate["criteria"], prefix="#     ")
    lines += ["#",
              "# These are enforced by required reviewers on the GitHub",
              "# Environment, NOT by anything in this file. A step cannot wait",
              "# for a person. If the Environment has no reviewers configured,",
              "# there is no approval gate however many are declared here."]
    return lines


def workflow_yaml(workflow: dict, spec: dict) -> str:
    gates = spec.get("gates") or []
    secrets = (spec.get("secrets") or {}).get("required") or []
    environments = {j["environment"] for j in workflow["jobs"] if j.get("environment")}

    lines = [
        f"# {workflow['name']}",
        "#",
        "# GENERATED from cicd/01-pipeline.yaml by",
        "# framework/generators/generate_workflows.py -- do not edit by hand.",
        "# Change the spec and regenerate; ci-validate fails on drift.",
    ]
    if workflow.get("description"):
        lines.append("#")
        lines += wrap(workflow["description"])
    lines += gates_for(gates, environments)
    lines.append("")

    lines.append(f"name: {quote(workflow['name'])}")
    lines.append("")
    lines += triggers_yaml(workflow["triggers"])
    lines.append("")

    # Least privilege. A deploy workflow has no reason to write to the repo,
    # and id-token is what a federated credential would need later.
    lines += ["permissions:", "  contents: read", "  id-token: write", ""]
    lines += ["concurrency:",
              "  group: ${{ github.workflow }}-${{ github.ref }}",
              # Deployments are not cancelled midway: a half-applied migration
              # is worse than a queued run.
              "  cancel-in-progress: false", ""]
    lines.append("jobs:")

    for job in workflow["jobs"]:
        lines.append(f"  {job['id']}:")
        if job.get("description"):
            lines += [f"    {line}" for line in wrap(job["description"], prefix="# ")]
        lines.append("    runs-on: ubuntu-latest")

        # A deploying job is skipped until a repository variable turns it on.
        #
        # Without this, merging the workflows makes every push to a deploy
        # branch go red -- not because anything is wrong, but because the
        # secrets and Environments do not exist yet. A red build that means
        # "not configured" is one people learn to ignore.
        #
        # `vars` is used rather than `secrets` because the secrets context is
        # not available in a job-level `if`.
        guard = spec.get("deploy_guard")
        if guard and job.get("environment"):
            lines.append(f"    if: vars.{guard} == 'true'")

        if job.get("environment"):
            lines.append(f"    environment: {quote(job['environment'])}")
        if job.get("needs"):
            lines.append(f"    needs: [{', '.join(job['needs'])}]")

        if secrets and job.get("environment"):
            lines.append("    env:")
            for name in secrets:
                lines.append(f"      {name}: ${{{{ secrets.{name} }}}}")

        lines.append("    steps:")
        lines += [
            "      - uses: actions/checkout@v4",
            "",
        ]

        # The framework lives in another repository, so CI must fetch it before
        # any step can run. Checked out to the SAME relative path the steps
        # already use, so nothing about them changes.
        framework = spec.get("framework")
        if framework:
            lines += [
                f"      - name: Check out the framework ({framework.get('ref', 'default')})",
                "        uses: actions/checkout@v4",
                "        with:",
                f"          repository: {quote(framework['repository'])}",
            ]
            if framework.get("ref"):
                lines.append(f"          ref: {quote(framework['ref'])}")
            lines.append(f"          path: {quote(framework['path'])}")
            if framework.get("token_secret"):
                lines.append(
                    f"          token: ${{{{ secrets.{framework['token_secret']} }}}}")
            lines.append("")

        # requirements.txt lives inside the framework, so its path moves with
        # the framework. Hardcoding `framework/requirements.txt` worked only
        # while the two shared a repository, and failed silently on the split:
        # the checkout succeeds, and the FIRST thing to break is pip, whose
        # error names a missing file rather than a misplaced checkout.
        requirements = "framework/requirements.txt"
        if framework:
            requirements = f"{framework['path'].rstrip('/')}/framework/requirements.txt"

        lines += [
            "      - uses: actions/setup-python@v5",
            "        with:",
            f"          python-version: {quote(PYTHON_VERSION)}",
            "          cache: pip",
            "",
            "      - name: Install dependencies",
            "        run: |",
            "          python -m pip install --upgrade pip",
            f"          pip install -r {requirements}",
            "",
        ]
        for step in job["steps"]:
            name = step.split("/")[-1].split(" ")[0] if "/" in step else step[:60]
            lines.append(f"      - name: {quote(name)}")
            lines.append(f"        run: {step}")
            lines.append("")

        while lines and lines[-1] == "":
            lines.pop()
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def referenced_scripts(spec: dict) -> list[tuple[str, str]]:
    """Every `python <path>` a step invokes, as (workflow, path)."""
    found = []
    for workflow in spec.get("workflows", []):
        for job in workflow["jobs"]:
            for step in job["steps"]:
                parts = step.split()
                for index, token in enumerate(parts):
                    if token == "python" and index + 1 < len(parts):
                        found.append((workflow["name"], parts[index + 1]))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True)
    parser.add_argument("--out", required=True,
                        help="repository root; workflows go to .github/workflows/")
    parser.add_argument("--repo-root", default=None,
                        help="where step paths are resolved from (default: --out)")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    spec = load_spec(Path(args.specs), "pipeline", track="cicd")
    out = Path(args.out)
    root = Path(args.repo_root) if args.repo_root else out

    # A step naming a script that does not exist fails only once CI is running,
    # on someone else's pull request. Resolve them now.
    missing = [(w, p) for w, p in referenced_scripts(spec) if not (root / p).exists()]
    if missing:
        print("  ERROR  these steps reference scripts that do not exist:")
        for workflow, path in missing:
            print(f"           {workflow}: {path}")
        print(f"         Resolved against {root}. A workflow generated from this "
              f"would fail\n         only once CI was already running.")
        return 1

    planned = {Path(w["file"]): workflow_yaml(w, spec) for w in spec["workflows"]}

    if args.check:
        drifted = [str(p) for p, text in planned.items()
                   if not (out / p).exists()
                   or (out / p).read_text(encoding="utf-8") != text]
        if drifted:
            print("Generated workflows are out of date with the spec:")
            for path in drifted:
                print(f"  {path}")
            return 1
        print(f"All {len(planned)} workflows match the spec.")
        return 0

    for path, text in planned.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    print(f"Generated {len(planned)} workflows in {out / '.github' / 'workflows'}")
    for workflow in spec["workflows"]:
        jobs = len(workflow["jobs"])
        steps = sum(len(j["steps"]) for j in workflow["jobs"])
        print(f"  {workflow['name']:<18} {jobs} jobs, {steps} steps")

    manual = [g for g in spec.get("gates") or [] if not g.get("automated")]
    if manual:
        print()
        print("Manual gates are NOT in these files -- configure required reviewers")
        print("on the GitHub Environments, or they do not exist:")
        for gate in manual:
            print(f"  {gate['id']:<16} {', '.join(gate.get('approvers') or ['UNNAMED'])}"
                  f"  -> environments: {', '.join(gate['blocks'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
