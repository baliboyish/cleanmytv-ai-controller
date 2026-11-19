#!/usr/bin/env python3
"""Unit tests for cmtv Phase 4 (preset engine + native themes) — mock."""
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
        os.environ["CMTV_PRESETS_DIR"] = os.path.join(self.tmp, "presets")
        run_cli("preset", "list")  # materialize mock state

    def tearDown(self):
        for k in ("CMTV_STATE_DIR", "CMTV_MOCK", "CMTV_PRESETS_DIR"):
            os.environ.pop(k, None)

    def cfg(self):
        return _load(os.path.join(self.tmp, "mock-tv.json"))

    def stack_len(self):
        p = os.path.join(self.tmp, "stack.json")
        return len(_load(p)) if os.path.exists(p) else 0


class TestBuiltinValidity(unittest.TestCase):
    def test_all_builtins_schema_valid(self):
        for name, fields in cmtv.BUILTIN_PRESETS.items():
            self.assertTrue(set(fields) <= set(cmtv.PRESET_FIELDS),
                            f"{name} has non-preset fields")
            cmtv.validate_change(fields)  # raises if invalid

    def test_preset_fields_never_touch_apps_or_schedules(self):
        for f in ("order", "hidden", "quick", "folders", "sections",
                  "day_night", "day_at", "night_at", "slide_minutes",
                  "lock_on", "layout"):
            self.assertNotIn(f, cmtv.PRESET_FIELDS)


class TestPresets(Base):
    def test_save_from_current_captures_visual_only(self):
        code, _, _ = run_cli("preset", "save", "My Style")
        self.assertEqual(code, 0)
        data = _load(os.path.join(
            self.tmp, "presets", "my-style.json"))
        self.assertEqual(data["fields"]["accent"], "#FFB23C")
        self.assertNotIn("order", data["fields"])
        self.assertNotIn("quick", data["fields"])

    def test_save_with_overrides_and_bad_field(self):
        code, _, _ = run_cli("preset", "save", "Blue Squircle",
                             "--set", "accent=#3B82F6",
                             "--set", "tile_shape=squircle",
                             "--set", "focus=glow")
        self.assertEqual(code, 0)
        _, fields, _ = cmtv.load_preset("blue squircle")
        self.assertEqual(fields["accent"], "#3B82F6")
        self.assertEqual(fields["tile_shape"], "squircle")
        code, _, _ = run_cli("preset", "save", "Bad", "--set",
                             "order=com.nuvio.livetv")
        self.assertEqual(code, 1)

    def test_preview_is_a_pure_dry_run(self):
        run_cli("preset", "save", "Snapshot Look")
        run_cli("set", "accent", "#111111")
        before_cfg = dict(self.cfg())
        before_stack = self.stack_len()
        code, out, _ = run_cli("preset", "preview", "Snapshot Look")
        self.assertEqual(code, 0)
        self.assertIn("accent", out)
        self.assertIn("Preview only", out)
        self.assertEqual(self.cfg(), before_cfg)
        self.assertEqual(self.stack_len(), before_stack)

    def test_apply_and_undo_restores(self):
        code, out, _ = run_cli("preset", "apply", "midnight")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["accent"], "#7C5CFF")
        self.assertTrue(self.cfg()["snow"])
        code, _, _ = run_cli("undo")  # "balikin tampilan sebelumnya"
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["accent"], "#FFB23C")
        self.assertFalse(self.cfg()["snow"])

    def test_apply_refused_while_daynight_on(self):
        run_cli("set", "day_night", "true")
        code, _, err = run_cli("preset", "apply", "midnight")
        self.assertEqual(code, 1)
        self.assertIn("day/night", err)
        self.assertEqual(self.cfg()["accent"], "#FFB23C")
        code, _, _ = run_cli("preset", "apply", "midnight", "--force")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["accent"], "#7C5CFF")
        self.assertTrue(self.cfg()["day_night"])  # schedule untouched

    def test_delete_rules(self):
        run_cli("preset", "save", "Temp")
        code, _, _ = run_cli("preset", "delete", "Temp")
        self.assertEqual(code, 0)
        code, _, _ = run_cli("preset", "delete", "cinema")
        self.assertEqual(code, 1)  # built-in
        code, _, _ = run_cli("preset", "delete", "nope")
        self.assertEqual(code, 1)

    def test_corrupt_preset_refused(self):
        os.makedirs(os.path.join(self.tmp, "presets"), exist_ok=True)
        _dump(os.path.join(self.tmp, "presets", "evil.json"), {"name": "Evil", "fields": {"order": ["a.b"]}})
        code, _, err = run_cli("preset", "preview", "Evil")
        self.assertEqual(code, 1)
        self.assertIn("not visual preset fields", err)

    def test_user_preset_shadows_builtin(self):
        run_cli("preset", "save", "Cinema", "--set", "accent=#010203")
        _, fields, origin = cmtv.load_preset("cinema")
        self.assertEqual(fields["accent"], "#010203")
        self.assertIn("saved", origin)


class TestThemes(Base):
    def test_status_list_save(self):
        code, out, _ = run_cli("theme", "status")
        self.assertIn("Native themes on the TV: 0", out)
        code, out, _ = run_cli("theme", "list")
        self.assertIn("Aurora", out)
        self.assertIn("Gruvbox Green", out)
        code, out, _ = run_cli("theme", "save", "My Style")
        self.assertEqual(code, 0)
        self.assertIn("my-my-style", out)
        code, out, _ = run_cli("theme", "status")
        self.assertIn("Native themes on the TV: 1", out)
        code, _, _ = run_cli("theme", "save", "Second")
        self.assertEqual(code, 0)
        code, out, _ = run_cli("theme", "status")
        self.assertIn("Native themes on the TV: 2", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
