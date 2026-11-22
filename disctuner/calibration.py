"""The calibration pipeline: (erase), burn a test pattern, read it back, install the preset.

Everything heavy is delegated to disc-probe (pattern, geometry fit, preset export) and to
its IMAPI2 burn script. This module sequences those steps, turns their output into
progress events, and stops cleanly when cancelled.
"""

from __future__ import annotations

import enum
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .disc import DiscInfo, DiscState, ProbeError, read_disc_info
from .media import SECTOR_BYTES, Family
from .procs import CancelToken, powershell, python_exe, stream, tool_env
from .project import ProjectError, supports, write_project

SETTLE_SECONDS = 25  # the drive needs to spin the reloaded disc up before timing is steady
RELOAD_TIMEOUT_SECONDS = 180
ERASE_READY_TIMEOUT_SECONDS = 90
ERASE_SCRIPT = Path(__file__).parent / "scripts" / "erase_disc.ps1"


class Stage(enum.Enum):
    ERASE = "Erase disc"
    PATTERN = "Prepare pattern"
    BURN = "Burn test disc"
    RELOAD = "Reload disc"
    MEASURE = "Measure track pitch"
    INSTALL = "Install preset"


def stages_for(disc: DiscInfo) -> list[Stage]:
    return [s for s in Stage if s is not Stage.ERASE or disc.needs_erase]


@dataclass(frozen=True)
class Progress:
    """A stage is running. `fraction` is None when its length is unknown."""

    stage: Stage
    fraction: float | None
    detail: str = ""


@dataclass(frozen=True)
class StageDone:
    stage: Stage
    detail: str = ""


@dataclass(frozen=True)
class LogLine:
    text: str


Event = Progress | StageDone | LogLine


@dataclass(frozen=True)
class Fit:
    pitch_um: float
    residual_deg: float | None  # None for a Blu-ray timed by period, which has no angle residual
    points: int | None
    clv: bool


@dataclass(frozen=True)
class CalibrationResult:
    fit: Fit
    points: int
    preset_name: str
    preset_id: str
    notes: tuple[str, ...] = ()
    project: Path | None = None


class CalibrationError(RuntimeError):
    """A step failed; the message is meant for the person at the screen."""


_RUNG = re.compile(r"^\s*rung\s+(\d+):")
_ZONE = re.compile(r"^Zone\s+[\d.]+\s+\(LBA")
_PERCENT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%")
_ESTIMATE = re.compile(r"about\s+(\d+)\s*min", re.IGNORECASE)
_RESULT_DIR = re.compile(r"summary\.json(?:, events\.csv)?:\s*(.+?)\s*$")
_FREE_SECTORS = re.compile(r"free sectors:\s*(\d+)")
_STOPPED = re.compile(r"^stopped:\s*(.+)$")


def parse_rung(line: str) -> int | None:
    match = _RUNG.match(line)
    return int(match.group(1)) if match else None


def is_zone(line: str) -> bool:
    return bool(_ZONE.match(line))


def parse_percent(line: str) -> float | None:
    match = _PERCENT.match(line)
    return float(match.group(1)) / 100 if match else None


def read_fit(summary: dict) -> Fit:
    """The headline numbers of a geo run. Angle ladders (DVD, CD) and period fits (Blu-ray) differ."""
    fit = summary["fit"]
    if "pitch_um_at_bit" in fit:
        return Fit(float(fit["pitch_um_at_bit"]), float(fit["residual_rms_deg"]), None, clv=False)
    if fit.get("model") == "clv":
        return Fit(float(fit["pitch_um"]), None, fit.get("zones_used"), clv=True)
    raise KeyError("fit")


def preset_identity(disc: DiscInfo) -> tuple[str, str]:
    """Name and id DiscImageStudio will show and key the preset by."""
    name = f"{disc.media} ({disc.mid}, Calibrated)" if disc.mid else f"{disc.media} (Calibrated)"
    preset_id = re.sub(r"[^a-z0-9]+", "-", (disc.mid or disc.media).lower()).strip("-") + "-calibrated"
    return name, preset_id


def format_clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


class Calibrator:
    def __init__(self, discprobe_dir: Path, emit: Callable[[Event], None]) -> None:
        self._dir = discprobe_dir
        self._emit = emit
        self._env = tool_env(discprobe_dir)
        self._python = python_exe()

    # -- public ---------------------------------------------------------------------------

    def run(self, disc: DiscInfo, token: CancelToken) -> CalibrationResult:
        family = disc.kind
        if family is None or not disc.can_calibrate:
            raise CalibrationError("Calibration needs a blank, or erasable, CD, DVD or Blu-ray recordable disc.")

        if disc.needs_erase:
            disc = self._erase(disc, token)
        disc = self._with_capacity(disc, token)
        length = family.pattern_length(disc.sectors)
        iso = self._prepare_pattern(family, length, token)
        self._burn(disc, family, iso, token)
        if not family.keep_pattern:
            iso.unlink(missing_ok=True)
        self._reload(disc, token)
        summary, count = self._measure(disc, family, token)
        return self._install(disc, family, disc.sectors or length, summary, count, token)

    # -- steps ----------------------------------------------------------------------------

    def _erase(self, disc: DiscInfo, token: CancelToken) -> DiscInfo:
        self._progress(Stage.ERASE, None, "Erasing")
        result = stream(
            powershell(["-File", str(ERASE_SCRIPT), "-Drive", disc.drive]),
            token, on_line=lambda line: self._emit(LogLine(line)),
        )
        if not result.ok:
            raise CalibrationError(f"The disc could not be erased.\n{result.tail()}")
        fresh = self._await_disc(
            Stage.ERASE, disc.drive, lambda d: d.can_calibrate, ERASE_READY_TIMEOUT_SECONDS, token,
            "The disc did not come back after erasing. Close the tray and try again.",
        )
        self._done(Stage.ERASE)
        return replace(disc, state=DiscState.BLANK, sectors=fresh.sectors or disc.sectors)

    def _with_capacity(self, disc: DiscInfo, token: CancelToken) -> DiscInfo:
        """A blank Blu-ray reports no capacity to `info`, but IMAPI knows how much room is free."""
        if disc.sectors:
            return disc
        self._progress(Stage.PATTERN, None, "Reading capacity")
        found: list[int] = []

        def on_line(line: str) -> None:
            self._emit(LogLine(line))
            if (match := _FREE_SECTORS.search(line)):
                found.append(int(match.group(1)))

        script = self._dir / "tools" / "burn_imapi.ps1"
        stream(powershell(["-File", str(script), "-Drive", disc.drive]), token, cwd=self._dir, on_line=on_line)
        return replace(disc, sectors=found[0]) if found else disc

    def _prepare_pattern(self, family: Family, sectors: int, token: CancelToken) -> Path:
        """The random-data pattern is deterministic, so one copy is reused for every disc."""
        iso = self._dir / "iso" / family.pattern_file
        if self._has_length(iso, sectors):
            self._done(Stage.PATTERN, "Reusing existing")
            return iso

        iso.parent.mkdir(exist_ok=True)
        partial = iso.with_name(iso.name + ".part")
        self._progress(Stage.PATTERN, None, "Starting")

        def on_line(line: str) -> None:
            self._emit(LogLine(line))
            fraction = parse_percent(line)
            if fraction is not None:
                self._progress(Stage.PATTERN, fraction, f"{fraction:.0%}")

        try:
            result = stream(
                [self._python, "-m", "discprobe", "random", "--media", family.key,
                 "--sectors", str(sectors), "--output", str(partial)],
                token, cwd=self._dir, env=self._env, on_line=on_line,
            )
            if not result.ok:
                raise CalibrationError(f"The test pattern could not be created.\n{result.tail()}")
            os.replace(partial, iso)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        self._done(Stage.PATTERN)
        return iso

    @staticmethod
    def _has_length(iso: Path, sectors: int) -> bool:
        try:
            return iso.stat().st_size == sectors * SECTOR_BYTES
        except OSError:
            return False

    def _burn(self, disc: DiscInfo, family: Family, iso: Path, token: CancelToken) -> None:
        script = self._dir / "tools" / "burn_imapi.ps1"
        cmd = powershell([
            "-File", str(script), "-Drive", disc.drive, "-Iso", str(iso),
            "-Speed", str(family.write_speed), "-Burn", "-Reload",
        ])
        state = {"started": None, "estimate": None}
        self._progress(Stage.BURN, None, "Starting")

        def on_line(line: str) -> None:
            self._emit(LogLine(line))
            if line.startswith("Writing ..."):
                state["started"] = time.monotonic()
            estimate = _ESTIMATE.search(line)
            if estimate:
                state["estimate"] = int(estimate.group(1)) * 60

        def on_tick() -> None:
            # IMAPI2 reports no progress during Write(), so this is elapsed time against the
            # script's own estimate from the write speed.
            if state["started"] is None:
                return
            elapsed = time.monotonic() - state["started"]
            if state["estimate"]:
                fraction = min(elapsed / state["estimate"], 0.99)
                detail = f"{format_clock(elapsed)} of about {state['estimate'] // 60} min"
            else:
                fraction, detail = None, format_clock(elapsed)
            self._progress(Stage.BURN, fraction, detail)

        result = stream(cmd, token, cwd=self._dir, on_line=on_line, on_tick=on_tick)
        if not result.ok:
            raise CalibrationError(f"The burn failed.\n{result.tail()}")
        self._done(Stage.BURN)

    def _reload(self, disc: DiscInfo, token: CancelToken) -> None:
        """Wait for the burned disc to come back, then let the drive settle."""
        def burned(now: DiscInfo) -> bool:
            if now.state is DiscState.BLANK:
                raise CalibrationError("The disc came back blank, so the burn did not write anything.")
            # A burned RW disc still reads as erasable, and a closed CD-R reports itself as CD-ROM
            # (unsupported for a new run, but readable, which is all the measurement needs).
            return now.state in (DiscState.WRITTEN, DiscState.ERASABLE, DiscState.UNSUPPORTED)

        self._await_disc(
            Stage.RELOAD, disc.drive, burned, RELOAD_TIMEOUT_SECONDS, token,
            "The burned disc did not come back. Close the tray, then run disc-probe's "
            f"`geo --drive {disc.drive}` to measure it.",
        )
        for remaining in range(SETTLE_SECONDS, 0, -1):
            self._progress(Stage.RELOAD, 1 - remaining / SETTLE_SECONDS, f"Settling {remaining}s")
            token.sleep(1)
        self._done(Stage.RELOAD)

    def _await_disc(
        self, stage: Stage, drive: str, accept: Callable[[DiscInfo], bool], timeout_s: float,
        token: CancelToken, timeout_message: str,
    ) -> DiscInfo:
        waiting = {
            DiscState.TRAY_OPEN: "Close the tray",
            DiscState.NO_DISC: "Waiting for the disc",
            DiscState.LOADING: "Drive is reading the disc",
        }
        deadline = time.monotonic() + timeout_s
        while True:
            token.raise_if_cancelled()
            now: DiscInfo | None
            try:
                now = read_disc_info(self._dir, drive)
            except ProbeError as exc:
                now = None
                self._emit(LogLine(str(exc)))
            if now is not None and accept(now):
                return now
            if time.monotonic() > deadline:
                raise CalibrationError(timeout_message)
            self._progress(stage, None, waiting.get(now.state, "Waiting") if now else "Waiting for the drive")
            token.sleep(3)

    def _measure(self, disc: DiscInfo, family: Family, token: CancelToken) -> tuple[Path, int]:
        total = family.measure_points
        count = 0
        result_dir: Path | None = None
        stopped: str | None = None
        started = time.monotonic()
        self._progress(Stage.MEASURE, 0.0, "Starting")

        def advance(n: int) -> None:
            nonlocal count
            count = max(count, n)
            self._progress(Stage.MEASURE, min(count / total, 1.0), f"{family.measure_unit} {count} of {total}")

        def on_line(line: str) -> None:
            nonlocal result_dir, stopped
            self._emit(LogLine(line))
            if (rung := parse_rung(line)) is not None:
                advance(rung)
            elif is_zone(line):
                advance(count + 1)
            if (found := _RESULT_DIR.search(line)):
                result_dir = Path(found.group(1))
            if (halt := _STOPPED.match(line)):
                stopped = halt.group(1)

        def on_tick() -> None:
            if count == 0:  # before the first point, show that something is happening
                self._progress(Stage.MEASURE, 0.0, f"Settling {format_clock(time.monotonic() - started)}")

        # geo needs the drive to hold constant angular speed, which depends on drive and speed.
        # If a speed gives constant linear speed instead, geo says so and stops: try the next one.
        for speed in family.speeds or (None,):
            stopped = None
            count = 0
            command = [self._python, "-m", "discprobe", "geo", "--media", family.key, "--drive", disc.drive]
            command += ["--speed", speed] if speed else []
            result = stream(
                [*command, *family.geo_args], token, cwd=self._dir, env=self._env, on_line=on_line, on_tick=on_tick,
            )
            if result.ok or not (stopped and "constant speed" in stopped):
                break
            self._emit(LogLine(f"Not constant speed at {speed}; trying the next speed"))
        if not result.ok:
            raise CalibrationError(f"The measurement stopped: {stopped or result.tail(3)}")
        summary = result_dir / "summary.json" if result_dir else None
        if summary is None or not summary.is_file():
            raise CalibrationError("The measurement finished but wrote no summary.json.")
        self._done(Stage.MEASURE, f"{count} {family.measure_unit.lower()}s")
        return summary, count

    def _install(
        self, disc: DiscInfo, family: Family, sectors: int, summary: Path, count: int, token: CancelToken,
    ) -> CalibrationResult:
        self._progress(Stage.INSTALL, None, "Writing preset")
        try:
            with open(summary, encoding="utf-8") as f:
                fit = read_fit(json.load(f))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise CalibrationError(f"{summary} holds no usable fit ({exc!r}).") from exc
        name, preset_id = preset_identity(disc)

        result = stream(
            [
                self._python, "-m", "discprobe", "disexport", "--media", family.key,
                "--geo", str(summary), "--sectors", str(sectors),
                "--name", name, "--id", preset_id, "--install", "--replace",
            ],
            token, cwd=self._dir, env=self._env, on_line=lambda line: self._emit(LogLine(line)),
        )
        if not result.ok:
            raise CalibrationError(f"DiscImageStudio's preset could not be written.\n{result.tail()}")
        self._done(Stage.INSTALL)

        project = None
        if supports(family.key):
            try:
                project = write_project(family.key, preset_id, name)
            except ProjectError as exc:
                raise CalibrationError(f"The preset was added, but the project file was not. {exc}") from exc

        notes = [family.note] if family.note else []
        if fit.clv:
            notes.append("Only the inner radius was timed; the pitch in the preset is nominal.")
        return CalibrationResult(fit, fit.points or count, name, preset_id, tuple(notes), project)

    # -- helpers --------------------------------------------------------------------------

    def _progress(self, stage: Stage, fraction: float | None, detail: str = "") -> None:
        self._emit(Progress(stage, fraction, detail))

    def _done(self, stage: Stage, detail: str = "") -> None:
        self._emit(StageDone(stage, detail))
