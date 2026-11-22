"""Entry point: `python -m disctuner`."""

import traceback
from tkinter import messagebox

from . import paths


def run() -> None:
    from .ui.app import main

    try:
        main()
    except Exception:
        # Under pythonw there is no console, so a crash would otherwise vanish.
        log = paths.data_dir() / "crash.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        messagebox.showerror("DiscTuner", f"DiscTuner stopped unexpectedly.\nDetails: {log}")
        raise


if __name__ == "__main__":
    run()
