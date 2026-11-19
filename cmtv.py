#!/usr/bin/env python3
"""cmtv — Universal CleanMyTV Home Launcher Controller (Phase 1).

Talks to the launcher's ConfigProvider over the already-paired ADB bridge:

    content://io.github.wingedpanther.cleanmytv.home.config
      get                 read the whole config (+ status fields)
      set64 <b64 JSON>    merge a partial config change

Commands:
    cmtv get [--field F] [--raw]     read config (schema fields; --raw = all)
    cmtv set F V [F V ...]           validated, selective change (snapshot first)
    cmtv set --json '{...}'          same, as one JSON object
    cmtv diff [--snapshot FILE]      current config vs latest snapshot
    cmtv verify                      config parses; last change still in effect
    cmtv undo                        revert the fields of the most recent set
    cmtv snapshots                   list snapshots / undo stack
    cmtv apps [--json]               the TV app catalog + where each app sits
    cmtv org move NAME [--to N|--first|--last]   reorder (arranged order)
    cmtv org quick-add NAME...       add app shortcuts to the quick bar
    cmtv org quick-remove NAME       remove a quick-bar entry
    cmtv org dock                    show the dock (first dock_size arranged)
    cmtv org dock-add NAME           promote an app into the dock (to front)
    cmtv org hide|unhide NAME        manage the hidden list (explicit only)
    cmtv org folder-add|folder-remove FOLDER NAME
    cmtv org prune                   drop entries for uninstalled apps
    cmtv slides status               slideshow count, interval, source
    cmtv slides upload FILES... [--replace]
                                     upload a new slideshow set (<=12 JPEGs,
                                     staged; the old set stays live until the
                                     commit, then is NOT recoverable via the
                                     API — sets are archived locally first)
    cmtv slides enable [--minutes N] rotate the uploaded set (default 5)
    cmtv slides disable              stop rotating (files stay on the TV)
    cmtv looks status                day/night schedule + saved slots
    cmtv looks save-day|save-night [--force]
                                     save the current look into a slot
                                     (an occupied slot is overwritten only
                                     with --force; the old look is then lost)
    cmtv looks schedule --day HH:MM --night HH:MM
    cmtv looks disable               stop the automatic day/night switching
    cmtv preset list                 Muse's local visual presets
    cmtv preset save NAME [--set F=V ...]
                                     capture the current visual fields
                                     (the launcher's THEME_KEYS + tile_glass)
    cmtv preset preview NAME         dry-run: field old -> new, no change
    cmtv preset apply NAME [--force] apply a preset (snapshot + undo;
                                     --force needed while day/night is on)
    cmtv preset delete NAME          delete a saved preset (not built-ins)
    cmtv theme status|list          native CleanMyTV themes on the TV
    cmtv theme save NAME             save the current look as a native
                                     theme on the TV (additive; there is
                                     no per-theme delete in the provider)
    cmtv fire status                 fireplace state (asset + settings)
    cmtv fire enable [--sound]       fire video as the Home wallpaper
    cmtv fire disable                back to the picture wallpaper
    cmtv fire settings [--sound on|off] [--wall-sound on|off]
                       [--volume 0-100] [--timer 0-240]
                                     view or change fireplace settings
                                     (the full-screen fireplace has no
                                     API trigger: use the quick bar's
                                     fireplace item on the TV itself)

App names resolve against the provider's `apps` catalog (exact package,
exact label, or unique substring). Ambiguity is an error, never a guess.
There is no `dock` config field: the dock is the first `dock_size`
visible apps of the arranged order (order-prefix, then alphabetical).

Safety rules (Phase 1 brief):
  * Field names, types and allowed values are validated against the schema
    derived from the official source: src/main/home.ts (DEFAULTS, CHOICES,
    RANGES, BOOLS, HEX, THEME_PICTURE, INPUT_ID, POWER_ON_WAITS) and the
    launcher's Config.kt field list. Unknown or protected fields are refused.
  * A full-config snapshot is saved before every change; a change record
    (which fields, from which snapshot) goes on the undo stack.
  * set64 only ever receives the fields being changed — never the whole
    config. Undo restores exactly the fields the last change touched.
  * The app-lock PIN is never a config field and is never accepted, stored
    or printed here. (lock_pin is a separate provider method, out of scope.)

State:  ~/.cmtv-state/ (snapshots/, stack.json; CMTV_STATE_DIR)
        override with CMTV_STATE_DIR. Mock TV: CMTV_MOCK=1 (or --mock),
        persisted at <state>/mock-tv.json so tests need no TV.

Schema source: wingedpanther/cleanmytv, cloned 2026-11-19; launcher v1.13.0.
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time


def _atomic_write_text(path, text):
    """Write a file atomically: temp file in the same directory,
    flush + fsync, then os.replace. A crash or a failing write can
    never leave a torn file behind — the old content survives."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_json(path, obj):
    _atomic_write_text(path, json.dumps(obj, indent=1,
                                        ensure_ascii=False))


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
from datetime import datetime

def _adb_bin():
    """adb resolution: CMTV_ADB env, then PATH, then a local
    platform-tools checkout, then a bare 'adb' (fails with a clear
    message from subprocess if truly absent)."""
    if os.environ.get("CMTV_ADB"):
        return os.environ["CMTV_ADB"]
    found = shutil.which("adb")
    if found:
        return found
    local = os.path.expanduser("~/workspace/tools/platform-tools/adb")
    return local if os.path.exists(local) else "adb"


def _serial():
    """The adb target. Default is a localhost forward to the TV;
    override with CMTV_SERIAL (e.g. 192.168.1.10:5555)."""
    return os.environ.get("CMTV_SERIAL", "127.0.0.1:15555")


ADB = _adb_bin()
SERIAL = _serial()
URI = "content://io.github.wingedpanther.cleanmytv.home.config"

# ---------------------------------------------------------------- schema ---
# From src/main/home.ts (wingedpanther/cleanmytv), verified 2026-11-19.
BOOLS = {
    "show_names", "name_focus", "follow_colour", "pin_focus", "tilt", "intro",
    "lock_on", "lock_system", "fire_wall", "fire_sound", "fire_wall_sound",
    "other_apps", "live_tv", "visualiser", "viz_peaks", "buttons_on",
    "memory", "day_night", "power_on_input", "edit_on_tv", "bing_daily",
    "bing_caption", "theme_memory", "lock_editor", "update_check",
    "keyboard_guard", "snow", "weather_hours",
}
CHOICES = {
    "layout": ["shelf", "split", "rail"],
    "focus": ["lift", "glow", "outline", "none"],
    "clock": ["time_date", "time", "off"],
    "viz_style": ["bars", "mirror", "wave"],
    "viz_colour": ["accent", "rainbow", "white"],
    "weather_unit": ["c", "f"],
    "icon_style": ["banner", "colour", "mono", "duo"],
    "tile_shape": ["wide", "square", "squircle", "round"],
    "lock_for": ["sleep", "15", "always"],
    "lock_show": ["badge", "hide"],
    "slide_from": ["mine", "theme"],
    "idle_style": ["corner", "center", "photos"],
    "font": ["default", "condensed", "serif", "casual"],
}
RANGES = {  # inclusive [min, max], integers
    "top_space": (0, 40), "dock_scale": (60, 150), "tile_scale": (60, 150),
    "wallpaper_dim": (0, 80), "dock_size": (0, 8), "columns": (3, 8),
    "idle_minutes": (0, 60), "viz_bars": (8, 64), "viz_height": (4, 40),
    "viz_opacity": (20, 100), "viz_sensitivity": (25, 300),
    "fire_volume": (0, 100), "fire_timer": (0, 240), "slide_minutes": (0, 240),
    "tile_glass": (0, 60), "day_at": (0, 24 * 60 - 1),
    "night_at": (0, 24 * 60 - 1), "shade_top": (0, 100),
    "shade_top_reach": (0, 100), "shade_bottom": (0, 100),
    "shade_bottom_reach": (0, 100), "wash": (50, 100), "wash_reach": (10, 100),
}
COLORS = {"accent", "background"}
SECTION_NAMES = ["nowplaying", "recent", "dock", "watchnext", "rows",
                 "folders", "grid"]
QUICK_CHOICES = ["settings", "wifi", "inputs", "bluetooth", "display",
                 "apps", "sleep", "search", "music", "panel", "themes",
                 "fireplace", "memory", "customize", "timer"]
MAX_QUICK_APPS = 6
PKG_RE = re.compile(r"^[A-Za-z][\w]*(\.[\w]+)+$")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
THEME_PICTURE_RE = re.compile(r"^[a-z0-9-]{1,40}:[0-9]$")
INPUT_ID_RE = re.compile(r"^[A-Za-z0-9_.$/:-]{1,200}$")
POWER_ON_WAITS = (0, 3, 5, 10)
STRING_FIELDS = {"greeting": 200, "weather_place": 200}
NUMBER_FIELDS = {"weather_lat": (-90, 90), "weather_lon": (-180, 180)}
PKG_LIST_FIELDS = {"order", "hidden", "lock_apps"}
OBJECT_FIELDS = {"names", "limits"}
PROTECTED = {"stock_redirect"}          # set by the desktop app only
STATUS_FIELDS = {                        # returned by the provider, not config
    "has_wallpaper", "configured", "quick_available", "tv_inputs",
    "lock_pin_set", "themes_on_tv", "fire_on_tv", "slides_on_tv",
    "can_change_settings", "looks_saved", "sections_known",
}
SCHEMA_FIELDS = (
    BOOLS | set(CHOICES) | set(RANGES) | COLORS | set(STRING_FIELDS) |
    set(NUMBER_FIELDS) | PKG_LIST_FIELDS | OBJECT_FIELDS |
    {"sections", "quick", "rows", "folders", "buttons", "power_on_wait",
     "power_on_target", "wallpaper_from"}
)


class CmtvError(Exception):
    pass


def validate_field(field, value):
    """Return the normalized value, or raise CmtvError. Schema per home.ts."""
    if field in PROTECTED:
        raise CmtvError(f"{field}: protected (set by the desktop app only)")
    if field in STATUS_FIELDS:
        raise CmtvError(f"{field}: status field reported by the launcher, "
                        "not a setting")
    if field in ("pin", "lock_pin"):
        raise CmtvError(f"{field}: the app-lock PIN is never handled here "
                        "(it is a separate provider method, out of scope)")
    if field not in SCHEMA_FIELDS:
        raise CmtvError(f"{field}: unknown field (not in the launcher schema)")

    if field in BOOLS:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "1", "on", "yes"):
                value = True
            elif v in ("false", "0", "off", "no"):
                value = False
        if not isinstance(value, bool):
            raise CmtvError(f"{field}: must be true/false, got {value!r}")
        return value
    if field in CHOICES:
        if value not in CHOICES[field]:
            raise CmtvError(f"{field}: must be one of {CHOICES[field]}, "
                            f"got {value!r}")
        return value
    if field in RANGES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CmtvError(f"{field}: must be a number, got {value!r}")
        if int(value) != value:
            raise CmtvError(f"{field}: must be a whole number, got {value!r}")
        lo, hi = RANGES[field]
        if not lo <= value <= hi:
            raise CmtvError(f"{field}: must be {lo}..{hi}, got {value}")
        return int(value)
    if field in COLORS:
        if not isinstance(value, str) or not HEX_RE.match(value):
            raise CmtvError(f"{field}: must be a #RRGGBB colour, got {value!r}")
        return value.upper()
    if field in STRING_FIELDS:
        if not isinstance(value, str) or len(value) > STRING_FIELDS[field]:
            raise CmtvError(f"{field}: must be a string of at most "
                            f"{STRING_FIELDS[field]} chars")
        return value
    if field in NUMBER_FIELDS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CmtvError(f"{field}: must be a number, got {value!r}")
        lo, hi = NUMBER_FIELDS[field]
        if not lo <= value <= hi:
            raise CmtvError(f"{field}: must be {lo}..{hi}, got {value}")
        return value
    if field == "power_on_wait":
        if value not in POWER_ON_WAITS:
            raise CmtvError(f"power_on_wait: must be one of "
                            f"{list(POWER_ON_WAITS)}, got {value!r}")
        return value
    if field == "power_on_target":
        if value != "" and not (isinstance(value, str)
                                and INPUT_ID_RE.match(value)):
            raise CmtvError("power_on_target: must be '' or a TV input id")
        return value
    if field == "wallpaper_from":
        if value != "" and not (isinstance(value, str)
                                and THEME_PICTURE_RE.match(value)):
            raise CmtvError("wallpaper_from: must be '' or '<theme>:<0-9>'")
        return value
    if field == "sections":
        if not isinstance(value, list) or not value:
            raise CmtvError("sections: must be a non-empty list")
        bad = [s for s in value if s not in SECTION_NAMES]
        if bad:
            raise CmtvError(f"sections: unknown sections {bad}; "
                            f"allowed {SECTION_NAMES}")
        if len(set(value)) != len(value):
            raise CmtvError("sections: duplicates not allowed")
        return value
    if field == "quick":
        if not isinstance(value, list):
            raise CmtvError("quick: must be a list")
        apps = 0
        for q in value:
            if isinstance(q, str) and q.startswith("app:"):
                if not PKG_RE.match(q[4:]):
                    raise CmtvError(f"quick: bad app package in {q!r}")
                apps += 1
            elif q not in QUICK_CHOICES:
                raise CmtvError(f"quick: {q!r} is not one of {QUICK_CHOICES} "
                                "and not app:<package>")
        if apps > MAX_QUICK_APPS:
            raise CmtvError(f"quick: at most {MAX_QUICK_APPS} app shortcuts")
        return value
    if field in PKG_LIST_FIELDS:
        if not isinstance(value, list) or \
                any(not isinstance(p, str) or not PKG_RE.match(p)
                    for p in value):
            raise CmtvError(f"{field}: must be a list of package names")
        return value
    if field in ("rows", "folders"):
        if not isinstance(value, list) or \
                any(not isinstance(r, dict) for r in value):
            raise CmtvError(f"{field}: must be a list of objects")
        if field == "folders":
            for f in value:
                if not isinstance(f.get("name"), str) or \
                        not isinstance(f.get("apps"), list):
                    raise CmtvError("folders: each folder needs "
                                    "{name: string, apps: list}")
        return value
    if field == "buttons":
        if not isinstance(value, list) or \
                any(not isinstance(b, dict) for b in value):
            raise CmtvError("buttons: must be a list of {code, action, app?}")
        for b in value:
            code = b.get("code")
            if not isinstance(code, int) or not 1 <= code <= 65535:
                raise CmtvError("buttons: code must be 1..65535")
            if not isinstance(b.get("action"), str):
                raise CmtvError("buttons: each button needs an action string")
        return value
    if field in OBJECT_FIELDS:
        if not isinstance(value, dict):
            raise CmtvError(f"{field}: must be a JSON object")
        return value
    raise CmtvError(f"{field}: no validator (schema bug)")


def validate_change(change):
    if not isinstance(change, dict) or not change:
        raise CmtvError("change must be a non-empty JSON object")
    return {f: validate_field(f, v) for f, v in change.items()}


# ------------------------------------------------------------ adb layer ----
class RealAdb:
    def shell(self, *args):
        out = subprocess.run([ADB, "-s", SERIAL, "shell", *args],
                             capture_output=True, text=True, timeout=60)
        return (out.stdout or "") + (out.stderr or "")

    def content_call(self, method, arg=None):
        cmd = [ADB, "-s", SERIAL, "shell", "content", "call",
               "--uri", URI, "--method", method]
        if arg is not None:
            cmd += ["--arg", arg]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        text = (out.stdout or "") + (out.stderr or "")
        low = text.lower()
        if "exception" in low or "error while" in low \
                or "unknown method" in low:
            raise CmtvError(f"{method} failed: {text.strip()[:300]}")
        return text


MOCK_CATALOG = [
    {"pkg": "com.nuvio.livetv", "label": "Nuvio + IPTV", "tv": True},
    {"pkg": "org.smarttube.stable", "label": "SmartTube", "tv": True},
    {"pkg": "com.google.android.youtube.tv", "label": "YouTube",
     "tv": True},
    {"pkg": "com.google.android.youtube.tvmusic", "label": "YouTube Music",
     "tv": True},
    {"pkg": "com.spotify.tv.android", "label": "Spotify", "tv": True},
    {"pkg": "org.xbmc.kodi", "label": "Kodi", "tv": True},
]


class MockAdb:
    """Simulates the ConfigProvider with the same Bundle text format, so
    the parser and the whole command flow are exercised without a TV.
    State persists in <state_dir>/mock-tv.json (defaults + merges)."""

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "mock-tv.json")
        self.spath = os.path.join(state_dir, "mock-tv-state.json")
        if os.path.exists(self.spath):
            self.state = _read_json(self.spath)
        else:
            self.state = {"slides": [], "looks": [], "themes": [],
                          "fail": {}}
            self._save_state()
        self._staging = {}
        self._staged = {}
        self._stage_idx = None
        if os.path.exists(self.path):
            self.config = _read_json(self.path)
        else:
            self.config = {
                "layout": "shelf", "sections": ["folders"], "top_space": 40,
                "quick": ["wifi", "bluetooth", "customize", "settings",
                          "panel", "fireplace"],
                "rows": [], "folders": [{"name": "Apps",
                                         "apps": ["com.nuvio.livetv"]}],
                "order": ["com.nuvio.livetv", "org.smarttube.stable",
                          "com.stale.gone"],
                "hidden": [],
                "slide_minutes": 0, "slide_from": "mine",
                "day_night": False, "day_at": 420, "night_at": 1140,
                "accent": "#FFB23C", "background": "#060606",
                "font": "condensed", "snow": False, "bing_daily": False,
                "wallpaper_dim": 0, "columns": 8, "dock_size": 5,
                # the real provider always returns every field; the
                # mock mirrors that (undo restores snapshot values)
                "focus": "glow", "dock_scale": 60, "tile_scale": 70,
                "show_names": True, "clock": "time_date",
                "visualiser": False, "viz_style": "bars", "viz_bars": 24,
                "viz_height": 14, "viz_opacity": 85,
                "viz_colour": "accent", "viz_peaks": True,
                "viz_sensitivity": 100, "icon_style": "banner",
                "tile_shape": "wide", "tile_glass": 10,
                "shade_top": 60, "shade_top_reach": 55,
                "shade_bottom": 45, "shade_bottom_reach": 55,
                "wash": 50, "wash_reach": 60,
                "fire_wall": False, "fire_sound": True,
                "fire_wall_sound": False, "fire_volume": 40,
                "fire_timer": 0, "wallpaper_from": "",
            }
            self._save()

    def _save_state(self):
        _atomic_write_json(self.spath, self.state)

    def _save(self):
        _atomic_write_json(self.path, self.config)

    def _bundle(self):
        merged = dict(self.config)
        merged.update({"has_wallpaper": True, "configured": True,
                       "lock_pin_set": False,
                       "themes_on_tv": len(self.state.get("themes", [])),
                       "fire_on_tv": True,
                       "slides_on_tv": len(self.state["slides"]),
                       "looks_saved": sorted(self.state["looks"]),
                       "sections_known": SECTION_NAMES})
        return "Result: Bundle[{config=" + json.dumps(merged) + "}]"

    def content_call(self, method, arg=None):
        if method == "get":
            return self._bundle()
        if method == "apps":
            return ("Result: Bundle[{apps=" +
                    json.dumps(MOCK_CATALOG) + "}]")
        if method == "slides_begin":
            self._staging, self._staged = {}, {}
            return self._bundle()
        if method == "slide_file_begin":
            n = int(arg)
            if not 0 <= n < 12:
                raise CmtvError(f"bad slide {n}")
            self._stage_idx = n
            self._staging[n] = bytearray()
            return self._bundle()
        if method == "slide_file_chunk":
            import base64 as _b64
            fail = self.state.get("fail") or {}
            if fail.get("chunk_always"):
                raise CmtvError("mock: chunk failed (injected)")
            data = _b64.b64decode(arg or "")
            buf = self._staging[self._stage_idx]
            if fail.get("corrupt_once") and data:
                data = bytes([data[0] ^ 0xFF]) + data[1:]
                self.state["fail"] = {}
                self._save_state()
            if len(buf) + len(data) > 4 * 1024 * 1024:
                raise CmtvError("picture is over 4 MB")
            buf += data
            return self._bundle()
        if method == "slide_file_end":
            import hashlib as _h
            buf = bytes(self._staging.pop(self._stage_idx, b""))
            got = _h.md5(buf).hexdigest()
            if got != (arg or "").lower():
                raise CmtvError(f"picture upload is damaged (md5 {got})")
            self._staged[self._stage_idx] = buf
            return self._bundle()
        if method == "slides_end":
            self.state["slides"] = [
                {"size": len(self._staged[i]),
                 "md5": __import__("hashlib").md5(self._staged[i]).hexdigest()}
                for i in sorted(self._staged)]
            self._staging, self._staged = {}, {}
            self._save_state()
            return self._bundle()
        if method == "look_save":
            if arg not in ("day", "night"):
                raise CmtvError(f"bad look {arg}")
            if arg not in self.state["looks"]:
                self.state["looks"].append(arg)
                self._save_state()
            return self._bundle()
        if method == "theme_save":
            import base64 as _b64
            import re as _re
            name = _b64.b64decode(arg or "").decode("utf-8")
            slug = _re.sub(r"[^a-z0-9]+", "-",
                           name.lower()).strip("-") or "theme"
            tid = f"my-{slug}"
            themes = self.state.setdefault("themes", [])
            if tid not in themes:  # same name reuses the id (overwrite)
                themes.append(tid)
            self._save_state()
            # Provider side effect (ThemePack.saveCurrent): the
            # config's wallpaper_from is pointed at the saved theme.
            self.config["wallpaper_from"] = f"{tid}:0"
            self._save()
            return ("Result: Bundle[{theme=" + tid + ", config=" +
                    json.dumps(self.config) + "}]")
        if method == "set64":
            if self.state.get("fail", {}).get("set64"):
                raise CmtvError("mock: injected set64 failure")
            change = json.loads(base64.b64decode(arg).decode("utf-8"))
            self.config.update(change)
            self._save()
            return self._bundle()
        raise CmtvError(f"mock: unsupported method {method}")


def parse_config(bundle_text):
    """Extract the config JSON from 'Result: Bundle[{config={...}}]' by
    balanced-brace scanning (the JSON itself contains nested braces)."""
    i = bundle_text.find("config=")
    if i < 0:
        raise CmtvError(f"no config in provider reply: "
                        f"{bundle_text.strip()[:200]}")
    start = bundle_text.find("{", i)
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(bundle_text)):
        c = bundle_text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(bundle_text[start:j + 1])
    raise CmtvError("unbalanced config JSON in provider reply")


def parse_apps(bundle_text):
    """Extract the app catalog from 'Result: Bundle[{apps=[{...}]}]' —
    [{pkg, label, tv}], the shape listApps() in MainActivity.kt emits."""
    i = bundle_text.find("apps=")
    if i < 0:
        raise CmtvError(f"no apps in provider reply: "
                        f"{bundle_text.strip()[:200]}")
    start = bundle_text.find("[", i)
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(bundle_text)):
        c = bundle_text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                apps = json.loads(bundle_text[start:j + 1])
                for a in apps:
                    if not isinstance(a, dict) or "pkg" not in a \
                            or "label" not in a:
                        raise CmtvError("malformed app entry in catalog")
                return apps
    raise CmtvError("unbalanced apps JSON in provider reply")


# ------------------------------------------------------------ state dir ----
def state_dir():
    d = os.environ.get("CMTV_STATE_DIR") or \
        os.path.expanduser("~/.cmtv-state")
    os.makedirs(os.path.join(d, "snapshots"), exist_ok=True)
    return d


def stack_path():
    return os.path.join(state_dir(), "stack.json")


def load_stack():
    p = stack_path()
    return _read_json(p) if os.path.exists(p) else []


def save_stack(stack):
    _atomic_write_json(stack_path(), stack)


def take_snapshot(config, note):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{ts}-{time.time_ns() % 100000:05d}.json"
    path = os.path.join(state_dir(), "snapshots", name)
    _atomic_write_json(path, {"taken_at": ts, "note": note,
                              "config": config})
    return name


def schema_view(config):
    return {k: v for k, v in config.items()
            if k in SCHEMA_FIELDS or k in PROTECTED}


def diff_configs(old, new):
    out = []
    for k in sorted(set(old) | set(new)):
        if k in STATUS_FIELDS:
            continue
        o, n = old.get(k), new.get(k)
        if o != n:
            out.append((k, o, n))
    return out


def fmt(v):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 90 else s[:87] + "…"


# -------------------------------------------------------------- commands ---
class Ctx:
    def __init__(self, mock):
        self.adb = MockAdb(state_dir()) if mock else RealAdb()

    def get_config(self):
        return parse_config(self.adb.content_call("get"))

    def get_apps(self):
        return parse_apps(self.adb.content_call("apps"))

    def call(self, method, arg=None):
        return self.adb.content_call(method, arg)

    def get_status(self):
        """The full provider reply: config fields + status fields
        (slides_on_tv, looks_saved, has_wallpaper, …)."""
        return parse_config(self.adb.content_call("get"))

    def apply(self, change):
        arg = base64.b64encode(
            json.dumps(change).encode("utf-8")).decode()
        return parse_config(self.adb.content_call("set64", arg))


def cmd_get(ctx, args):
    cfg = ctx.get_config()
    if args.field:
        if args.field not in cfg:
            raise CmtvError(f"{args.field}: not present in the config")
        print(json.dumps(cfg[args.field], ensure_ascii=False))
        return
    view = cfg if args.raw else schema_view(cfg)
    print(json.dumps(view, indent=1, ensure_ascii=False))


def apply_change(ctx, change, note):
    """The one safe path for every config change (set + organizer):
    validate -> snapshot -> selective set64 -> stack -> report -> verify
    the launcher kept the intended values. Returns the effective change,
    or None when nothing would change."""
    change = validate_change(change)
    before = ctx.get_config()
    effective = {f: v for f, v in change.items() if before.get(f) != v}
    if not effective:
        return None
    snap = take_snapshot(before, note=note)
    # The undo entry is written BEFORE the change is applied, so a
    # crash between set64 and bookkeeping cannot leave an unlogged
    # change. An apply whose failure is CONFIRMED by read-back pops
    # the entry again; an ambiguous one keeps it (see below).
    stack = load_stack()
    stack.append({"at": datetime.now().isoformat(timespec="seconds"),
                  "fields": sorted(effective), "snapshot": snap,
                  "values": {f: effective[f] for f in sorted(effective)}})
    save_stack(stack)
    try:
        after = ctx.apply(effective)
    except Exception as apply_err:
        # Ambiguous failure: the reply can be lost (timeout, adb
        # disconnect) AFTER the provider already applied the change.
        # Never retry the mutation blindly. Read back once and let
        # the evidence decide what happens to the undo entry.
        try:
            now = ctx.get_config()
        except Exception:
            now = None
        if now is not None and all(now.get(f) == v
                                   for f, v in effective.items()):
            print(f"WARNING: set64 reported an error ({apply_err}), "
                  "but the read-back confirms the change WAS "
                  "applied; keeping the undo entry.")
            after = now
        elif now is not None and all(now.get(f) == before.get(f)
                                     for f in effective):
            stack.pop()
            save_stack(stack)
            raise CmtvError(
                f"apply failed ({apply_err}); the read-back confirms "
                "nothing was applied, so the undo entry was removed")
        else:
            stack[-1]["unconfirmed"] = True
            save_stack(stack)
            detail = ("the read-back also failed"
                      if now is None else
                      "the read-back shows unexpected values")
            raise CmtvError(
                f"apply outcome UNKNOWN ({apply_err}); {detail}. "
                "The mutation was NOT retried and the undo entry "
                "was KEPT, flagged unconfirmed, for recovery — "
                "check `cmtv get` / `cmtv diff`, then `cmtv undo` "
                "if the change should be reverted")
    print(f"Snapshot saved: {snap}")
    for f, o, n in diff_configs(before, after):
        mark = "" if f in effective else "  (unexpected!)"
        print(f"  {f}: {fmt(o)} -> {fmt(n)}{mark}")
    drift = [f for f in effective if after.get(f) != effective[f]]
    if drift:
        raise CmtvError(f"launcher did not keep the intended values for "
                        f"{drift}; run `cmtv verify` and `cmtv undo`")
    return effective


def cmd_set(ctx, args):
    if args.json:
        raw = json.loads(args.json)
    else:
        if not args.pairs or len(args.pairs) % 2:
            raise CmtvError("set needs FIELD VALUE pairs (or --json)")
        raw = {}
        for i in range(0, len(args.pairs), 2):
            raw[args.pairs[i]] = coerce_scalar(args.pairs[i + 1])
    if apply_change(ctx, raw, "before set") is None:
        print("No change: those fields already hold those values.")


def cmd_diff(ctx, args):
    cfg = ctx.get_config()
    if args.snapshot:
        snap = _read_json(args.snapshot)["config"]
        label = args.snapshot
    else:
        stack = load_stack()
        if not stack:
            print("No snapshots yet — nothing to diff against.")
            return
        label = stack[-1]["snapshot"]
        snap = _read_json(os.path.join(
            state_dir(), "snapshots", label))["config"]
    d = diff_configs(snap, cfg)
    if not d:
        print(f"No differences vs {label}.")
    for f, o, n in d:
        print(f"  {f}: snapshot {fmt(o)}  !=  current {fmt(n)}")


def cmd_verify(ctx, args):
    cfg = ctx.get_config()
    problems = []
    for f in ("layout", "font", "accent"):
        if f not in cfg:
            problems.append(f"missing core field {f}")
    try:
        for f, v in schema_view(cfg).items():
            if f in SCHEMA_FIELDS:
                validate_field(f, v)
    except CmtvError as e:
        problems.append(f"current value fails schema: {e}")
    stack = load_stack()
    if stack:
        top = stack[-1]
        for f, want in top["values"].items():
            if cfg.get(f) != want:
                problems.append(f"{f}: expected {fmt(want)} from the last "
                                f"change, found {fmt(cfg.get(f))}")
    unknown = [k for k in cfg if k not in SCHEMA_FIELDS
               and k not in STATUS_FIELDS and k not in PROTECTED]
    if problems:
        for p in problems:
            print(f"PROBLEM: {p}")
        raise CmtvError("verify failed")
    print("OK: config parses, values are schema-valid"
          + (", last change still in effect." if stack else "."))
    if unknown:
        print(f"Note: launcher reports extra fields not in the Phase 1 "
              f"schema: {sorted(unknown)}")


def cmd_undo(ctx, args):
    stack = load_stack()
    if not stack:
        print("Nothing to undo.")
        return
    top = stack[-1]
    snap = _read_json(os.path.join(
        state_dir(), "snapshots", top["snapshot"]))["config"]
    restore = {f: snap.get(f) for f in top["fields"]}
    restore = validate_change(restore)
    before = ctx.get_config()
    # Conflict detection: if any field this change set no longer
    # holds the value cmtv put there, something else (the TV remote,
    # the desktop app, a look crossing) changed it afterwards.
    # Stomping it silently would lose that change, so refuse unless
    # --force. Fields the change never touched are not checked.
    applied = top.get("values")
    if applied is None:
        print("Note: this change predates conflict detection; "
              "undoing without an external-change check.")
    else:
        conflicts = [f for f in top["fields"]
                     if before.get(f) != applied.get(f)]
        if conflicts and not getattr(args, "force", False):
            detail = "; ".join(
                f"{f}: cmtv set {fmt(applied.get(f))}, now "
                f"{fmt(before.get(f))}, undo would restore "
                f"{fmt(restore[f])}" for f in conflicts)
            raise CmtvError(
                f"undo conflict — {detail}. Something changed these "
                "fields after cmtv did (TV remote, desktop app, or a "
                "day/night crossing). Refusing to overwrite; re-run "
                "`cmtv undo --force` if overwriting is intended.")
    after = ctx.apply(restore)
    bad = [f for f in restore if after.get(f) != restore[f]]
    if bad:
        raise CmtvError(f"undo did not restore {bad}; stack left in place")
    stack.pop()
    save_stack(stack)
    print(f"Undid change from {top['at']} (snapshot {top['snapshot']}):")
    for f, o, n in diff_configs(before, after):
        print(f"  {f}: {fmt(o)} -> {fmt(n)}")
    if not stack:
        print("Undo stack is now empty.")


def cmd_snapshots(ctx, args):
    stack = load_stack()
    files = sorted(os.listdir(os.path.join(state_dir(), "snapshots")))
    print(f"{len(files)} snapshot(s), {len(stack)} change(s) on the undo "
          "stack.")
    for e in stack:
        flag = "  UNCONFIRMED (apply outcome was unknown)" \
            if e.get("unconfirmed") else ""
        print(f"  {e['at']}  fields={e['fields']}  "
              f"snapshot={e['snapshot']}{flag}")


# ------------------------------------------------------- app organizer ---
# Semantics follow the launcher source (MainActivity.kt arrange()):
# display order = config `order` first (installed apps only), then the
# rest alphabetically by label; `hidden` apps are filtered out. There is
# NO `dock` config field: the dock is the first `dock_size` visible apps
# of that arranged order (Config.kt: "the first visible apps in `order`
# sit in the dock").

def resolve_app(catalog, name):
    """Resolve a user-given name to exactly one catalog app. Exact package
    or exact label wins; a unique substring match is accepted; ambiguity
    and absence are errors — a package is never guessed."""
    name = (name or "").strip()
    if not name:
        raise CmtvError("empty app name")
    for a in catalog:
        if a["pkg"] == name:
            return a
    low = name.lower()
    exact = [a for a in catalog if a["label"].lower() == low]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise CmtvError(f"{name!r} is ambiguous: " +
                        ", ".join(f'{a["label"]} ({a["pkg"]})'
                                  for a in exact))
    sub = [a for a in catalog if low in a["label"].lower()]
    if len(sub) == 1:
        return sub[0]
    if len(sub) > 1:
        raise CmtvError(f"{name!r} is ambiguous — matches: " +
                        ", ".join(f'{a["label"]} ({a["pkg"]})'
                                  for a in sub) +
                        ". Be more specific.")
    raise CmtvError(f"{name!r}: not in the TV's app catalog — "
                    "refusing to guess a package name")


def arranged_apps(catalog, cfg):
    """The launcher's display order (arrange() in MainActivity.kt)."""
    by_pkg = {a["pkg"]: a for a in catalog}
    order = cfg.get("order") or []
    hidden = set(cfg.get("hidden") or [])
    first = [by_pkg[p] for p in order if p in by_pkg]
    rest = sorted((a for a in catalog if a["pkg"] not in set(order)),
                  key=lambda a: a["label"].lower())
    return [a for a in first + rest if a["pkg"] not in hidden]


def stale_entries(catalog, cfg):
    """Packages named in order/folders/quick but not installed."""
    known = {a["pkg"] for a in catalog}
    stale = set()
    stale |= {p for p in (cfg.get("order") or []) if p not in known}
    for g in (cfg.get("folders") or []) + (cfg.get("rows") or []):
        stale |= {p for p in g.get("apps", []) if p not in known}
    stale |= {q[4:] for q in (cfg.get("quick") or [])
              if q.startswith("app:") and q[4:] not in known}
    return sorted(stale)


def cmd_apps(ctx, args):
    catalog = ctx.get_apps()
    cfg = ctx.get_config()
    if args.json:
        print(json.dumps(catalog, indent=1, ensure_ascii=False))
        return
    arranged = arranged_apps(catalog, cfg)
    pos = {a["pkg"]: i + 1 for i, a in enumerate(arranged)}
    quick = set(cfg.get("quick") or [])
    dock_n = cfg.get("dock_size") or 0
    folder_of = {}
    for g in (cfg.get("folders") or []):
        for p in g.get("apps", []):
            folder_of.setdefault(p, []).append(g["name"])
    hidden = set(cfg.get("hidden") or [])
    print(f"{len(catalog)} apps in the TV catalog "
          f"(dock = first {dock_n} visible):")
    for a in sorted(catalog, key=lambda x: pos.get(x["pkg"], 999)):
        marks = []
        if a["pkg"] in pos:
            marks.append(f'#{pos[a["pkg"]]}')
            if pos[a["pkg"]] <= dock_n:
                marks.append("dock")
        if a["pkg"] in hidden:
            marks.append("HIDDEN")
        if f'app:{a["pkg"]}' in quick:
            marks.append("quick")
        for f in folder_of.get(a["pkg"], []):
            marks.append(f'folder:{f}')
        print(f'  {a["label"]}  ({a["pkg"]})'
              + (f'  [{" ".join(marks)}]' if marks else ""))
    stale = stale_entries(catalog, cfg)
    if stale:
        print(f"Stale config entries (not installed): {', '.join(stale)}")


def _org_move(ctx, pkg, position, note):
    """Move pkg to a 1-based position in the arranged order. Every other
    entry — including stale and hidden ones sitting in `order` — keeps
    its data and relative order (they trail the arranged list, inert)."""
    cfg = ctx.get_config()
    arranged = [a["pkg"] for a in arranged_apps(ctx.get_apps(), cfg)]
    if pkg not in arranged:
        raise CmtvError("app is hidden or not installed — it has no "
                        "position in the arranged order")
    arranged.remove(pkg)
    position = max(1, min(position, len(arranged) + 1))
    arranged.insert(position - 1, pkg)
    leftovers = [p for p in (cfg.get("order") or []) if p not in set(arranged)]
    new_order = arranged + leftovers
    if apply_change(ctx, {"order": new_order}, note) is None:
        print("No change: the app is already at that position.")
        return False
    return True


def org_move(ctx, args):
    app = resolve_app(ctx.get_apps(), args.name)
    if args.to is not None:
        pos = args.to
    elif args.last:
        pos = 10 ** 9
    else:
        pos = 1
    _org_move(ctx, app["pkg"], pos, f"org move {app['pkg']}")


def org_quick_add(ctx, args):
    catalog = ctx.get_apps()
    cfg = ctx.get_config()
    quick = list(cfg.get("quick") or [])
    added = []
    for name in args.names:
        app = resolve_app(catalog, name)
        entry = f'app:{app["pkg"]}'
        if entry in quick:
            print(f'{app["label"]}: already in the quick bar.')
            continue
        quick.append(entry)
        added.append(app["label"])
    if not added:
        return
    if apply_change(ctx, {"quick": quick}, "org quick-add") is not None:
        print(f'Quick bar: added {", ".join(added)}.')


def org_quick_remove(ctx, args):
    cfg = ctx.get_config()
    quick = list(cfg.get("quick") or [])
    name = args.name.strip()
    entry = None
    if name in quick:                      # a built-in quick id, named exactly
        entry = name
    else:
        try:
            entry = f'app:{resolve_app(ctx.get_apps(), name)["pkg"]}'
        except CmtvError:
            if f"app:{name}" in quick:    # stale app shortcut, named by pkg
                entry = f"app:{name}"
            else:
                raise
    if entry not in quick:
        print(f"{name!r}: not in the quick bar.")
        return
    quick.remove(entry)
    if apply_change(ctx, {"quick": quick}, "org quick-remove") is not None:
        print(f"Quick bar: removed {entry}.")


def org_dock(ctx, args):
    cfg = ctx.get_config()
    n = cfg.get("dock_size") or 0
    dock = arranged_apps(ctx.get_apps(), cfg)[:n]
    print(f"Dock (first {n} visible apps in the arranged order):")
    for i, a in enumerate(dock, 1):
        print(f'  {i}. {a["label"]} ({a["pkg"]})')
    if not dock:
        print("  (empty)")


def org_dock_add(ctx, args):
    cfg = ctx.get_config()
    n = cfg.get("dock_size") or 0
    app = resolve_app(ctx.get_apps(), args.name)
    arranged = arranged_apps(ctx.get_apps(), cfg)
    pos = [a["pkg"] for a in arranged].index(app["pkg"]) + 1 \
        if app["pkg"] in [a["pkg"] for a in arranged] else None
    if pos is not None and pos <= n:
        print(f'{app["label"]} is already in the dock (position {pos}).')
        return
    _org_move(ctx, app["pkg"], 1, f'org dock-add {app["pkg"]}')
    print(f'{app["label"]} moved to the front — now in the dock.')


def org_hide(ctx, args, hide=True):
    cfg = ctx.get_config()
    hidden = list(cfg.get("hidden") or [])
    if hide:
        app = resolve_app(ctx.get_apps(), args.name)
        pkg = app["pkg"]
        if pkg in hidden:
            print(f'{app["label"]}: already hidden.')
            return
        hidden.append(pkg)
        label = app["label"]
    else:
        pkg = args.name.strip()
        if pkg not in hidden:
            app = resolve_app(ctx.get_apps(), args.name)
            pkg = app["pkg"]
        if pkg not in hidden:
            print(f"{args.name!r}: is not hidden.")
            return
        hidden.remove(pkg)
        label = pkg
    change = {"hidden": sorted(hidden)}
    if apply_change(ctx, change, "org hide" if hide else "org unhide") \
            is not None:
        print(f'{label}: {"hidden" if hide else "visible again"}.')


def _find_folder(cfg, name):
    folders = cfg.get("folders") or []
    exact = [g for g in folders if g["name"].lower() == name.lower()]
    if len(exact) == 1:
        return exact[0]
    if not exact:
        raise CmtvError(f"no folder named {name!r} "
                        f"(folders: {[g['name'] for g in folders]})")
    raise CmtvError(f"folder name {name!r} is ambiguous")


def org_folder(ctx, args, add=True):
    cfg = ctx.get_config()
    folders = json.loads(json.dumps(cfg.get("folders") or []))
    group = _find_folder(cfg, args.folder)
    real = next(g for g in folders if g["name"] == group["name"])
    if add:
        app = resolve_app(ctx.get_apps(), args.name)
        if app["pkg"] in real["apps"]:
            print(f'{app["label"]}: already in folder {group["name"]!r}.')
            return
        real["apps"].append(app["pkg"])
        label = app["label"]
    else:
        pkg = args.name.strip()
        if pkg not in real["apps"]:
            pkg = resolve_app(ctx.get_apps(), args.name)["pkg"]
        if pkg not in real["apps"]:
            print(f"{args.name!r}: not in folder {group['name']!r}.")
            return
        real["apps"].remove(pkg)
        label = pkg
    if apply_change(ctx, {"folders": folders}, "org folder") is not None:
        print(f'Folder {group["name"]!r}: '
              f'{"added" if add else "removed"} {label}.')


def org_prune(ctx, args):
    catalog = ctx.get_apps()
    cfg = ctx.get_config()
    stale = set(stale_entries(catalog, cfg))
    if not stale:
        print("No stale entries — order, folders and quick all name "
              "installed apps.")
        return
    change = {}
    order = [p for p in (cfg.get("order") or []) if p not in stale]
    if order != (cfg.get("order") or []):
        change["order"] = order
    folders = json.loads(json.dumps(cfg.get("folders") or []))
    touched = False
    for g in folders:
        new = [p for p in g["apps"] if p not in stale]
        if new != g["apps"]:
            g["apps"] = new
            touched = True
    if touched:
        change["folders"] = folders
    quick = [q for q in (cfg.get("quick") or [])
             if not (q.startswith("app:") and q[4:] in stale)]
    if quick != (cfg.get("quick") or []):
        change["quick"] = quick
    if apply_change(ctx, change, "org prune") is not None:
        print(f'Pruned stale entries: {", ".join(sorted(stale))}.')


# ------------------------------------------------------ slides & looks ---
# Slideshow protocol (Slides.kt + Upload.kt): files stage into
# slides.upload/ and replace the live set ONLY at slides_end (the old
# directory is deleted there). An aborted upload therefore leaves the
# old set untouched — but a committed set can never be read back: there
# is no slide_read method, so a replaced set is gone for good. Local
# archives of every set we upload are the recovery strategy.
# Looks (DayNight.kt): a look = SAVED_LOOK_KEYS of the config + a copy
# of the wallpaper, stored per slot in files the provider cannot read
# back. Saving over an occupied slot destroys the old look; the CLI
# gates that behind --force. Config snapshots do NOT cover slide files
# or look slots — `undo` only restores config fields.

import hashlib

SLIDE_MAX_FILES = 12          # Slides.MAX
SLIDE_MAX_BYTES = 4 * 1024 * 1024
CHUNK_RAW = 30 * 1024         # proven over the adb CLI (wallpaper tool)


def _send_slide_file(ctx, n, data):
    """One slide, begin -> chunks -> end(md5), restarted once on any
    failure (the desktop app's sendFile strategy; Upload.start wipes
    stale .upload files, so a restart is clean)."""
    md5 = hashlib.md5(data).hexdigest()
    pieces = [base64.b64encode(data[i:i + CHUNK_RAW]).decode()
              for i in range(0, len(data), CHUNK_RAW)]
    for attempt in (1, 2):
        try:
            ctx.call("slide_file_begin", str(n))
            for p in pieces:
                ctx.call("slide_file_chunk", p)
            ctx.call("slide_file_end", md5)
            return
        except CmtvError:
            if attempt == 2:
                raise


def _upload_slides(ctx, jpegs):
    ctx.call("slides_begin")
    for n, data in enumerate(jpegs):
        _send_slide_file(ctx, n, data)
    after = parse_config(ctx.call("slides_end"))
    if after.get("slides_on_tv") != len(jpegs):
        raise CmtvError(f"slideshow commit mismatch: TV reports "
                        f"{after.get('slides_on_tv')} slides, "
                        f"expected {len(jpegs)}")
    return after


def _assets_dir():
    d = os.environ.get("CMTV_ASSETS_DIR") or \
        os.path.expanduser("~/.cmtv-assets")
    os.makedirs(d, exist_ok=True)
    return d


def _require_pillow():
    import importlib.util
    if importlib.util.find_spec("PIL") is None:
        raise CmtvError(
            "Pillow is not installed, and image conversion needs "
            "it. Install the dependency with: pip install -r "
            "requirements.txt  (or: pip install Pillow)")


def _load_wallpaper_tool():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "set_tv_wallpaper.py")
    spec = importlib.util.spec_from_file_location("set_tv_wallpaper",
                                                  path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def cmd_slides(ctx, args):
    if args.slides_cmd == "status":
        st = ctx.get_status()
        n = st.get("slides_on_tv", 0)
        mins = st.get("slide_minutes", 0)
        active = bool(mins) and n > 0 and st.get("slide_from") == "mine"
        print(f"Slides on TV: {n}   interval: {mins} min   "
              f"source: {st.get('slide_from')}")
        print("Slideshow is " + ("ACTIVE (the wallpaper rotates through "
              "the set)." if active else "off."))
        return
    if args.slides_cmd == "upload":
        files = args.files
        if not 1 <= len(files) <= SLIDE_MAX_FILES:
            raise CmtvError(f"a slideshow takes 1..{SLIDE_MAX_FILES} "
                            f"pictures, got {len(files)}")
        for f in files:
            if not os.path.isfile(f):
                raise CmtvError(f"no such file: {f}")
        st = ctx.get_status()
        current = st.get("slides_on_tv", 0)
        if current and not args.replace:
            raise CmtvError(
                f"the TV already holds {current} slide(s). Replacing "
                "them cannot be undone — the provider has no way to "
                "read a slide set back. Re-run with --replace if that "
                "set is disposable (sets uploaded by cmtv are archived "
                "locally under cmtv-assets/slides/).")
        _require_pillow()
        wtw = _load_wallpaper_tool()
        jpegs = [wtw.to_jpeg(f) for f in files]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        arch = os.path.join(_assets_dir(), "slides", stamp)
        os.makedirs(arch, exist_ok=True)
        manifest = {"uploaded_at": stamp, "replaced_existing": bool(current),
                    "files": []}
        for i, (src, data) in enumerate(zip(files, jpegs)):
            name = f"slide-{i}.jpg"
            with open(os.path.join(arch, name), "wb") as fh:
                fh.write(data)
            manifest["files"].append(
                {"source": os.path.abspath(src), "archived_as": name,
                 "bytes": len(data), "md5": hashlib.md5(data).hexdigest()})
        _atomic_write_json(os.path.join(arch, "manifest.json"),
                           manifest)
        _upload_slides(ctx, jpegs)
        print(f"Uploaded {len(jpegs)} slide(s); the TV now holds "
              f"{len(jpegs)}. Local archive: {arch}")
        print("Note: config undo does not remove slide files. "
              "`cmtv slides disable` stops the rotation; the set stays "
              "on the TV until another upload replaces it.")
        return
    if args.slides_cmd == "enable":
        mins = validate_field("slide_minutes", args.minutes)
        if mins < 1:
            raise CmtvError("enable needs --minutes >= 1 "
                            "(use `slides disable` to stop)")
        st = ctx.get_status()
        if not st.get("slides_on_tv"):
            raise CmtvError("no slides on the TV yet — "
                            "`cmtv slides upload` first")
        if apply_change(ctx, {"slide_minutes": mins,
                              "slide_from": "mine"},
                        "slides enable") is None:
            print(f"Slideshow already enabled at {mins} min.")
        else:
            print(f"Slideshow on: a new picture every {mins} min. "
                  "Note the rotation works by replacing the wallpaper "
                  "with the next slide; disabling leaves the last "
                  "slide as the wallpaper.")
        return
    if args.slides_cmd == "disable":
        if apply_change(ctx, {"slide_minutes": 0},
                        "slides disable") is None:
            print("Slideshow already off.")
        else:
            print("Slideshow off. The slide files stay on the TV; the "
                  "wallpaper remains the last picture shown — restore "
                  "another with the wallpaper tool if wanted.")
        return
    raise CmtvError(f"unknown slides command {args.slides_cmd}")


def parse_hhmm(s):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (s or "").strip())
    if not m:
        raise CmtvError(f"{s!r}: expected HH:MM (e.g. 06:00, 18:30)")
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        raise CmtvError(f"{s!r}: not a valid time of day")
    return h * 60 + mi


def fmt_hhmm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def slot_at(minute, day_at, night_at):
    """Mirror of DayNight.slotAt (minutes since midnight, TV-local)."""
    if day_at == night_at:
        return "day"
    if day_at < night_at:
        return "day" if day_at <= minute < night_at else "night"
    return "night" if night_at <= minute < day_at else "day"


def cmd_looks(ctx, args):
    if args.looks_cmd == "status":
        st = ctx.get_status()
        day_at = st.get("day_at", 0)
        night_at = st.get("night_at", 0)
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("Asia/Jakarta"))
            cur = slot_at(now.hour * 60 + now.minute, day_at, night_at)
            now_s = now.strftime("%H:%M")
        except Exception:
            cur, now_s = "?", "?"
        print(f"Day/night switching: "
              f"{'ON' if st.get('day_night') else 'OFF'}   "
              f"day from {fmt_hhmm(day_at)}   night from "
              f"{fmt_hhmm(night_at)}")
        print(f"Saved looks: {st.get('looks_saved') or 'none'}   "
              f"(TV time {now_s} WIB -> {cur} half)")
        return
    if args.looks_cmd in ("save-day", "save-night"):
        slot = "day" if args.looks_cmd == "save-day" else "night"
        st = ctx.get_status()
        if slot in (st.get("looks_saved") or []) and not args.force:
            raise CmtvError(
                f"the {slot} slot already holds a look. Saving over it "
                "destroys the old look — look slots cannot be read back "
                "through the provider. Re-run with --force to overwrite "
                "deliberately.")
        ctx.call("look_save", slot)
        after = ctx.get_status()
        if slot not in (after.get("looks_saved") or []):
            raise CmtvError(f"look_save {slot} did not register")
        print(f"Saved the current look as the {slot} look (colours, "
              "icons, glass, font, snow + the current wallpaper).")
        return
    if args.looks_cmd == "schedule":
        day_at = parse_hhmm(args.day)
        night_at = parse_hhmm(args.night)
        if day_at == night_at:
            raise CmtvError("day and night times must differ")
        if apply_change(ctx, {"day_at": day_at, "night_at": night_at,
                              "day_night": True},
                        "looks schedule") is None:
            print("Schedule already set that way.")
        else:
            print(f"Day/night on: day look from {args.day}, night look "
                  f"from {args.night} (TV local time). The launcher "
                  "re-checks within a minute and applies the look for "
                  "the current half.")
        return
    if args.looks_cmd == "disable":
        if apply_change(ctx, {"day_night": False},
                        "looks disable") is None:
            print("Day/night switching already off.")
        else:
            print("Day/night switching off. The look currently on "
                  "screen stays; the saved slots are untouched.")
        return
    raise CmtvError(f"unknown looks command {args.looks_cmd}")


# ---------------------------------------------------- presets & themes ---
# A Muse preset is a LOCAL file holding visual fields only: the
# launcher's own THEME_KEYS (src/shared/themes.ts — "a theme never
# touches apps, rows, folders, layout, buttons or anything personal")
# plus tile_glass (a look key the day/night system saves). Arrangement,
# app, lock, power, slideshow and day/night fields can never enter a
# preset, so applying one cannot disturb apps or schedules.
PRESET_FIELDS = [
    "accent", "focus", "background", "wallpaper_dim", "top_space",
    "dock_scale", "tile_scale", "show_names", "clock", "visualiser",
    "viz_style", "viz_bars", "viz_height", "viz_opacity", "viz_colour",
    "viz_peaks", "viz_sensitivity", "icon_style", "tile_shape",
    "shade_top", "shade_top_reach", "shade_bottom", "shade_bottom_reach",
    "wash", "wash_reach", "font", "snow", "tile_glass",
]
BUILTIN_PRESETS = {
    "minimalist": {
        "accent": "#E8E6DF", "background": "#101012", "focus": "outline",
        "tile_shape": "squircle", "tile_glass": 0, "icon_style": "mono",
        "wallpaper_dim": 25, "snow": False, "clock": "time",
        "font": "default", "wash": 100, "shade_top": 0,
        "shade_bottom": 0, "show_names": True,
    },
    "cinema": {
        "accent": "#FFB23C", "background": "#060606", "focus": "glow",
        "tile_shape": "wide", "tile_glass": 10, "icon_style": "banner",
        "wallpaper_dim": 0, "snow": False, "clock": "time_date",
        "font": "condensed", "wash": 50,
    },
    "midnight": {
        "accent": "#7C5CFF", "background": "#070B1E", "focus": "glow",
        "tile_shape": "squircle", "tile_glass": 20, "icon_style": "colour",
        "wallpaper_dim": 10, "snow": True, "clock": "time_date",
        "font": "condensed", "shade_top": 20,
    },
    "nature": {
        "accent": "#4CAF50", "background": "#0B120C", "focus": "lift",
        "tile_shape": "round", "tile_glass": 0, "icon_style": "colour",
        "wallpaper_dim": 0, "snow": False, "clock": "time_date",
        "font": "casual", "wash": 80,
    },
}
# The launcher's built-in native themes (src/shared/themes.ts): name,
# accent, background, focus. Their wallpapers are procedural art drawn
# by the desktop app — there are no picture files to upload from here.
NATIVE_THEMES = [
    ("aurora", "Aurora", "#2EC4B6", "#08161E", "lift"),
    ("ember", "Ember", "#FFB23C", "#140806", "lift"),
    ("midnight", "Midnight", "#7C5CFF", "#070B1E", "lift"),
    ("forest", "Forest", "#4CAF50", "#1C2A1F", "lift"),
    ("graphite", "Graphite", "#FFFFFF", "#111214", "outline"),
    ("christmas", "Christmas", "#F2C14E", "#0B1222", "glow"),
    ("gruvbox", "Gruvbox", "#FE8019", "#1D2021", "lift"),
    ("gruvbox-aqua", "Gruvbox Aqua", "#8EC07C", "#1D2021", "lift"),
    ("gruvbox-purple", "Gruvbox Purple", "#D3869B", "#282828", "outline"),
    ("gruvbox-yellow", "Gruvbox Yellow", "#FABD2F", "#1D2021", "lift"),
    ("gruvbox-blue", "Gruvbox Blue", "#83A598", "#1D2021", "lift"),
    ("gruvbox-green", "Gruvbox Green", "#B8BB26", "#282828", "outline"),
]


def presets_dir():
    d = os.environ.get("CMTV_PRESETS_DIR") or \
        os.path.expanduser("~/.cmtv-presets")
    os.makedirs(d, exist_ok=True)
    return d


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if not s:
        raise CmtvError(f"{name!r}: not a usable preset name")
    return s


def _user_presets():
    out = {}
    for f in sorted(os.listdir(presets_dir())):
        if f.endswith(".json"):
            try:
                data = _read_json(os.path.join(presets_dir(), f))
                out[f[:-5]] = data
            except (ValueError, OSError):
                continue
    return out


def load_preset(name):
    """(display_name, fields, origin) — user presets shadow built-ins.
    Every field must be a visual preset field and schema-valid."""
    slug = _slug(name)
    users = _user_presets()
    if slug in users:
        data = users[slug]
        fields, origin = data.get("fields"), f"saved {data.get('created_at', '')}"
        display = data.get("name", slug)
    elif slug in BUILTIN_PRESETS:
        fields, origin, display = BUILTIN_PRESETS[slug], "built-in", slug
    else:
        raise CmtvError(f"no preset named {name!r} "
                        f"(see `cmtv preset list`)")
    if not isinstance(fields, dict) or not fields:
        raise CmtvError(f"preset {name!r}: empty or malformed")
    bad = [f for f in fields if f not in PRESET_FIELDS]
    if bad:
        raise CmtvError(f"preset {name!r}: {bad} are not visual preset "
                        "fields — refusing to apply a preset that "
                        "touches apps, layout or schedules")
    return display, validate_change(fields), origin


def _preset_diff(cfg, fields):
    return [(f, cfg.get(f), v) for f, v in sorted(fields.items())
            if cfg.get(f) != v]


def _daynight_warning(ctx):
    st = ctx.get_status()
    return bool(st.get("day_night"))


def cmd_preset(ctx, args):
    if args.preset_cmd == "list":
        users = _user_presets()
        print("Built-in presets:")
        for slug, fields in BUILTIN_PRESETS.items():
            tag = " (shadowed by a saved preset)" if slug in users else ""
            print(f"  {slug} — {len(fields)} fields{tag}")
        print("Saved presets:")
        if not users:
            print("  (none yet — `cmtv preset save NAME`)")
        for slug, data in users.items():
            print(f"  {data.get('name', slug)} — "
                  f"{len(data.get('fields', {}))} fields, "
                  f"saved {data.get('created_at', '?')}")
        return
    if args.preset_cmd == "save":
        cfg = ctx.get_config()
        fields = {f: cfg[f] for f in PRESET_FIELDS if f in cfg}
        for pair in args.sets or []:
            if "=" not in pair:
                raise CmtvError(f"--set expects FIELD=VALUE, got {pair!r}")
            f, v = pair.split("=", 1)
            if f not in PRESET_FIELDS:
                raise CmtvError(f"{f}: not a visual preset field")
            fields[f] = coerce_scalar(v)
        fields = validate_change(fields)
        slug = _slug(args.name)
        path = os.path.join(presets_dir(), slug + ".json")
        _atomic_write_json(path,
                           {"name": args.name,
                            "created_at": datetime.now().isoformat(
                                timespec="seconds"),
                            "fields": fields})
        note = ""
        if _daynight_warning(ctx):
            note = (" Note: day/night is ON — this captured the look of "
                    "the currently active half.")
        print(f"Saved preset {args.name!r} ({len(fields)} visual "
              f"fields) to {path}.{note}")
        return
    if args.preset_cmd in ("preview", "apply"):
        display, fields, origin = load_preset(args.name)
        cfg = ctx.get_config()
        diff = _preset_diff(cfg, fields)
        print(f"Preset {display!r} ({origin}) — dry run:")
        if not diff:
            print("  No changes: the current look already matches.")
            return
        for f, o, n in diff:
            print(f"  {f}: {fmt(o)} -> {fmt(n)}")
        if args.preset_cmd == "preview":
            print("Preview only — nothing was changed.")
            return
        if _daynight_warning(ctx) and not args.force:
            raise CmtvError(
                "day/night switching is ON: the schedule owns the look "
                "fields and would overwrite this preset at the next "
                "half-change (and the preset would diverge from the "
                "saved look slots). Disable day/night first "
                "(`cmtv looks disable`) or re-run apply with --force "
                "to apply anyway.")
        if apply_change(ctx, fields, f"preset apply {display}") \
                is not None:
            print(f"Applied preset {display!r}. Undo with `cmtv undo`.")
        return
    if args.preset_cmd == "delete":
        slug = _slug(args.name)
        if slug in BUILTIN_PRESETS and slug not in _user_presets():
            raise CmtvError(f"{args.name!r} is a built-in preset — "
                            "built-ins cannot be deleted")
        path = os.path.join(presets_dir(), slug + ".json")
        if not os.path.exists(path):
            raise CmtvError(f"no saved preset named {args.name!r}")
        os.remove(path)
        print(f"Deleted saved preset {args.name!r}.")
        return
    raise CmtvError(f"unknown preset command {args.preset_cmd}")


def cmd_theme(ctx, args):
    if args.theme_cmd == "status":
        st = ctx.get_status()
        print(f"Native themes on the TV: {st.get('themes_on_tv', 0)}")
        print("Theme packs are write-only through the provider: their "
              "contents cannot be listed or read back, and the only "
              "removal is pack_clear, which wipes ALL themes at once "
              "(cmtv does not expose it).")
        return
    if args.theme_cmd == "list":
        print("Built-in native themes (looks from src/shared/themes.ts; "
              "their wallpapers are procedural art drawn by the "
              "desktop app, so they are applied from there, not from "
              "this CLI):")
        for tid, name, accent, bg, focus in NATIVE_THEMES:
            print(f"  {name} — accent {accent}, background {bg}, "
                  f"focus {focus}")
        st = ctx.get_status()
        print(f"Saved on this TV: {st.get('themes_on_tv', 0)} theme(s) "
              "(via theme save; ids are not listable).")
        return
    if args.theme_cmd == "save":
        name = (args.name or "").strip()[:40] or "My theme"
        before = ctx.get_status().get("themes_on_tv", 0)
        wf_before = ctx.get_config().get("wallpaper_from", "")
        reply = ctx.call("theme_save",
                         base64.b64encode(name.encode()).decode())
        m = re.search(r"theme=([A-Za-z0-9_-]+)", reply)
        after = ctx.get_status().get("themes_on_tv", 0)
        wf_after = ctx.get_config().get("wallpaper_from", "")
        if after == before + 1:
            outcome = "new theme"
        elif after == before:
            outcome = ("an existing theme with this name was "
                       "overwritten (the provider reuses its id)")
        else:
            raise CmtvError("theme_save: unexpected theme count "
                            f"change {before} -> {after}")
        print(f"Saved the current look as native theme "
              f"{name!r} (id {m.group(1) if m else '?'}); "
              f"themes on TV: {after} ({outcome}).")
        print(f"Provider side effect: wallpaper_from "
              f"{fmt(wf_before)} -> {fmt(wf_after)} — theme_save "
              "points the config at the saved theme. This happens "
              "outside cmtv's snapshot/undo system and is NOT "
              "undoable here.")
        print("Note: a saved native theme cannot be deleted on its "
              "own — only pack_clear (all themes at once) exists, and "
              "cmtv does not expose it.")
        return
    raise CmtvError(f"unknown theme command {args.theme_cmd}")


QUICK_LABELS = {
    "wifi": {"wi-fi", "wifi"}, "bluetooth": {"bluetooth"},
    "customize": {"customize", "customise"},
    "settings": {"settings"}, "panel": {"panel", "quick settings"},
    "fireplace": {"fireplace"},
}


def resolve_steps(quick, focused, target="fireplace"):
    """Signed DPAD steps from the focused quick item to the target,
    resolved from the ACTIVE config's quick list — never hardcoded."""
    if target not in quick:
        raise CmtvError(f"{target!r} is not in the quick bar "
                        f"({quick}); not navigating")
    if focused not in quick:
        raise CmtvError(f"focused item {focused!r} is not a quick "
                        "item; not navigating")
    return quick.index(target) - quick.index(focused)


def _bounds(node):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                 node.get("bounds") or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    return (x1, y1, x2, y2)


def focused_quick_item(dump_xml, quick):
    """The focused quick item in a uiautomator dump, or None when
    focus cannot be proven. On the real launcher the focused node
    itself carries no label — the item's label sits on a child node
    at the same place — so the item is identified geometrically:
    the labelled node whose bounds contain the focused node's
    centre (nearest centre as a bounded fallback). Labels are
    matched exactly ("Quick settings" is the panel, not settings)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(dump_xml)
    except ET.ParseError:
        return None
    label_map = {}
    for item in quick:
        for word in QUICK_LABELS.get(item, {item}):
            label_map[word] = item
    labelled = []
    focus = None
    for node in root.iter("node"):
        desc = (node.get("content-desc") or "").strip().lower()
        b = _bounds(node)
        if node.get("focused") == "true" and b:
            focus = b
            if desc in label_map:
                return label_map[desc]
        if desc in label_map and b:
            labelled.append((label_map[desc], b))
    if focus is None:
        return None
    cx, cy = (focus[0] + focus[2]) // 2, (focus[1] + focus[3]) // 2
    best, best_d = None, None
    for item, b in labelled:
        if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
            return item
        d = ((b[0] + b[2]) // 2 - cx) ** 2 + \
            ((b[1] + b[3]) // 2 - cy) ** 2
        if best_d is None or d < best_d:
            best, best_d = item, d
    return best if best_d is not None and best_d < 200 ** 2 else None


def _dump_focus(ctx, quick):
    ctx.adb.shell("uiautomator", "dump", "/sdcard/cmtv-dump.xml")
    xml = ctx.adb.shell("cat", "/sdcard/cmtv-dump.xml")
    return focused_quick_item(xml, quick)


def cmd_fire_open(ctx):
    """Open the full-screen fireplace by quick-bar navigation.
    Experimental: every step is focus-verified via uiautomator and
    the command aborts BEFORE pressing OK whenever focus cannot be
    proven. It never modifies the quick bar."""
    if not hasattr(ctx.adb, "shell"):
        raise CmtvError("fire open needs a real TV connection "
                        "(focus cannot be proven against the mock)")
    quick = ctx.get_config().get("quick") or []
    if "fireplace" not in quick:
        raise CmtvError("the fireplace is not in the quick bar; "
                        "not navigating")
    ctx.adb.shell("input", "keyevent", "KEYCODE_HOME")
    time.sleep(1.5)
    focused = _dump_focus(ctx, quick)
    if focused is None:
        raise CmtvError("could not prove which quick item is "
                        "focused; aborting before any navigation")
    steps = resolve_steps(quick, focused)
    key = "KEYCODE_DPAD_RIGHT" if steps > 0 else "KEYCODE_DPAD_LEFT"
    for _ in range(abs(steps)):
        ctx.adb.shell("input", "keyevent", key)
        time.sleep(0.6)
    focused = _dump_focus(ctx, quick)
    if focused != "fireplace":
        raise CmtvError(f"focus verification failed (focused="
                        f"{focused!r}, expected 'fireplace'); "
                        "aborting before OK")
    ctx.adb.shell("input", "keyevent", "KEYCODE_DPAD_CENTER")
    print("Full-screen fireplace opened (focus verified at every "
          "step). Back returns to Home.")


def _onoff(v):
    if v == "on":
        return True
    if v == "off":
        return False
    raise CmtvError(f"expected on|off, got {v!r}")


def cmd_fire(ctx, args):
    if args.fire_cmd == "open":
        cmd_fire_open(ctx)
        return
    cfg = ctx.get_config()
    st = ctx.get_status()
    if args.fire_cmd == "status":
        print(f"Fire video on the TV: "
              f"{'yes' if st.get('fire_on_tv') else 'no'} "
              f"(fireplace.mp4 + crackle.m4a, uploaded by the desktop "
              f"app; cmtv never uploads or deletes them)")
        print(f"Fire as Home wallpaper: "
              f"{'ON' if cfg.get('fire_wall') else 'off'}")
        print(f"Sound, full-screen + screensaver: "
              f"{'on' if cfg.get('fire_sound') else 'off'}")
        print(f"Sound behind Home: "
              f"{'on' if cfg.get('fire_wall_sound') else 'off'}")
        print(f"Volume: {cfg.get('fire_volume')}")
        t = cfg.get('fire_timer') or 0
        print(f"Full-screen timer: "
              f"{'off (burns until closed)' if t == 0 else str(t) + ' min, then the TV sleeps'}")
        print("Full-screen fireplace: no API trigger exists — the "
              "launcher starts it internally from the quick bar's "
              "fireplace item (or its screensaver). Config changes "
              "never open it.")
        if st.get("day_night"):
            print("Note: day/night is ON. fire_wall is not a look "
                  "field, so the fire stays across the 06:00/18:00 "
                  "crossings; the look wallpapers keep switching "
                  "unseen behind it.")
        return
    if args.fire_cmd == "enable":
        if not st.get("fire_on_tv"):
            raise CmtvError("no fire video on the TV — the fireplace "
                            "asset is uploaded by the CleanMyTV "
                            "desktop app; cmtv does not upload one")
        fields = {"fire_wall": True}
        if args.sound:
            fields["fire_wall_sound"] = True
        if apply_change(ctx, fields, "fire enable") is not None:
            print("Fireplace is now the Home wallpaper"
                  + (" (with sound)." if args.sound else
                     " (silent; --sound or settings --wall-sound on "
                     "for crackle)."))
        return
    if args.fire_cmd == "disable":
        if apply_change(ctx, {"fire_wall": False},
                        "fire disable") is not None:
            print("Fireplace wallpaper off — the picture wallpaper "
                  "is back.")
        return
    if args.fire_cmd == "settings":
        fields = {}
        if args.sound is not None:
            fields["fire_sound"] = _onoff(args.sound)
        if args.wall_sound is not None:
            fields["fire_wall_sound"] = _onoff(args.wall_sound)
        if args.volume is not None:
            fields["fire_volume"] = args.volume
        if args.timer is not None:
            fields["fire_timer"] = args.timer
        if not fields:
            print(f"fire_sound={fmt(cfg.get('fire_sound'))} "
                  f"fire_wall_sound={fmt(cfg.get('fire_wall_sound'))} "
                  f"fire_volume={cfg.get('fire_volume')} "
                  f"fire_timer={cfg.get('fire_timer')}")
            print("The timer acts in the full-screen fireplace: "
                  "when it runs out the fire fades and the TV "
                  "sleeps. 0 = burns until closed.")
            return
        if apply_change(ctx, validate_change(fields),
                        "fire settings") is not None:
            print("Fireplace settings updated.")
        return
    raise CmtvError(f"unknown fire command {args.fire_cmd}")


def coerce_scalar(s):
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return s


def build_parser():
    ap = argparse.ArgumentParser(prog="cmtv",
                                 description="CleanMyTV Home controller")
    ap.add_argument("--mock", action="store_true",
                    help="use the mock TV (tests)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get")
    g.add_argument("--field")
    g.add_argument("--raw", action="store_true")
    s = sub.add_parser("set")
    s.add_argument("--json")
    s.add_argument("pairs", nargs="*")
    d = sub.add_parser("diff")
    d.add_argument("--snapshot")
    sub.add_parser("verify")
    un = sub.add_parser("undo")
    un.add_argument("--force", action="store_true",
                    help="overwrite external changes made after "
                         "cmtv's change")
    sub.add_parser("snapshots")
    a = sub.add_parser("apps", help="list the TV app catalog + placement")
    a.add_argument("--json", action="store_true")
    o = sub.add_parser("org", help="app organizer (order/quick/dock/"
                                   "hidden/folders)")
    osub = o.add_subparsers(dest="org_cmd", required=True)
    mv = osub.add_parser("move", help="move an app in the arranged order")
    mv.add_argument("name")
    mv.add_argument("--to", type=int)
    mv.add_argument("--first", action="store_true")
    mv.add_argument("--last", action="store_true")
    qa = osub.add_parser("quick-add")
    qa.add_argument("names", nargs="+")
    qr = osub.add_parser("quick-remove")
    qr.add_argument("name")
    osub.add_parser("dock", help="show the dock (first dock_size apps)")
    da = osub.add_parser("dock-add")
    da.add_argument("name")
    hd = osub.add_parser("hide")
    hd.add_argument("name")
    uh = osub.add_parser("unhide")
    uh.add_argument("name")
    fa = osub.add_parser("folder-add")
    fa.add_argument("folder")
    fa.add_argument("name")
    fr = osub.add_parser("folder-remove")
    fr.add_argument("folder")
    fr.add_argument("name")
    osub.add_parser("prune", help="drop config entries for uninstalled "
                                  "apps (explicit command only)")
    sl = sub.add_parser("slides", help="slideshow (upload/enable/disable)")
    ssub = sl.add_subparsers(dest="slides_cmd", required=True)
    ssub.add_parser("status")
    su = ssub.add_parser("upload")
    su.add_argument("files", nargs="+")
    su.add_argument("--replace", action="store_true")
    se = ssub.add_parser("enable")
    se.add_argument("--minutes", type=int, default=5)
    ssub.add_parser("disable")
    lk = sub.add_parser("looks", help="day/night looks")
    lsub = lk.add_subparsers(dest="looks_cmd", required=True)
    lsub.add_parser("status")
    for nm in ("save-day", "save-night"):
        sp = lsub.add_parser(nm)
        sp.add_argument("--force", action="store_true")
    sc = lsub.add_parser("schedule")
    sc.add_argument("--day", required=True)
    sc.add_argument("--night", required=True)
    lsub.add_parser("disable")
    pr = sub.add_parser("preset", help="local visual presets")
    psub = pr.add_subparsers(dest="preset_cmd", required=True)
    psub.add_parser("list")
    ps = psub.add_parser("save")
    ps.add_argument("name")
    ps.add_argument("--set", dest="sets", action="append")
    pp = psub.add_parser("preview")
    pp.add_argument("name")
    pa = psub.add_parser("apply")
    pa.add_argument("name")
    pa.add_argument("--force", action="store_true")
    pd = psub.add_parser("delete")
    pd.add_argument("name")
    th = sub.add_parser("theme", help="native CleanMyTV themes")
    tsub = th.add_subparsers(dest="theme_cmd", required=True)
    tsub.add_parser("status")
    tsub.add_parser("list")
    ts = tsub.add_parser("save")
    ts.add_argument("name")
    fr = sub.add_parser("fire", help="fireplace and ambient control")
    fsub = fr.add_subparsers(dest="fire_cmd", required=True)
    fsub.add_parser("status")
    fsub.add_parser("open", help="open the full-screen fireplace "
                                 "(experimental; focus-verified)")
    fe = fsub.add_parser("enable")
    fe.add_argument("--sound", action="store_true")
    fsub.add_parser("disable")
    fs = fsub.add_parser("settings")
    fs.add_argument("--sound", choices=["on", "off"])
    fs.add_argument("--wall-sound", choices=["on", "off"])
    fs.add_argument("--volume", type=int)
    fs.add_argument("--timer", type=int)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    mock = args.mock or os.environ.get("CMTV_MOCK") == "1"
    ctx = Ctx(mock)
    try:
        if args.cmd == "org":
            {"move": org_move, "quick-add": org_quick_add,
             "quick-remove": org_quick_remove, "dock": org_dock,
             "dock-add": org_dock_add,
             "hide": lambda c, a: org_hide(c, a, True),
             "unhide": lambda c, a: org_hide(c, a, False),
             "folder-add": lambda c, a: org_folder(c, a, True),
             "folder-remove": lambda c, a: org_folder(c, a, False),
             "prune": org_prune}[args.org_cmd](ctx, args)
        elif args.cmd == "slides":
            cmd_slides(ctx, args)
        elif args.cmd == "looks":
            cmd_looks(ctx, args)
        elif args.cmd == "preset":
            cmd_preset(ctx, args)
        elif args.cmd == "theme":
            cmd_theme(ctx, args)
        elif args.cmd == "fire":
            cmd_fire(ctx, args)
        else:
            {"get": cmd_get, "set": cmd_set, "diff": cmd_diff,
             "verify": cmd_verify, "undo": cmd_undo,
             "snapshots": cmd_snapshots, "apps": cmd_apps}[args.cmd](ctx,
                                                                     args)
    except CmtvError as e:
        print(f"cmtv: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
