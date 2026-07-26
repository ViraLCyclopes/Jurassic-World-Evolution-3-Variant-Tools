"""Build the installable Blender add-on zip.

    python build_addon.py                 -> jwe3_variant_tools.zip beside this folder
    python build_addon.py --out <path>    -> somewhere else

The add-on is a FOLDER, not a file. Installing a lone `blender_listener.py` copies one file into
Blender's addons folder where none of its siblings exist, and it fails on the first import -- so the
zip contains the whole package under a single top-level `jwe3_variant_tools/` directory, which is
what Blender expects to unpack.

WHAT IS AND IS NOT INCLUDED. Code, plus our own derived data (`data/gradient_coefficients.json`,
`data/swatch_params.json` -- measurements this project produced). NOT game assets: the Swatch
Library and per-species textures are Frontier's, so those folders ship empty with a README telling
the user to supply them. That keeps the zip a few hundred KB instead of 45 MB, and keeps it legal to
redistribute.

Run:  python build_addon.py --selftest   -> selftest ok
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
# The folder inside the zip becomes Blender's module name for the add-on. Hyphens are fine there --
# Blender loads add-ons with `importlib.import_module(<folder name>)`, a string, so a GitHub source
# zip (`...-main/`) installs perfectly well, exactly like cobra-tools-master does. This build just
# produces a tidier, smaller zip without the repo scaffolding.
PKG_NAME = "VariantEditor"

# Anything not matched here is skipped, so a stray capture or a 45 MB texture dump can never be
# swept into a release by accident.
INCLUDE_EXT = (".py", ".json", ".md", ".txt")
SKIP_DIRS = {"__pycache__", ".git", "Build", "PaletteJSON", "Generated"}
SKIP_FILES = {"build_addon.py"}
# Folders that ship EMPTY (their README only): the user supplies the game assets.
EMPTY_ONLY = {"SwatchLibrary", "Textures"}


def files_to_ship(root=HERE):
    """[(absolute path, path inside the zip)] -- exactly what a release contains."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        top = rel_dir.split(os.sep)[0] if rel_dir else ""
        for name in sorted(filenames):
            if name in SKIP_FILES or not name.lower().endswith(INCLUDE_EXT):
                continue
            if top in EMPTY_ONLY and not name.lower().endswith(".md"):
                continue                     # keep the README, drop any assets the user put there
            src = os.path.join(dirpath, name)
            out.append((src, os.path.join(PKG_NAME, rel_dir, name).replace(os.sep, "/")))
    return out


def build(out_path=None):
    out_path = out_path or os.path.join(os.path.dirname(HERE), PKG_NAME + ".zip")
    entries = files_to_ship()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in entries:
            z.write(src, arc)
    return out_path, entries


def selftest():
    entries = files_to_ship()
    arcs = [a for _s, a in entries]
    assert arcs, "nothing would be shipped"
    assert all(a.startswith(PKG_NAME + "/") for a in arcs), "everything must sit under one folder"

    # the pieces without which the add-on cannot work
    for required in ("__init__.py", "blender_listener.py", "preview_assets.py", "fgm_io.py",
                     "coeff_store.py", "vendor/blender_layer_nodes.py", "vendor/_paths.py",
                     "data/gradient_coefficients.json", "data/swatch_params.json"):
        assert PKG_NAME + "/" + required in arcs, "missing from the zip: " + required

    # game assets must NEVER be shipped -- only the READMEs from those folders
    for src, arc in entries:
        top = arc.split("/")[1] if arc.count("/") > 1 else ""
        if top in EMPTY_ONLY:
            assert arc.lower().endswith(".md"), "would ship game assets: " + arc
    assert not any(a.lower().endswith((".png", ".dds", ".tex", ".ovl", ".rdc")) for a in arcs)
    assert not any("__pycache__" in a for a in arcs)
    assert not any(a.endswith("build_addon.py") for a in arcs)

    # a real build must be small and readable back
    import tempfile
    out = os.path.join(tempfile.mkdtemp(), PKG_NAME + ".zip")
    path, shipped = build(out)
    size = os.path.getsize(path)
    assert size < 5_000_000, "release is %.1f MB -- something large slipped in" % (size / 1e6)
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        assert PKG_NAME + "/__init__.py" in z.namelist()
    print("selftest ok  (%d files, %.0f KB)" % (len(shipped), size / 1024))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        dest = None
        if "--out" in sys.argv:
            dest = sys.argv[sys.argv.index("--out") + 1]
        path, entries = build(dest)
        print("built %s\n  %d files, %.0f KB" % (path, len(entries), os.path.getsize(path) / 1024))
        print("Install in Blender: Preferences > Add-ons > v > Install from Disk... > this zip")
