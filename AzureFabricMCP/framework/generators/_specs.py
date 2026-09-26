"""
Locate a project's Fabric specs in whichever layout is in force.

The project is migrating from a flat `specs/` collection to track folders. Both
layouts work while stages move across, and every generator resolves through
here so the migration lives in one file rather than in each of them.

New layout wins when present:

    fabric/01-scaffolding.yaml   <-  specs/00-platform.yaml
    fabric/02-sources.yaml       <-  specs/02-sources.yaml
    fabric/03-bronze.yaml        <-  specs/mappings/bronze.yaml
    fabric/04-silver.yaml        <-  specs/mappings/silver.yaml
    fabric/05-gold.yaml          <-  specs/mappings/gold.yaml
"""

from __future__ import annotations

from pathlib import Path

import yaml

# stage -> (new path, legacy path)
LAYOUT = {
    "scaffolding": (Path("fabric") / "01-scaffolding.yaml", Path("specs") / "00-platform.yaml"),
    "sources":     (Path("fabric") / "02-sources.yaml",     Path("specs") / "02-sources.yaml"),
    "bronze":      (Path("fabric") / "03-bronze.yaml",      Path("specs") / "mappings" / "bronze.yaml"),
    "silver":      (Path("fabric") / "04-silver.yaml",      Path("specs") / "mappings" / "silver.yaml"),
    "gold":        (Path("fabric") / "05-gold.yaml",        Path("specs") / "mappings" / "gold.yaml"),
}

# Tracks added after the flat layout was retired have no legacy path.
TRACKS = {
    "powerbi": {
        "semantic-model": Path("powerbi") / "01-semantic-model.yaml",
        "reports":        Path("powerbi") / "02-reports.yaml",
    },
    "dataops": {
        "monitoring": Path("dataops") / "01-monitoring.yaml",
        "audit":      Path("dataops") / "02-audit.yaml",
    },
    "cicd": {
        "pipeline": Path("cicd") / "01-pipeline.yaml",
    },
}


def resolve(root: Path, stage: str, track: str = "fabric") -> Path:
    """Path to a stage's spec, preferring the track layout."""
    if track != "fabric":
        path = root / TRACKS[track][stage]
        if not path.exists():
            raise FileNotFoundError(f"no {track} spec for stage {stage!r}: {path}")
        return path

    new, legacy = LAYOUT[stage]

    # `root` may be the project directory or a spec directory, since the older
    # generators were invoked with --specs pointing straight at specs/.
    candidates = [root / new, root / legacy]
    if root.name == "specs":
        candidates += [root.parent / new, root / legacy.name,
                       root / "mappings" / legacy.name]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"no spec for stage {stage!r} under {root}. Looked for: "
        + ", ".join(str(c) for c in candidates)
    )


def load(root: Path, stage: str, track: str = "fabric") -> dict:
    return yaml.safe_load(resolve(root, stage, track).read_text(encoding="utf-8"))


def load_all(root: Path) -> dict[str, dict]:
    """Every Fabric stage a generator needs, keyed by stage name."""
    return {stage: load(root, stage) for stage in LAYOUT}
