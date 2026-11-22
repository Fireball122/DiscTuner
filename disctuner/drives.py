"""Optical drives: listing, tray control."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .procs import powershell, run

_LETTER = re.compile(r"^\s*([A-Za-z]):?[\\/]?\s*$")


class DriveError(RuntimeError):
    """Windows would not tell us about, or move, the drive."""


@dataclass(frozen=True)
class Drive:
    letter: str  # "E:"
    name: str

    @property
    def label(self) -> str:
        return f"{self.letter}  {self.name}"


def normalize_letter(value: str) -> str:
    """'e', 'E:' and 'E:\\' all become 'E:'. Anything else is rejected before it reaches a shell."""
    match = _LETTER.match(value)
    if not match:
        raise ValueError(f"not a drive letter: {value!r}")
    return match.group(1).upper() + ":"


def list_drives() -> list[Drive]:
    result = run(
        powershell(["-Command", "Get-CimInstance Win32_CDROMDrive | Select-Object Drive, Name | ConvertTo-Json -Compress"]),
        timeout=30,
    )
    if not result.ok:
        raise DriveError(result.tail() or "Windows could not list optical drives.")
    text = result.stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise DriveError(f"Unreadable drive list: {text[:120]}") from exc
    entries = data if isinstance(data, list) else [data]
    drives = [Drive(normalize_letter(e["Drive"]), (e.get("Name") or "Optical drive").strip()) for e in entries if e.get("Drive")]
    return sorted(drives, key=lambda d: d.letter)


def eject(letter: str) -> None:
    letter = normalize_letter(letter)
    script = f"(New-Object -ComObject Shell.Application).NameSpace(17).ParseName('{letter}').InvokeVerb('Eject')"
    result = run(powershell(["-Command", script]), timeout=30)
    if not result.ok:
        raise DriveError(result.tail() or f"{letter} would not eject.")


def close_tray(letter: str) -> None:
    letter = normalize_letter(letter)
    script = (
        "$m = New-Object -ComObject IMAPI2.MsftDiscMaster2;"
        "foreach ($id in $m) {"
        "  $r = New-Object -ComObject IMAPI2.MsftDiscRecorder2; $r.InitializeDiscRecorder($id);"
        f"  if ($r.VolumePathNames -contains '{letter}\\') {{ $r.CloseTray(); exit 0 }}"
        "}"
        "exit 1"
    )
    result = run(powershell(["-Command", script]), timeout=30)
    if not result.ok:
        raise DriveError(f"{letter} could not close its tray. Push it in by hand.")
