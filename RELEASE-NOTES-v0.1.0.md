# cmtv v0.1.0 — Release Candidate 2

**RC2** applies the independent release-audit fixes: the Pillow
dependency is declared (`requirements.txt`) with clear
missing-dependency errors; state files are written atomically;
a lost `set64` reply is resolved by read-back instead of a blind
retry (UNKNOWN outcomes keep their undo entry, flagged
UNCONFIRMED); README examples are smoke-tested against the real
parser. 97 tests. See CHANGELOG.md for the full list.

---

# cmtv v0.1.0 — Release Candidate

**An AI-friendly controller for CleanMyTV Home (v1.13.0).**

cmtv lets an AI assistant — or a human in a shell — run a CleanMyTV
Home launcher safely over ADB: validated config changes with
snapshots and conflict-aware undo, app organisation, wallpaper
slideshows, day/night looks, visual presets, native theme saving,
and fireplace control.

## Highlights

- **Safety first**: every config change is schema-validated,
  snapshotted, and verified after applying; undo refuses to stomp
  changes made elsewhere (TV remote, desktop app) unless forced.
- **Honest asset semantics**: slides, look slots and theme packs
  live outside the config. The audit (docs/AUDIT.md) catalogues
  every provider method used, its side effects — including
  `theme_save` repointing `wallpaper_from` — and exactly what can
  and cannot be rolled back.
- **Almost no dependencies**: one Python file; the core runs on
  the standard library alone, and image conversion uses Pillow
  (declared in `requirements.txt`). The test suite runs against
  a mock TV.

## Requirements

Python 3.10+, Android platform-tools (`adb`), and a TV running
CleanMyTV Home v1.13.0 reachable over ADB.

## Notes

- This is a release candidate: the command surface is stable, but
  `cmtv fire open` (full-screen fireplace via focus-verified
  quick-bar navigation) is live-verified on the reference TV
  and aborts when focus cannot be proven.
- Third-party integration; not affiliated with the CleanMyTV or
  CleanYourTV projects. Thanks to wingedpanther and Gathin23.
