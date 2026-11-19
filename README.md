# cmtv — CleanMyTV AI Controller

# 📱 Your Phone. 🤖 Your AI. 📺 Your TV.

**Control your Android TV launcher with natural language — powered by AI and ADB.**

`cmtv` is a command-line controller for the **CleanMyTV Home** Android TV
launcher. It gives an AI agent — or a human in a shell — a safe set of
commands for running your launcher: changing its configuration,
organising apps, managing wallpaper slideshows and day/night looks,
applying visual presets, and controlling the fireplace. Every change is
validated, snapshotted, and undoable.

*Independent third-party project — not affiliated with the CleanMyTV or
CleanYourTV projects. Full credits at the bottom of this page.*

---

## Your Phone. Your AI. Your TV.

Most TV launchers are configured the slow way: remote in hand, menu
after menu, one setting at a time.

`cmtv` takes the other route. The CleanMyTV Home launcher exposes its
own configuration interface — so instead of clicking through menus,
you simply **tell an AI assistant what you want**, and the assistant
changes the launcher for you.

> **YOU:** "Hey AI, make my TV launcher minimal and move SmartTube to
> the front."
>
> **AI AGENT:**
> - Interprets the request
> - Runs `cmtv preset apply minimalist`
> - Runs `cmtv org move SmartTube --first`
> - Verifies the result through ADB
>
> **TV:** Launcher configuration updated. ✔

No remote gymnastics. No settings spelunking. You describe the result;
the agent does the clicking.

---

## Turn Your AI Assistant Into an Android TV Controller

The flow is simple:

```text
User ──► Natural Language ──► AI Agent ──► cmtv ──► ADB ──► Android TV
 (you)     "make it minimal"   (with shell    (this     (the      (CleanMyTV
                                access)      project)  launcher)  Home)
```

Any AI agent that can execute shell commands and reach your TV over
ADB can drive `cmtv`. Things you can ask for — and the real commands
that run underneath:

| You say | The agent runs |
| --- | --- |
| "Make my TV launcher look clean and minimal." | `cmtv preset apply minimalist` |
| "Move SmartTube to the first position." | `cmtv org move SmartTube --first` |
| "Set up a relaxing night theme." | `cmtv preset apply midnight` |
| "Start a nature wallpaper slideshow." | `cmtv slides upload *.jpg` + `cmtv slides enable --minutes 5` |
| "Turn on the fireplace with soft crackling sounds." | `cmtv fire enable` + `cmtv fire settings --wall-sound on --volume 25` |
| "Undo the last launcher configuration change." | `cmtv undo` |

Every one of these commands exists today and is covered by the test
suite — see the full [CLI reference](#cli-reference) below.

---

## What Can You Do?

- **Personalised launcher layouts** — arrange apps, build the dock,
  manage the quick bar and folders, hide what you never use.
- **Day/night appearance management** — one look for the morning, a
  different one after dark, switched automatically on a schedule.
- **App organisation without the remote** — reorder, group, and prune
  stale entries in seconds instead of dozens of remote clicks.
- **Wallpaper slideshow management** — upload a set of pictures and
  rotate them on an interval.
- **Fireplace ambience control** — the launcher's fire video as your
  Home wallpaper, with optional crackle sound and volume, plus a
  full-screen fireplace mode.
- **Safer experimentation** — every configuration change is
  snapshotted first and can be undone; undo even refuses to overwrite
  changes you made yourself with the remote (see
  [Safety and limitations](#safety-and-limitations)).

**Potential workflows** (possible with the right agent setup; not
built into `cmtv` itself):

- **Voice-to-TV automation** — talk to a voice-capable AI agent that
  has tool execution, and it runs the same commands hands-free.
- **Routines** — an agent that supports scheduling could apply a
  "movie night" preset every Friday, or switch looks for special
  occasions, using the same commands.

---

## Control Your TV Using Just Your Phone

Here is the part people don't expect: **a Windows PC is not required.**

`cmtv` needs exactly three things at runtime — Python, an `adb`
binary, and network reachability to the TV. Where those run is up to
you:

- **On an Android phone** — Termux provides Python and ADB on the
  phone itself. Configure it once, and your phone becomes the
  controller: open your AI assistant or terminal and just ask.
- **On a small always-on computer or home server** — the agent runs
  there; you talk to it from your phone.
- **On a remote machine** — any environment with ADB access to the TV
  works, as long as the agent can execute commands in it.

The scenario: you carry only your phone. You open the AI assistant
you already chat with, type *"turn on the fireplace and switch to
the night look"*, and your TV at home obeys.

One honest distinction: a **chat-only AI cannot do this**. The AI
must be an *agent* — one with shell/terminal tool execution and a
path to ADB. Plain chatbots that only produce text have nothing to
run the commands with.

### A real-world setup: an AI agent + Tailscale

`cmtv` was developed and is used daily with a setup worth describing,
because it shows what is possible with off-the-shelf pieces:

- The user talks to a personal AI agent from a **chat app on their
  phone** — the same conversation they use for everything else.
- The agent runs on a small **always-on computer** and executes
  `cmtv` commands on the user's behalf.
- **Tailscale** (a WireGuard-based mesh network) links the agent's
  machine and the Android TV in one private network — no router
  port-forwarding, no exposed ADB port on the LAN or internet.
- ADB travels over that private connection, and `cmtv` talks to the
  launcher through it.
- Because the agent is reachable from the phone, launcher changes
  can be requested **from the couch — or from outside the house**.

This is a usage example, not an official integration with any AI
platform: the same pattern works with any agent that has command
execution, on any machine that can reach the TV.

---

## Quick Start

### Supported versions

- CleanMyTV Home **v1.13.0** (package
  `io.github.wingedpanther.cleanmytv.home`). Other versions may work
  but are untested; the configuration schema is validated locally
  before anything is sent.

### Prerequisites

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

### Install

```bash
git clone https://github.com/baliboyish/cleanmytv-ai-controller.git cmtv
cd cmtv
pip install -r requirements.txt        # Pillow, for image conversion

# point cmtv at your TV (defaults: adb from PATH, serial 127.0.0.1:15555)
export CMTV_SERIAL=192.168.1.10:5555   # your TV's adb address
python3 cmtv.py get                    # should print the live config
```

---

## Supported AI Agent Integrations

`cmtv` is deliberately agent-agnostic. It works with **any AI agent
that can run shell commands** in an environment where Python and ADB
can reach the TV — for example:

- AI coding/personal agents with terminal access (the category that
  includes agents like Muse, and similar tool-using assistants)
- Local scripts or automations that call the CLI directly
- You, in a terminal — every command is human-usable as-is

There is **no official integration** with any specific AI platform,
and `cmtv` needs none: natural language goes in at the agent, plain
CLI commands come out, and the CLI's output is structured so the
agent can verify what actually happened before reporting back.

---

## Safety and Limitations

Letting an AI rearrange your TV only feels safe if the tooling is
conservative. `cmtv` is built around that:

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
  says so at every step (see below).
- **The PIN is never handled**: PIN fields are refused by the schema
  layer and never read, logged, or stored.

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

---

## Technical Documentation

### Architecture

```text
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

### CLI reference

```text
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

### Environment variables

| Variable | Meaning | Default |
| --- | --- | --- |
| `CMTV_SERIAL` | adb target | `127.0.0.1:15555` |
| `CMTV_ADB` | adb binary | `adb` from PATH |
| `CMTV_STATE_DIR` | snapshots + undo stack | `~/.cmtv-state` |
| `CMTV_PRESETS_DIR` | saved presets | `~/.cmtv-presets` |
| `CMTV_ASSETS_DIR` | local slide archives | `~/.cmtv-assets` |
| `CMTV_MOCK=1` | run against the built-in mock TV (tests/dev) | off |

### Troubleshooting

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

### Development

```bash
CMTV_MOCK=1 python3 -m unittest discover -s tests   # 97 tests
```

The mock provider simulates the TV's Bundle responses, staging,
MD5 failures and side effects, so the whole command surface is
testable without hardware. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Credits and Attribution

> **Third-party integration, not an official feature.** This project is
> an independent integration built on the launcher's public
> configuration interface. It is **not** affiliated with, endorsed by,
> or part of the CleanMyTV or CleanYourTV projects.
>
> Credits and thanks:
>
> - **CleanMyTV** by wingedpanther —
>   https://github.com/wingedpanther/cleanmytv (MIT)
> - **CleanYourTV** by Gathin23 —
>   https://github.com/Gathin23/CleanYourTV

## License

MIT — see [LICENSE](LICENSE). CleanMyTV itself is MIT-licensed by
its author; this project only talks to it over its configuration
interface and includes none of its code.
