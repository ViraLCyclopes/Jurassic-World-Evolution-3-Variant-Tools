"""Dump a pattern FGM plus its baked LUT to JSON, so the Blender side never imports cobra-tools.

Mirrors export_palette.py. numpy arrays are converted with .tolist() -- json.dump refuses numpy
scalars, and a leaked one fails at write time with an unhelpful message.

`threshold` carries the species' "no pattern" level recovered from the opacity curve (PATTERNS.md
4.6), so the Blender side can warn about unreachable keys without needing the pattern map.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (HERE, PKG):
    if p not in sys.path:
        sys.path.insert(0, p)

import _paths
import pattern_lut
from pattern_io import load_pattern_fgm


def export(fgm_path, out_path=None, interp="linear"):
    model = load_pattern_fgm(fgm_path)
    lut = pattern_lut.bake(model, interp=interp)
    idx, byte = pattern_lut.threshold_from_model(model, interp=interp)
    data = {
        "source": os.path.basename(fgm_path),
        "model": model.to_dict(),
        "lut": {k: v.tolist() for k, v in lut.items()},
        "interp": interp,
        "threshold": {"index": float(idx), "byte": int(byte)},
    }
    if out_path is None:
        base = os.path.splitext(os.path.basename(fgm_path))[0]
        out_path = os.path.join(_paths.palettejson_dir(), base + ".pattern.json")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    return data


def selftest():
    import tempfile
    sample = os.environ.get("JWE3_SAMPLE_PATTERN_FGM")
    if not sample or not os.path.isfile(sample):
        raise SystemExit("selftest needs JWE3_SAMPLE_PATTERN_FGM set to a real pattern FGM")

    out = os.path.join(tempfile.mkdtemp(), "p.json")
    d = export(sample, out)
    assert d["source"] == os.path.basename(sample)
    assert set(d["lut"]) == {"colour", "emissive", "opacity"}
    assert len(d["lut"]["colour"]) == 32 and len(d["lut"]["colour"][0]) == 3
    assert len(d["lut"]["opacity"]) == 32 and len(d["lut"]["opacity"][0]) == 1
    assert d["interp"] == "linear"
    assert 0 <= d["threshold"]["index"] <= 31 and 0 <= d["threshold"]["byte"] <= 255

    # it must be JSON-clean: no numpy scalars, which json.dump refuses
    with open(out) as f:
        on_disk = json.load(f)
    assert on_disk == d, "written JSON differs from the returned dict"
    assert all(isinstance(c, float) for c in on_disk["lut"]["colour"][0]), "numpy leaked into JSON"
    assert isinstance(on_disk["threshold"]["byte"], int)

    # the model survives the crossing intact
    assert on_disk["model"] == load_pattern_fgm(sample).to_dict()

    # the default output path lands inside the package, never outside it
    d2 = export(sample)
    default = os.path.join(_paths.palettejson_dir(),
                           os.path.splitext(os.path.basename(sample))[0] + ".pattern.json")
    assert os.path.isfile(default), default
    assert os.path.normcase(os.path.abspath(default)).startswith(
        os.path.normcase(os.path.abspath(PKG))), "wrote outside the package"
    assert d2["source"] == d["source"]
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(export(sys.argv[1])["source"])
    else:
        selftest()
