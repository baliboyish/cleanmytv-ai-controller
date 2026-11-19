#!/usr/bin/env python3
"""Unit tests for cmtv Phase 3 (slideshow + day/night looks) — mock only."""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

spec = importlib.util.spec_from_file_location(
    "cmtv", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cmtv.py"))
cmtv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmtv)


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _dump(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh)


def _read(path):
    with open(path) as fh:
        return fh.read()


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cmtv.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CMTV_STATE_DIR"] = self.tmp
        os.environ["CMTV_MOCK"] = "1"
        os.environ["CMTV_ASSETS_DIR"] = os.path.join(self.tmp, "assets")
        run_cli("slides", "status")  # materialize mock state
        from PIL import Image
        self.pics = []
        for i, color in enumerate([(200, 30, 30), (30, 200, 30),
                                   (30, 30, 200)]):
            p = os.path.join(self.tmp, f"pic{i}.jpg")
            Image.new("RGB", (320, 180), color).save(p, "JPEG")
            self.pics.append(p)

    def tearDown(self):
        for k in ("CMTV_STATE_DIR", "CMTV_MOCK", "CMTV_ASSETS_DIR"):
            os.environ.pop(k, None)

    def cfg(self):
        return _load(os.path.join(self.tmp, "mock-tv.json"))

    def tv_state(self):
        return _load(os.path.join(self.tmp, "mock-tv-state.json"))

    def set_fail(self, fail):
        st = self.tv_state()
        st["fail"] = fail
        _dump(os.path.join(self.tmp, "mock-tv-state.json"), st)

    def upload(self, pics=None, *extra):
        return run_cli("slides", "upload", *(pics or self.pics[:2]),
                       *extra)


class TestTimeHelpers(unittest.TestCase):
    def test_hhmm(self):
        self.assertEqual(cmtv.parse_hhmm("06:00"), 360)
        self.assertEqual(cmtv.parse_hhmm("18:30"), 1110)
        self.assertEqual(cmtv.fmt_hhmm(420), "07:00")
        for bad in ("6", "24:00", "12:60", "ab:cd", ""):
            with self.assertRaises(cmtv.CmtvError):
                cmtv.parse_hhmm(bad)

    def test_slot_at(self):
        # day 07:00, night 19:00
        self.assertEqual(cmtv.slot_at(12 * 60, 420, 1140), "day")
        self.assertEqual(cmtv.slot_at(23 * 60, 420, 1140), "night")
        self.assertEqual(cmtv.slot_at(3 * 60, 420, 1140), "night")
        self.assertEqual(cmtv.slot_at(3 * 60, 420, 420), "day")


class TestSlides(Base):
    def test_upload_happy_path_and_archive(self):
        code, out, _ = self.upload()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.tv_state()["slides"]), 2)
        code, out, _ = run_cli("slides", "status")
        self.assertIn("Slides on TV: 2", out)
        arch = os.path.join(self.tmp, "assets", "slides")
        sets = os.listdir(arch)
        self.assertEqual(len(sets), 1)
        files = os.listdir(os.path.join(arch, sets[0]))
        self.assertIn("manifest.json", files)
        self.assertEqual(len([f for f in files if f.endswith(".jpg")]), 2)

    def test_too_many_files_refused(self):
        code, _, err = self.upload(self.pics * 5)  # 15 files
        self.assertEqual(code, 1)
        self.assertEqual(len(self.tv_state()["slides"]), 0)

    def test_replace_gate(self):
        self.upload()
        first = [s["md5"] for s in self.tv_state()["slides"]]
        code, _, err = self.upload(self.pics[2:])
        self.assertEqual(code, 1)
        self.assertIn("--replace", err)
        self.assertEqual([s["md5"] for s in self.tv_state()["slides"]],
                         first)
        code, _, _ = self.upload(self.pics[2:], "--replace")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.tv_state()["slides"]), 1)
        self.assertNotEqual([s["md5"] for s in self.tv_state()["slides"]],
                            first)

    def test_corrupt_once_retries_and_succeeds(self):
        self.set_fail({"corrupt_once": True})
        code, _, _ = self.upload()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.tv_state()["slides"]), 2)

    def test_persistent_failure_keeps_old_set(self):
        self.upload()
        first = [s["md5"] for s in self.tv_state()["slides"]]
        self.set_fail({"chunk_always": True})
        code, _, _ = self.upload(self.pics[2:], "--replace")
        self.assertEqual(code, 1)
        # slides_end never committed: the old set is still live.
        self.assertEqual([s["md5"] for s in self.tv_state()["slides"]],
                         first)

    def test_enable_disable_cycle(self):
        code, _, err = run_cli("slides", "enable", "--minutes", "5")
        self.assertEqual(code, 1)  # no slides yet
        self.upload()
        code, _, _ = run_cli("slides", "enable", "--minutes", "5")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["slide_minutes"], 5)
        self.assertEqual(self.cfg()["slide_from"], "mine")
        code, _, _ = run_cli("undo")   # config rollback covers the enable
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["slide_minutes"], 0)
        # …but undo does NOT remove the uploaded files (documented limit)
        self.assertEqual(len(self.tv_state()["slides"]), 2)
        run_cli("slides", "enable", "--minutes", "5")
        code, _, _ = run_cli("slides", "disable")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["slide_minutes"], 0)

    def test_send_file_gives_up_after_one_retry(self):
        class FakeCtx:
            def __init__(self):
                self.begins = 0

            def call(self, method, arg=None):
                if method == "slide_file_begin":
                    self.begins += 1
                if method == "slide_file_end":
                    raise cmtv.CmtvError("damaged (md5 x)")
                return ""
        fake = FakeCtx()
        with self.assertRaises(cmtv.CmtvError):
            cmtv._send_slide_file(fake, 0, b"\xff\xd8" + b"0" * 100)
        self.assertEqual(fake.begins, 2)


class TestLooks(Base):
    def test_save_gate_and_force(self):
        code, out, _ = run_cli("looks", "save-day")
        self.assertEqual(code, 0)
        self.assertEqual(self.tv_state()["looks"], ["day"])
        code, _, err = run_cli("looks", "save-day")
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        code, _, _ = run_cli("looks", "save-day", "--force")
        self.assertEqual(code, 0)
        code, out, _ = run_cli("looks", "status")
        self.assertIn("day", out)

    def test_schedule_and_undo(self):
        code, _, _ = run_cli("looks", "schedule", "--day", "06:00",
                             "--night", "18:00")
        self.assertEqual(code, 0)
        cfg = self.cfg()
        self.assertEqual((cfg["day_at"], cfg["night_at"],
                          cfg["day_night"]), (360, 1080, True))
        code, _, _ = run_cli("undo")
        self.assertEqual(code, 0)
        cfg = self.cfg()
        self.assertEqual((cfg["day_at"], cfg["night_at"],
                          cfg["day_night"]), (420, 1140, False))

    def test_schedule_validation(self):
        code, _, _ = run_cli("looks", "schedule", "--day", "06:00",
                             "--night", "06:00")
        self.assertEqual(code, 1)
        code, _, _ = run_cli("looks", "schedule", "--day", "pagi",
                             "--night", "18:00")
        self.assertEqual(code, 1)
        self.assertFalse(self.cfg()["day_night"])

    def test_disable(self):
        run_cli("looks", "schedule", "--day", "06:00", "--night", "18:00")
        code, _, _ = run_cli("looks", "disable")
        self.assertEqual(code, 0)
        self.assertFalse(self.cfg()["day_night"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
