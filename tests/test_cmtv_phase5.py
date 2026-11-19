#!/usr/bin/env python3
"""Unit tests for cmtv Phase 5 (fireplace & ambient) — mock TV only."""
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
        run_cli("fire", "status")

    def tearDown(self):
        for k in ("CMTV_STATE_DIR", "CMTV_MOCK"):
            os.environ.pop(k, None)

    def cfg(self):
        return _load(os.path.join(self.tmp, "mock-tv.json"))

    def stack_len(self):
        p = os.path.join(self.tmp, "stack.json")
        return len(_load(p)) if os.path.exists(p) else 0


class TestFireSchema(unittest.TestCase):
    def test_fire_fields_are_bools_not_enums(self):
        # Config.kt: fireWall/fireSound/fireWallSound are Boolean.
        cmtv.validate_change({"fire_wall": True, "fire_sound": False,
                              "fire_wall_sound": True})
        with self.assertRaises(cmtv.CmtvError):
            cmtv.validate_change({"fire_wall": "original"})

    def test_fire_ranges(self):
        cmtv.validate_change({"fire_volume": 0, "fire_timer": 240})
        with self.assertRaises(cmtv.CmtvError):
            cmtv.validate_change({"fire_volume": 101})
        with self.assertRaises(cmtv.CmtvError):
            cmtv.validate_change({"fire_timer": 241})


class TestFire(Base):
    def test_status_reports_asset_and_fields(self):
        code, out, _ = run_cli("fire", "status")
        self.assertEqual(code, 0)
        self.assertIn("Fire video on the TV: yes", out)
        self.assertIn("Fire as Home wallpaper: off", out)
        self.assertIn("Volume: 40", out)
        self.assertIn("no API trigger", out)

    def test_enable_and_undo(self):
        code, _, _ = run_cli("fire", "enable")
        self.assertEqual(code, 0)
        self.assertTrue(self.cfg()["fire_wall"])
        code, _, _ = run_cli("undo")
        self.assertEqual(code, 0)
        self.assertFalse(self.cfg()["fire_wall"])

    def test_enable_with_sound(self):
        code, _, _ = run_cli("fire", "enable", "--sound")
        self.assertEqual(code, 0)
        self.assertTrue(self.cfg()["fire_wall"])
        self.assertTrue(self.cfg()["fire_wall_sound"])

    def test_disable(self):
        run_cli("fire", "enable")
        code, _, _ = run_cli("fire", "disable")
        self.assertEqual(code, 0)
        self.assertFalse(self.cfg()["fire_wall"])

    def test_settings_view_is_pure(self):
        before = self.stack_len()
        code, out, _ = run_cli("fire", "settings")
        self.assertEqual(code, 0)
        self.assertIn("fire_volume=40", out)
        self.assertEqual(self.stack_len(), before)

    def test_settings_apply_and_validation(self):
        code, _, _ = run_cli("fire", "settings", "--volume", "25",
                             "--timer", "30", "--sound", "off",
                             "--wall-sound", "on")
        self.assertEqual(code, 0)
        cfg = self.cfg()
        self.assertEqual(cfg["fire_volume"], 25)
        self.assertEqual(cfg["fire_timer"], 30)
        self.assertFalse(cfg["fire_sound"])
        self.assertTrue(cfg["fire_wall_sound"])
        code, _, _ = run_cli("fire", "settings", "--volume", "500")
        self.assertEqual(code, 1)

    def test_fire_does_not_touch_daynight_or_looks(self):
        run_cli("set", "day_night", "true")
        run_cli("looks", "save-day")
        code, _, _ = run_cli("fire", "enable")
        self.assertEqual(code, 0)
        self.assertTrue(self.cfg()["day_night"])
        state = _load(os.path.join(self.tmp,
                                            "mock-tv-state.json"))
        self.assertEqual(state["looks"], ["day"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
