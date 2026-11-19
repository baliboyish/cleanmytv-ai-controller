#!/usr/bin/env python3
"""Unit tests for cmtv (Phase 1) — mock ADB only, never touches the TV."""
import importlib.util
import io
import json
import os
import sys
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

    def tearDown(self):
        os.environ.pop("CMTV_STATE_DIR", None)
        os.environ.pop("CMTV_MOCK", None)

    def mock_config(self):
        return _load(os.path.join(self.tmp, "mock-tv.json"))


class TestParser(unittest.TestCase):
    def test_nested_and_braces_in_strings(self):
        bundle = ('Result: Bundle[{config={"folders":[{"name":"A } brace",'
                  '"apps":["x.y"]}],"accent":"#FFB23C"}}]')
        cfg = cmtv.parse_config(bundle)
        self.assertEqual(cfg["accent"], "#FFB23C")
        self.assertEqual(cfg["folders"][0]["name"], "A } brace")

    def test_missing_config(self):
        with self.assertRaises(cmtv.CmtvError):
            cmtv.parse_config("Result: Bundle[{other=1}]")


class TestValidation(unittest.TestCase):
    def v(self, f, val):
        return cmtv.validate_field(f, val)

    def test_unknown_field_refused(self):
        with self.assertRaises(cmtv.CmtvError):
            self.v("nonsense_field", 1)

    def test_protected_and_status_refused(self):
        with self.assertRaises(cmtv.CmtvError):
            self.v("stock_redirect", "x")
        with self.assertRaises(cmtv.CmtvError):
            self.v("has_wallpaper", True)

    def test_pin_never_handled(self):
        with self.assertRaises(cmtv.CmtvError):
            self.v("lock_pin", "1234")
        with self.assertRaises(cmtv.CmtvError):
            self.v("pin", "1234")
        # …but pin_focus is an ordinary bool setting, not the PIN.
        self.assertIs(self.v("pin_focus", True), True)

    def test_enum(self):
        self.assertEqual(self.v("font", "serif"), "serif")
        with self.assertRaises(cmtv.CmtvError):
            self.v("font", "apple")  # no custom fonts in the launcher

    def test_range(self):
        self.assertEqual(self.v("columns", 6), 6)
        with self.assertRaises(cmtv.CmtvError):
            self.v("columns", 99)
        with self.assertRaises(cmtv.CmtvError):
            self.v("columns", 4.5)

    def test_color(self):
        self.assertEqual(self.v("accent", "#3b82f6"), "#3B82F6")
        with self.assertRaises(cmtv.CmtvError):
            self.v("accent", "blue")
        with self.assertRaises(cmtv.CmtvError):
            self.v("accent", "#12345")

    def test_bool_coercion(self):
        self.assertIs(self.v("snow", "on"), True)
        self.assertIs(self.v("snow", False), False)
        with self.assertRaises(cmtv.CmtvError):
            self.v("snow", "maybe")

    def test_sections(self):
        self.assertEqual(self.v("sections", ["folders", "dock"]),
                         ["folders", "dock"])
        with self.assertRaises(cmtv.CmtvError):
            self.v("sections", ["folders", "folders"])
        with self.assertRaises(cmtv.CmtvError):
            self.v("sections", ["nope"])

    def test_quick(self):
        self.assertEqual(self.v("quick", ["wifi", "app:com.nuvio.livetv"]),
                         ["wifi", "app:com.nuvio.livetv"])
        with self.assertRaises(cmtv.CmtvError):
            self.v("quick", ["app:not a package"])
        with self.assertRaises(cmtv.CmtvError):
            self.v("quick", ["app:a.b"] * 7)

    def test_power_on_wait(self):
        self.assertEqual(self.v("power_on_wait", 3), 3)
        with self.assertRaises(cmtv.CmtvError):
            self.v("power_on_wait", 4)

    def test_wallpaper_from(self):
        self.assertEqual(self.v("wallpaper_from", ""), "")
        self.assertEqual(self.v("wallpaper_from", "midnight:2"),
                         "midnight:2")
        with self.assertRaises(cmtv.CmtvError):
            self.v("wallpaper_from", "http://x/y.jpg")


class TestFlow(Base):
    def test_get(self):
        code, out, _ = run_cli("get", "--field", "accent")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), "#FFB23C")

    def test_set_snapshot_diff_undo(self):
        code, out, _ = run_cli("set", "accent", "#3B82F6")
        self.assertEqual(code, 0)
        self.assertIn("Snapshot saved", out)
        self.assertEqual(self.mock_config()["accent"], "#3B82F6")
        # snapshot holds the pre-change config
        stack = _load(os.path.join(self.tmp, "stack.json"))
        self.assertEqual(len(stack), 1)
        self.assertEqual(stack[0]["fields"], ["accent"])
        snap = _load(os.path.join(
            self.tmp, "snapshots", stack[0]["snapshot"]))
        self.assertEqual(snap["config"]["accent"], "#FFB23C")
        # diff shows the change
        code, out, _ = run_cli("diff")
        self.assertEqual(code, 0)
        self.assertIn("accent", out)
        # verify passes
        code, out, _ = run_cli("verify")
        self.assertEqual(code, 0)
        self.assertIn("OK", out)
        # undo restores exactly
        code, out, _ = run_cli("undo")
        self.assertEqual(code, 0)
        self.assertEqual(self.mock_config()["accent"], "#FFB23C")
        code, out, _ = run_cli("undo")
        self.assertIn("Nothing to undo", out)

    def test_set_is_selective(self):
        # Only the requested field may change on the mock TV.
        before = None
        run_cli("get")  # materialize mock state
        before = dict(self.mock_config())
        code, _, _ = run_cli("set", "--json", '{"snow": true}')
        self.assertEqual(code, 0)
        after = self.mock_config()
        changed = {k for k in before if before[k] != after.get(k)}
        self.assertEqual(changed, {"snow"})
        self.assertTrue(after["snow"])

    def test_noop_set_pushes_nothing(self):
        run_cli("get")
        code, out, _ = run_cli("set", "accent", "#FFB23C")
        self.assertEqual(code, 0)
        self.assertIn("No change", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp,
                                                     "stack.json")))

    def test_multi_field_undo(self):
        run_cli("get")
        code, _, _ = run_cli("set", "--json",
                             '{"accent": "#111111", "columns": 5}')
        self.assertEqual(code, 0)
        self.assertEqual(self.mock_config()["columns"], 5)
        code, _, _ = run_cli("undo")
        self.assertEqual(code, 0)
        cfg = self.mock_config()
        self.assertEqual(cfg["accent"], "#FFB23C")
        self.assertEqual(cfg["columns"], 8)

    def test_invalid_set_changes_nothing(self):
        run_cli("get")
        before = dict(self.mock_config())
        code, _, err = run_cli("set", "font", "apple")
        self.assertEqual(code, 1)
        self.assertIn("font", err)
        self.assertEqual(self.mock_config(), before)
        self.assertFalse(os.path.exists(os.path.join(self.tmp,
                                                     "stack.json")))

    def test_verify_detects_drift(self):
        run_cli("get")
        run_cli("set", "accent", "#3B82F6")
        cfg = self.mock_config()
        cfg["accent"] = "#000000"  # someone changed it behind our back
        _dump(os.path.join(self.tmp, "mock-tv.json"), cfg)
        code, out, _ = run_cli("verify")
        self.assertEqual(code, 1)
        self.assertIn("PROBLEM", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
