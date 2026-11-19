# Changelog

## v0.1.0-rc2 — Release Audit Fixes (2026-11-19)

Fixes from the independent release audit of rc1 (85/85 tests, NO-GO
pending these items):

- **Pillow dependency declared**: `requirements.txt` added; the
  README no longer claims the whole project is standard-library
  only. The wallpaper tool and slideshow uploads now stop with a
  clear install message when Pillow is missing. Verified in a
  fresh virtual environment.
- **Atomic persistence**: the undo stack, snapshots, presets and
  slide-archive manifests are written via temp file + fsync +
  `os.replace`; a failed write provably leaves the previous file
  intact (regression tests included).
- **Ambiguous apply outcomes**: when `set64`'s reply is lost
  (timeout/disconnect) the change is never retried blindly. cmtv
  reads the config back once: confirmed-not-applied removes the
  undo entry, confirmed-applied keeps it, and anything else keeps
  it flagged UNCONFIRMED with a clear UNKNOWN status for manual
  recovery.
- **README/CLI accuracy**: clone URL marked as a pre-publication
  placeholder; `org move` and `get` examples corrected to the real
  parser; the CLI reference lists one command per line; a smoke
  test now parses every README example against the real argparse
  parser. `fire open` is labelled EXPERIMENTAL.
- **Test hygiene**: all bare `open()` JSON reads/writes in code
  and tests replaced with context managers / atomic helpers; the
  suite runs clean under `-W error::ResourceWarning`.
- 97 tests total (12 new).

## v0.1.0 — Release Candidate (2026-11-19)

First public release candidate. Developed and live-verified against
CleanMyTV Home v1.13.0 on a Sony X90J.

### Added
- **Core (Phase 1)** — schema-validated `get/set/diff/verify/undo`
  with full-config snapshots before every change.
- **App organiser (Phase 2)** — `apps` and `org move/quick-add/
  quick-remove/dock/dock-add/hide/unhide/folder-add/folder-remove/
  prune`, with name resolution against the TV's app catalogue
  (packages are never guessed).
- **Slideshow & day/night looks (Phase 3)** — chunked, MD5-verified
  slide uploads with local archiving; look slots with an overwrite
  gate; day/night scheduling in TV-local time.
- **Preset engine & native themes (Phase 4)** — local visual
  presets (the launcher's own `THEME_KEYS` + `tile_glass`) with
  dry-run preview and a day/night apply guard; built-ins
  *minimalist, cinema, midnight, nature*; native theme status/list
  and `theme save` with its provider side effect surfaced.
- **Fireplace & ambient (Phase 5)** — `fire status/enable/disable/
  settings` over the existing fire assets; documented full-screen
  behaviour and timer semantics.
- **Hardening (Phase 6)** — undo conflict detection, undo-entry
  ordering fix, environment-based adb/serial resolution,
  focus-verified `fire open` (experimental), full security audit
  (docs/AUDIT.md).

### Known limitations
- Asset rollback limits for slides, look slots and theme packs —
  see README § *Security & rollback limitations*.
- `fire open` is experimental and aborts when focus cannot be
  proven via `uiautomator`.
