r"""THE config. One file, one place, read by every tool here and by the Blender add-on.

Before this, each tool found things its own way -- `gen_seedsweep_v2.py` hard-coded
`C:\Program Files (x86)\Steam\...`, `reader_kit.py` hard-coded a different copy of the same path,
and the Blender add-on kept its own preference. On one machine that looks fine; on anyone else's it
is three separate things to fix, and they can disagree with each other silently.

So: **one config file**, set once (by `setup_gui.py`), read by everything.

    %LOCALAPPDATA%\JWE3VariantTools\jwe3_variant_tools.json      (Windows)
    ~/.jwe3_variant_tools/jwe3_variant_tools.json                (otherwise)

Precedence for every setting, highest first:

    1. environment variable   -- JWE3_GAME_DIR, JWE3_COBRA_TOOLS, JWE3_SWATCH_DIR
    2. the config file        -- what the setup GUI wrote
    3. auto-detection         -- Steam's registry + libraryfolders.vdf, the installed Blender
                                 add-on, folders beside this one

Auto-detection means the common case needs no setup at all; the config exists for when detection
cannot decide (two game installs) or cannot know (where you put the Swatch Library).

Run:  python jwe3_config.py            -> selftest ok
      python jwe3_config.py --status   -> what is currently resolved, and from where
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # the software's own folder

GAME_FOLDER = "Jurassic World Evolution 3"
STEAM_APPID = "2958130"

# Only things that genuinely live OUTSIDE the software are settings. Everything the tool needs of
# its own -- the node modules, the coefficient table, the LayerJSONs -- ships inside the package,
# so there is deliberately no "research folder" setting any more.
ENV = {
    "game_dir": "JWE3_GAME_DIR",
    "cobra_tools": "JWE3_COBRA_TOOLS",
    "swatch_dir": "JWE3_SWATCH_DIR",
}
KEYS = tuple(ENV)


# ---------------------------------------------------------------- config file
def config_dir():
    override = os.environ.get("JWE3_CONFIG_DIR")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "JWE3VariantTools")
    return os.path.join(os.path.expanduser("~"), ".jwe3_variant_tools")


def config_path():
    return os.path.join(config_dir(), "jwe3_variant_tools.json")


def read():
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def write(**values):
    """Merge settings into the config. Passing None for a key clears it."""
    cfg = read()
    for k, v in values.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    os.makedirs(config_dir(), exist_ok=True)
    with open(config_path(), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=1)
    return cfg


# ---------------------------------------------------------------- helpers
def _same(a, b):
    """Windows paths are case-insensitive -- the registry reports Steam's folder in two different
    cases, and comparing as plain strings offers the user the same install twice."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _uniq(seq, path):
    if path and not any(_same(path, s) for s in seq):
        seq.append(path)
    return seq


# ---------------------------------------------------------------- detection
def steam_libraries():
    """Every Steam library folder, on every drive, from Steam's own config."""
    roots, libs = [], []
    try:
        import winreg
        for hive, key, name in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(os.path.normpath(winreg.QueryValueEx(k, name)[0]))
            except OSError:
                continue
    except ImportError:
        pass
    roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]

    for root in roots:
        if os.path.isdir(root):
            _uniq(libs, root)
        try:
            with open(os.path.join(root, "steamapps", "libraryfolders.vdf"),
                      "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            p = os.path.normpath(m.group(1).replace("\\\\", "\\"))
            if os.path.isdir(p):
                _uniq(libs, p)
    return libs


def detect_game_dirs():
    """EVERY JWE3 `Win64\\ovldata` found, most recently updated first.

    A list, not one answer: moving a Steam library leaves the old copy behind, and staging into the
    install you are not playing looks exactly like the tool doing nothing.
    """
    found = []
    for lib in steam_libraries():
        ovl = os.path.join(lib, "steamapps", "common", GAME_FOLDER, "Win64", "ovldata")
        if os.path.isdir(ovl):
            _uniq(found, os.path.abspath(ovl))

    def updated(ovldata):
        exe = os.path.join(os.path.dirname(os.path.dirname(ovldata)), "JWE3.exe")
        try:
            return os.path.getmtime(exe)
        except OSError:
            return 0.0

    found.sort(key=updated, reverse=True)
    return found


def detect_cobra_tools():
    """A cobra-tools checkout: the installed Blender add-on, or a folder beside this one."""
    cands = []
    try:
        import addon_utils
        for mod in addon_utils.modules():
            name = (getattr(mod, "__name__", "") or "").lower()
            f = getattr(mod, "__file__", None)
            if f and ("cobra" in name or "ovl" in name):
                cands.append(os.path.dirname(os.path.abspath(f)))
    except Exception:
        pass
    up = HERE
    for _ in range(5):
        up = os.path.dirname(up)
        if not up:
            break
        cands += [os.path.join(up, "cobra-tools-master"), os.path.join(up, "cobra-tools")]
    for c in cands:
        if os.path.isdir(os.path.join(c, "generated", "formats", "fgm")) \
                and os.path.isdir(os.path.join(c, "modules", "formats")):
            return os.path.abspath(c)
    return None


def detect_swatch_dir():
    """The Swatch Library, if the user has put it somewhere we look. Never auto-extracted."""
    for c in (os.path.join(config_dir(), "SwatchLibrary"),
              os.path.join(HERE, "SwatchLibrary")):
        if os.path.isdir(c) and any(f.lower().endswith(".png") for f in os.listdir(c)):
            return os.path.abspath(c)
    return None


DETECTORS = {
    "game_dir": lambda: (detect_game_dirs() or [None])[0],
    "cobra_tools": detect_cobra_tools,
    "swatch_dir": detect_swatch_dir,
}


# ---------------------------------------------------------------- the API
def get(key, required=False):
    """Resolve one setting: env, then config, then detection. None if unknown."""
    if key not in KEYS:
        raise KeyError("unknown setting %r (expected one of %s)" % (key, ", ".join(KEYS)))
    value = os.environ.get(ENV[key]) or read().get(key)
    if value and os.path.isdir(value):
        return os.path.abspath(value)
    detected = DETECTORS[key]()
    if detected:
        return detected
    if required:
        raise RuntimeError(
            "%s is not set and could not be detected.\nRun the setup tool:  python setup_gui.py\n"
            "or set the %s environment variable." % (key, ENV[key]))
    return None


def source(key):
    """Where the current value came from -- 'environment', 'config', 'detected' or 'missing'."""
    if os.environ.get(ENV[key]) and os.path.isdir(os.environ[ENV[key]]):
        return "environment"
    v = read().get(key)
    if v and os.path.isdir(v):
        return "config"
    return "detected" if DETECTORS[key]() else "missing"


def status():
    return {k: {"value": get(k), "source": source(k)} for k in KEYS}


def status_text():
    lines = ["config: %s%s" % (config_path(), "" if os.path.isfile(config_path()) else "  (not written yet)")]
    for k, info in status().items():
        lines.append("  %-13s %-10s %s" % (k, "[%s]" % info["source"], info["value"] or "-- not found --"))
    games = detect_game_dirs()
    if len(games) > 1:
        lines.append("\n  NOTE: %d game installs found -- run `python setup_gui.py` to choose:" % len(games))
        lines += ["    " + g for g in games]
    return "\n".join(lines)


def selftest():
    import tempfile
    old_dir = os.environ.get("JWE3_CONFIG_DIR")
    os.environ["JWE3_CONFIG_DIR"] = tempfile.mkdtemp()
    try:
        assert read() == {}, "a fresh config folder must start empty"
        write(game_dir="X:\\nope")
        assert read()["game_dir"] == "X:\\nope"
        write(game_dir=None)
        assert "game_dir" not in read(), "None must clear a key"

        # a stored path that does not exist must NOT win over detection
        write(game_dir="X:\\definitely\\not\\here")
        assert get("game_dir") != "X:\\definitely\\not\\here"

        # env beats config, and only when it is real
        real = tempfile.mkdtemp()
        os.environ[ENV["game_dir"]] = real
        assert get("game_dir") == os.path.abspath(real), get("game_dir")
        assert source("game_dir") == "environment"
        os.environ.pop(ENV["game_dir"], None)

        write(game_dir=real)
        assert get("game_dir") == os.path.abspath(real)
        assert source("game_dir") == "config"
        write(game_dir=None)

        assert set(KEYS) == {"game_dir", "cobra_tools", "swatch_dir"}, KEYS
        try:
            get("nonsense")
        except KeyError:
            pass
        else:
            raise AssertionError("unknown key should raise")

        # detection: whatever it finds must be real, and free of case-duplicates
        games = detect_game_dirs()
        for g in games:
            assert os.path.isdir(g) and os.path.basename(g).lower() == "ovldata", g
        norm = [os.path.normcase(g) for g in games]
        assert len(norm) == len(set(norm)), games
        libs = [os.path.normcase(p) for p in steam_libraries()]
        assert len(libs) == len(set(libs)), steam_libraries()

        ct = detect_cobra_tools()
        if ct:
            assert os.path.isdir(os.path.join(ct, "generated", "formats", "fgm")), ct
        assert isinstance(status_text(), str) and "config:" in status_text()
    finally:
        if old_dir is None:
            os.environ.pop("JWE3_CONFIG_DIR", None)
        else:
            os.environ["JWE3_CONFIG_DIR"] = old_dir
    print("selftest ok")


if __name__ == "__main__":
    if "--status" in sys.argv:
        print(status_text())
    else:
        selftest()
