"""Predict a variant's colours in pure Python, so the editor can show them without Blender.

This is a transcription of the shader graph `blender_palette_nodes.palette_group` builds -- the same
maths, evaluated on numbers instead of wired into nodes. That means the editor can draw a live
colour ramp as you drag a slider with no Blender running at all, and the Blender viewport stays the
authority for how it lands on the actual model.

WHAT IT CAN AND CANNOT KNOW. The final pixel colour depends on the dinosaur's own albedo texture,
which this module does not have. So it evaluates the pipeline against a NEUTRAL mid-grey albedo:
what you see is the palette's own colour, not the finished skin. Read it as "what hue/brightness is
this variant pushing", not "this is my dinosaur". Two consequences worth remembering:

  * the ramp is honest about the grade (brightness/saturation/hue/strength) -- those act on any
    albedo the same way;
  * for a seed with no harvested coefficients the gradient is flat, so the ramp collapses to one
    colour. That is the truth about the preview, not a bug (in game the gradient is still there).

The pipeline, in the order the node graph applies it:

    grade(c)  = hue_matrix @ (c * brightness), then saturation toward its REC709 luma
    graded    = mix(keyBlend, gradeBase(albedo), gradePalette(albedo))
    out       = mix(colourWeight, albedo, graded)
    gradient  = cos-palette over t = height*100*paletteScale + paletteOffset   (per channel)
    grey      = mix(colourWeight * paletteStrength * keyBlend, 0.5, gradient)
    result    = overlay(saturate(out - 1/255), grey)

Run:  python palette_preview.py   -> selftest ok
"""
import math

REC709 = (0.2126, 0.7152, 0.0722)
S10 = 511.0                     # the 10-bit fixed-point scale the packed block values use

# Reference albedo: 18% linear grey, the standard photographic mid-grey, NOT 0.5.
# 0.5 was wrong and visibly so -- shipped variants routinely carry brightness 1.5-2.0, which drives
# a 0.5 albedo straight to 1.0 and the whole ramp renders solid white (seen on Baryonyx v00:
# brightnessPalette 2.0 -> every band 254,254,254). Real dinosaur base diffuse sits far darker, so
# 0.18 keeps the grade inside range and the ramp shows the actual hue.
NEUTRAL_ALBEDO = (0.18, 0.18, 0.18)


def _saturate(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def hue_matrix(packed):
    """The circulant hue-rotation matrix, unpacked. Rows are cyclic shifts of (p, q, r)/511."""
    p, q, r = (v / S10 for v in packed)
    return ((p, q, r), (r, p, q), (q, r, p))


def _grade(colour, packed, brightness, saturation):
    """One of the two hue grades: brightness, then hue rotation, then saturation about luma."""
    cur = [c * brightness for c in colour]
    cur = [sum(c * k for c, k in zip(cur, row)) for row in hue_matrix(packed)]
    # lum = sqrt(dot(cur, cur * REC709)) -- the shader's luma, which is NOT a plain dot product
    lum = math.sqrt(max(0.0, sum(c * c * k for c, k in zip(cur, REC709))))
    return [c * saturation + lum * (1.0 - saturation) for c in cur]


def _mix(factor, a, b):
    return [x * (1.0 - factor) + y * factor for x, y in zip(a, b)]


def _overlay(a, b):
    """Blender's OVERLAY at factor 1: `a` is the base, `b` the blend layer."""
    return [2.0 * x * y if x < 0.5 else 1.0 - 2.0 * (1.0 - x) * (1.0 - y) for x, y in zip(a, b)]


def ts_for_height(block, height):
    """The shader's palette parameter `ts` at a given height-map value."""
    t = height * 100.0 * block["instancePaletteScale"] + block["instancePaletteOffset"]
    return t / 51.0


def palette_period(block):
    """How much `ts` covers ONE full cycle of the palette.

    Every observed `gradFreq` is a multiple of 51 (51, 102, 153, 204 -> harmonics 1..4 of a common
    fundamental), so the whole three-channel pattern repeats over one period of the LOWEST non-zero
    frequency. This is what makes a meaningful swatch possible: sweeping height instead would run
    hundreds of cycles (paletteScale 3.36 gives ~336 across height 0..1) and alias into mush.
    """
    freqs = [f for f in block["gradFreq"] if f]
    return 1.0 / min(freqs) if freqs else 1.0


def gradient_at_ts(block, ts):
    """The cosine palette at palette-parameter `ts`, or None if the seed has no coefficients."""
    if not block.get("gradientEnabled"):
        return None
    out = []
    for i in range(3):
        arg = 2.0 * math.pi * (ts * block["gradFreq"][i] + block["gradPhase"][i] / S10)
        v = math.cos(arg) * block["gradAmplitude"][i] + block["gradOffset"][i]
        out.append(_saturate(v / S10))
    return out


def gradient_colour(block, height):
    """The cosine palette at one height-map value, or None when there are no coefficients."""
    if not block.get("gradientEnabled"):
        return None
    return gradient_at_ts(block, ts_for_height(block, height))


def predicted_at_ts(block, ts, albedo=NEUTRAL_ALBEDO, colour_weight=1.0, key_blend=1.0):
    """The colour at palette-parameter `ts`, on a given albedo. Linear floats 0..1."""
    a = _grade(albedo, block["hueMatrixBase"], block["brightnessBase"], block["saturationBase"])
    b = _grade(albedo, block["hueMatrixPalette"], block["brightnessPalette"],
               block["saturationPalette"])
    graded = _mix(key_blend, a, b)
    out = _mix(colour_weight, list(albedo), graded)

    grad = gradient_at_ts(block, ts) if block.get("gradientEnabled") else None
    if grad is None:
        return [_saturate(c) for c in out]

    s = colour_weight * block["paletteStrength"] * key_blend
    grey = _mix(s, [0.5, 0.5, 0.5], grad)
    base = [_saturate(c - 1.0 / 255.0) for c in out]
    return [_saturate(c) for c in _overlay(base, grey)]


def predicted_colour(block, height, **kw):
    """The colour at one height-map value. Linear floats 0..1."""
    return predicted_at_ts(block, ts_for_height(block, height), **kw)


def linear_to_srgb(c):
    """Encode a linear value for display. The shader works in linear light; painting those numbers
    straight into a widget renders everything far too dark, so the ramp must be encoded."""
    c = _saturate(c)
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def ramp(block, steps=64, encode=True, **kw):
    """The variant's palette: `steps` colours across ONE full cycle, as (r, g, b) ints 0..255.

    Swept over the palette's own period rather than over height. Height is the wrong axis for a
    swatch -- a shipped variant runs hundreds of cycles across height 0..1, so an evenly-spaced
    height sweep aliases to a single flat colour and hides the entire palette. One period shows
    every colour the variant can actually produce.

    `encode=False` returns raw linear values, for comparing against the shader's own numbers.
    """
    ts0 = ts_for_height(block, 0.0)
    period = palette_period(block)
    out = []
    for i in range(steps):
        f = i / float(steps - 1) if steps > 1 else 0.0
        rgb = predicted_at_ts(block, ts0 + f * period, **kw)
        if encode:
            rgb = [linear_to_srgb(c) for c in rgb]
        out.append(tuple(int(round(_saturate(c) * 255.0)) for c in rgb))
    return out


def is_flat(block):
    """True when the ramp carries no gradient (unharvested seed) -- the UI says so explicitly."""
    return not block.get("gradientEnabled")


def selftest():
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from preview_bridge import model_to_block
    from variant_model import VariantModel

    # a harvested seed -> a real, varying ramp
    m = VariantModel.template(); m.seed = 9; m.complexity = 10
    blk = model_to_block(m)
    r = ramp(blk, steps=32)
    assert len(r) == 32 and all(len(c) == 3 for c in r)
    assert all(0 <= v <= 255 for c in r for v in c), "colours must be 8-bit clamped"
    assert len(set(r)) > 1, "a harvested seed must produce a VARYING ramp"
    assert not is_flat(blk)

    # an unharvested seed -> flat, and honestly reported as such
    m2 = VariantModel.template(); m2.seed = 999
    blk2 = model_to_block(m2)
    r2 = ramp(blk2, steps=16)
    assert len(set(r2)) == 1, "no coefficients means one flat colour"
    assert is_flat(blk2)

    # identity hue rotation must leave a neutral albedo neutral-ish, and brightness must brighten
    m3 = VariantModel.template()          # hue 0, brightness 1, saturation 1
    b3 = model_to_block(m3)
    c = predicted_colour(b3, 0.5)
    assert all(abs(x - c[0]) < 0.02 for x in c), "identity rotation should stay grey: %s" % (c,)
    m3.brightnessBase = m3.brightnessPalette = 2.0
    brighter = predicted_colour(model_to_block(m3), 0.5)
    assert sum(brighter) > sum(c), (brighter, c)

    # saturation 0 collapses toward luma (grey); the maths must not explode on the extremes
    m4 = VariantModel.template(); m4.saturationBase = m4.saturationPalette = 0.0
    g = predicted_colour(model_to_block(m4), 0.5)
    assert all(abs(x - g[0]) < 1e-6 for x in g), g
    for bad in (0.0, 10.0):
        m5 = VariantModel.template(); m5.paletteScale = bad; m5.paletteStrength = bad / 10.0
        assert all(0.0 <= v <= 1.0 for v in predicted_colour(model_to_block(m5), 1.0))

    # REGRESSION: a shipped variant's real settings must not blow the ramp out to white. Baryonyx
    # v00 (brightness 1.5/2.0, saturation 2.0/1.5) did exactly that against a 0.5 albedo.
    real = VariantModel(seed=36, complexity=2, brightnessBase=1.5, brightnessPalette=2.0,
                        saturationBase=2.0, saturationPalette=1.5, paletteScale=3.36,
                        paletteOffset=4.51, paletteStrength=0.5)
    rr = ramp(model_to_block(real), steps=32)
    assert not all(c == (255, 255, 255) or c == (254, 254, 254) for c in rr), \
        "ramp blew out to white on a real variant: %s" % (rr[:3],)
    assert len(set(rr)) > 1, "a real harvested variant must still vary across height"

    # REGRESSION: the swatch must sweep the palette's PERIOD, not height. Sweeping height on a real
    # variant (paletteScale 3.36 -> ~336 cycles across 0..1) aliased to one flat colour and hid the
    # whole palette. Over one period the same variant must show real variation.
    rb = model_to_block(real)
    assert palette_period(rb) == 1.0 / min(f for f in rb["gradFreq"] if f)
    spread = max(max(c) - min(c) for c in zip(*ramp(rb, steps=64, encode=False)))
    assert spread > 0.02, "palette swatch is flat for a harvested seed: spread %.4f" % spread
    # sampling density must not change the answer much (i.e. we are no longer aliasing)
    a32 = ramp(rb, steps=32)
    a128 = ramp(rb, steps=128)
    assert len(set(a32)) > 4 and len(set(a128)) > 4, (len(set(a32)), len(set(a128)))

    # display encoding: linear 0.18 must land near sRGB mid-grey (~0.46), not at 0.18
    assert 0.44 < linear_to_srgb(0.18) < 0.49, linear_to_srgb(0.18)
    assert linear_to_srgb(0.0) == 0.0 and abs(linear_to_srgb(1.0) - 1.0) < 1e-9

    # the hue rotation actually rotates: a half-turn must move the colour off grey
    m6 = VariantModel.template(); m6.seed = 9; m6.complexity = 10; m6.hueRotationPalette = 0.5
    rot = predicted_colour(model_to_block(m6), 0.25)
    base = predicted_colour(model_to_block(VariantModel(seed=9, complexity=10)), 0.25)
    assert rot != base, "hue rotation had no effect"
    print("selftest ok")


if __name__ == "__main__":
    selftest()
