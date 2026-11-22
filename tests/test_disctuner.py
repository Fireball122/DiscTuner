"""Run with:  python -m unittest discover -s tests"""

import json
import os
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from disctuner import calibration
from disctuner.calibration import (
    CalibrationError,
    Calibrator,
    LogLine,
    Progress,
    Stage,
    StageDone,
    read_fit,
    stages_for,
)
from disctuner.disc import DiscInfo, DiscState, parse_info
from disctuner.drives import normalize_letter
from disctuner.media import FAMILIES
from disctuner.project import GEOMETRY_KEYS, ProjectError, write_project
from disctuner.procs import Cancelled, CancelToken, python_exe, stream


def report(code=0x1B, status="empty", blank=True, free=2295104, sense=None, cap_last=0, adip=("SONY", "D21", "0"), atip=None):
    return {
        "test_unit_ready": {"sense": sense},
        "inquiry": {"parsed": {"vendor": "HL-DT-ST", "product": "BDDVDRW UH12NS40"}},
        "configuration": {"parsed": {"current_profile": code, "current_profile_name": "whatever"}},
        "disc_information": {"parsed": {"disc_status": status}},
        "track_1": {"parsed": {"blank": blank, "free_blocks": free, "track_start": 0}},
        "capacity": {"parsed": {"last_lba": cap_last}},
        "dvd_plus_adip_11": {"parsed": {"manufacturer_id": adip[0], "media_type_id": adip[1], "product_revision": adip[2]} if adip else {}},
        "atip": {"parsed": atip or {}},
    }


class ParseInfo(unittest.TestCase):
    def test_blank_dvd_plus_r(self):
        disc = parse_info("e:", report())
        self.assertEqual(disc.state, DiscState.BLANK)
        self.assertEqual((disc.drive, disc.media, disc.family, disc.mid, disc.sectors),
                         ("E:", "DVD+R", "dvd", "SONY/D21/0", 2295104))

    def test_dvd_minus_r_is_recognised_by_profile_code(self):
        # disc-probe names this profile "DVD-R Sequential"; matching on the name would miss it
        disc = parse_info("E:", report(code=0x11, adip=None))
        self.assertEqual((disc.state, disc.media, disc.family), (DiscState.BLANK, "DVD-R", "dvd"))

    def test_blank_cd_r_uses_atip_for_identity_and_size(self):
        disc = parse_info("E:", report(code=0x09, free=0, adip=None,
                                        atip={"leadin_start_msf": "97:26:66", "last_leadout_start_lba": 359847}))
        self.assertEqual((disc.state, disc.family, disc.mid, disc.sectors), (DiscState.BLANK, "cd", "ATIP 97:26:66", 359847))

    def test_blu_ray_without_a_reported_capacity(self):
        disc = parse_info("E:", report(code=0x42, free=0, adip=None))
        self.assertEqual((disc.state, disc.family, disc.sectors), (DiscState.BLANK, "bd", 0))

    def test_written_r_disc_is_not_calibratable(self):
        disc = parse_info("E:", report(status="complete", blank=False, free=0, cap_last=2295103))
        self.assertEqual((disc.state, disc.sectors, disc.can_calibrate), (DiscState.WRITTEN, 2295104, False))

    def test_written_rewritable_can_be_erased(self):
        disc = parse_info("E:", report(code=0x14, status="complete", blank=False, free=0, cap_last=2295103))
        self.assertEqual((disc.state, disc.can_calibrate, disc.needs_erase), (DiscState.ERASABLE, True, True))

    def test_unsupported_discs_say_why(self):
        dual = parse_info("E:", report(code=0x2B))
        self.assertEqual((dual.state, dual.media), (DiscState.UNSUPPORTED, "DVD+R DL"))
        self.assertIn("Dual-layer", dual.reason)
        self.assertEqual(parse_info("E:", report(code=0x10)).state, DiscState.UNSUPPORTED)

    def test_tray_and_missing_disc_come_from_sense(self):
        self.assertEqual(parse_info("E:", report(code=0, sense="2/3A/02")).state, DiscState.TRAY_OPEN)
        self.assertEqual(parse_info("E:", report(code=0, sense="2/3A/00")).state, DiscState.NO_DISC)
        self.assertEqual(parse_info("E:", report(code=0, sense="2/04/01")).state, DiscState.LOADING)
        self.assertEqual(parse_info("E:", report(code=0)).state, DiscState.NO_DISC)


class Parsers(unittest.TestCase):
    def test_progress_lines(self):
        self.assertEqual(calibration.parse_rung("  rung  9: LBA   123456 (+1.0 turns) phase 1.0"), 9)
        self.assertIsNone(calibration.parse_rung("Reference LBA 5; readable ranges [(1, 2)]"))
        self.assertTrue(calibration.is_zone("Zone 0.35 (LBA 4230880): T = 6.26 ms (9578.9 rpm)"))
        self.assertAlmostEqual(calibration.parse_percent("   37.5 %"), 0.375)

    def test_both_geo_result_lines_name_their_folder(self):
        for line, folder in (
            ("targets.csv, summary.json, events.csv: C:\\r\\geo-E-1", "C:\\r\\geo-E-1"),
            ("zones.csv, summary.json: C:\\r\\geo-E-2", "C:\\r\\geo-E-2"),
        ):
            self.assertEqual(calibration._RESULT_DIR.search(line).group(1), folder)

    def test_fits_from_angle_ladders_and_period_fits(self):
        angular = read_fit({"fit": {"pitch_um_at_bit": 0.7395, "residual_rms_deg": 0.31}})
        self.assertEqual((angular.pitch_um, angular.residual_deg, angular.clv), (0.7395, 0.31, False))
        period = read_fit({"fit": {"model": "clv", "pitch_um": 0.32, "zones_used": 9}})
        self.assertEqual((period.pitch_um, period.residual_deg, period.points, period.clv), (0.32, None, 9, True))
        with self.assertRaises(KeyError):
            read_fit({"fit": {}})

    def test_preset_identity_keeps_existing_dvd_ids(self):
        disc = DiscInfo("E:", DiscState.BLANK, media="DVD+R", family="dvd", mid="SONY/D21/0", sectors=1)
        self.assertEqual(calibration.preset_identity(disc), ("DVD+R (SONY/D21/0, Calibrated)", "sony-d21-0-calibrated"))

    def test_preset_identity_without_a_manufacturer_code(self):
        disc = DiscInfo("E:", DiscState.BLANK, media="BD-R", family="bd")
        self.assertEqual(calibration.preset_identity(disc), ("BD-R (Calibrated)", "bd-r-calibrated"))

    def test_pattern_shrinks_for_smaller_discs(self):
        cd = FAMILIES["cd"]
        self.assertEqual(cd.pattern_length(359_847), 359_600)  # 80 minutes
        self.assertEqual(cd.pattern_length(333_000), 333_000 - 247)  # 74 minutes
        self.assertEqual(cd.pattern_length(0), 359_600)
        self.assertEqual(FAMILIES["dvd"].pattern_length(2_298_496), 2_295_104)  # DVD-R holds more

    def test_erase_step_only_for_rewritables(self):
        blank = DiscInfo("E:", DiscState.BLANK, media="DVD-R", family="dvd")
        used_rw = DiscInfo("E:", DiscState.ERASABLE, media="DVD-RW", family="dvd")
        self.assertNotIn(Stage.ERASE, stages_for(blank))
        self.assertEqual(stages_for(used_rw)[0], Stage.ERASE)

    def test_drive_letters_are_validated_before_reaching_a_shell(self):
        self.assertEqual(normalize_letter("e"), "E:")
        self.assertEqual(normalize_letter("E:\\"), "E:")
        for bad in ("E:'; calc;'", "", "EE:", "1:"):
            with self.assertRaises(ValueError):
                normalize_letter(bad)


class Streaming(unittest.TestCase):
    def test_lines_arrive_while_the_command_runs_and_ticks_fire(self):
        code = "import time\nfor i in range(3):\n    print(i, flush=True); time.sleep(0.4)"
        seen, ticks = [], []
        result = stream([python_exe(), "-c", code], CancelToken(), on_line=seen.append,
                        on_tick=lambda: ticks.append(1), tick_s=0.2)
        self.assertEqual((result.ok, seen), (True, ["0", "1", "2"]))
        self.assertGreaterEqual(len(ticks), 2)

    def test_cancel_stops_the_child_promptly(self):
        token = CancelToken()
        threading.Timer(0.5, token.cancel).start()
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            stream([python_exe(), "-c", "import time; time.sleep(30)"], token)
        self.assertLess(time.monotonic() - started, 5)


FAKE_DISCPROBE = {
    "discprobe/__init__.py": "",
    "discprobe/__main__.py": textwrap.dedent('''
        import json, os, sys
        args = sys.argv
        cmd = args[1]
        media = args[args.index("--media") + 1] if "--media" in args else "dvd"
        if cmd == "random":
            out = args[args.index("--output") + 1]
            sectors = int(args[args.index("--sectors") + 1])
            print("   50.0 %"); open(out, "wb").write(b"\\0" * 2048 * sectors)
        elif cmd == "geo":
            out = os.path.join(os.getcwd(), "results", "geo-test"); os.makedirs(out, exist_ok=True)
            if media == "bd":
                for i in range(3):
                    print(f"Zone 0.{i + 1}5 (LBA {i * 1000}): T = 6.2 ms (9578.9 rpm)", flush=True)
                fit = {"model": "clv", "pitch_um": 0.32, "r0_mm": 24.07, "zones_used": 3}
                print(f"zones.csv, summary.json: {out}")
            else:
                for i in range(1, 4):
                    print(f"  rung {i:2d}: LBA {i * 1000:>8} phase 1.0 +/- 0.1 deg", flush=True)
                fit = {"pitch_um_at_bit": 0.7395, "residual_rms_deg": 0.31}
                print(f"targets.csv, summary.json, events.csv: {out}")
            json.dump({"fit": fit}, open(os.path.join(out, "summary.json"), "w"))
        elif cmd == "disexport":
            assert args[args.index("--media") + 1] == media
            assert "--install" in args and "--replace" in args
            open(os.path.join(os.getcwd(), "exported.txt"), "w").write(args[args.index("--sectors") + 1])
        else:
            sys.exit("unexpected " + cmd)
    '''),
    "tools/burn_imapi.ps1": textwrap.dedent('''
        param([string]$Drive, [string]$Iso, [double]$Speed, [switch]$Burn, [switch]$Reload)
        if (-not $Iso) { "Media type 18 (DVD), blank: True, free sectors: 300, total: 300, next writable LBA: 0"; return }
        "Image: $Iso, speed $Speed"
        "The drive does not report progress here. Estimate from the write speed: about 1 min."
        "Writing ... (started 00:00:00)"
        "Done in 0.0 min"
    '''),
}
FAKE_ERASE = '"Erasing ..."\n"Erased"\n'


@unittest.skipUnless(os.name == "nt", "the burn step runs a PowerShell script")
class Pipeline(unittest.TestCase):
    def _run(self, disc, drive_after=None):
        """Run the whole pipeline against a fake disc-probe; return (result, events, folder contents)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, body in FAKE_DISCPROBE.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(body, encoding="utf-8")
            erase = root / "erase.ps1"
            erase.write_text(FAKE_ERASE, encoding="utf-8")

            events = []
            patched = {
                "read_disc_info": lambda *_: drive_after or DiscInfo("E:", DiscState.WRITTEN, sectors=disc.sectors),
                "SETTLE_SECONDS": 1,
                "ERASE_SCRIPT": erase,
            }
            original = {name: getattr(calibration, name) for name in patched}
            for name, value in patched.items():
                setattr(calibration, name, value)
            try:
                result = Calibrator(root, events.append).run(disc, CancelToken())
            finally:
                for name, value in original.items():
                    setattr(calibration, name, value)
            leftovers = sorted(p.name for p in (root / "iso").iterdir())
            return result, events, leftovers, (root / "exported.txt").read_text()

    def test_dvd_run(self):
        disc = DiscInfo("E:", DiscState.BLANK, media="DVD+R", family="dvd", mid="SONY/D21/0", sectors=100)
        result, events, leftovers, exported_sectors = self._run(disc)

        self.assertEqual((result.fit.pitch_um, result.fit.residual_deg, result.points), (0.7395, 0.31, 3))
        self.assertEqual([e.stage for e in events if isinstance(e, StageDone)], stages_for(disc))
        measured = [e.fraction for e in events if isinstance(e, Progress) and e.stage is Stage.MEASURE]
        self.assertIn(1 / FAMILIES["dvd"].measure_points, measured)
        self.assertTrue(any(isinstance(e, LogLine) and "Writing" in e.text for e in events))
        self.assertEqual(exported_sectors, "100")
        self.assertEqual(leftovers, ["random.iso"])  # a DVD pattern is kept for the next disc

    def test_blu_ray_run_counts_zones_and_deletes_its_huge_pattern(self):
        disc = DiscInfo("E:", DiscState.BLANK, media="BD-R", family="bd", sectors=200)
        result, events, leftovers, exported_sectors = self._run(disc)

        self.assertTrue(result.fit.clv)
        self.assertEqual(result.points, 3)
        self.assertTrue(any("experimental" in n for n in result.notes))
        self.assertTrue(any("nominal" in n for n in result.notes))
        self.assertEqual(leftovers, [])
        self.assertEqual(exported_sectors, "200")

    def test_capacity_comes_from_imapi_when_info_reports_none(self):
        disc = DiscInfo("E:", DiscState.BLANK, media="BD-R", family="bd", sectors=0)
        _, _, _, exported_sectors = self._run(disc)
        self.assertEqual(exported_sectors, "300")  # the fake burn script's "free sectors"

    def test_rewritable_disc_is_erased_first_and_burned_disc_still_reads_as_erasable(self):
        disc = DiscInfo("E:", DiscState.ERASABLE, media="DVD-RW", family="dvd", sectors=100)
        after = DiscInfo("E:", DiscState.ERASABLE, sectors=100)  # a burned RW disc is not "blank"
        result, events, _, _ = self._run(disc, drive_after=after)

        done = [e.stage for e in events if isinstance(e, StageDone)]
        self.assertEqual(done[0], Stage.ERASE)
        self.assertEqual(done, stages_for(disc))
        self.assertEqual(result.points, 3)

    def test_refuses_discs_it_cannot_calibrate(self):
        calibrator = Calibrator(Path("."), lambda e: None)
        for disc in (
            DiscInfo("E:", DiscState.WRITTEN, media="DVD+R", family="dvd", sectors=100),
            DiscInfo("E:", DiscState.UNSUPPORTED, media="DVD+R DL"),
        ):
            with self.assertRaises(CalibrationError):
                calibrator.run(disc, CancelToken())


class CdProject(unittest.TestCase):
    """The project is what anyone imports, so it must carry the installed preset's geometry."""

    PRESET = {
        "id": "atip-97-26-66-calibrated", "displayName": "CD-R (ATIP 97:26:66, Calibrated)",
        "sectors": 359846, "innerRadiusMm": 25.27, "outerRadiusMm": 58.28,
        "pitchLinear": 2.3e-08, "pitchQuadratic": -5.6e-08, "pitchCubic": 4.3e-08,
        "linearVelocityMmPerSecond": 1200,
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.presets = self.folder / "disc-presets.json"
        self.presets.write_text(json.dumps({"cdPresets": [self.PRESET], "dvdPresets": []}), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_project_geometry_is_the_installed_preset(self):
        path = write_project("atip-97-26-66-calibrated", "CD-R (ATIP 97:26:66, Calibrated)",
                             folder=self.folder / "projects", presets_file=self.presets)
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(doc["discFamily"], "cd")
        self.assertEqual(doc["schemaVersion"], 2)
        self.assertEqual(doc["geometry"], {k: self.PRESET[k] for k in GEOMETRY_KEYS})
        self.assertEqual(doc["layers"], [])
        self.assertEqual(path.name, "CD-R (ATIP 97_26_66, Calibrated).discproj")

    def test_unknown_preset_is_an_error_not_an_empty_project(self):
        with self.assertRaises(ProjectError):
            write_project("no-such-preset", "x", folder=self.folder, presets_file=self.presets)
        self.assertEqual(list(self.folder.glob("*.discproj")), [])


if __name__ == "__main__":
    unittest.main()
