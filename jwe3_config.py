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
    "fur_library": "JWE3_FUR_LIBRARY",
}
KEYS = tuple(ENV)

# Settings that may name SEVERAL folders, separated by os.pathsep (';' on Windows, ':' elsewhere).
#
# Both are libraries of game textures the user extracts themselves, and there is no reason they
# should live in one place: people keep a DinosaurFur dump per game version, or split the Swatch
# Library across drives. `get()` still returns a single folder (the first that exists) so every
# existing caller keeps working; `get_dirs()` returns all of them, in order, for the lookups that
# should search more than one.
MULTI = ("swatch_dir", "fur_library")


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


# ---------------------------------------------------------------- the texture folder
#
# ONE folder, repointed as you go -- not a per-species map. You work on one species at a time, so a
# single setting the editor can change in two clicks beats a registry of paths that has to be kept
# in step with a species list.
#
# This replaces the packaged `Textures/<Species>` folder, which required copying large extracted
# texture sets INTO the install: lost on every reinstall, impossible to share, and ~450 MB of game
# data sitting in the source tree. That folder ships EMPTY and remains only as a legacy fallback --
# see `preview_assets.mask_dir_for`.
#
# Kept out of `KEYS` deliberately: KEYS is what `setup_gui` renders as a fixed one-time setup form,
# and this one is changed constantly from the editor instead.

def textures_dir():
    """The configured texture folder, or None if unset or no longer on disk.

    The `isdir` check means a folder that has been moved or unplugged reports as unset rather than
    resolving to a dead path -- the caller then falls back cleanly instead of hunting for masks in
    a directory that is not there.
    """
    v = read().get("textures_dir")
    return str(v) if v and os.path.isdir(str(v)) else None


def set_textures_dir(path):
    """Set the texture folder. `path` None or empty clears it. Returns the stored value."""
    v = os.path.abspath(str(path)) if path else None
    write(textures_dir=v)
    return v


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
              # NOTE: no `HERE/SwatchLibrary` candidate. The software ships no game textures, so
              # that folder does not exist; probing it only ever produced a dead path.
              ):
        if os.path.isdir(c) and any(f.lower().endswith(".png") for f in os.listdir(c)):
            return os.path.abspath(c)
    return None


def detect_fur_library():
    """The shared `DinosaurFur` card-texture folder, if it sits somewhere we look.

    Not auto-extracted, same as the Swatch Library. The usual case needs nothing set at all:
    `part_manifest.fur_library_dirs` finds `DinosaurFur` by walking UP from the species folder,
    which is where an extraction naturally puts it. This is for when it lives elsewhere.
    """
    for c in (os.path.join(config_dir(), "DinosaurFur"),
              os.path.join(HERE, "DinosaurFur")):
        if os.path.isdir(c):
            return os.path.abspath(c)
    return None


DETECTORS = {
    "game_dir": lambda: (detect_game_dirs() or [None])[0],
    "cobra_tools": detect_cobra_tools,
    "swatch_dir": detect_swatch_dir,
    "fur_library": detect_fur_library,
}


# ---------------------------------------------------------------- the API
def get_dirs(key):
    """EVERY folder configured for `key`, in order, existing ones only.

    For a `MULTI` setting the env var and the config value may each list several folders separated
    by `os.pathsep`; the detected folder is appended last so a user-supplied path always wins.
    Duplicates are dropped, case-insensitively on Windows.
    """
    if key not in KEYS:
        raise KeyError("unknown setting %r (expected one of %s)" % (key, ", ".join(KEYS)))
    raw = []
    for src in (os.environ.get(ENV[key]), read().get(key)):
        if not src:
            continue
        raw.extend(src.split(os.pathsep) if key in MULTI else [src])
    detected = DETECTORS[key]()
    if detected:
        raw.append(detected)
    out, seen = [], set()
    for p in raw:
        p = (p or "").strip()
        if not p or not os.path.isdir(p):
            continue
        ap = os.path.abspath(p)
        k = os.path.normcase(ap)
        if k not in seen:
            seen.add(k)
            out.append(ap)
    return out


def get(key, required=False):
    """Resolve one setting: env, then config, then detection. None if unknown.

    For a `MULTI` setting this is the FIRST configured folder -- callers that should search all of
    them use `get_dirs`.
    """
    if key not in KEYS:
        raise KeyError("unknown setting %r (expected one of %s)" % (key, ", ".join(KEYS)))
    if key in MULTI:
        dirs = get_dirs(key)
        if dirs:
            return dirs[0]
        if required:
            raise RuntimeError(
                "%s is not set and could not be detected.\nRun the setup tool:  python setup_gui.py\n"
                "or set the %s environment variable." % (key, ENV[key]))
        return None
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
    """Where the current value came from -- 'environment', 'config', 'detected' or 'missing'.

    A MULTI setting may list several folders, so test each entry rather than the raw string --
    `os.path.isdir("A;B")` is False and would have reported a perfectly good pair as 'missing'.
    """
    def any_dir(v):
        if not v:
            return False
        parts = v.split(os.pathsep) if key in MULTI else [v]
        return any(p.strip() and os.path.isdir(p.strip()) for p in parts)

    if any_dir(os.environ.get(ENV[key])):
        return "environment"
    if any_dir(read().get(key)):
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

        assert set(KEYS) == {"game_dir", "cobra_tools", "swatch_dir", "fur_library"}, KEYS
        assert set(MULTI) <= set(KEYS) and set(MULTI) == {"swatch_dir", "fur_library"}, MULTI

        # --- MULTI settings: several folders, os.pathsep-separated, in order, existing only.
        #     `get` must keep returning ONE folder so every existing caller is unaffected.
        a, b = os.path.abspath(HERE), os.path.abspath(config_dir())
        os.makedirs(b, exist_ok=True)
        old_env = os.environ.get(ENV["fur_library"])
        try:
            os.environ[ENV["fur_library"]] = os.pathsep.join([a, "Z:/does-not-exist", b])
            dirs = get_dirs("fur_library")
            assert dirs[:2] == [a, b], dirs          # bogus entry dropped, order kept
            assert get("fur_library") == a, get("fur_library")
            assert source("fur_library") == "environment"
            # a duplicate, differing only by case, must not appear twice
            os.environ[ENV["fur_library"]] = os.pathsep.join([a, a.upper()])
            assert len(get_dirs("fur_library")) == 1, get_dirs("fur_library")
            # every entry bogus -> nothing configured, and `source` must not claim otherwise
            os.environ[ENV["fur_library"]] = os.pathsep.join(["Z:/nope", "Z:/also-nope"])
            assert get_dirs("fur_library") == [] or all(os.path.isdir(d)
                                                        for d in get_dirs("fur_library"))
            assert source("fur_library") != "environment"
        finally:
            if old_env is None:
                os.environ.pop(ENV["fur_library"], None)
            else:
                os.environ[ENV["fur_library"]] = old_env
        # a single-valued setting must NOT be split on os.pathsep
        assert "game_dir" not in MULTI
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
