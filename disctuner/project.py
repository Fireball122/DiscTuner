"""Write a DiscImageStudio project (.discproj) for a calibrated CD preset.

The project opens with File > Open in DiscImageStudio, so nobody has to find a preset in a list.
It has no image: the person adds one in the app. Its geometry is copied from the preset that
was just installed, so the project and the preset always agree.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

from .paths import dis_presets_file, dis_projects_dir

GEOMETRY_KEYS = ("innerRadiusMm", "outerRadiusMm", "sectors", "pitchLinear", "pitchQuadratic", "pitchCubic")


class ProjectError(RuntimeError):
    """The project could not be written; the message is meant for the person at the screen."""


def preset_geometry(preset_id: str, presets_file: Path | None = None) -> dict:
    """The geometry of one CD preset in DiscImageStudio's preset file."""
    path = presets_file or dis_presets_file()
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ProjectError(f"DiscImageStudio's preset file could not be read ({path}: {exc!r}).") from exc
    for entry in data.get("cdPresets", []):
        if entry.get("id") == preset_id:
            missing = [key for key in GEOMETRY_KEYS if key not in entry]
            if missing:
                raise ProjectError(f"preset {preset_id} has no {', '.join(missing)}.")
            return {key: entry[key] for key in GEOMETRY_KEYS}
    raise ProjectError(f"preset {preset_id} is not in {path}.")


def project_document(name: str, geometry: dict, output_iso: Path) -> dict:
    """A schema 2 CD project with no layers: the person adds the image in DiscImageStudio."""
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "schemaVersion": 2,
        "name": name,
        "discFamily": "cd",
        "geometry": dict(geometry),
        "layers": [],
        "outputs": {
            "outputPath": str(output_iso),
            "writeCue": True,
            "dataDirectory": "",
            "volumeLabel": "CD_IMAGE",
            "interleave": True,
        },
        "calibrationSessionPath": None,
        "thumbnailPngBase64": None,
        "createdUtc": now,
        "modifiedUtc": now,
        "tone": {"ditherStrength": 0.75, "levels": 2, "brightness": 0, "srgbLinearization": False},
        "bluRay": None,
    }


def write_project(preset_id: str, name: str, folder: Path | None = None,
                  presets_file: Path | None = None) -> Path:
    """Write <folder>/<name>.discproj for the preset and return its path.

    An existing project with the same name is replaced, like the preset it matches.
    """
    folder = folder or dis_projects_dir()
    geometry = preset_geometry(preset_id, presets_file)
    stem = re.sub(r"[^A-Za-z0-9 ._(),-]+", "_", name).strip() or preset_id
    path = folder / f"{stem}.discproj"
    doc = project_document(name, geometry, folder / f"{stem}.iso")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise ProjectError(f"the project could not be written to {path} ({exc}).") from exc
    return path
