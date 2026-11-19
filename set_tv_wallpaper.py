#!/usr/bin/env python3
"""Set the CleanMyTV Home wallpaper on a TV over ADB.

Implements the official wallpaper protocol from wingedpanther/cleanmytv
(src/main/home.ts, setWallpaper() + pieces()):

  content call --uri content://io.github.wingedpanther.cleanmytv.home.config
      --method wallpaper_begin
      --method wallpaper_chunk --arg <base64 piece>   (repeated)
      --method wallpaper_end   --arg <md5 hex of the JPEG>

Pieces are raw-JPEG slices whose byte length is a multiple of 3, so base64
padding only appears in the final piece (per pieces()). The desktop app
uses 48 KiB pieces over its own transport; over the `adb shell` CLI a
48 KiB piece (64 KiB of base64 + the command wrapper) gets silently
truncated and wallpaper_end fails its MD5 check (verified 2026-11-19),
so this script uses 30 KiB pieces (~40 KiB base64 per call).
JPEG must be <= 4 MB with FF D8 magic; this script converts/resizes first.

Usage: set_tv_wallpaper.py <image-file> [--serial 127.0.0.1:15555]
Exit code 0 = wallpaper_end accepted (config returned, has_wallpaper=true
when the launcher reports it). The caller should still verify visually
with a Home screenshot.
"""
import argparse
import base64
import hashlib
import io
import os
import shutil
import subprocess
import sys


def _adb_bin():
    if os.environ.get("CMTV_ADB"):
        return os.environ["CMTV_ADB"]
    found = shutil.which("adb")
    if found:
        return found
    local = os.path.expanduser("~/workspace/tools/platform-tools/adb")
    return local if os.path.exists(local) else "adb"


ADB = _adb_bin()
URI = "content://io.github.wingedpanther.cleanmytv.home.config"
CHUNK = 30 * 1024  # raw bytes per piece, multiple of 3 (per pieces())
MAX_JPEG = 4 * 1024 * 1024


def to_jpeg(path: str) -> bytes:
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit(
            "Pillow is not installed — image conversion needs it. "
            "Install with: pip install -r requirements.txt "
            "(or: pip install Pillow)")

    im = Image.open(path).convert("RGB")
    # Cap at 3840x2160 (the TV's panel), keep aspect.
    im.thumbnail((3840, 2160), Image.LANCZOS)
    for quality in (90, 85, 80, 75, 70, 60):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= MAX_JPEG:
            return data
    raise SystemExit(f"image still over 4 MB after quality steps: {path}")


def call(serial: str, method: str, arg: str | None = None) -> str:
    cmd = [ADB, "-s", serial, "shell", "content", "call",
           "--uri", URI, "--method", method]
    if arg is not None:
        cmd += ["--arg", arg]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    text = (out.stdout or "") + (out.stderr or "")
    if "exception" in text.lower() or "Error while" in text or "unknown method" in text.lower():
        raise SystemExit(f"{method} failed: {text.strip()[:300]}")
    return text.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--serial",
                    default=os.environ.get("CMTV_SERIAL",
                                           "127.0.0.1:15555"))
    a = ap.parse_args()

    # Make sure the (Tailscale-forwarded) ADB endpoint is connected.
    subprocess.run([ADB, "connect", a.serial], capture_output=True, text=True, timeout=30)

    data = to_jpeg(a.image)
    assert data[0] == 0xFF and data[1] == 0xD8, "not a JPEG after conversion"
    md5 = hashlib.md5(data).hexdigest()
    pieces = [base64.b64encode(data[i:i + CHUNK]).decode()
              for i in range(0, len(data), CHUNK)]
    print(f"jpeg={len(data)} bytes md5={md5} pieces={len(pieces)}")

    call(a.serial, "wallpaper_begin")
    for i, piece in enumerate(pieces, 1):
        call(a.serial, "wallpaper_chunk", piece)
        print(f"chunk {i}/{len(pieces)} ok")
    result = call(a.serial, "wallpaper_end", md5)
    print("wallpaper_end:", result[:300])
    if "has_wallpaper=true" in result:
        print("VERIFIED: launcher reports has_wallpaper=true")
    print("DONE")


if __name__ == "__main__":
    main()
