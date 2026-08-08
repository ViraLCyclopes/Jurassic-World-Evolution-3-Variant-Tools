"""Dump a species' resolved layer chain to JSON so Blender can read it.

Blender has cobra-tools installed as an add-on, but `reader_kit` opens the pristine OVLs from this
folder and that pulls a second copy of the library into Blender's interpreter. Keeping the read on
this side and handing Blender a plain JSON file avoids the clash and makes the Blender script
runnable without the game data present.

    python export_layers.py Lokiceratops Spinosaurus
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layer_chain  # noqa: E402

import _paths  # noqa: E402  (vendored: generated output stays inside the package)
OUT = _paths.layerjson_dir()


def export_from_folder(folder, species, sex="Female", out_dir=OUT, aliases=(), filename=None):
    """Write a LayerJSON from a folder YOU extracted -- no game install needed.

    PREFERRED over `export()`: it works for modded, hybrid and custom species, and does not depend
    on the game's OVL layout (which varies -- some species' OVLs carry no layer definition at all).

    THE FILENAME IS NOT SIGNIFICANT. `preview_assets.layers_json_for` matches on the `species` and
    `aliases` fields INSIDE the file, so call it whatever you like via `filename`. `aliases` lets
    one file answer to several names -- useful when the editor's species box shows something other
    than the folder name, which is the usual case for hybrids.
    """
    os.makedirs(out_dir, exist_ok=True)
    layers = layer_chain.resolve_from_folder(folder, sex)
    path = os.path.join(out_dir, filename or f"{species}_{sex}.json")
    with open(path, "w") as fh:
        json.dump({"species": species, "sex": sex, "aliases": [str(a) for a in aliases],
                   "source": folder, "layers": layers}, fh, indent=1)
    return path, layers


def species_folder(species, sex="Female"):
    """The EXTRACTED folder for a species, or None. Config first, no guessing beyond it.

    `jwe3_config`'s `species_dirs` maps a species to the folder you extracted it to. Some species
    are sexless (IndominusRex has no `Female/` subfolder), so `<base>/<sex>` and `<base>` are both
    tried -- and the folder only counts if it actually contains a layers file, which is what stops
    a stale or wrong mapping being reported as a success.
    """
    try:
        import jwe3_config
    except ImportError:
        return None
    # read(), not get(): `species_dirs` is not in get()'s whitelist -- nothing else consumes it.
    base = (jwe3_config.read().get("species_dirs") or {}).get(species)
    if not base:
        return None
    key = species.replace("_", "").lower()
    for cand in (os.path.join(base, sex), base):
        try:
            if not os.path.isdir(cand):
                continue
            found = layer_chain.find_layers_file(cand, sex)
            if not found:
                continue
            # SANITY-CHECK THE MAPPING. A stale species_dirs entry points at another animal's
            # folder -- the live config has had `Baryonyx` -> the IndominusRex folder -- and that
            # folder has a perfectly good layers file, so "it resolved" proves nothing. Requiring
            # the species' own name in the layers filename turns a silent wrong-animal export into
            # a fall back to the OVL route.
            if key not in os.path.basename(found).replace("_", "").lower():
                continue
            return cand
        except Exception:
            continue
    return None


def export(species, sex="Female", out_dir=OUT, aliases=(), filename=None):
    """Write a species' LayerJSON. EXTRACTED FOLDER FIRST, game OVLs only as a fallback.

    The OVL route builds one exact path (`pristine(species, sex)/<Species>_<Sex>.ovl`) and reads
    the layers out of it. That works for stock species laid out the way the game ships them and
    fails for everything else -- hybrids, mods, renamed or sexless species -- which is why
    `export_from_folder` existed as a manual escape hatch. Preferring the folder makes the escape
    hatch the normal path and leaves the OVL route for anyone with no extraction on disk.
    """
    folder = species_folder(species, sex)
    if folder:
        return export_from_folder(folder, species, sex, out_dir, aliases, filename)
    os.makedirs(out_dir, exist_ok=True)
    layers = layer_chain.resolve(species, sex)
    path = os.path.join(out_dir, filename or f"{species}_{sex}.json")
    with open(path, "w") as fh:
        json.dump({"species": species, "sex": sex, "aliases": [str(a) for a in aliases],
                   "source": "ovl", "layers": layers}, fh, indent=1)
    return path, layers


def selftest():
    path, layers = export("Lokiceratops")
    got = json.load(open(path))
    assert got["species"] == "Lokiceratops"
    assert len(got["layers"]) == 16
    # the JSON must survive the round trip with the fields the Blender side needs
    for L in got["layers"]:
        for key in ("layer_no", "swatch", "used", "slices", "blend_texture",
                    "blend_channel", "params"):
            assert key in L, (key, L)
    assert got["layers"][0]["params"]["pUVTile"][0] > 25.0
    print(f"selftest ok - {path}")


def species_sex_from_path(folder):
    """('Herrerasaurus', 'Female') from an extracted folder path.

    Extractions are `<...>/<Species>/<Sex>` for split species and `<...>/<Species>` for sexless
    ones (IndominusRex has no Female/ subfolder). So a trailing Female/Male/Juvenile names the SEX
    and the species is its parent; anything else IS the species, defaulting to Female -- which is
    only a label in the filename and the `sex` field, since lookup matches on `species`.
    """
    folder = os.path.normpath(folder).rstrip(os.sep)
    base = os.path.basename(folder)
    if base.lower() in ("female", "male", "juvenile"):
        return os.path.basename(os.path.dirname(folder)), base.capitalize()
    return base, "Female"


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--selftest" in sys.argv or not args:
        selftest()
        sys.exit(0)

    # ACCEPT A PASTED FOLDER PATH, not just a species name. Pasting the extraction path is how
    # this is actually used, and it is the only route that works for hybrids, mods, renamed and
    # sexless species -- the species-name route has to resolve a folder or fall back to the game's
    # own OVL layout. A path is unambiguous, so it wins whenever the argument is a directory.
    for arg in args:
        if os.path.isdir(arg):
            species, sex = species_sex_from_path(arg)
            path, layers = export_from_folder(arg, species, sex)
            print("%s  (%s %s, %d layers, from folder)"
                  % (path, species, sex, len(layers)))
        else:
            path, layers = export(arg)
            print("%s  (%s, %d layers)" % (path, arg, len(layers)))
        print("   %d/16 layers used" % sum(1 for x in layers if x.get("used")))
