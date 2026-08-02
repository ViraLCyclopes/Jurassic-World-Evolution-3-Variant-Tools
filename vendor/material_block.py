"""Decode JWE3's GPU material-parameter block, and evaluate the palette from it.

This is the bridge between a RenderDoc capture and the colour model. The model itself was read
off the shader disassembly -- see `..\\Shader Research\\PALETTE.md`, which this file implements
verbatim. Register numbers in the comments refer to
`Shader Research\\ir\\0300_ps_DinosaurLayered_Layered_Opaque_GBuffer_0_Win64_SM60.txt`.

WHY THIS EXISTS. Everything about the dinosaur colour model is solved except one lookup:
`(seed, complexity) -> twelve signed 10-bit gradient coefficients`. That bake happens in the game
executable, so it cannot be read off disk -- but it IS sitting in the GPU material buffer at
runtime. `find_blocks()` locates the block by fingerprinting f16 values we already know from the
variant's FGM; `decode()` then reads the twelve unknowns straight out of it.

NOTHING HERE IS VERIFIED AGAINST A RENDER YET. The maths is a faithful transcription of the
disassembly and it reproduces two independent in-game measurements (see PALETTE.md), but no
capture has been decoded and no Blender render has been compared. Treat it as unproven.
"""
import math
import struct
import sys

# ---------------------------------------------------------------- bit unpacking


def s10x3(word):
    """Three signed 10-bit fields from a uint, as the shader does it.

    `shl 22 / ashr 22`, `shl 12 / ashr 22`, `shl 2 / ashr 22` -- i.e. bits 0-9, 10-19, 20-29,
    each sign-extended. Bits 30-31 are ignored.
    """
    def sx(v):
        v &= 0x3FF
        return v - 1024 if v & 0x200 else v
    return (sx(word), sx(word >> 10), sx(word >> 20))


def f16pair(word):
    """The low and high halves of a uint as two f16s."""
    lo = struct.unpack("<e", struct.pack("<H", word & 0xFFFF))[0]
    hi = struct.unpack("<e", struct.pack("<H", (word >> 16) & 0xFFFF))[0]
    return float(lo), float(hi)


# ---------------------------------------------------------------- block decode

def decode(words):
    """words = 12 uints (three uint4 loads at +0, +16, +32) -> a named dict.

    Layout is the table in PALETTE.md, decoded from the IR at container 300 line ~3346.
    """
    if len(words) != 12:
        raise ValueError(f"need 12 uints (+0, +16, +32), got {len(words)}")
    w = list(words)

    # The two key scalars are uploaded as RECIPROCALS, and not in the order their names suggest:
    # word 0's high half is 1/u_globalKeyThreshold and it multiplies the colour DISTANCE, while
    # word 1's high half is 1/u_globalKeyTolerance and it multiplies the resulting MASK.
    # Verified on nine captured blocks across six species (Baryonyx, Giganotosaurus, Apatosaurus,
    # Albertosaurus, Atrociraptor, Lokiceratops) -- every one agrees to 4-5 significant figures.
    key_r, inv_thr = f16pair(w[0])
    key_g, inv_tol = f16pair(w[1])
    key_b, _ = f16pair(w[2])
    pal_scale, pal_offset = f16pair(w[3])

    bright_base, bright_pal = f16pair(w[6])
    sat_base, sat_pal = f16pair(w[7])

    return {
        # +0
        "keyColour": (key_r, key_g, key_b),
        "keyTolerance": 1.0 / inv_tol if inv_tol else float("inf"),
        "keyThreshold": 1.0 / inv_thr if inv_thr else float("inf"),
        "keyType": (w[2] >> 16) & 1,               # bit 16 -- SET in every clean capture so far
        "gradientEnabled": bool(w[2] & 0x20000),   # bit 17
        "paletteStrength": ((w[2] >> 24) & 0xFF) / 255.0,
        "instancePaletteScale": pal_scale,
        "instancePaletteOffset": pal_offset,
        # +16
        "hueMatrixBase": s10x3(w[4]),
        "hueMatrixPalette": s10x3(w[5]),
        "brightnessBase": bright_base,
        "brightnessPalette": bright_pal,
        "saturationBase": sat_base,
        "saturationPalette": sat_pal,
        # +32 -- the twelve unknowns
        "gradOffset": s10x3(w[8]),
        "gradAmplitude": s10x3(w[9]),
        "gradFreq": s10x3(w[10]),
        "gradPhase": s10x3(w[11]),
    }


# ---------------------------------------------------------------- the colour model

def gradient(block, height):
    """The cosine-gradient palette colour at a given composited height. PALETTE.md section 5.

    Scaling is deliberately NOT uniform -- freq raw, phase/511 inside the cosine, and the
    amplitude+offset sum /511 outside. Copied from registers %2864-%2886.
    """
    t = height * 100.0 * block["instancePaletteScale"] + block["instancePaletteOffset"]
    ts = t / T_DIVISOR
    out = []
    for i in range(3):
        arg = 2.0 * math.pi * (ts * block["gradFreq"][i] + block["gradPhase"][i] / 511.0)
        v = (block["gradOffset"][i] + block["gradAmplitude"][i] * math.cos(arg)) / 511.0
        out.append(min(1.0, max(0.0, v)))
    return tuple(out)


def hue_matrix(packed):
    """Expand three signed 10-bit values into the circulant hue-rotation matrix. Section 4."""
    p, q, r = (v / 511.0 for v in packed)
    return ((p, q, r), (r, p, q), (q, r, p))


REC709 = (0.2126, 0.7152, 0.0722)

# `%2864 = fmul fast float %2824, 0x3F940A0500000000` -- that constant is 0.01956947 = 1/51.1, NOT
# 1/51 (0.01960784). PALETTE.md was corrected; this file and blender_palette_nodes had drifted.
T_DIVISOR = 51.1


def hue_matrix_from_rotation(rot):
    """Predict the packed circulant matrix from a rotation parameter. VERIFIED against a capture.

    `rot` is `u_globalColourRotationOffsetBase/Palette`; the angle is `theta = pi * rot`, which is
    the independently measured "hue += 180 deg * rotP" law. The standard hue-rotation-about-the-
    grey-axis matrix is circulant with

        p = cos + (1-cos)/3,  q = (1-cos)/3 - sqrt(1/3)*sin,  r = (1-cos)/3 + sqrt(1/3)*sin

    Confirmed exactly on Albertosaurus_Juvenile variant 4 (rotP 0.322): predicted
    (351.1, -170.1, 330.1), the capture holds (351, -170, 330).
    """
    th = math.pi * rot
    c, s = math.cos(th), math.sin(th)
    k = (1 - c) / 3.0
    r3 = math.sqrt(1 / 3.0)
    return tuple(int(round(v * 511)) for v in (c + k, k - r3 * s, k + r3 * s))


def is_hue_matrix(packed, tol=2):
    """The rows of a hue-rotation matrix always sum to 1, so the packed triple sums to 511.

    p + q + r = cos + 3*(1-cos)/3 = 1 identically, for any angle. That makes this a free,
    angle-independent structural test -- the cheap filter for finding material blocks in a
    multi-gigabyte capture.
    """
    return abs(sum(packed) - 511) <= tol


def grade(rgb, packed_matrix, brightness, saturation):
    """One of the two hue grades: brightness, circulant hue rotation, then sqrt-grey saturation."""
    c = [v * brightness for v in rgb]
    m = hue_matrix(packed_matrix)
    c = [sum(m[i][j] * c[j] for j in range(3)) for i in range(3)]
    g = math.sqrt(max(0.0, sum(c[i] * c[i] * REC709[i] for i in range(3))))
    return tuple(g + (v - g) * saturation for v in c)


def key_blend(block, key_source):
    """The key-colour mask: 0 takes the base grade, 1 takes the palette grade. Section 3.

        mask  = saturate(1 - saturate(distance / keyThreshold))
        blend = saturate(1 - mask / keyTolerance)                 # keyType set

    TWO THINGS THE PARAMETER NAMES GET WRONG, both settled against captured GPU blocks:

    *   `keyThreshold` scales the DISTANCE and `keyTolerance` scales the MASK -- the opposite of
        what the names imply. See `decode`.
    *   `keyType` is set on every clean capture, so the sense is INVERTED: a pixel CLOSE to the key
        colour (white, for every variant of every species checked) gets blend 0 and keeps its base
        grade. That is what paints a dinosaur's pale belly: the countershading already in the base
        diffuse survives the palette while the darker back is repainted. Reading it the other way
        round grades the whole animal uniformly and gives a cold grey belly.

    `key_source` is the RAW base diffuse (%2241), not the composited albedo.
    """
    d = math.dist(key_source, block["keyColour"])
    m = min(1.0, max(0.0, 1.0 - min(1.0, max(0.0, d / block["keyThreshold"]))))
    sign, bias = (-1.0, 1.0) if block["keyType"] else (1.0, 0.0)
    return min(1.0, max(0.0, sign * m / block["keyTolerance"] + bias))


def overlay(base, blend):
    """out = base < 0.5 ? 2*base*blend : 1 - 2*(1-base)*(1-blend). Section: final combine."""
    return tuple(2 * b * g if b < 0.5 else 1 - 2 * (1 - b) * (1 - g)
                 for b, g in zip(base, blend))


def shade(block, albedo, height, colour_weight, key_source=None):
    """The whole pixel path, sections 3-6.

    `albedo` is the composited layer stack overlaid onto the base diffuse (%2588). `key_source` is
    the RAW base diffuse the key mask measures against (%2241); it defaults to `albedo` only
    because a lot of test code has no separate base map, and that default is an approximation.
    """
    blend = key_blend(block, albedo if key_source is None else key_source)
    a = grade(albedo, block["hueMatrixBase"], block["brightnessBase"], block["saturationBase"])
    b = grade(albedo, block["hueMatrixPalette"], block["brightnessPalette"],
              block["saturationPalette"])
    graded = tuple(a[i] + (b[i] - a[i]) * blend for i in range(3))
    # section 6: the layer weight lerps between the ungraded albedo and the graded colour
    out = tuple(albedo[i] + (graded[i] - albedo[i]) * colour_weight for i in range(3))
    if not block["gradientEnabled"]:
        return out
    g = gradient(block, height)
    strength = colour_weight * block["paletteStrength"] * blend
    g = tuple(v * strength + (1 - strength) * 0.5 for v in g)
    base = tuple(min(1.0, max(0.0, v - 1 / 255.0)) for v in out)
    return overlay(base, g)


# ---------------------------------------------------------------- capture search

def find_blocks(buf, expected, tol=1e-3):
    """Scan a raw byte buffer for material blocks matching known FGM values.

    `expected` is a dict of any of the decoded f16 field names -> the value from the variant's
    FGM (brightnessBase, saturationPalette, instancePaletteScale, ...). Those we already know,
    so they identify the block; the gradient coefficients are what we are after.

    Yields (byte_offset, decoded_block). Stride is 4 bytes, so a block spanning the +0/+16/+32
    layout is 48 bytes.
    """
    n = len(buf)
    for off in range(0, n - 48 + 1, 4):
        words = struct.unpack_from("<12I", buf, off)
        try:
            blk = decode(words)
        except Exception:
            continue
        ok = True
        for k, want in expected.items():
            got = blk.get(k)
            if got is None or not isinstance(got, float):
                ok = False
                break
            if not (abs(got - want) <= tol * max(1.0, abs(want))):
                ok = False
                break
        if ok:
            yield off, blk


# ---------------------------------------------------------------- self-test

def _pack_s10x3(a, b, c):
    return (a & 0x3FF) | ((b & 0x3FF) << 10) | ((c & 0x3FF) << 20)


def _pack_f16pair(lo, hi):
    l = struct.unpack("<H", struct.pack("<e", lo))[0]
    h = struct.unpack("<H", struct.pack("<e", hi))[0]
    return l | (h << 16)


def selftest():
    # signed 10-bit round-trip, including both sign boundaries
    for trip in [(0, 0, 0), (511, -512, 1), (-1, -1, -1), (200, -300, 100)]:
        assert s10x3(_pack_s10x3(*trip)) == trip, trip

    # f16 pair round-trip
    lo, hi = f16pair(_pack_f16pair(1.5, 2.25))
    assert (lo, hi) == (1.5, 2.25), (lo, hi)

    # a synthetic block decodes back to what we put in
    words = [
        _pack_f16pair(0.5, 1 / 1.25),    # keyColour.r, 1/keyThreshold
        _pack_f16pair(0.25, 1 / 0.75),   # keyColour.g, 1/keyTolerance
        _pack_f16pair(0.125, 0.0) | (1 << 16) | (1 << 17) | (128 << 24),
        _pack_f16pair(2.0, 3.0),         # paletteScale, paletteOffset
        _pack_s10x3(400, 50, 60),        # hue matrix base
        _pack_s10x3(300, 100, 110),      # hue matrix palette
        _pack_f16pair(1.0, 1.5),         # brightness base / palette
        _pack_f16pair(2.0, 2.5),         # saturation base / palette
        _pack_s10x3(255, 255, 255),      # gradient offset
        _pack_s10x3(200, -200, 100),     # gradient amplitude
        _pack_s10x3(3, 5, 7),            # gradient freq
        _pack_s10x3(0, 128, -128),       # gradient phase
    ]
    b = decode(words)
    assert b["keyType"] == 1 and b["gradientEnabled"]
    # the two key scalars are stored as reciprocals, threshold in word 0 and tolerance in word 1
    assert abs(b["keyThreshold"] - 1.25) < 2e-3, b["keyThreshold"]
    assert abs(b["keyTolerance"] - 0.75) < 2e-3, b["keyTolerance"]
    assert abs(b["paletteStrength"] - 128 / 255) < 1e-9
    assert b["instancePaletteScale"] == 2.0 and b["instancePaletteOffset"] == 3.0
    assert b["gradFreq"] == (3, 5, 7), b["gradFreq"]
    assert b["gradPhase"] == (0, 128, -128), b["gradPhase"]
    assert b["brightnessPalette"] == 1.5

    # gradient: at height 0, t = offset = 3, so the cosine argument is fully determined.
    # Recompute it independently here rather than reusing gradient()'s own expression.
    g = gradient(b, 0.0)
    ts = 3.0 / 51.1
    for i, (o, a, f, p) in enumerate(zip(b["gradOffset"], b["gradAmplitude"],
                                         b["gradFreq"], b["gradPhase"])):
        want = (o + a * math.cos(2 * math.pi * (ts * f + p / 511.0))) / 511.0
        assert abs(g[i] - min(1.0, max(0.0, want))) < 1e-12, (i, g[i], want)

    # overlay must match the two branches exactly
    assert overlay((0.25,), (0.5,))[0] == 2 * 0.25 * 0.5
    assert abs(overlay((0.75,), (0.5,))[0] - (1 - 2 * 0.25 * 0.5)) < 1e-12

    # THE COUNTERSHADING INVARIANT. With the key colour white and keyType set, a pale belly must
    # come out near 0 (keeps its base grade, stays warm) and a dark back near 1 (repainted by the
    # palette). Loki "Mangrove Forest" numbers, which is the case the game screenshot settles.
    loki = {"keyColour": (1.0, 1.0, 1.0), "keyThreshold": 1.56, "keyTolerance": 0.276,
            "keyType": 1}
    belly = key_blend(loki, (0.75, 0.70, 0.60))
    back = key_blend(loki, (0.35, 0.15, 0.10))
    assert belly < 0.05, belly
    assert back > 0.5, back
    assert key_blend(loki, (1.0, 1.0, 1.0)) == 0.0        # exactly on the key colour
    # and reading it the old way -- names taken at face value, keyType clear -- must NOT reproduce
    # that, otherwise this test is not actually pinning anything down
    flat = dict(loki, keyType=0)
    assert key_blend(flat, (0.75, 0.70, 0.60)) > 0.5

    # the hue matrix must be circulant -- each row a rotation of the last
    m = hue_matrix((511, 0, 0))
    assert m[0] == (1.0, 0.0, 0.0) and m[1] == (0.0, 1.0, 0.0) and m[2] == (0.0, 0.0, 1.0), m

    # Overlay against exactly 0.5 is the identity -- both branches collapse to `base`.
    # That matters: it is why a zero palette strength cleanly disables the gradient.
    for v in (0.1, 0.4999, 0.5, 0.7, 0.99):
        assert abs(overlay((v,), (0.5,))[0] - v) < 1e-12, v

    # colour_weight = 0 must leave the albedo untouched apart from the shader's own 1/255 bias
    # (%2902: `base = saturate(graded - 1/255)` before the overlay). This is the invariant that
    # explains why baseColourSaturation/Contrast measured as inert at weight 1.0 (section 6).
    alb = (0.4, 0.3, 0.2)
    got = shade(b, alb, 0.5, 0.0)
    assert all(abs(got[i] - (alb[i] - 1 / 255)) < 1e-9 for i in range(3)), got

    # find_blocks locates a planted block inside noise and ignores the rest
    blob = bytearray(b"\x00" * 64) + bytearray(struct.pack("<12I", *words)) + bytearray(b"\x11" * 64)
    hits = list(find_blocks(bytes(blob), {"brightnessPalette": 1.5, "saturationBase": 2.0}))
    assert any(off == 64 for off, _ in hits), [o for o, _ in hits]

    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        selftest()
