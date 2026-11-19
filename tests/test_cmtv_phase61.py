#!/usr/bin/env python3
"""Unit tests for cmtv Phase 6.1 (release audit fixes) — mock only.

Covers: atomic persistence, ambiguous apply outcomes (read-back
disambiguation, UNKNOWN status, undo entry retention), the Pillow
dependency gate, README examples vs the real argparse parser, and
file-handle hygiene (ResourceWarning).
"""
import gc
import importlib.util
import io
import json
import os
import re
import shlex
import tempfile
import unittest
import warnings
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

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


class TestAtomicWrite(Base):
    def test_failed_replace_keeps_old_stack(self):
        path = os.path.join(self.tmp, "stack.json")
        _dump(path, [{"sentinel": 1}])
        with mock.patch.object(os, "replace",
                               side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                cmtv.save_stack([{"new": 2}])
        self.assertEqual(_load(path), [{"sentinel": 1}])
        leftovers = [f for f in os.listdir(self.tmp)
                     if f.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_save_stack_roundtrip(self):
        cmtv.save_stack([{"a": 1}])
        self.assertEqual(cmtv.load_stack(), [{"a": 1}])

    def test_failed_snapshot_write_leaves_no_partial_file(self):
        snap_dir = os.path.join(self.tmp, "snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        before = set(os.listdir(snap_dir))
        with mock.patch.object(os, "replace",
                               side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                cmtv.take_snapshot({"accent": "#000000"}, "test")
        self.assertEqual(set(os.listdir(snap_dir)), before)


class FlakyAdb:
    """Wraps the mock: simulates transport failures around set64."""

    def __init__(self, inner, mode):
        self.inner, self.mode, self.applied = inner, mode, False

    def content_call(self, method, arg=None):
        if method == "set64":
            if self.mode == "raise_before":
                raise cmtv.CmtvError("injected: lost before send")
            res = self.inner.content_call(method, arg)
            self.applied = True
            if self.mode in ("raise_after", "raise_after_noread"):
                raise cmtv.CmtvError("injected: reply lost")
            return res
        if (method == "get" and self.mode == "raise_after_noread"
                and self.applied):
            raise cmtv.CmtvError("injected: read-back unreachable")
        return self.inner.content_call(method, arg)


class TestAmbiguousApply(Base):
    def _ctx(self, mode):
        ctx = cmtv.Ctx(True)
        ctx.adb = FlakyAdb(ctx.adb, mode)
        return ctx

    def _apply(self, ctx):
        out = io.StringIO()
        with redirect_stdout(out):
            return cmtv.apply_change(ctx, {"accent": "#111111"},
                                     "test"), out.getvalue()

    def test_confirmed_failure_pops_entry(self):
        ctx = self._ctx("raise_before")
        with self.assertRaises(cmtv.CmtvError) as cm:
            self._apply(ctx)
        self.assertIn("nothing was applied", str(cm.exception))
        self.assertEqual(cmtv.load_stack(), [])
        self.assertEqual(ctx.get_config()["accent"], "#FFB23C")

    def test_applied_despite_error_keeps_entry(self):
        ctx = self._ctx("raise_after")
        eff, out = self._apply(ctx)
        self.assertEqual(eff, {"accent": "#111111"})
        self.assertIn("WAS applied", out)
        self.assertEqual(len(cmtv.load_stack()), 1)
        self.assertEqual(ctx.get_config()["accent"], "#111111")

    def test_unknown_outcome_keeps_flagged_entry(self):
        ctx = self._ctx("raise_after_noread")
        with self.assertRaises(cmtv.CmtvError) as cm:
            self._apply(ctx)
        self.assertIn("UNKNOWN", str(cm.exception))
        stack = cmtv.load_stack()
        self.assertEqual(len(stack), 1)
        self.assertTrue(stack[-1].get("unconfirmed"))


class TestPillowGate(Base):
    def test_require_pillow_message(self):
        real = importlib.util.find_spec
        with mock.patch.object(
                importlib.util, "find_spec",
                side_effect=lambda n, *a, **k:
                    None if n == "PIL" else real(n, *a, **k)):
            with self.assertRaises(cmtv.CmtvError) as cm:
                cmtv._require_pillow()
        self.assertIn("Pillow", str(cm.exception))
        self.assertIn("pip install", str(cm.exception))

    def test_wallpaper_tool_message(self):
        wtw = cmtv._load_wallpaper_tool()
        with mock.patch.dict(os.sys.modules, {"PIL": None}):
            with self.assertRaises(SystemExit) as cm:
                wtw.to_jpeg(__file__)
        self.assertIn("Pillow", str(cm.exception))
        self.assertIn("pip install", str(cm.exception))

    def test_slides_upload_blocked_without_pillow(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed in this environment")
        img = os.path.join(self.tmp, "one.jpg")
        Image.new("RGB", (64, 48), (10, 20, 30)).save(img)
        real = importlib.util.find_spec
        with mock.patch.object(
                importlib.util, "find_spec",
                side_effect=lambda n, *a, **k:
                    None if n == "PIL" else real(n, *a, **k)):
            code, _, err = run_cli("slides", "upload", img)
        self.assertEqual(code, 1)
        self.assertIn("Pillow", err)


def _readme_path():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "README.md"),
                 os.path.join(here, "..", "..", "cmtv-release",
                              "README.md")):
        if os.path.exists(cand):
            return cand
    raise AssertionError("README.md not found")


def _readme_commands(text):
    cmds, in_fence = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            s = line.strip()
            if s.startswith("cmtv "):
                cmds.append(re.split(r"\s{2,}", s)[0])
        for span in re.findall(r"`([^`]+)`", line):
            if span.startswith("cmtv "):
                cmds.append(span)
    return cmds


def _sanitize(cmd):
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return None
    out = []
    int_flags = {"--to", "--minutes", "--volume", "--timer"}
    for i, raw in enumerate(toks):
        tok = raw.strip("[]")
        if tok in ("…", "..."):
            break
        if tok == "|":
            continue
        if tok.endswith("…"):
            out.append("x.jpg")
        elif tok == "HH:MM":
            out.append("06:00")
        elif re.fullmatch(r"\d+-\d+", tok):
            out.append("25")
        elif tok.startswith("{"):
            out.append("{}")
        elif "|" in tok and not tok.startswith("--"):
            out.append(tok.split("|")[0])
        elif re.fullmatch(r"[A-Z][A-Z0-9_]*", tok):
            prev = toks[i - 1].strip("[]") if i else ""
            out.append("5" if prev in int_flags else "x")
        else:
            out.append(tok)
    return out


class TestReadmeExamples(unittest.TestCase):
    def test_all_readme_examples_parse(self):
        cmds = _readme_commands(_read(_readme_path()))
        self.assertGreaterEqual(len(cmds), 20,
                                f"too few examples found: {cmds}")
        parser = cmtv.build_parser()
        for cmd in cmds:
            toks = _sanitize(cmd)
            self.assertIsNotNone(toks, cmd)
            self.assertEqual(toks[0], "cmtv")
            try:
                with redirect_stderr(io.StringIO()):
                    parser.parse_args(toks[1:])
            except SystemExit:
                self.fail(f"README example does not parse: "
                          f"{cmd!r} -> {toks}")


class TestHygiene(unittest.TestCase):
    def test_no_bare_json_open_patterns(self):
        src = _read(cmtv.__file__)
        self.assertNotIn("json.load(open(", src)
        self.assertIsNone(
            re.search(r"json\.dump\([^)]*open\(", src))

    def test_no_resource_warnings_in_set_undo_cycle(self):
        tmp = tempfile.mkdtemp()
        os.environ["CMTV_STATE_DIR"] = tmp
        os.environ["CMTV_MOCK"] = "1"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", ResourceWarning)
                run_cli("get")
                run_cli("set", "accent", "#123456")
                run_cli("undo")
                gc.collect()
        finally:
            for k in ("CMTV_STATE_DIR", "CMTV_MOCK"):
                os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
