"""Write a DiscImageStudio project (.discproj) for a calibrated preset.

The project opens with File > Open in DiscImageStudio, so nobody has to find a preset in a list.
It has no image: the person adds one in the app. Its geometry is copied from the preset that
was just installed, so the project and the preset always agree.

CD and DVD projects are written. Blu-ray is not: its project block (`bluRay`) is not known yet,
so a guessed file could fail to open.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

from .paths import dis_presets_file, dis_projects_dir

GEOMETRY_KEYS = ("innerRadiusMm", "outerRadiusMm", "sectors", "pitchLinear", "pitchQuadratic", "pitchCubic")

# Per family: the preset list in disc-presets.json, the preset key that holds the sector count
# (DVD presets say totalSectors; DiscImageStudio's own DVD projects say sectors), and the output
# settings for that medium. CD: as written by 2.1.0. DVD: as in DiscImageStudio's own test.discproj.
PROJECT_FAMILIES = {
    "cd": {"presets": "cdPresets", "sectors": "sectors", "volumeLabel": "CD_IMAGE",
           "writeCue": True, "interleave": True},
    "dvd": {"presets": "dvdPresets", "sectors": "totalSectors", "volumeLabel": "DISC_IMAGE",
            "writeCue": False, "interleave": False},
}


class ProjectError(RuntimeError):
    """The project could not be written; the message is meant for the person at the screen."""


def supports(family: str) -> bool:
    """Whether a project is written for this disc family."""
    return family in PROJECT_FAMILIES


def preset_geometry(family: str, preset_id: str, presets_file: Path | None = None) -> dict:
    """The geometry of one preset in DiscImageStudio's preset file, in project terms."""
    spec = PROJECT_FAMILIES.get(family)
    if spec is None:
        raise ProjectError(f"no project is written for {family} discs.")
    path = presets_file or dis_presets_file()
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ProjectError(f"DiscImageStudio's preset file could not be read ({path}: {exc!r}).") from exc
    source = {key: spec["sectors"] if key == "sectors" else key for key in GEOMETRY_KEYS}
    for entry in data.get(spec["presets"], []):
        if entry.get("id") == preset_id:
            missing = [source[key] for key in GEOMETRY_KEYS if source[key] not in entry]
            if missing:
                raise ProjectError(f"preset {preset_id} has no {', '.join(missing)}.")
            return {key: entry[source[key]] for key in GEOMETRY_KEYS}
    raise ProjectError(f"preset {preset_id} is not in {path}.")


def project_document(name: str, family: str, geometry: dict, output_iso: Path) -> dict:
    """A schema 2 project with no layers: the person adds the image in DiscImageStudio."""
    spec = PROJECT_FAMILIES[family]
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "schemaVersion": 2,
        "name": name,
        "discFamily": family,
        "geometry": dict(geometry),
        "layers": [],
        "outputs": {
            "outputPath": str(output_iso),
            "writeCue": spec["writeCue"],
            "dataDirectory": "",
            "volumeLabel": spec["volumeLabel"],
            "interleave": spec["interleave"],
        },
        "calibrationSessionPath": None,
        "thumbnailPngBase64": None,
        "createdUtc": now,
        "modifiedUtc": now,
        "tone": {"ditherStrength": 0.75, "levels": 2, "brightness": 0, "srgbLinearization": False},
        "bluRay": None,
    }


def write_project(family: str, preset_id: str, name: str, folder: Path | None = None,
                  presets_file: Path | None = None) -> Path:
    """Write <folder>/<name>.discproj for the preset and return its path.

    An existing project with the same name is replaced, like the preset it matches.
    """
    folder = folder or dis_projects_dir()
    geometry = preset_geometry(family, preset_id, presets_file)
    stem = re.sub(r"[^A-Za-z0-9 ._(),-]+", "_", name).strip() or preset_id
    path = folder / f"{stem}.discproj"
    doc = project_document(name, family, geometry, folder / f"{stem}.iso")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise ProjectError(f"the project could not be written to {path} ({exc}).") from exc
    return path
