# Security & behaviour audit — cmtv v0.1.0 RC

Audit date: 2026-11-19. Scope: every provider method `cmtv` calls,
every side effect it can cause, and the state it keeps locally.
Reference source: CleanMyTV Home v1.13.0 (`ConfigProvider.kt`,
`Config.kt`, `Slides.kt`, `DayNight.kt`, `ThemePack.kt`,
`Fireplace.kt`, `Upload.kt`, `HomeScreen.kt`) and the desktop app's
shared model (`home.ts`, `themes.ts`).

## 1. Operation catalog

| Provider method | Class | Used by | Side effects beyond the obvious | Rollback |
|---|---|---|---|---|
| `get` | read-only | all commands | none | n/a |
| `apps` | read-only | `apps`, `org *` | none | n/a |
| `set64` | config mutation | `set`, `org *`, `preset apply`, `looks schedule/disable`, `slides enable/disable`, `fire *` | The launcher may react to specific fields: changing `day_night`/`day_at`/`night_at` triggers `DayNight.reset` (the current half's look is re-applied within a minute). | Full: snapshot + undo, with conflict detection. |
| `slides_begin`, `slide_file_begin/chunk/end`, `slides_end` | asset mutation | `slides upload` | Files stage in `slides.upload/`; the live set is replaced only at `slides_end` (old directory deleted). A failed upload leaves the old set untouched. | Committed replacement is **permanent** (no `slide_read` exists). Mitigation: local archive of every set before upload + `--replace` gate. |
| `look_save` | asset mutation | `looks save-day/save-night` | Overwrites the slot's `look.json` + `wallpaper.jpg` in the app's private storage — outside the config. | **Not covered by config snapshots.** CLI gate: occupied slot needs `--force`. |
| `theme_save` | asset mutation **+ config side effect** | `theme save` | Stores the current look as a theme **and sets `wallpaper_from` to `<id>:0`** via a config update outside cmtv's snapshot system. Re-saving an existing name reuses its id and overwrites that theme's folder. Requires a wallpaper to exist. | Theme itself: **no per-theme delete** (only `pack_clear`, all themes — not exposed). The `wallpaper_from` change is visible in `cmtv get` and can be repointed by other operations, but is not on the undo stack. The CLI prints the side effect when it happens. |
| (wallpaper tool) `wallpaper_begin/chunk/end` | asset mutation | companion script `set_tv_wallpaper.py` | Replaces `wallpaper.jpg`; the provider resets `wallpaper_from` to the custom picture. | Re-upload a previous picture. |

Methods deliberately **never** called: `pack_begin/pack_meta/
pack_file_*/pack_end` (theme pack upload), `pack_clear`,
`fire_begin/fire_file_*/fire_end` (fireplace asset upload),
`fire_clear`, `slides_clear`, `lock_pin`, `lock_clear`, `keys`
(remote-button mapping), `keyboard` (on-screen keyboard), `set`
(legacy single-field path; `set64` covers it), and anything that
touches the lock PIN. PIN fields (`pin`, `lock_pin`) are refused by
the schema layer before any call.

Runtime/UI actions (not provider calls): `adb shell input keyevent`
and `screencap` for screenshots, and `uiautomator dump` for focus
verification in `fire open`. These change nothing persistent;
`fire open` never writes config and never edits the quick bar.

## 2. Findings and fixes (Phase 6)

1. **`theme_save` config side effect (found live, Phase 4).**
   `wallpaper_from` changed from `""` to `"my-my-style:0"` after a
   theme save. Root cause: `ThemePack.saveCurrent` always repoints
   it. Fix: `theme save` now captures and prints the before/after
   values, documents non-undoability, and accepts the provider's
   same-name overwrite semantics instead of misreporting them as a
   failure. The mock provider now reproduces the side effect and a
   regression test pins it.
2. **Undo could silently overwrite external changes.**
   If a field was changed on the TV (remote, desktop app, a
   day/night crossing) after cmtv's change, undo restored the old
   snapshot value without noticing. Fix: undo compares every touched
   field against the value cmtv recorded when applying; on mismatch
   it refuses with a per-field explanation. `--force` overrides.
   Fields cmtv never touched are not checked and are preserved.
3. **Undo-entry ordering window.** The stack entry used to be
   written *after* `set64`; a crash in between left an unlogged
   change. Fix: the entry is written before applying and popped if
   the apply fails (a test pins the no-entry-on-failure behaviour;
   the snapshot file of a failed apply remains as an orphan, which
   is harmless and visible via `cmtv snapshots`).
4. **Mock provider fidelity.** The mock returned a sparse config;
   the real provider always returns every field. Two bugs hid in
   that gap (an undo restore of a not-yet-present field, a missing
   `wallpaper_from`). The mock now mirrors the full field set.
5. **Fireplace navigation was positional folklore.** The working
   route pressed DPAD_RIGHT a fixed number of times. Fix:
   `cmtv fire open` resolves the step count from the *live* `quick`
   list, reads the focused item from a `uiautomator` dump before
   and after moving, and aborts before OK whenever focus cannot be
   proven. Live-verified 2026-11-19 on the reference TV (Sony X90J, CleanMyTV Home v1.13.0), including a run that started two items away from the fireplace; still labelled experimental for other devices. (First live attempt aborted safely: the focused node carries no label on this launcher, so the item is now identified geometrically from node bounds, with exact label matching — "Quick settings" is the panel, not settings.)
6. **Environment leakage / portability.** adb path and serial were
   hardcoded to one machine's layout. Both are now resolved from
   `CMTV_ADB` / `CMTV_SERIAL` with sane defaults, and state moved
   from a workspace directory to `~/.cmtv-state` (likewise presets
   and asset archives). A test scans the source for personal data
   (private-network addresses from the author's VPN range, names,
   absolute home paths).

## 3. Atomicity & failure behaviour

- `set64` carries all changed fields in one call; the launcher
  applies and persists them together. cmtv never sends partial
  multi-field updates.
- Uploads chunk at 30 KiB raw (larger chunks can be truncated by
  the `content` CLI, producing MD5 mismatches). Each file's MD5 is
  verified by the provider at `*_file_end`; a mismatch deletes the
  staging file. cmtv retries a failed file once from the beginning
  (the provider's staging wipe makes restarts clean), then fails
  the whole upload — a slideshow commit never happens with missing
  files, and the previous set stays live.
- `verify` re-reads the config after every apply and fails loudly
  if the launcher did not keep the intended values.

## 4. Local state & privacy

- Snapshots contain the full launcher config (which can include a
  weather place name and a greeting text). They are stored only
  under `~/.cmtv-state/` and are covered by the repo `.gitignore`.
- No credentials, PINs, tokens, or network addresses of the author's
  home setup appear in code, logs, or state. The default adb serial
  is a localhost forward (`127.0.0.1:15555`).
- Nothing in cmtv phones home; the only network activity is the adb
  connection to the user's own TV.

## 5. Phase 6.1 addendum (release-audit fixes, 2026-11-19)

An independent audit of the rc1 package (85/85 tests, NO-GO
pending fixes) produced three P1 and two P2 items; all are fixed
in rc2:

- **Dependency truth**: image conversion uses Pillow. It is now
  declared in `requirements.txt`, the README's claims are
  corrected, and both the wallpaper tool and slideshow uploads
  fail with a clear install message when Pillow is absent
  (verified in a fresh venv, with and without the dependency).
- **Atomic persistence**: stack, snapshots, presets and slide
  manifests go through a temp-file + fsync + `os.replace` write;
  a write-failure test proves the previous stack survives.
- **Ambiguous apply outcomes**: a lost `set64` reply is resolved
  by a single read-back (never a blind retry). Confirmed failure
  removes the undo entry; confirmed success keeps it; anything
  else keeps it flagged `unconfirmed`, and the command reports a
  clear UNKNOWN status with recovery instructions. `cmtv
  snapshots` marks such entries UNCONFIRMED. Note the interplay
  with undo conflict detection: undoing an unconfirmed entry
  whose change never landed will (safely) refuse as a conflict,
  because the live values differ from the recorded intended
  values — `--force` restores the snapshot values, which are the
  pre-change ones, so forcing is safe in that specific case.
- **Docs accuracy**: every command example in the README is
  parsed against the real argparse parser by a smoke test;
  several examples were corrected in the process (`org move`,
  `get`, `org dock`), and the clone URL is marked as a
  pre-publication placeholder.
- **Hygiene**: no bare `open()` JSON I/O remains in code or
  tests; the suite is clean under `-W error::ResourceWarning`.

## 6. Open limitations

- Slide sets, look slots and theme packs cannot be read back or
  selectively deleted through the provider; recovery relies on
  local archives (slides) or on not overwriting (looks, themes).
- `theme_save`'s `wallpaper_from` repointing is by design in the
  launcher and outside the undo system.
- Undo conflict detection compares values, not authorship: a
  day/night crossing that rewrites a look field with the *same*
  value is indistinguishable from no change (harmless), and one
  that changes it will (correctly) make a pending undo of that
  field refuse.
- `fire open` depends on `uiautomator` exposing the quick bar's
  focus with recognisable labels; where it cannot, the command
  aborts by design. The remote control always works.
- The fireplace *screensaver* selection is a system setting and is
  intentionally out of scope.
