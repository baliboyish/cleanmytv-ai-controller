# cmtv — an AI-friendly controller for CleanMyTV Home

`cmtv` is a command-line controller for the **CleanMyTV Home** Android TV
launcher. It talks to the launcher's own `ConfigProvider` over ADB, so an
AI assistant (or a human in a shell) can safely change the launcher's
configuration, organise apps, manage wallpaper slideshows and day/night
looks, apply visual presets, and control the fireplace — with a snapshot
before every change and a working undo.

> **Third-party integration, not an official feature.** This project is
> an independent integration built on the launcher's public
> configuration interface. It is **not** affiliated with, endorsed by,
> or part of the CleanMyTV or CleanYourTV projects.
>
> Credits and thanks:
> - **CleanMyTV** by wingedpanther —
>   https://github.com/wingedpanther/cleanmytv (MIT)
> - **CleanYourTV** by Gathin23 —
>   https://github.com/Gathin23/CleanYourTV

## Supported versions

- CleanMyTV Home **v1.13.0** (package
  `io.github.wingedpanther.cleanmytv.home`). Other versions may work
  but are untested; the configuration schema is validated locally
  before anything is sent.

## Prerequisites

- Python 3.10+. The core CLI uses only the standard library.
  Image conversion (the wallpaper tool and slideshow uploads)
  needs **Pillow** — install it with
  `pip install -r requirements.txt`. Without it, everything else
  still works; the image commands stop with a clear message.
- `adb` (Android platform-tools) with the TV reachable:
  `adb connect <tv-ip>:5555`, or any tunnel/forward you prefer.
- CleanMyTV Home installed on the TV and set as the launcher.
- The launcher's configuration provider only accepts calls from the
  shell user, the system, or the app itself — which is exactly what
  `adb shell content call` provides.

## Install

```sh
git clone https://github.com/baliboyish/cleanmytv-ai-controller.git cmtv
cd cmtv
pip install -r requirements.txt        # Pillow, for image conversion
# point cmtv at your TV (defaults: adb from PATH, serial 127.0.0.1:15555)
export CMTV_SERIAL=192.168.1.10:5555   # your TV's adb address
python3 cmtv.py get                    # should print the live config
```

Environment variables:

| Variable | Meaning | Default |
|---|---|---|
| `CMTV_SERIAL` | adb target | `127.0.0.1:15555` |
| `CMTV_ADB` | adb binary | `adb` from PATH |
| `CMTV_STATE_DIR` | snapshots + undo stack | `~/.cmtv-state` |
| `CMTV_PRESETS_DIR` | saved presets | `~/.cmtv-presets` |
| `CMTV_ASSETS_DIR` | local slide archives | `~/.cmtv-assets` |
| `CMTV_MOCK=1` | run against the built-in mock TV (tests/dev) | off |

## Talking to it (natural language)

The CLI is designed to be driven by an AI assistant. Typical requests
and what runs underneath:

| You say | The assistant runs |
|---|---|
| "Make the launcher minimal." | `cmtv preset apply minimalist` |
| "Save this look as My Style." | `cmtv preset save "My Style"` |
| "Preview the Cinema preset." | `cmtv preset preview cinema` |
| "Go back to how it was." | `cmtv undo` |
| "Turn the fireplace on." | `cmtv fire enable` |
| "Crackle sound, low." | `cmtv fire settings --wall-sound on --volume 25` |
| "Fireplace for 30 minutes." | `cmtv fire settings --timer 30` |
| "Day look is the lake, night is Tokyo, 06:00–18:00." | wallpaper tool + `cmtv looks save-day` + `cmtv looks save-night` + `cmtv looks schedule --day 06:00 --night 18:00` |
| "Nature slideshow every 5 minutes." | `cmtv slides upload *.jpg` + `cmtv slides enable --minutes 5` |
| "Move SmartTube to the front." | `cmtv org move SmartTube --first` |

## Architecture

```
 you ──► AI assistant ──► cmtv.py ──► adb shell content call
                              │            │
                              │            ▼
                              │     ConfigProvider (on the TV)
                              │     io.github.…cleanmytv.home.config
                              ▼
              ~/.cmtv-state/snapshots/*.json   (before every change)
              ~/.cmtv-state/stack.json         (undo entries)
              ~/.cmtv-presets/*.json           (local presets)
              ~/.cmtv-assets/slides/<ts>/      (slide archives)
```

Design rules:

- **One safe path for config changes**: validate against the schema →
  snapshot the full config → record the undo entry → send only the
  changed fields (`set64`) → read back and verify. If the apply's
  reply is lost (timeout, disconnect), cmtv reads the config back
  once and lets the evidence decide: a confirmed failure removes
  the entry, a confirmed success keeps it, and anything else keeps
  it flagged **UNCONFIRMED** with a clear UNKNOWN status — the
  mutation is never retried blindly. State files (stack,
  snapshots, presets) are written atomically (temp file + fsync +
  `os.replace`), so a crash cannot tear them.
- **Undo detects conflicts**: if a field cmtv changed no longer holds
  the value cmtv set (TV remote, desktop app, a day/night crossing),
  `cmtv undo` refuses and explains instead of silently overwriting.
  `cmtv undo --force` overrides deliberately.
- **Assets are not config**: slides, look slots and theme packs live
  outside the config and are *not* covered by snapshots/undo. The CLI
  says so at every step (see *Security & rollback limitations*).
- **The PIN is never handled**: PIN fields are refused by the schema
  layer and never read, logged, or stored.

## CLI reference

```
cmtv get [--field F] [--raw]     show the live config
cmtv set FIELD VALUE …           change config fields (validated)
cmtv set --json '{…}'            same, as one JSON object
cmtv diff                        config vs the latest snapshot
cmtv verify                      schema-check + last change intact
cmtv undo [--force]              revert the last cmtv change
cmtv snapshots                   list snapshots and the undo stack

cmtv apps                        app catalogue + arrangement
cmtv org move APP [--to N | --first | --last]
cmtv org quick-add NAME…         add quick-bar app shortcuts
cmtv org quick-remove APP
cmtv org dock                    show the dock (first dock_size apps;
                                 resize via cmtv set dock_size N)
cmtv org dock-add APP            pull an app into the dock
cmtv org hide APP
cmtv org unhide APP
cmtv org folder-add FOLDER APP
cmtv org folder-remove FOLDER APP
cmtv org prune                   drop stale entries (uninstalled apps)

cmtv slides status
cmtv slides upload FILES… [--replace]
cmtv slides enable [--minutes N]
cmtv slides disable
cmtv looks status
cmtv looks save-day [--force]
cmtv looks save-night [--force]
cmtv looks schedule --day HH:MM --night HH:MM
cmtv looks disable

cmtv preset list
cmtv preset save NAME [--set F=V …]
cmtv preset preview NAME
cmtv preset apply NAME [--force]
cmtv preset delete NAME
cmtv theme status
cmtv theme list
cmtv theme save NAME

cmtv fire status
cmtv fire enable [--sound]
cmtv fire disable
cmtv fire settings [--sound on|off] [--wall-sound on|off]
                   [--volume 0-100] [--timer 0-240]
cmtv fire open                   EXPERIMENTAL: full-screen fireplace
                                 via focus-verified quick-bar
                                 navigation (no API exists)
```

## Security & rollback limitations

Read this before you rely on undo. The full audit is in
[docs/AUDIT.md](docs/AUDIT.md).

- Config changes (including presets, organiser, fire settings,
  day/night schedule fields) are snapshot-backed and undoable, with
  conflict detection on undo.
- **Slides**: an upload commits atomically (the old set survives a
  failed upload), but a *committed* replacement is permanent — the
  provider has no slide read-back. Every set is archived locally
  first (`~/.cmtv-assets/slides/<timestamp>/`); re-uploading an
  archive is the recovery path.
- **Look slots**: `look_save` overwrites a slot's stored look +
  wallpaper copy, which live outside the config. Config snapshots do
  **not** restore them. The CLI refuses to overwrite an occupied slot
  without `--force`.
- **Native themes**: packs are write-only (no read-back, no per-theme
  delete; only a wipe-everything `pack_clear` exists, which cmtv
  deliberately does not expose). `theme save` also has a provider
  side effect: it points `wallpaper_from` at the saved theme. That
  happens outside the snapshot system and is not undoable here.
- **Fireplace assets** are uploaded by the CleanMyTV desktop app;
  cmtv never uploads, overwrites, or clears them.
- State files contain your launcher configuration (which can include
  a weather place name and a greeting). They stay on your machine;
  the `.gitignore` keeps them out of this repo.

## Troubleshooting

- **`content call` fails / permission error** — the provider accepts
  only shell/system/app callers; always go through `adb shell`.
- **TV unreachable** — check `adb devices`; if you use a tunnel or
  port forward, make sure it is running and `CMTV_SERIAL` matches.
- **MD5 mismatch on upload** — chunk size matters: uploads use
  30 KiB raw chunks because larger ones can be truncated by the
  `content` CLI. cmtv already does this; a failed file is retried
  once from the beginning, and a failed slideshow upload never
  commits.
- **Undo refuses with a conflict** — a field was changed outside
  cmtv after cmtv's change. Read the message; use `--force` only if
  overwriting is intended.
- **Fireplace won't open full-screen** — there is no API for it;
  `cmtv fire open` navigates the quick bar and aborts unless focus
  is provably on the fireplace item. You can always open it with
  the TV remote instead.

## Development

```sh
CMTV_MOCK=1 python3 -m unittest discover -s tests   # 97 tests
```

The mock provider simulates the TV's Bundle responses, staging,
MD5 failures and side effects, so the whole command surface is
testable without hardware. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE). CleanMyTV itself is MIT-licensed by
its author; this project only talks to it over its configuration
interface and includes none of its code.
