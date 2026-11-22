"""Palette and type for DiscTuner.

One graphite ramp, three text roles, one accent. The accent is the colour of the burned
track on the disc; chrome stays neutral so the disc carries the colour.
"""

from __future__ import annotations

import tkinter.font as tkfont
from dataclasses import dataclass

import customtkinter as ctk

# Surfaces, darkest to lightest
BG = "#101113"
PANEL = "#17181b"
RAISED = "#1e2024"
HOVER = "#26282d"
BORDER = "#2a2c31"

# Text
TEXT = "#ececee"
TEXT_2 = "#a0a3aa"
TEXT_3 = "#6d7078"

# Meaning
ACCENT = "#e0a23c"
OK = "#6fb58b"
ERROR = "#e0786c"

# Primary action: off-white with dark text, as on the rest of the ramp
PRIMARY = TEXT
PRIMARY_HOVER = "#ffffff"
PRIMARY_DISABLED = "#2a2c31"


@dataclass
class Fonts:
    """Built once a Tk root exists. Sizes: 12 meta, 13 body, 15 heading, 20 title."""

    title: ctk.CTkFont
    heading: ctk.CTkFont
    body: ctk.CTkFont
    emphasis: ctk.CTkFont
    meta: ctk.CTkFont
    mono: ctk.CTkFont
    figure: ctk.CTkFont

    @classmethod
    def load(cls) -> Fonts:
        families = set(tkfont.families())

        def pick(*names: str) -> str:
            return next((n for n in names if n in families), names[-1])

        ui = pick("Segoe UI Variable Text", "Segoe UI")
        semibold = pick("Segoe UI Semibold", ui)
        mono = pick("Cascadia Mono", "Consolas")
        return cls(
            title=ctk.CTkFont(family=semibold, size=20),
            heading=ctk.CTkFont(family=semibold, size=15),
            body=ctk.CTkFont(family=ui, size=13),
            emphasis=ctk.CTkFont(family=semibold, size=13),
            meta=ctk.CTkFont(family=ui, size=12),
            mono=ctk.CTkFont(family=mono, size=12),
            figure=ctk.CTkFont(family=mono, size=20),
        )
