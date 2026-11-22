"""What is in a drive, read through disc-probe's `info` command."""

from __future__ import annotations

import enum
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .drives import normalize_letter
from .media import FAMILIES, Family, profile_for
from .procs import python_exe, run, tool_env

# SCSI sense (key/ASC/ASCQ) as `info` records it for TEST UNIT READY.
_TRAY_OPEN = "2/3A/02"
_NO_MEDIUM = "2/3A"
_BECOMING_READY = ("2/04", "6/28")


class ProbeError(RuntimeError):
    """disc-probe could not talk to the drive."""


class DiscState(enum.Enum):
    TRAY_OPEN = "tray open"
    NO_DISC = "no disc"
    LOADING = "loading"
    BLANK = "blank"
    ERASABLE = "has data, can be erased"
    WRITTEN = "written"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class DiscInfo:
    drive: str
    state: DiscState
    drive_name: str = ""
    media: str = ""  # "DVD+R", "CD-RW", ...
    family: str = ""  # key of media.FAMILIES; empty when unsupported
    mid: str = ""  # manufacturer code, e.g. SONY/D21/0
    sectors: int = 0  # capacity, what a preset needs; 0 when the drive does not say
    reason: str = ""  # why the disc is unsupported

    @property
    def can_calibrate(self) -> bool:
        return self.state in (DiscState.BLANK, DiscState.ERASABLE)

    @property
    def needs_erase(self) -> bool:
        return self.state is DiscState.ERASABLE

    @property
    def kind(self) -> Family | None:
        return FAMILIES.get(self.family)


def _parsed(report: dict, key: str) -> dict:
    return (report.get(key) or {}).get("parsed") or {}


def _mid(report: dict) -> str:
    """The manufacturer code in the form disc-probe prints it."""
    prerecorded = _parsed(report, "dvd_prerecorded_0e").get("manufacturer_id")
    if prerecorded:
        return str(prerecorded).replace("\x00", " ").strip()
    adip = _parsed(report, "dvd_plus_adip_11")
    if adip.get("manufacturer_id"):
        parts = (adip.get("manufacturer_id"), adip.get("media_type_id"), adip.get("product_revision"))
        return "/".join(str(p) for p in parts if p is not None)
    atip = _parsed(report, "atip")
    if atip.get("leadin_start_msf"):
        return f"ATIP {atip['leadin_start_msf']}"
    return ""


def _sectors(report: dict) -> int:
    """Capacity of the disc, in the order disc-probe's own `disexport` guidance trusts."""
    track = _parsed(report, "track_1")
    if track.get("blank") and track.get("free_blocks"):
        return int(track.get("track_start") or 0) + int(track["free_blocks"])
    atip_end = _parsed(report, "atip").get("last_leadout_start_lba")
    if atip_end:
        return int(atip_end)
    last_lba = _parsed(report, "capacity").get("last_lba")
    return int(last_lba) + 1 if last_lba else 0


def parse_info(drive: str, report: dict) -> DiscInfo:
    """Turn an `info.json` report into a DiscInfo."""
    drive = normalize_letter(drive)
    inquiry = _parsed(report, "inquiry")
    drive_name = " ".join(filter(None, (inquiry.get("vendor"), inquiry.get("product")))).strip()
    sense = (report.get("test_unit_ready") or {}).get("sense") or ""

    def info(state: DiscState, **fields) -> DiscInfo:
        return DiscInfo(drive=drive, state=state, drive_name=drive_name, **fields)

    if sense.startswith(_TRAY_OPEN):
        return info(DiscState.TRAY_OPEN)
    if sense.startswith(_NO_MEDIUM):
        return info(DiscState.NO_DISC)
    if sense.startswith(_BECOMING_READY):
        return info(DiscState.LOADING)

    configuration = _parsed(report, "configuration")
    code = configuration.get("current_profile")
    if not code:
        return info(DiscState.NO_DISC)

    profile = profile_for(code, configuration.get("current_profile_name") or "")
    if not profile.family:
        return info(DiscState.UNSUPPORTED, media=profile.label, reason=profile.reason)

    # A CD-R may not report a track record while empty, so only an explicit "not blank" overrides the status.
    blank = _parsed(report, "disc_information").get("disc_status") == "empty" and _parsed(report, "track_1").get("blank") is not False
    if blank:
        state = DiscState.BLANK
    elif profile.rewritable:
        state = DiscState.ERASABLE
    else:
        state = DiscState.WRITTEN
    return info(state, media=profile.label, family=profile.family, mid=_mid(report), sectors=_sectors(report))


def read_disc_info(discprobe_dir: Path, drive: str) -> DiscInfo:
    """Ask the drive what it holds. Read-only; the dump goes to a scratch folder."""
    drive = normalize_letter(drive)
    with tempfile.TemporaryDirectory(prefix="disctuner-info-") as scratch:
        result = run(
            [python_exe(), "-m", "discprobe", "info", "--drive", drive, "--out", scratch],
            cwd=discprobe_dir,
            env=tool_env(discprobe_dir),
        )
        reports = sorted(Path(scratch).glob("*/info.json"))
        if not reports:
            raise ProbeError(result.tail() or f"disc-probe could not open {drive}.")
        with open(reports[-1], encoding="utf-8") as f:
            return parse_info(drive, json.load(f))
