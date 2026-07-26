"""Assemble a variant's full colour block and dump it to JSON for the Blender side.

Two sources, because no single one has everything:

  * the variant FGM (`variant_reader.py`) gives the key colour/tolerance/threshold/type, both
    rotations, both brightnesses and saturations, the palette height scale/offset/strength, and the
    seed and complexity;
  * `gradient_coefficients.json` gives the twelve gradient numbers, which are baked from
    (seed, complexity) CPU-side and exist nowhere in the game files -- they have to be harvested
    from a RenderDoc capture of the species actually on screen.

A variant is renderable in colour if its SEED has been harvested -- at any complexity. `gradOffset`,
`gradAmplitude` and `gradPhase` depend on the seed alone; complexity only selects `gradFreq`, and
that has a closed form (`freq_high`). So `coefficients_for` reuses a seed's numbers across
complexities and rebuilds the frequencies, which doubles coverage without new captures. Rows reached
that way are flagged `coeffExact: False` -- see `by_seed` for exactly how thin the evidence is.

    python export_palette.py Baryonyx 0
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import material_block as mb  # noqa: E402
import variant_reader as vr  # noqa: E402

import _paths  # noqa: E402  (vendored: data lives inside the package, not beside this file)
COEFFS = _paths.coeffs()
OUT = _paths.palettejson_dir()


def _s(v):
    return float(v[0]) if isinstance(v, (list, tuple)) else float(v)


# One harvested row is corrupt: its fields are shifted by a slot. Proof rather than suspicion --
# its `gradFreq` (20, 476, 497) is byte-for-byte the `gradPhase` of the seed-30 cx-1 row, its
# `gradAmplitude` is (307, 307, 307) where 307 is exactly the cx-5 high frequency, and its
# `gradOffset` contains negatives where every other row sits in 104..408.
CORRUPT_ROWS = {(30, 5)}

FREQ_LOW = 51           # = round(511 * 1/10)


def freq_high(complexity):
    """The 'fast' gradient frequency for a complexity, `min(511, round(511*(cx+1)/10))`.

    Fits all 63 channels of all 21 clean harvested rows: cx 1 -> 102, 2 -> 153, 3 -> 204,
    5 -> 307, 8 -> 460, 10 -> 511 (clamped, the unclamped value would be 562).
    """
    return min(511, int(round(511.0 * (complexity + 1) / 10.0)))


def available():
    """{(seed, complexity): row} for every clean harvested coefficient set."""
    return {(int(r["seed"]), int(r["complexity"])): r
            for r in json.load(open(COEFFS)).values()
            if (int(r["seed"]), int(r["complexity"])) not in CORRUPT_ROWS}


def by_seed():
    """{seed: row} -- coefficients keyed on the SEED ALONE, complexity dropped.

    WHY THIS IS LEGITIMATE. `gradOffset`, `gradAmplitude` and `gradPhase` are functions of the seed
    only; complexity feeds `gradFreq` and nothing else. Seed 30 was harvested at both complexity 1
    and complexity 2 and all nine of those numbers are identical between the two rows, while the
    frequencies differ exactly as `freq_high` predicts (102 vs 153).

    THE EVIDENCE IS ONE SEED OBSERVED TWICE. It is a strong fit but a thin sample, so anything
    rendered through this path is marked `coeffExact: False` and should be sanity-checked against
    the game before being trusted. It doubles usable coverage (10 -> 20 of 48 variants across the
    four species we have) without a single new capture, which is why it is worth having.
    """
    out = {}
    for (seed, cx), r in sorted(available().items()):
        out.setdefault(seed, r)
    return out


def coefficients_for(seed, complexity):
    """(row, exact) for a seed at an arbitrary complexity, or (None, False).

    `gradFreq` is rebuilt rather than reused: each channel keeps whichever of the two levels the
    harvested row chose -- that selection comes from the seed -- but the fast level is recomputed
    for the complexity being asked for.
    """
    exact = available().get((seed, complexity))
    if exact is not None:
        return exact, True
    row = by_seed().get(seed)
    if row is None:
        return None, False
    hi = freq_high(complexity)
    row = dict(row)
    row["gradFreq"] = [FREQ_LOW if f == FREQ_LOW else hi for f in row["gradFreq"]]
    return row, False


def block(species, variant, sex="Female", coeffs=None):
    """The decoded colour block for one variant, or a KeyError if its seed is not harvested.

    `coeffs` forces a specific harvested row -- useful for previewing what a species would look
    like under a palette we do have, which is NOT the same as rendering it accurately. Anything
    built that way must be labelled as a stand-in.
    """
    v = vr.read_all_variants(species, sex)[variant]
    seed = int(_s(v["u_globalPaletteSeed"]))
    cx = int(_s(v["u_globalPaletteMaximumComplexity"]))
    exact = True
    if coeffs is None:
        coeffs, exact = coefficients_for(seed, cx)
    got = coeffs is not None
    return {
        "species": species, "sex": sex, "variant": variant, "seed": seed, "complexity": cx,
        "keyColour": [float(x) for x in v["u_globalKeyColour"]],
        "keyTolerance": _s(v["u_globalKeyTolerance"]),
        "keyThreshold": _s(v["u_globalKeyThreshold"]),
        # NOT read from u_globalKeyType. That field is 0.0 in every variant of every species
        # checked, so it carries no information, while bit 16 of the uploaded block is SET in
        # every capture clean enough to read. The bit is what the shader branches on, and with it
        # clear the mask is inert (blend saturates to one value over the whole animal). See
        # material_block.key_blend. If a variant ever turns up whose FGM value is non-zero, this
        # is the line to revisit.
        "keyType": True,
        "brightnessBase": _s(v["u_globalColourBrightnessBase"]),
        "brightnessPalette": _s(v["u_globalColourBrightnessPalette"]),
        "saturationBase": _s(v["u_globalColourSaturationBase"]),
        "saturationPalette": _s(v["u_globalColourSaturationPalette"]),
        # the GPU gets a precomputed circulant matrix, not the rotation -- rebuild it the same way
        "hueMatrixBase": list(mb.hue_matrix_from_rotation(
            _s(v["u_globalColourRotationOffsetBase"]))),
        "hueMatrixPalette": list(mb.hue_matrix_from_rotation(
            _s(v["u_globalColourRotationOffsetPalette"]))),
        "instancePaletteScale": _s(v["u_instancePaletteScale"]),
        "instancePaletteOffset": _s(v["u_instancePaletteOffset"]),
        "paletteStrength": _s(v["u_instancePaletteStrength"]),
        "gradientEnabled": got,
        # False means the coefficients came from this seed harvested at a DIFFERENT complexity and
        # the frequencies were rebuilt by `coefficients_for`. See `by_seed` for how solid that is.
        "coeffExact": got and exact,
        "gradOffset": coeffs["gradOffset"] if got else [255, 255, 255],
        "gradAmplitude": coeffs["gradAmplitude"] if got else [0, 0, 0],
        "gradFreq": coeffs["gradFreq"] if got else [0, 0, 0],
        "gradPhase": coeffs["gradPhase"] if got else [0, 0, 0],
        "coeffSource": coeffs["from"] if got else None,
    }


def export(species, variant, sex="Female", coeffs=None, out_dir=OUT):
    os.makedirs(out_dir, exist_ok=True)
    b = block(species, variant, sex, coeffs)
    path = os.path.join(out_dir, f"{species}_{sex}_v{variant:02d}.json")
    with open(path, "w") as fh:
        json.dump(b, fh, indent=1)
    return path, b


def report(species, sex="Female"):
    """Which of a species' variants can be rendered in accurate colour, and which cannot."""
    have = available()
    print(f"{species} {sex}   ({len(have)} coefficient sets harvested)\n")
    for i, v in enumerate(vr.read_all_variants(species, sex)):
        seed = int(_s(v["u_globalPaletteSeed"]))
        cx = int(_s(v["u_globalPaletteMaximumComplexity"]))
        row, exact = coefficients_for(seed, cx)
        if row is None:
            mark = "no"
        elif exact:
            mark = "YES        " + row["from"][1]
        else:
            mark = f"reused cx{row['complexity']}  " + row["from"][1]
        print(f"  variant {i:>2}  seed {seed:>3} complexity {cx:>2}   {mark}")


def selftest():
    have = available()
    assert have, "gradient_coefficients.json is empty"
    # Baryonyx variant 0 is seed 36 complexity 2, which IS harvested -- the demo case
    b = block("Baryonyx", 0)
    assert b["gradientEnabled"], b
    assert (b["seed"], b["complexity"]) == (36, 2), (b["seed"], b["complexity"])
    assert b["coeffSource"][1] == "baryonyx_variant_01_00.fgm", b["coeffSource"]

    # a hue matrix is circulant, so its packed triple sums to 511 for ANY rotation
    for key in ("hueMatrixBase", "hueMatrixPalette"):
        assert abs(sum(b[key]) - 511) <= 2, (key, b[key])

    # every value the shader reads must be present and finite
    for k in ("keyTolerance", "keyThreshold", "brightnessBase", "saturationPalette",
              "instancePaletteScale", "paletteStrength"):
        assert math.isfinite(b[k]), (k, b[k])

    assert b["coeffExact"], "Baryonyx 0 is harvested at its own complexity"

    # an unharvested SEED must be reported honestly, not silently faked
    un = block("Lokiceratops", 0)                       # seed 0, held by no harvested row
    assert not un["gradientEnabled"] and un["coeffSource"] is None, un
    assert un["gradAmplitude"] == [0, 0, 0], "unharvested must flatten, not invent"
    assert not un["coeffExact"], un

    # ---- the closed form for gradFreq, fitted to all 63 clean harvested channels
    assert [freq_high(c) for c in (1, 2, 3, 5, 8, 10)] == [102, 153, 204, 307, 460, 511]
    assert freq_high(10) == 511, "cx 10 must clamp; the unclamped value is 562"
    for (seed, cx), row in available().items():
        for f in row["gradFreq"]:
            assert f in (FREQ_LOW, freq_high(cx)), (seed, cx, row["gradFreq"])

    # ---- the corrupt row must be excluded from every lookup
    assert (30, 5) not in available(), "the shifted seed-30 cx-5 row must stay quarantined"
    assert by_seed()[30]["complexity"] in (1, 2), by_seed()[30]

    # ---- seed-only reuse: Lokiceratops 1 is seed 54 cx 4, harvested only at cx 5
    reused = block("Lokiceratops", 1)
    assert reused["gradientEnabled"], "seed 54 is held, so this must now render"
    assert not reused["coeffExact"], "but it must be flagged as reconstructed"
    src = available()[(54, 5)]
    assert reused["gradOffset"] == src["gradOffset"], "offset is seed-only, carry it verbatim"
    assert reused["gradAmplitude"] == src["gradAmplitude"], "amplitude is seed-only"
    assert reused["gradPhase"] == src["gradPhase"], "phase is seed-only"
    # frequencies rebuilt for cx 4, keeping each channel's low/high selection
    want = [FREQ_LOW if f == FREQ_LOW else freq_high(4) for f in src["gradFreq"]]
    assert reused["gradFreq"] == want, (reused["gradFreq"], want)
    assert freq_high(4) != freq_high(5), "the rebuild must actually change something"

    # ---- and the evidence the whole re-keying rests on: seed 30 at two complexities
    a, c = available()[(30, 1)], available()[(30, 2)]
    for k in ("gradOffset", "gradAmplitude", "gradPhase"):
        assert a[k] == c[k], f"{k} must not depend on complexity"
    assert a["gradFreq"] != c["gradFreq"], "only the frequencies may differ"

    p, _ = export("Baryonyx", 0)
    assert json.load(open(p))["seed"] == 36
    print(f"selftest ok - {p}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--selftest" in sys.argv or not args:
        selftest()
    elif len(args) == 1:
        report(args[0])
    else:
        p, b = export(args[0], int(args[1]))
        print(f"seed {b['seed']} complexity {b['complexity']} "
              f"gradient {'HARVESTED' if b['gradientEnabled'] else 'NOT harvested'} -> {p}")
