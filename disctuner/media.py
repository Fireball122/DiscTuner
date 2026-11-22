"""Which discs DiscTuner can calibrate, and how each family is handled.

The three families differ in how disc-probe measures them, not in how DiscTuner drives it:
`--media` selects disc-probe's own defaults for the test pattern, read speed and measurement.
What is kept here is only what DiscTuner itself has to know.
"""

from __future__ import annotations

from dataclasses import dataclass

SECTOR_BYTES = 2048


@dataclass(frozen=True)
class Family:
    key: str  # disc-probe's --media value
    name: str
    pattern_file: str  # test pattern file under disc-probe's iso/
    pattern_sectors: int  # disc-probe's default pattern length for this family
    close_margin: int  # sectors a burner needs beyond the image to close the disc
    write_speed: int  # IMAPI multiples of 1x; 0 lets the drive choose
    measure_points: int  # how many steps the measurement reports, for progress
    measure_unit: str
    geo_args: tuple[str, ...]  # extra `geo` arguments beyond --media, --drive and --speed
    keep_pattern: bool  # False when the pattern is too large to leave on disk
    speeds: tuple[str, ...] = ()  # read speeds to try in turn; empty leaves disc-probe's default
    note: str = ""  # a caveat to show with the finished preset

    def pattern_length(self, capacity: int) -> int:
        """Sectors to burn: the default, or less on a smaller disc (a 74-minute CD, say)."""
        if capacity and self.pattern_sectors + self.close_margin > capacity:
            return capacity - self.close_margin
        return self.pattern_sectors


FAMILIES: dict[str, Family] = {
    "dvd": Family(
        key="dvd", name="DVD", pattern_file="random.iso", pattern_sectors=2_295_104, close_margin=0,
        write_speed=4, measure_points=22, measure_unit="Point", geo_args=(), speeds=("keep",),
        keep_pattern=True,
    ),
    "cd": Family(
        key="cd", name="CD", pattern_file="cd-random.iso", pattern_sectors=359_600, close_margin=247,
        write_speed=8, measure_points=36, measure_unit="Point", geo_args=(), speeds=("2822", "4234", "1411"),
        keep_pattern=True,
        note="A CD preset from timing leaves one constant unmeasured. Fine-calibrate it once in "
             "DiscImageStudio with a second blank disc.",
    ),
    "bd": Family(
        key="bd", name="Blu-ray", pattern_file="bd-random.iso", pattern_sectors=12_219_392, close_margin=0,
        write_speed=0, measure_points=25, measure_unit="Zone", geo_args=("--bd-zones", "25"),
        keep_pattern=False,
        note="Blu-ray calibration is experimental. Check the preset with a test burn before a picture.",
    ),
}


@dataclass(frozen=True)
class Profile:
    """A disc type as the drive reports it (MMC current profile)."""

    label: str
    family: str  # key of FAMILIES, or "" when the disc cannot be calibrated
    rewritable: bool = False
    reason: str = ""  # why it is unsupported


_UNSUPPORTED_DL = "Dual-layer discs are not supported"
_UNSUPPORTED_READONLY = "This kind of disc cannot be burned"

PROFILES: dict[int, Profile] = {
    0x08: Profile("CD-ROM", "", reason=_UNSUPPORTED_READONLY),
    0x09: Profile("CD-R", "cd"),
    0x0A: Profile("CD-RW", "cd", rewritable=True),
    0x10: Profile("DVD-ROM", "", reason=_UNSUPPORTED_READONLY),
    0x11: Profile("DVD-R", "dvd"),
    0x12: Profile("DVD-RAM", "", reason=_UNSUPPORTED_READONLY),
    0x13: Profile("DVD-RW", "dvd", rewritable=True),
    0x14: Profile("DVD-RW", "dvd", rewritable=True),
    0x15: Profile("DVD-R DL", "", reason=_UNSUPPORTED_DL),
    0x16: Profile("DVD-R DL", "", reason=_UNSUPPORTED_DL),
    0x1A: Profile("DVD+RW", "dvd", rewritable=True),
    0x1B: Profile("DVD+R", "dvd"),
    0x2A: Profile("DVD+RW DL", "", reason=_UNSUPPORTED_DL),
    0x2B: Profile("DVD+R DL", "", reason=_UNSUPPORTED_DL),
    0x40: Profile("BD-ROM", "", reason=_UNSUPPORTED_READONLY),
    0x41: Profile("BD-R", "bd"),
    0x42: Profile("BD-R", "bd"),
    0x43: Profile("BD-RE", "bd", rewritable=True),
}


def profile_for(code: int | None, name: str = "") -> Profile:
    if code in PROFILES:
        return PROFILES[code]
    return Profile(name or "Unknown disc", "", reason="DiscTuner does not recognise this disc type")
