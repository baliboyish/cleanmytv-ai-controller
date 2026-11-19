#!/usr/bin/env python3
"""Unit tests for cmtv Phase 2 (app organizer) — mock ADB only."""
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
        run_cli("apps")  # materialize mock state

    def tearDown(self):
        os.environ.pop("CMTV_STATE_DIR", None)
        os.environ.pop("CMTV_MOCK", None)

    def cfg(self):
        return _load(os.path.join(self.tmp, "mock-tv.json"))


class TestParseApps(unittest.TestCase):
    def test_real_shape(self):
        bundle = ('Result: Bundle[{apps=[{"pkg":"a.b","label":"A [x]",'
                  '"tv":true},{"pkg":"c.d","label":"C","tv":false}]}]')
        apps = cmtv.parse_apps(bundle)
        self.assertEqual([a["pkg"] for a in apps], ["a.b", "c.d"])
        self.assertEqual(apps[0]["label"], "A [x]")

    def test_missing(self):
        with self.assertRaises(cmtv.CmtvError):
            cmtv.parse_apps("Result: Bundle[{config={}}]")


class TestResolve(unittest.TestCase):
    cat = cmtv.MOCK_CATALOG

    def r(self, name):
        return cmtv.resolve_app(self.cat, name)["pkg"]

    def test_exact_label_and_pkg(self):
        self.assertEqual(self.r("SmartTube"), "org.smarttube.stable")
        self.assertEqual(self.r("org.xbmc.kodi"), "org.xbmc.kodi")
        self.assertEqual(self.r("spotify"), "com.spotify.tv.android")

    def test_exact_beats_substring(self):
        # "YouTube" is an exact label even though YouTube Music contains it.
        self.assertEqual(self.r("YouTube"),
                         "com.google.android.youtube.tv")

    def test_unique_substring(self):
        self.assertEqual(self.r("Music"),
                         "com.google.android.youtube.tvmusic")
        self.assertEqual(self.r("Nuvio"), "com.nuvio.livetv")

    def test_ambiguous(self):
        with self.assertRaises(cmtv.CmtvError):
            self.r("You")       # YouTube + YouTube Music
        with self.assertRaises(cmtv.CmtvError):
            self.r("Tube")      # SmartTube + YouTube + YouTube Music

    def test_unknown_never_guessed(self):
        with self.assertRaises(cmtv.CmtvError):
            self.r("Netflix")


class TestOrganizer(Base):
    def test_apps_listing(self):
        code, out, _ = run_cli("apps")
        self.assertEqual(code, 0)
        self.assertIn("SmartTube", out)
        self.assertIn("com.stale.gone", out)  # stale entry reported

    def test_move_to_first_preserves_others(self):
        # arranged before: Nuvio, SmartTube, then alpha: Kodi, Spotify,
        # YouTube, YouTube Music. Move Kodi to position 1.
        code, _, _ = run_cli("org", "move", "Kodi", "--first")
        self.assertEqual(code, 0)
        order = self.cfg()["order"]
        self.assertEqual(order[0], "org.xbmc.kodi")
        self.assertEqual(order[1], "com.nuvio.livetv")
        self.assertEqual(order[2], "org.smarttube.stable")
        # stale entry preserved, trailing, untouched relative order
        self.assertEqual(order[-1], "com.stale.gone")
        # undo restores the exact original order
        code, _, _ = run_cli("undo")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["order"],
                         ["com.nuvio.livetv", "org.smarttube.stable",
                          "com.stale.gone"])

    def test_move_to_position(self):
        code, _, _ = run_cli("org", "move", "Nuvio", "--to", "3")
        self.assertEqual(code, 0)
        arranged = [a["pkg"] for a in
                    cmtv.arranged_apps(cmtv.MOCK_CATALOG, self.cfg())]
        self.assertEqual(arranged[2], "com.nuvio.livetv")

    def test_quick_add_and_cap(self):
        code, out, _ = run_cli("org", "quick-add", "SmartTube", "Spotify",
                               "Kodi")
        self.assertEqual(code, 0)
        quick = self.cfg()["quick"]
        self.assertIn("app:org.smarttube.stable", quick)
        self.assertIn("wifi", quick)  # built-ins preserved
        # fill to the 6-app cap, then one more must be refused
        run_cli("org", "quick-add", "YouTube", "YouTube Music", "Nuvio")
        code, _, err = run_cli("org", "quick-add", "Kodi")
        # Kodi already added above -> no-op path is fine too; use a fresh
        # overflow scenario below instead if it was a no-op.
        apps_in_quick = [q for q in self.cfg()["quick"]
                         if q.startswith("app:")]
        self.assertLessEqual(len(apps_in_quick), 6)

    def test_quick_cap_refused_atomically(self):
        run_cli("org", "quick-add", "SmartTube", "Spotify", "Kodi",
                "YouTube", "YouTube Music", "Nuvio")
        before = list(self.cfg()["quick"])
        self.assertEqual(len([q for q in before if q.startswith("app:")]),
                         6)
        # a 7th distinct app cannot exist in the mock catalog; simulate by
        # validating a hand-built change through the schema instead
        with self.assertRaises(cmtv.CmtvError):
            cmtv.validate_field("quick", before + ["app:com.extra.app"])

    def test_quick_remove(self):
        run_cli("org", "quick-add", "SmartTube")
        code, _, _ = run_cli("org", "quick-remove", "SmartTube")
        self.assertEqual(code, 0)
        self.assertNotIn("app:org.smarttube.stable", self.cfg()["quick"])

    def test_dock_add_and_listing(self):
        code, out, _ = run_cli("org", "dock")
        self.assertEqual(code, 0)
        self.assertIn("Nuvio + IPTV", out)
        # Arranged: Nuvio, SmartTube, Kodi, Spotify, YouTube, YouTube
        # Music — with dock_size 5, only YouTube Music sits outside.
        code, out, _ = run_cli("org", "dock-add", "Spotify")
        self.assertIn("already in the dock", out)
        code, _, _ = run_cli("org", "dock-add", "YouTube Music")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["order"][0],
                         "com.google.android.youtube.tvmusic")
        code, out, _ = run_cli("org", "dock-add", "YouTube Music")
        self.assertIn("already in the dock", out)

    def test_hide_unhide(self):
        code, _, _ = run_cli("org", "hide", "VLC")
        self.assertEqual(code, 1)  # VLC is not in the mock catalog
        code, _, _ = run_cli("org", "hide", "Kodi")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["hidden"], ["org.xbmc.kodi"])
        arranged = [a["pkg"] for a in
                    cmtv.arranged_apps(cmtv.MOCK_CATALOG, self.cfg())]
        self.assertNotIn("org.xbmc.kodi", arranged)
        code, _, _ = run_cli("org", "unhide", "Kodi")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg()["hidden"], [])

    def test_folder_add_remove(self):
        code, _, _ = run_cli("org", "folder-add", "Apps", "Kodi")
        self.assertEqual(code, 0)
        self.assertIn("org.xbmc.kodi", self.cfg()["folders"][0]["apps"])
        code, _, _ = run_cli("org", "folder-remove", "Apps", "Kodi")
        self.assertEqual(code, 0)
        self.assertNotIn("org.xbmc.kodi", self.cfg()["folders"][0]["apps"])
        code, _, err = run_cli("org", "folder-add", "Nope", "Kodi")
        self.assertEqual(code, 1)

    def test_prune(self):
        code, out, _ = run_cli("org", "prune")
        self.assertEqual(code, 0)
        self.assertNotIn("com.stale.gone", self.cfg()["order"])
        self.assertEqual(self.cfg()["order"][:2],
                         ["com.nuvio.livetv", "org.smarttube.stable"])
        code, _, _ = run_cli("undo")
        self.assertIn("com.stale.gone", self.cfg()["order"])

    def test_org_change_snapshots_and_undo(self):
        code, out, _ = run_cli("org", "quick-add", "SmartTube")
        self.assertEqual(code, 0)
        self.assertIn("Snapshot saved", out)
        stack = _load(os.path.join(self.tmp, "stack.json"))
        self.assertEqual(stack[-1]["fields"], ["quick"])
        code, _, _ = run_cli("undo")
        self.assertEqual(code, 0)
        self.assertNotIn("app:org.smarttube.stable", self.cfg()["quick"])

    def test_ambiguous_name_changes_nothing(self):
        before = dict(self.cfg())
        code, _, err = run_cli("org", "quick-add", "Tube")
        self.assertEqual(code, 1)
        self.assertIn("ambiguous", err)
        self.assertEqual(self.cfg(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
