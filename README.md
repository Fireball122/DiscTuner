# DiscTuner

Calibrate a blank disc for [DiscImageStudio](https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio) with one click and one disc.

DiscTuner drives [disc-probe](https://github.com/Ashley-QwQ/disc-probe). It burns disc-probe's random-data test pattern, reloads the disc, measures the spiral through the drive's read timing, fits the geometry, and gives you the result as a ready-to-open DiscImageStudio project (and as a preset in DiscImageStudio's preset file). The test disc is used up; burn your pictures on the next discs from the same batch.

<img src="docs/screenshot-measuring.png" width="720" alt="DiscTuner measuring a DVD+R: the disc drawing with the burned track in amber, five steps, and the Cancel button">

*The window while measuring. The amber track is the burned part of the disc; the dots along one radius are the measurement points.*

DiscTuner is not affiliated with disc-probe or DiscImageStudio. It only automates their documented workflow.

## Quick start

1. Install Python, Git and DiscImageStudio (links in [Install](#install)). Download DiscTuner and double-click `setup.bat`.
2. Double-click `run_disctuner.bat`, put one blank disc from your spindle in the drive, and press **Calibrate**.
3. When it finishes, the window shows `Project saved: <path>`. In DiscImageStudio choose **File > Open** (or **Open project** on the home screen), pick that file, and import your picture.
4. Burn your pictures on the other discs from the same spindle.

This works the same for CD, DVD and Blu-ray. The details are under [Use](#use). Read [What is not guaranteed](#what-is-not-guaranteed) before you spend a disc.

## Supported discs

| Disc | Handling |
| --- | --- |
| CD-R, DVD-R, DVD+R, BD-R | Calibrated directly |
| CD-RW, DVD-RW, DVD+RW, BD-RE | Erased first, after you confirm, then calibrated |
| Dual layer, ROM, RAM | Refused, with the reason shown |

Caveats, also shown in the app:

- **CD:** timing leaves one constant unmeasured. Fine-calibrate the CD preset once in DiscImageStudio with a second blank.
- **Blu-ray:** experimental. The inner radius is measured; the pitch is nominal. The 25 GB test pattern is deleted after the burn.
- **BD-RE** can't be erased through Windows. Use a blank BD-R, or erase it elsewhere first.

## Will it work with my drive?

Not guaranteed. The measurement reads sector timing, so the drive has to cooperate:

- It must be burnable through Windows' IMAPI2 and answer disc-probe's low-level commands.
- It must hold a constant rotation speed at some read speed. DiscTuner tries several speeds and moves on when disc-probe reports the drive turned at constant linear speed instead. A drive that never holds constant rotation fails with a clear message, after the burn.
- Check disc-probe's list of [drives with reports](https://github.com/Ashley-QwQ/disc-probe#drives-with-hands-on-reports) before spending a disc.

What has been checked:

- **Real hardware:** one HL-DT-ST BDDVDRW UH12NS40, with a CD-R through DiscTuner's measure and install steps, and DVD+R results from disc-probe on the same drive. Burning, erasing and Blu-ray inside the app are covered by tests against a fake disc-probe, not by real discs.
- **The project files:** projects written for CD, DVD and Blu-ray were opened in DiscImageStudio 1.5.0 and took an imported image without an error. The Blu-ray project was made from a test preset, not from a real Blu-ray calibration.

Reports from other drives and discs are welcome, especially Blu-ray.

## What is not guaranteed

DiscTuner is a hobby tool, provided as is, without warranty (see [LICENSE](LICENSE)). It automates other people's software and talks to your drive's firmware, so some things are outside its control:

- **A calibration can fail, and the test disc is then lost.** Failures such as an unsupported drive, or a drive that never holds constant rotation, are only found after the burn. Burned discs can't be rewritten.
- **The result is a measurement, not a promise.** It describes the disc you calibrated. A different batch, brand or burn speed can behave differently, and the image you burn afterwards can still come out imperfect. Calibrate once per batch and check your first burn.
- **Blu-ray is experimental.** The Blu-ray steps have not been run on a real disc by the author. The inner radius is measured, the pitch is nominal, and the result may need adjusting in DiscImageStudio.
- **DVD:** DiscTuner's own DVD burn and measure steps have not been run on a real disc by the author.
- **Your drive may not work.** Only one drive, an HL-DT-ST BDDVDRW UH12NS40, has been used. Some drives don't expose what the measurement needs.
- **Other software can change.** The project files follow DiscImageStudio 1.5.0 and the measurement uses disc-probe as it is today. An update to either can break DiscTuner until it is updated.
- **The disc you put in is your responsibility.** DiscTuner erases rewritable discs only after asking, but check the disc first. The author is not liable for lost discs, lost data or damaged hardware.

If something here fails for you, please [open an issue](https://github.com/Fireball122/DiscTuner/issues) with the Log tab text and your drive model. Real-disc reports are how the untested parts get tested.

## Install

You need Windows 10 or 11 and these:

| What | Where | Notes |
| --- | --- | --- |
| Python 3.11 or newer | https://www.python.org/downloads/ | Tick "Add python.exe to PATH" when installing. |
| Git | https://git-scm.com/download/win | `setup.bat` uses it to fetch disc-probe. Optional if you extract disc-probe's ZIP yourself. |
| DiscImageStudio | https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio/releases/latest | The program DiscTuner installs its presets into. |
| A burner | see [Will it work with my drive?](#will-it-work-with-my-drive) | |

Get DiscTuner in either of two ways:

1. Download `DiscTuner-v2.2.2.zip` from the [latest release](https://github.com/Fireball122/DiscTuner/releases/latest) and unzip it.
2. Or clone it: `git clone https://github.com/Fireball122/DiscTuner`

Then, in the DiscTuner folder, double-click `setup.bat`. When it finishes, double-click `run_disctuner.bat`.

`setup.bat` checks the Python version, installs the packages DiscTuner needs (disc-probe uses the same ones), clones disc-probe into the folder next to DiscTuner, runs disc-probe's self-test, and says whether DiscImageStudio is installed. It is safe to run again. `run_disctuner.bat` starts DiscTuner without a console window.

To do it by hand instead:

```
python -m pip install -r requirements.txt
git clone https://github.com/Ashley-QwQ/disc-probe ../disc-probe
python -m disctuner
```

DiscTuner looks for disc-probe next to its own folder, in `Downloads`, `Documents` and your home folder, and in `DISCTUNER_DISCPROBE`. If it finds nothing, the app offers a **Locate disc-probe** button and remembers your choice. DiscImageStudio is found the same way (`DISCTUNER_DIS_EXE`, `Downloads`, `%LOCALAPPDATA%\Programs\DiscImageStudio`) and asked for once if missing.

## Use

1. Put one blank disc from the spindle in the drive. Use a disc from the batch you will burn your pictures on; the result describes that batch.
2. Press **Calibrate** and don't touch the tray until it finishes. DiscTuner burns the test pattern, reloads the disc and measures it. A rewritable disc is erased first, after you confirm.
3. The window ends with a line `Project saved: <path>`. The file is `<disc> (<manufacturer code>, Calibrated).discproj` in your DiscImageStudio projects folder, normally `Documents\DiscImageStudio Projects`. Colons in the name become underscores in the file name.
4. In DiscImageStudio choose **File > Open** (or **Open project** on the home screen) and pick that file. Its disc geometry is already filled in. Import your picture, then export and burn as usual on the other discs from the batch.

DiscTuner also installs the calibrated preset into DiscImageStudio's preset file. In DiscImageStudio 1.5.0 it did not show up in the home screen's template list in testing, so use the project file rather than looking for the preset.

Calibrate once per spindle or batch. Running it again for the same disc type replaces that preset and project file. disc-probe backs up DiscImageStudio's previous preset file first.

Burning writes a disc that can't be rewritten, and erasing wipes a rewritable one. DiscTuner asks before erasing and warns before you cancel mid-burn. You use it at your own risk.

## Troubleshooting

- **"The measurement stopped: the drive is not turning at a constant speed"** on every speed: the drive doesn't hold constant rotation for this disc type. Run `python -m discprobe timing --media <cd|dvd|bd> --drive E:` for details.
- **"The burned disc did not come back"**: push the tray in, wait, then measure by hand with `python -m discprobe geo --drive E:`.
- The **Log** tab shows everything disc-probe printed. Crashes are written to `%LOCALAPPDATA%\DiscTuner\crash.log`.
- **"The preset was added, but the project file was not"**: the calibration worked but DiscTuner could not write the project, for example because the folder is read-only. The message says why. The preset is installed; fix the cause and run the calibration again for the project.
- **No `Project saved` line**: you calibrated with DiscTuner 2.0.0 or earlier, which wrote no project. Update and calibrate again.
- **DiscImageStudio says the project is invalid**: the project follows DiscImageStudio 1.5.0. A different version may expect something else. Open an issue and attach the `.discproj` file and the version number.

## Development

```
python -m pip install -r requirements.txt ruff
python -m ruff check .
python -m unittest discover -s tests
```

The pipeline tests run against a fake disc-probe, so they need no drive.

| Module | Role |
| --- | --- |
| `disctuner/calibration.py` | The pipeline: erase, pattern, burn, reload, measure, install. Emits progress events; no UI. |
| `disctuner/project.py` | Writes the DiscImageStudio project for a calibration, copying the installed preset's geometry. |
| `disctuner/media.py` | Supported disc types and per-family settings. |
| `disctuner/disc.py` | Reads and parses `discprobe info` into a `DiscInfo`. |
| `disctuner/drives.py` | Drive list, eject, close tray. |
| `disctuner/procs.py` | Hidden-window subprocesses with streamed output and cancellation. |
| `disctuner/paths.py` | Locating disc-probe and DiscImageStudio; saved settings. |
| `disctuner/ui/` | Window, theme, and the disc drawing. |
| `disctuner/scripts/erase_disc.ps1` | Quick erase through IMAPI2. |

## License

MIT, see [LICENSE](LICENSE). disc-probe is GPL-3.0-or-later and is run as a separate program; it is not bundled here.
