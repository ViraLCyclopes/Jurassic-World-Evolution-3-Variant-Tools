"""Where the vendored modules find their data. Everything lives inside the software's own folder.

These modules came from the Variant Research folder, where each one resolved its data relative to
its own location and two of them hard-coded absolute paths to one developer's machine. Vendored into
the package they must not reach outside it, so every path they need is resolved here:

    ../data/        shipped data (gradient_coefficients.json, swatch_params.json) -- our own
                    measurements, safe to distribute
    ../SwatchLibrary/   game textures the USER supplies (see PLACE_SWATCH_LIBRARY_HERE.md)
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
    return _config("swatch_dir") or os.path.join(PKG, "SwatchLibrary")


def layerjson_dir():
    d = os.path.join(PKG, "LayerJSON")
    os.makedirs(d, exist_ok=True)
    return d


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
    for d in (layerjson_dir(), palettejson_dir()):
        assert os.path.isdir(d), d
    # every resolved path must stay INSIDE the package, except the two that legitimately do not
    for p in (coeffs(), swatch_params(), layerjson_dir(), palettejson_dir()):
        assert os.path.normcase(os.path.abspath(p)).startswith(
            os.path.normcase(os.path.abspath(PKG))), p
    print("selftest ok")


if __name__ == "__main__":
    selftest()
