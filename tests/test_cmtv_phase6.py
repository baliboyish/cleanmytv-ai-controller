#!/usr/bin/env python3
"""Unit tests for cmtv Phase 6 (hardening) — mock TV only."""
import importlib.util
import io
import json
import os
import re
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
        run_cli("get")

    def tearDown(self):
        for k in ("CMTV_STATE_DIR", "CMTV_MOCK"):
            os.environ.pop(k, None)

    def cfg(self):
        return _load(os.path.join(self.tmp, "mock-tv.json"))

    def external_set(self, field, value):
        """Change the mock config behind cmtv's back (TV remote)."""
        cfg = self.cfg()
        cfg[field] = value
        _dump(os.path.join(self.tmp, "mock-tv.json"), cfg)

    def stack(self):
        p = os.path.join(self.tmp, "stack.json")
        return _load(p) if os.path.exists(p) else []


class TestUndoConflict(Base):
    def test_external_change_blocks_undo(self):
        run_cli("set", "accent", "#111111")
        self.external_set("accent", "#999999")  # changed on the TV
        code, _, err = run_cli("undo")
        self.assertEqual(code, 1)
        self.assertIn("undo conflict", err)
        self.assertEqual(self.cfg()["accent"], "#999999")  # untouched
        self.assertEqual(len(self.stack()), 1)  # entry kept
        code, _, _ = run_cli("undo", "--force")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["accent"], "#FFB23C")

    def test_unrelated_external_change_does_not_block(self):
        run_cli("set", "accent", "#111111")
        self.external_set("font", "serif")  # a field cmtv didn't set
        code, _, _ = run_cli("undo")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["accent"], "#FFB23C")
        self.assertEqual(self.cfg()["font"], "serif")  # preserved

    def test_legacy_entry_without_values_undoes_with_note(self):
        run_cli("set", "accent", "#111111")
        stack = self.stack()
        del stack[-1]["values"]
        _dump(os.path.join(self.tmp, "stack.json"), stack)
        code, out, _ = run_cli("undo")
        self.assertEqual(code, 0)
        self.assertIn("predates conflict detection", out)


class TestApplyFailure(Base):
    def test_failed_apply_leaves_no_stack_entry(self):
        state_p = os.path.join(self.tmp, "mock-tv-state.json")
        state = _load(state_p)
        state.setdefault("fail", {})["set64"] = True
        _dump(state_p, state)
        before = len(self.stack())
        code, _, _ = run_cli("set", "accent", "#111111")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.stack()), before)


class TestThemeSaveSideEffect(Base):
    def test_wallpaper_from_points_at_saved_theme(self):
        self.assertEqual(self.cfg()["wallpaper_from"], "")
        code, out, _ = run_cli("theme", "save", "My Style")
        self.assertEqual(code, 0)
        self.assertIn("side effect", out)
        self.assertEqual(self.cfg()["wallpaper_from"],
                         "my-my-style:0")

    def test_resave_same_name_overwrites(self):
        run_cli("theme", "save", "My Style")
        code, out, _ = run_cli("theme", "save", "My Style")
        self.assertEqual(code, 0)
        self.assertIn("overwritten", out)
        code, out, _ = run_cli("theme", "status")
        self.assertIn("Native themes on the TV: 1", out)


class TestFireNav(unittest.TestCase):
    QUICK = ["wifi", "bluetooth", "customize", "settings", "panel",
             "fireplace"]

    def test_steps_resolved_from_config(self):
        self.assertEqual(cmtv.resolve_steps(self.QUICK, "customize"),
                         3)
        self.assertEqual(cmtv.resolve_steps(self.QUICK, "wifi"), 5)
        self.assertEqual(cmtv.resolve_steps(self.QUICK, "fireplace"),
                         0)
        self.assertEqual(
            cmtv.resolve_steps(["fireplace", "wifi"], "wifi"), -1)

    def test_steps_refuse_unknowns(self):
        with self.assertRaises(cmtv.CmtvError):
            cmtv.resolve_steps(["wifi"], "wifi")  # no fireplace
        with self.assertRaises(cmtv.CmtvError):
            cmtv.resolve_steps(self.QUICK, "mystery")

    def test_focus_parsing(self):
        # Real dump shape: the focused node is unlabelled; the
        # item's label sits on a child at the same bounds.
        xml = ('<hierarchy>'
               '<node focused="true" content-desc="" '
               'bounds="[1736,334][1832,422]"/>'
               '<node focused="false" content-desc="Fireplace" '
               'bounds="[1748,346][1820,410]"/>'
               '<node focused="false" content-desc="Wi-Fi" '
               'bounds="[1252,334][1348,422]"/>'
               '</hierarchy>')
        self.assertEqual(cmtv.focused_quick_item(xml, self.QUICK),
                         "fireplace")
        # "Quick settings" is the panel item, not settings.
        xml_panel = ('<hierarchy>'
                     '<node focused="true" content-desc="" '
                     'bounds="[1580,334][1676,422]"/>'
                     '<node focused="false" '
                     'content-desc="Quick settings" '
                     'bounds="[1592,346][1664,410]"/>'
                     '<node focused="false" content-desc="Settings" '
                     'bounds="[1436,346][1508,410]"/>'
                     '</hierarchy>')
        self.assertEqual(cmtv.focused_quick_item(xml_panel,
                                                 self.QUICK), "panel")
        # Directly labelled focused node (fast path).
        xml_direct = ('<hierarchy><node focused="true" '
                      'content-desc="Fireplace" '
                      'bounds="[0,0][10,10]"/></hierarchy>')
        self.assertEqual(cmtv.focused_quick_item(xml_direct,
                                                 self.QUICK),
                         "fireplace")
        # Unprovable cases return None.
        xml_none = ('<hierarchy><node focused="false" '
                    'content-desc="Fireplace" '
                    'bounds="[0,0][10,10]"/></hierarchy>')
        self.assertIsNone(cmtv.focused_quick_item(xml_none,
                                                  self.QUICK))
        self.assertIsNone(cmtv.focused_quick_item("not xml",
                                                  self.QUICK))


class TestFireOpenMock(Base):
    def test_open_refused_on_mock(self):
        code, _, err = run_cli("fire", "open")
        self.assertEqual(code, 1)
        self.assertIn("real TV", err)


class TestEnvAndPrivacy(unittest.TestCase):
    def test_serial_env_override(self):
        os.environ.pop("CMTV_SERIAL", None)
        self.assertEqual(cmtv._serial(), "127.0.0.1:15555")
        os.environ["CMTV_SERIAL"] = "192.168.1.10:5555"
        try:
            self.assertEqual(cmtv._serial(), "192.168.1.10:5555")
        finally:
            os.environ.pop("CMTV_SERIAL", None)

    def test_adb_env_override(self):
        os.environ["CMTV_ADB"] = "/custom/adb"
        try:
            self.assertEqual(cmtv._adb_bin(), "/custom/adb")
        finally:
            os.environ.pop("CMTV_ADB", None)

    def test_source_has_no_personal_data(self):
        src = _read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cmtv.py"))
        self.assertIsNone(
            re.search(r"100\.\d{1,3}\.\d{1,3}\.\d{1,3}", src),
            "Tailscale-range IP in source")
        self.assertIsNone(re.search(r"/home/[^/\s]+/", src),
                          "absolute home path in source")


if __name__ == "__main__":
    unittest.main(verbosity=2)
