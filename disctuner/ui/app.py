"""The DiscTuner window.

State lives in `App` (phase, disc, drive list, run progress). Workers never touch widgets:
they put messages on a queue and `_pump` applies them on the Tk thread. Every visible change
goes through `_refresh`, which draws from state, so the screen cannot disagree with it.
"""

from __future__ import annotations

import enum
import os
import queue
import threading
import time
import traceback
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw

from .. import paths
from ..calibration import (
    CalibrationError,
    CalibrationResult,
    Calibrator,
    Event,
    LogLine,
    Progress,
    Stage,
    StageDone,
    stages_for,
)
from ..disc import DiscInfo, DiscState, read_disc_info
from ..drives import Drive, DriveError, close_tray, eject, list_drives
from ..media import FAMILIES
from ..procs import Cancelled, CancelToken
from . import disc_view, theme

DISC_LOGICAL = 304
DISC_PIXELS = DISC_LOGICAL * 2  # drawn at 2x so it stays sharp on scaled displays
MAX_LOG_LINES = 2000

STEP_PENDING = "#3a3d44"


class Phase(enum.Enum):
    SCANNING = enum.auto()
    READY = enum.auto()
    RUNNING = enum.auto()
    DONE = enum.auto()
    FAILED = enum.auto()


class StepRow(ctk.CTkFrame):
    """One stage of the run: a dot, its name, and a short status on the right."""

    def __init__(self, master, text: str, fonts: theme.Fonts) -> None:
        super().__init__(master, fg_color="transparent", height=36)
        self.grid_columnconfigure(1, weight=1)
        self.grid_propagate(False)
        self._dot = ctk.CTkFrame(self, width=8, height=8, corner_radius=4, fg_color=STEP_PENDING)
        self._dot.grid(row=0, column=0, padx=(2, 12), sticky="w")
        self._label = ctk.CTkLabel(self, text=text, font=fonts.body, text_color=theme.TEXT_3, anchor="w")
        self._label.grid(row=0, column=1, sticky="w")
        self._meta = ctk.CTkLabel(self, text="", font=fonts.mono, text_color=theme.TEXT_3, anchor="e")
        self._meta.grid(row=0, column=2, sticky="e")
        self.grid_rowconfigure(0, weight=1)

    def set(self, status: str, meta: str = "") -> None:
        dot, label = {
            "pending": (STEP_PENDING, theme.TEXT_3),
            "active": (theme.ACCENT, theme.TEXT),
            "done": (theme.OK, theme.TEXT_2),
        }[status]
        self._dot.configure(fg_color=dot)
        self._label.configure(text_color=label)
        self._meta.configure(text=meta, text_color=theme.TEXT_2 if status == "active" else theme.TEXT_3)


class App(ctk.CTk):
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=theme.BG)
        self.title("DiscTuner")
        self.geometry("880x660")
        self.minsize(840, 640)
        self.fonts = theme.Fonts.load()

        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._phase = Phase.SCANNING
        self._drives: list[Drive] = []
        self._disc: DiscInfo | None = None
        self._notice = ""  # shown under the steps: probe errors, run failures
        self._notice_is_error = False
        self._token: CancelToken | None = None
        self._result: CalibrationResult | None = None
        self._active_stage: Stage | None = None
        self._burn = 0.0
        self._rungs = 0
        self._measuring = False
        self._disc_key: tuple | None = None
        self._disc_image: ctk.CTkImage | None = None

        try:
            self._discprobe = paths.find_discprobe()
            self._tool_error = ""
        except paths.ToolNotFound as exc:
            self._discprobe = None
            self._tool_error = str(exc)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(250, self._set_icon)  # CustomTkinter installs its own icon at ~200 ms
        self.after(60, self._pump)
        self._refresh()
        if self._discprobe:
            self.after(100, self._scan_drives)

    # -- construction ---------------------------------------------------------------------

    def _build(self) -> None:
        f = self.fonts
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Left: the disc and what the run measured
        card = ctk.CTkFrame(self, fg_color=theme.PANEL, corner_radius=16, width=360)
        card.grid(row=0, column=0, sticky="ns", padx=(24, 0), pady=24)
        card.grid_propagate(False)
        card.grid_columnconfigure((0, 1, 2), weight=1, uniform="readout")
        card.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(card, text="DiscTuner", font=f.title, text_color=theme.TEXT, anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=28, pady=(28, 0))
        ctk.CTkLabel(card, text="Track pitch calibration for DiscImageStudio", font=f.meta,
                     text_color=theme.TEXT_2, anchor="w").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=28, pady=(4, 0))

        self._disc_label = ctk.CTkLabel(card, text="", fg_color="transparent")
        self._disc_label.grid(row=2, column=0, columnspan=3, padx=28, pady=20)

        ctk.CTkFrame(card, height=1, fg_color=theme.BORDER).grid(
            row=3, column=0, columnspan=3, sticky="ew", padx=28)

        self._readouts: dict[str, ctk.CTkLabel] = {}
        self._readout_titles: dict[str, ctk.CTkLabel] = {}
        for column, (key, title) in enumerate((("pitch", "Pitch (µm)"), ("residual", "Residual (°)"), ("points", "Points"))):
            heading = ctk.CTkLabel(card, text=title, font=f.meta, text_color=theme.TEXT_3, anchor="w")
            heading.grid(row=4, column=column, sticky="w", padx=(28 if column == 0 else 8, 0), pady=(16, 0))
            value = ctk.CTkLabel(card, text="–", font=f.figure, text_color=theme.TEXT, anchor="w")
            value.grid(row=5, column=column, sticky="w", padx=(28 if column == 0 else 8, 0), pady=(2, 24))
            self._readouts[key] = value
            self._readout_titles[key] = heading

        # Right: drive, disc, steps, action
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(32, 32), pady=24)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(right, text="Drive", font=f.meta, text_color=theme.TEXT_3, anchor="w").grid(
            row=0, column=0, sticky="w", pady=(4, 8))

        drive_row = ctk.CTkFrame(right, fg_color="transparent")
        drive_row.grid(row=1, column=0, sticky="ew")
        drive_row.grid_columnconfigure(0, weight=1)
        self._drive_var = ctk.StringVar(value="")
        self._drive_menu = ctk.CTkOptionMenu(
            drive_row, variable=self._drive_var, values=[""], command=lambda _: self._scan_disc(),
            height=32, corner_radius=8, font=f.body, dropdown_font=f.body, anchor="w",
            fg_color=theme.RAISED, button_color=theme.RAISED, button_hover_color=theme.HOVER,
            text_color=theme.TEXT, dropdown_fg_color=theme.RAISED, dropdown_hover_color=theme.HOVER,
            dropdown_text_color=theme.TEXT)
        self._drive_menu.grid(row=0, column=0, sticky="ew")
        self._rescan_btn = self._ghost_button(drive_row, "Rescan", self._rescan)
        self._rescan_btn.grid(row=0, column=1, padx=(8, 0))
        self._tray_btn = self._ghost_button(drive_row, "Eject", self._toggle_tray)
        self._tray_btn.grid(row=0, column=2, padx=(8, 0))

        status = ctk.CTkFrame(right, fg_color="transparent")
        status.grid(row=2, column=0, sticky="ew", pady=(24, 0))
        status.grid_columnconfigure(1, weight=1)
        self._status_dot = ctk.CTkFrame(status, width=8, height=8, corner_radius=4, fg_color=theme.TEXT_3)
        self._status_dot.grid(row=0, column=0, padx=(2, 12), sticky="w")
        self._status_title = ctk.CTkLabel(status, text="", font=f.heading, text_color=theme.TEXT, anchor="w")
        self._status_title.grid(row=0, column=1, sticky="w")
        self._status_sub = ctk.CTkLabel(status, text="", font=f.mono, text_color=theme.TEXT_3, anchor="w")
        self._status_sub.grid(row=1, column=1, sticky="w", pady=(2, 0))

        self._tabs = ctk.CTkSegmentedButton(
            right, values=["Steps", "Log"], command=self._show_tab, height=28, corner_radius=8, width=160,
            font=f.meta, fg_color=theme.PANEL, selected_color=theme.RAISED, selected_hover_color=theme.RAISED,
            unselected_color=theme.PANEL, unselected_hover_color=theme.HOVER, text_color=theme.TEXT,
            border_width=2)
        self._tabs.set("Steps")
        self._tabs.grid(row=3, column=0, sticky="w", pady=(24, 8))

        body = ctk.CTkFrame(right, fg_color="transparent")
        body.grid(row=4, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._steps_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._steps_frame.grid(row=0, column=0, sticky="nsew")
        self._steps_frame.grid_columnconfigure(0, weight=1)
        self._steps: dict[Stage, StepRow] = {stage: StepRow(self._steps_frame, stage.value, f) for stage in Stage}

        self._log_box = ctk.CTkTextbox(
            body, font=f.mono, fg_color=theme.PANEL, text_color=theme.TEXT_2, corner_radius=8,
            border_width=0, wrap="word", state="disabled")

        self._notice_label = ctk.CTkLabel(right, text="", font=f.body, text_color=theme.TEXT_2,
                                          anchor="w", justify="left", wraplength=420)
        self._notice_label.grid(row=5, column=0, sticky="ew", pady=(12, 12))
        right.bind("<Configure>", lambda e: self._notice_label.configure(wraplength=max(e.width - 8, 200)))

        self._primary = ctk.CTkButton(
            right, text="", height=44, corner_radius=10, font=f.emphasis, fg_color=theme.PRIMARY,
            hover_color=theme.PRIMARY_HOVER, text_color=theme.BG, text_color_disabled=theme.TEXT_3)
        self._primary.grid(row=6, column=0, sticky="ew")
        self._secondary = ctk.CTkButton(
            right, text="", height=36, corner_radius=10, font=f.body, fg_color="transparent",
            hover_color=theme.RAISED, text_color=theme.TEXT_2, border_width=1, border_color=theme.BORDER)
        self._secondary.grid(row=7, column=0, sticky="ew", pady=(8, 0))

    def _ghost_button(self, master, text: str, command) -> ctk.CTkButton:
        return ctk.CTkButton(
            master, text=text, command=command, width=64, height=32, corner_radius=8, font=self.fonts.body,
            fg_color=theme.RAISED, hover_color=theme.HOVER, text_color=theme.TEXT_2,
            text_color_disabled=theme.TEXT_3)

    def _set_icon(self) -> None:
        """Replace CustomTkinter's default icon with the disc, as a multi-size .ico."""
        icon = disc_view.render(256, burn=0.7, rungs_total=0).convert("RGBA")
        mask = Image.new("L", icon.size, 0)
        ImageDraw.Draw(mask).ellipse((2, 2, 253, 253), fill=255)
        icon.putalpha(mask)
        path = paths.data_dir() / "disctuner.ico"
        path.parent.mkdir(parents=True, exist_ok=True)
        icon.save(path, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
        self.iconbitmap(str(path))

    # -- state -> screen ------------------------------------------------------------------

    def _selected_drive(self) -> Drive | None:
        letter = self._drive_var.get()[:2]
        return next((d for d in self._drives if d.letter == letter), None)

    def _status_text(self) -> tuple[str, str, str]:
        """(dot colour, title, subtitle) for the disc block."""
        disc = self._disc
        if self._tool_error:
            return theme.ERROR, "disc-probe not found", ""
        if self._phase is Phase.RUNNING:
            return theme.ACCENT, f"Calibrating {disc.media}" if disc else "Calibrating", disc.mid if disc else ""
        if self._phase is Phase.DONE and self._result is not None:
            return theme.OK, "Calibration complete", self._result.preset_name
        if self._phase is Phase.SCANNING and not self._drives:
            return theme.TEXT_3, "Looking for drives", ""
        if not self._drives:
            return theme.TEXT_3, "No optical drive found", "Connect a DVD writer, then rescan"
        if disc is None:
            return theme.TEXT_3, "Reading drive", ""
        mid = f"{disc.mid} · {disc.sectors:,} sectors" if disc.mid and disc.sectors else disc.mid
        return {
            DiscState.BLANK: (theme.OK, f"Blank {disc.media}", mid),
            DiscState.ERASABLE: (theme.ACCENT, f"{disc.media} has data", "Calibrating erases it first"),
            DiscState.WRITTEN: (theme.ERROR, f"{disc.media} already written", "Insert a blank disc"),
            DiscState.UNSUPPORTED: (theme.ERROR, f"{disc.media} is not supported", disc.reason),
            DiscState.TRAY_OPEN: (theme.TEXT_3, "Tray open", "Load a blank CD, DVD or Blu-ray disc"),
            DiscState.NO_DISC: (theme.TEXT_3, "No disc", "Insert a blank CD, DVD or Blu-ray disc"),
            DiscState.LOADING: (theme.TEXT_3, "Reading disc", "The drive is spinning up"),
        }[disc.state]

    def _refresh(self) -> None:
        phase, disc = self._phase, self._disc
        idle = phase in (Phase.READY, Phase.FAILED) and bool(self._drives)

        dot, title, sub = self._status_text()
        self._status_dot.configure(fg_color=dot)
        self._status_title.configure(text=title)
        self._status_sub.configure(text=sub)

        self._drive_menu.configure(state="normal" if idle else "disabled")
        self._rescan_btn.configure(state="normal" if phase is not Phase.RUNNING and self._discprobe else "disabled")
        tray_open = disc is not None and disc.state is DiscState.TRAY_OPEN
        self._tray_btn.configure(text="Close tray" if tray_open else "Eject", width=80,
                                 state="normal" if idle else "disabled")

        notice, colour = self._notice, theme.ERROR if self._notice_is_error else theme.TEXT_2
        if self._tool_error:
            notice, colour = self._tool_error, theme.ERROR
        elif phase is Phase.RUNNING:
            notice = "Keep the disc in the drive until every step is done."
        elif not notice and phase is Phase.READY and disc is not None and disc.can_calibrate:
            notice = (
                "Erases this disc, burns it, reloads it, reads the track back and adds the preset."
                if disc.needs_erase else
                "Burns this disc, reloads it, reads the track back and adds the preset."
            )
        self._notice_label.configure(text=notice, text_color=colour)

        for button in (self._primary, self._secondary):
            button.grid_remove()
        if self._tool_error:
            self._set_button(self._primary, "Locate disc-probe", self._locate_discprobe)
        elif phase is Phase.RUNNING:
            self._set_button(self._secondary, "Cancel", self._cancel)
        elif phase is Phase.DONE:
            self._set_button(self._primary, "Open DiscImageStudio", self._open_dis)
            self._set_button(self._secondary, "Calibrate another disc", self._reset)
        elif phase is Phase.FAILED:
            self._set_button(self._primary, "Try again", self._reset)
        else:
            ready = phase is Phase.READY and disc is not None and disc.can_calibrate and self._selected_drive() is not None
            label = "Erase and calibrate" if disc is not None and disc.needs_erase else "Calibrate"
            self._set_button(self._primary, label, self._start, enabled=ready)
        self._primary.configure(fg_color=theme.PRIMARY if self._primary.cget("state") == "normal" else theme.PRIMARY_DISABLED)

        r = self._result
        done = phase is Phase.DONE and r is not None
        self._readouts["pitch"].configure(text=f"{r.fit.pitch_um:.4f}" if done else "–")
        has_residual = done and r.fit.residual_deg is not None
        self._readouts["residual"].configure(text=f"{r.fit.residual_deg:.2f}" if has_residual else "–")
        shown_points = r.points if done else (self._rungs if self._measuring else 0)
        self._readouts["points"].configure(text=str(shown_points) if shown_points else "–")
        kind = disc.kind if disc else None
        self._readout_titles["points"].configure(text=f"{kind.measure_unit}s" if kind else "Points")

        wanted = stages_for(disc) if disc else [s for s in Stage if s is not Stage.ERASE]
        for stage, row in self._steps.items():
            if stage in wanted:
                row.grid(row=wanted.index(stage), column=0, sticky="ew")
            else:
                row.grid_remove()

        self._draw_disc()

    @staticmethod
    def _set_button(button: ctk.CTkButton, text: str, command, enabled: bool = True) -> None:
        button.configure(text=text, command=command, state="normal" if enabled else "disabled")
        button.grid()

    def _points_total(self) -> int:
        kind = self._disc.kind if self._disc else None
        return (kind or FAMILIES["dvd"]).measure_points

    def _draw_disc(self) -> None:
        present = self._phase in (Phase.RUNNING, Phase.DONE) or (
            self._disc is not None
            and self._disc.state in (DiscState.BLANK, DiscState.ERASABLE, DiscState.WRITTEN, DiscState.UNSUPPORTED))
        total = self._points_total()
        burn = 1.0 if self._phase is Phase.DONE else self._burn
        rungs = total if self._phase is Phase.DONE else self._rungs
        active = self._phase is Phase.RUNNING and self._measuring
        key = (round(burn * 200), rungs, active, present, total)
        if key == self._disc_key:
            return
        self._disc_key = key
        image = disc_view.render(DISC_PIXELS, burn=burn, rungs_done=rungs, rungs_total=total,
                                 active_rung=active, present=present)
        self._disc_image = ctk.CTkImage(light_image=image, dark_image=image, size=(DISC_LOGICAL, DISC_LOGICAL))
        self._disc_label.configure(image=self._disc_image)

    def _show_tab(self, tab: str) -> None:
        if tab == "Log":
            self._steps_frame.grid_remove()
            self._log_box.grid(row=0, column=0, sticky="nsew")
        else:
            self._log_box.grid_remove()
            self._steps_frame.grid()

    def _log(self, text: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        extra = int(self._log_box.index("end-1c").split(".")[0]) - MAX_LOG_LINES
        if extra > 0:
            self._log_box.delete("1.0", f"{extra + 1}.0")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    # -- background work ------------------------------------------------------------------

    def _spawn(self, name: str, fn) -> None:
        """Run `fn` off the Tk thread; its result (or exception) comes back as message `name`."""
        def work() -> None:
            try:
                self._events.put((name, fn()))
            except Exception as exc:  # delivered to the UI, which decides how to show it
                self._events.put((name, exc))

        threading.Thread(target=work, daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                name, payload = self._events.get_nowait()
                getattr(self, f"_on_{name}")(payload)
        except queue.Empty:
            pass
        self.after(80, self._pump)

    # -- drives and disc ------------------------------------------------------------------

    def _scan_drives(self) -> None:
        self._phase = Phase.SCANNING
        self._disc = None
        self._drives = []
        self._notice, self._notice_is_error = "", False
        self._refresh()
        self._spawn("drives", list_drives)

    def _on_drives(self, result: list[Drive] | Exception) -> None:
        if isinstance(result, Exception):
            self._phase, self._notice, self._notice_is_error = Phase.FAILED, str(result), True
            self._refresh()
            return
        previous = self._drive_var.get()[:2]
        self._drives = result
        if not result:
            self._drive_menu.configure(values=[""])
            self._drive_var.set("")
            self._phase = Phase.READY
            self._refresh()
            return
        labels = [d.label for d in result]
        self._drive_menu.configure(values=labels)
        self._drive_var.set(next((d.label for d in result if d.letter == previous), labels[0]))
        self._scan_disc()

    def _scan_disc(self) -> None:
        drive = self._selected_drive()
        if drive is None or self._discprobe is None:
            return
        self._phase, self._disc = Phase.SCANNING, None
        self._notice, self._notice_is_error = "", False
        self._refresh()
        self._spawn("disc", lambda: read_disc_info(self._discprobe, drive.letter))

    def _on_disc(self, result: DiscInfo | Exception) -> None:
        if self._phase is Phase.RUNNING:
            return
        if isinstance(result, Exception):
            self._phase, self._notice, self._notice_is_error = Phase.FAILED, str(result), True
        else:
            self._phase, self._disc = Phase.READY, result
        self._refresh()

    def _rescan(self) -> None:
        self._reset_run()
        self._scan_drives()

    def _locate_discprobe(self) -> None:
        chosen = filedialog.askdirectory(title="Choose the disc-probe folder")
        if not chosen:
            return
        if not paths.is_discprobe(chosen):
            messagebox.showerror("disc-probe", "That folder has no discprobe package in it. Choose the folder that contains it.")
            return
        paths.save_setting("discprobe_dir", chosen)
        self._discprobe, self._tool_error = paths.find_discprobe(), ""
        self._scan_drives()

    def _toggle_tray(self) -> None:
        drive = self._selected_drive()
        if drive is None:
            return
        closing = self._disc is not None and self._disc.state is DiscState.TRAY_OPEN
        self._tray_btn.configure(state="disabled")
        self._spawn("tray", lambda: (close_tray if closing else eject)(drive.letter))

    def _on_tray(self, result: object) -> None:
        if isinstance(result, DriveError):
            self._notice, self._notice_is_error = str(result), True
            self._refresh()
            return
        self.after(4000, self._scan_disc)  # the drive needs a moment before it reports the new state

    # -- the run --------------------------------------------------------------------------

    def _reset_run(self) -> None:
        self._result = None
        self._active_stage = None
        self._burn, self._rungs, self._measuring = 0.0, 0, False
        for row in self._steps.values():
            row.set("pending")

    def _reset(self) -> None:
        self._reset_run()
        self._scan_disc()

    def _start(self) -> None:
        disc = self._disc
        if disc is None or not disc.can_calibrate or self._discprobe is None:
            return
        if disc.needs_erase and not messagebox.askyesno(
            "Erase disc",
            f"This {disc.media} has data on it. Calibrating erases the disc first.\n\nErase it and continue?",
            icon="warning", default="no",
        ):
            return
        self._reset_run()
        self._token = token = CancelToken()
        self._phase = Phase.RUNNING
        self._notice, self._notice_is_error = "", False
        self._log(" ".join(filter(None, ("Calibrating", disc.media, disc.mid, "in", disc.drive))))
        self._refresh()

        calibrator = Calibrator(self._discprobe, lambda event: self._events.put(("event", event)))

        def work() -> None:
            try:
                outcome: object = calibrator.run(disc, token)
            except Exception as exc:
                if not isinstance(exc, (Cancelled, CalibrationError)):
                    exc.add_note(traceback.format_exc())
                outcome = exc
            self._events.put(("finished", outcome))

        threading.Thread(target=work, daemon=True).start()

    def _on_event(self, event: Event) -> None:
        if isinstance(event, LogLine):
            if event.text.strip():
                self._log(event.text)
            return
        row = self._steps[event.stage]
        if isinstance(event, Progress):
            self._active_stage = event.stage
            row.set("active", event.detail)
            if event.stage is Stage.BURN and event.fraction is not None:
                self._burn = event.fraction
            elif event.stage is Stage.MEASURE:
                self._measuring = True
                self._rungs = round((event.fraction or 0.0) * self._points_total())
        elif isinstance(event, StageDone):
            row.set("done", event.detail)
            if event.stage is Stage.BURN:
                self._burn = 1.0
            elif event.stage is Stage.MEASURE:
                self._measuring, self._rungs = False, self._points_total()
        self._refresh()

    def _on_finished(self, outcome: CalibrationResult | Exception) -> None:
        self._token = None
        self._active_stage = None
        if isinstance(outcome, CalibrationResult):
            self._result = outcome
            self._phase = Phase.DONE
            self._notice = "\n".join(("Added to DiscImageStudio. Choose it in the disc preset list.", *outcome.notes))
            self._notice_is_error = False
            self._log(f"Installed preset {outcome.preset_id}")
        elif isinstance(outcome, Cancelled):
            self._log("Stopped")
            self._reset_run()
            self._scan_disc()
            return
        else:
            self._phase = Phase.FAILED
            message = str(outcome)
            first = message.splitlines()[0] if message else type(outcome).__name__
            self._notice, self._notice_is_error = f"{first} See the log for details.", True
            self._log(f"Failed: {message}")
            for note in getattr(outcome, "__notes__", []):
                self._log(note)
        self._refresh()

    def _cancel(self) -> None:
        if self._token is None:
            return
        warning = {
            Stage.BURN: "Stopping during the burn leaves a half-written disc that can't be used again. Stop anyway?",
            Stage.ERASE: "Stopping during the erase can leave the disc unusable. Stop anyway?",
        }.get(self._active_stage, "Stop calibrating?")
        if messagebox.askyesno("Stop calibration", warning, icon="warning", default="no"):
            self._token.cancel()

    # -- DiscImageStudio ------------------------------------------------------------------

    def _open_dis(self) -> None:
        exe = paths.find_dis_exe()
        if exe is None:
            chosen = filedialog.askopenfilename(
                title="Locate DiscImageStudio", filetypes=[("DiscImageStudio", "*.exe")])
            if not chosen:
                return
            paths.save_setting("dis_exe", chosen)
            exe = chosen
        try:
            os.startfile(exe)
        except OSError as exc:
            messagebox.showerror("DiscImageStudio", f"Could not open it.\n{exc}")

    # -- shutdown -------------------------------------------------------------------------

    def _on_close(self) -> None:
        if self._token is not None:
            if not messagebox.askyesno(
                "Quit DiscTuner", "A calibration is running. Stop it and quit?", icon="warning", default="no"
            ):
                return
            self._token.cancel()
        self.destroy()


def main() -> None:
    App().mainloop()
