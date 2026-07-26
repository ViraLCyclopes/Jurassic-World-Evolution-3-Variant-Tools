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


def export(species, sex="Female", out_dir=OUT):
    os.makedirs(out_dir, exist_ok=True)
    layers = layer_chain.resolve(species, sex)
    path = os.path.join(out_dir, f"{species}_{sex}.json")
    with open(path, "w") as fh:
        json.dump({"species": species, "sex": sex, "layers": layers}, fh, indent=1)
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


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--selftest" in sys.argv or not args:
        selftest()
    for sp in args:
        p, L = export(sp)
        print(f"{sp}: {sum(1 for x in L if x['used'])}/16 layers used -> {p}")
