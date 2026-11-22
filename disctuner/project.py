"""Write a DiscImageStudio project (.discproj) for a calibrated preset.

The project opens with File > Open in DiscImageStudio, so nobody has to find a preset in a list.
It has no image: the person adds one in the app. Its geometry is copied from the preset that
was just installed, so the project and the preset always agree.

The shape follows DiscImageStudio 1.5.0's own project loader (ProjectJsonStore.Validate) and what
its "new project" dialog writes for each disc family:

* CD and DVD presets carry pitch coefficients; a Blu-ray preset does not (its pitch is nominal),
  so a Blu-ray project writes zeros, as the app does.
* `usableSectors` is the preset's value if it is valid, else the app's own default: total - 2 on a
  CD, (total - 608) rounded down to 16 on a DVD, total rounded down to 32 on a Blu-ray.
* A Blu-ray project also has a `bluRay` block holding the polarity of the luminance mapping.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

from .paths import dis_presets_file, dis_projects_dir

# Per family: the preset list in disc-presets.json, the preset key that holds the sector count
# (CD presets say sectors, DVD and Blu-ray presets say totalSectors; the project always says
# sectors), whether the preset has pitch coefficients, the sector multiple `usableSectors` must
# be, the app's default reserve, and the output settings for that medium.
PROJECT_FAMILIES = {
    "cd": {"presets": "cdPresets", "sectors": "sectors", "pitch": True, "unit": 1, "reserve": 2,
           "volumeLabel": "CD_IMAGE", "writeCue": True, "interleave": True},
    "dvd": {"presets": "dvdPresets", "sectors": "totalSectors", "pitch": True, "unit": 16, "reserve": 608,
            "volumeLabel": "DISC_IMAGE", "writeCue": False, "interleave": False},
    "bd": {"presets": "bluRayPresets", "sectors": "totalSectors", "pitch": False, "unit": 32, "reserve": 0,
           "volumeLabel": "BD_IMAGE", "writeCue": False, "interleave": False},
}

BD_SECTORS_PER_LAYER = 12_219_392   # DiscImageStudio.Core.ProjectDocument.BluRaySectorsPerLayer
BD_MAX_OUTER_RADIUS_MM = 60.0


class ProjectError(RuntimeError):
    """The project could not be written; the message is meant for the person at the screen."""


def supports(family: str) -> bool:
    """Whether a project is written for this disc family."""
    return family in PROJECT_FAMILIES


def usable_sectors(spec: dict, total: int, preset_value) -> int:
    """The preset's usableSectors when the app would accept it, else the app's default."""
    unit = spec["unit"]
    if isinstance(preset_value, int) and 0 < preset_value <= total and preset_value % unit == 0:
        return preset_value
    return max(total - spec["reserve"], 0) // unit * unit


def preset_geometry(family: str, preset_id: str, presets_file: Path | None = None) -> tuple[dict, dict | None]:
    """The geometry of one preset in DiscImageStudio's preset file, and its `bluRay` block (or None)."""
    spec = PROJECT_FAMILIES.get(family)
    if spec is None:
        raise ProjectError(f"no project is written for {family} discs.")
    path = presets_file or dis_presets_file()
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ProjectError(f"DiscImageStudio's preset file could not be read ({path}: {exc!r}).") from exc
    wanted = ["innerRadiusMm", "outerRadiusMm", spec["sectors"]]
    if spec["pitch"]:
        wanted += ["pitchLinear", "pitchQuadratic", "pitchCubic"]
    for entry in data.get(spec["presets"], []):
        if entry.get("id") != preset_id:
            continue
        missing = [key for key in wanted if key not in entry]
        if missing:
            raise ProjectError(f"preset {preset_id} has no {', '.join(missing)}.")
        total = int(entry[spec["sectors"]])
        geometry = {"innerRadiusMm": entry["innerRadiusMm"], "outerRadiusMm": entry["outerRadiusMm"], "sectors": total}
        for key in ("pitchLinear", "pitchQuadratic", "pitchCubic"):
            geometry[key] = entry[key] if spec["pitch"] else 0.0
        geometry["usableSectors"] = usable_sectors(spec, total, entry.get("usableSectors"))
        if family == "bd":
            _check_blu_ray(preset_id, geometry)
            return geometry, {"invertMapping": bool(entry.get("invertMapping", False))}
        return geometry, None
    raise ProjectError(f"preset {preset_id} is not in {path}.")


def _check_blu_ray(preset_id: str, geometry: dict) -> None:
    """DiscImageStudio refuses a Blu-ray project outside these limits; say so now, not at File > Open."""
    total = geometry["sectors"]
    if total <= 0 or total % 32 or total > BD_SECTORS_PER_LAYER:
        raise ProjectError(f"preset {preset_id} has {total:,} sectors; a Blu-ray project needs a multiple "
                           f"of 32 up to {BD_SECTORS_PER_LAYER:,}.")
    if not 0 < geometry["innerRadiusMm"] < geometry["outerRadiusMm"] <= BD_MAX_OUTER_RADIUS_MM:
        raise ProjectError(f"preset {preset_id} has radii {geometry['innerRadiusMm']} to "
                           f"{geometry['outerRadiusMm']} mm; a Blu-ray project needs 0 < inner < outer <= "
                           f"{BD_MAX_OUTER_RADIUS_MM:g} mm.")


def project_document(name: str, family: str, geometry: dict, output_iso: Path, blu_ray: dict | None = None) -> dict:
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
        "bluRay": blu_ray,
    }


def write_project(family: str, preset_id: str, name: str, folder: Path | None = None,
                  presets_file: Path | None = None) -> Path:
    """Write <folder>/<name>.discproj for the preset and return its path.

    An existing project with the same name is replaced, like the preset it matches.
    """
    folder = folder or dis_projects_dir()
    geometry, blu_ray = preset_geometry(family, preset_id, presets_file)
    stem = re.sub(r"[^A-Za-z0-9 ._(),-]+", "_", name).strip() or preset_id
    path = folder / f"{stem}.discproj"
    doc = project_document(name, family, geometry, folder / f"{stem}.iso", blu_ray)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise ProjectError(f"the project could not be written to {path} ({exc}).") from exc
    return path
