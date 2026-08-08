"""Where the vendored modules find their data. Everything lives inside the software's own folder.

These modules came from the Variant Research folder, where each one resolved its data relative to
its own location and two of them hard-coded absolute paths to one developer's machine. Vendored into
the package they must not reach outside it, so every path they need is resolved here:

    ../data/        shipped data (gradient_coefficients.json, swatch_params.json) -- our own
                    measurements, safe to distribute
    (swatches)      game textures the USER supplies, from a folder they configure -- NOT packaged.
                    Set it in `setup_gui.py` (Swatch Library) or via JWE3_SWATCH_DIR.
    ../LayerJSON/   generated on demand, written inside the package
    ../PaletteJSON/ likewise

The two things that genuinely live outside -- the game install and cobra-tools -- come from the
shared config (`jwe3_config`), which auto-detects both and is set once via `setup_gui.py`.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # .../VariantEditor/vendor
PKG = os.path.dirname(HERE)                             # .../VariantEditor
DATA = os.path.join(PKG, "data")


def _config(key):
    if PKG not in sys.path:
        sys.path.insert(0, PKG)
    try:
        import jwe3_config
        return jwe3_config.get(key)
    except Exception:
        return None


def data_file(name):
    return os.path.join(DATA, name)


def coeffs():
    """The shipped coefficient table. The layered user table is `coeff_store`'s business."""
    return data_file("gradient_coefficients.json")


def swatch_params():
    return data_file("swatch_params.json")


def swatch_dir():
    """The user's Swatch Library -- config first, then the folder shipped (empty) in the package."""
    # No packaged fallback. The software ships NO game textures, so `PKG/SwatchLibrary` does not
    # exist -- returning it produced a path that could never resolve and an error that blamed a
    # missing swatch instead of a missing setting. None means "not configured": say so.
    return _config("swatch_dir")


def shipped_layerjson_dir():
    """The LayerJSONs that ship INSIDE the package. Read-only baseline, always searched last."""
    d = os.path.join(PKG, "LayerJSON")
    os.makedirs(d, exist_ok=True)
    return d


def layerjson_dir():
    """Where a NEWLY generated LayerJSON is written -- the per-user folder, not the package.

    Writing into the package was the old behaviour and it does not survive a reinstall: the Blender
    add-on is a COPY of this folder, so a species generated after the last `build_addon.py` existed
    only in the source tree and the add-on could not see it. See `jwe3_config.detect_layerjson_dir`
    for what that silently did to the material.
    """
    d = _config("layerjson_dir")
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except OSError:
            pass
    return shipped_layerjson_dir()


def layerjson_dirs():
    """Every folder to SEARCH for LayerJSONs, highest priority first.

    User folders win over the shipped ones, so regenerating a species overrides what we ship
    without having to delete anything.
    """
    dirs = []
    if PKG not in sys.path:
        sys.path.insert(0, PKG)
    try:
        import jwe3_config
        dirs.extend(jwe3_config.get_dirs("layerjson_dir"))
    except Exception:
        pass
    dirs.append(shipped_layerjson_dir())
    out, seen = [], set()
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        k = os.path.normcase(os.path.abspath(d))
        if k not in seen:
            seen.add(k)
            out.append(os.path.abspath(d))
    return out


def palettejson_dir():
    d = os.path.join(PKG, "PaletteJSON")
    os.makedirs(d, exist_ok=True)
    return d


def cobra_tools(required=True):
    """The cobra-tools checkout. Not bundled: it is a large GPL project the user already installs
    as a Blender add-on, which is where this normally finds it."""
    repo = _config("cobra_tools")
    if not repo and required:
        raise RuntimeError(
            "cobra-tools not found. Install it in Blender, or run `python setup_gui.py` and set it.")
    return repo


def game_content():
    """`...\\Win64\\ovldata\\Content0` for the install being modded, or None."""
    ovldata = _config("game_dir")
    return os.path.join(ovldata, "Content0") if ovldata else None


def selftest():
    assert os.path.isdir(DATA), DATA
    assert os.path.isfile(coeffs()), coeffs()
    assert os.path.isfile(swatch_params()), swatch_params()
    for d in (layerjson_dir(), palettejson_dir(), shipped_layerjson_dir()):
        assert os.path.isdir(d), d
    # every resolved path must stay INSIDE the package, except the ones that legitimately do not.
    # layerjson_dir() is now deliberately OUTSIDE it -- generated LayerJSONs are user data and a
    # reinstall replaces the install, taking them with it.
    for p in (coeffs(), swatch_params(), palettejson_dir(), shipped_layerjson_dir()):
        assert os.path.normcase(os.path.abspath(p)).startswith(
            os.path.normcase(os.path.abspath(PKG))), p
    # the shipped folder must always be searched, and never be the FIRST choice when a user
    # folder is configured -- otherwise a regenerated species cannot override what we ship
    dirs = layerjson_dirs()
    assert dirs, dirs
    assert os.path.normcase(dirs[-1]) == os.path.normcase(shipped_layerjson_dir()), dirs
    print("selftest ok")


if __name__ == "__main__":
    selftest()
