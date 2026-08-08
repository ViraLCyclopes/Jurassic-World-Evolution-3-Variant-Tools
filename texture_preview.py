"""Apply a variant's grade to a whole diffuse texture, per pixel, with numpy.

WHAT THIS IS FOR. The palette strip and graph show what colours a variant can produce; they say
nothing about what it does to *your* texture. This does: give it the species' base diffuse and it
returns the graded image, so the key split -- which parts of the animal take the base grade and
which take the palette grade -- is visible directly instead of being inferred.

WHAT IS EXACT, AND WHAT IS AN ASSUMPTION. Read this before trusting a comparison:

  EXACT     the albedo (it IS the texture), the key mask (the shader keys off the RAW base
            diffuse, which is exactly this texture), both grades, and the gradient maths
  ASSUMED   `colour_weight = 1.0`. The real value is accumulated from the layer stack's blend
            masks, which the editor does not have -- so this shows the grade at FULL strength.
            Anywhere the layers would veto grading (Pyroraptor's mouth, for one) this is wrong.
  ASSUMED   the HEIGHT driving the gradient. The real one is the composited layer height, not
            anything in the diffuse, so it is a parameter here rather than a guess. Sweep it.

Blender remains the authority for a finished skin. This is for seeing what a control does.

NO SECOND IMPLEMENTATION OF THE MATHS. Every formula here is the vectorised twin of one in
`palette_preview` / `material_block`, and `selftest()` asserts they agree to 1e-6 on random inputs.
A preview that quietly disagrees with the maths it is meant to illustrate is worse than none.

Run:  python texture_preview.py --selftest
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

S10 = 511.0
REC709 = (0.2126, 0.7152, 0.0722)


def _hue_matrix(packed):
    p, q, r = (v / S10 for v in packed)
    return np.array(((p, q, r), (r, p, q), (q, r, p)), dtype=np.float64)


def _grade(rgb, packed, brightness, saturation):
    """Brightness, then the circulant hue rotation, then saturation about the RMS luma.

    `rgb` is (..., 3) linear. The luma is `sqrt(dot(c, c * Rec709))` -- the shader's, which is NOT
    a plain dot product, and getting that wrong shifts every saturation value slightly.
    """
    cur = rgb * float(brightness)
    cur = cur @ _hue_matrix(packed).T
    lum = np.sqrt(np.maximum(0.0, (cur * cur * np.array(REC709)).sum(axis=-1, keepdims=True)))
    return cur * float(saturation) + lum * (1.0 - float(saturation))


def key_blend(block, key_source):
    """Per-pixel key mask: 0 keeps the base grade, 1 takes the palette grade.

    Vectorised `material_block.key_blend`. `key_source` is the RAW base diffuse.
    """
    kc = np.array(block["keyColour"][:3], dtype=np.float64)
    dist = np.sqrt(((key_source - kc) ** 2).sum(axis=-1, keepdims=True))
    mask = np.clip(1.0 - np.clip(dist / float(block["keyThreshold"]), 0.0, 1.0), 0.0, 1.0)
    tol = float(block["keyTolerance"])
    inv = mask / tol if tol else mask * np.inf
    return np.clip(1.0 - inv, 0.0, 1.0) if block.get("keyType") else np.clip(inv, 0.0, 1.0)


def _gradient_at_ts(block, ts):
    """The cosine palette at `ts`, or None when there are no coefficients.

    `ts` may be a scalar -> (3,), or a per-texel (H, W) array -> (H, W, 3). The array form is the
    one the shader actually uses: the palette is driven by the COMPOSITED LAYER HEIGHT per texel,
    and at freq 51 with scale 10 it runs ~100 cycles across the body. Evaluating it at a single
    height collapses all of that to one flat colour, which is why a slider preview can never line
    up with a render however the strength is set.
    """
    if not block.get("gradientEnabled"):
        return None
    ts = np.asarray(ts, dtype=np.float64)
    out = []
    for i in range(3):
        arg = 2.0 * np.pi * (ts * block["gradFreq"][i] + block["gradPhase"][i] / S10)
        out.append(np.clip((block["gradOffset"][i] + block["gradAmplitude"][i] * np.cos(arg)) / S10,
                           0.0, 1.0))
    return np.stack(out, axis=-1)


def ts_for_height(block, height):
    from palette_preview import ts_for_height as _t
    return _t(block, height)


def grade_image(rgb, block, height=0.5, colour_weight=1.0, key_source=None):
    """Grade a linear float image `rgb` (H, W, 3) -> the graded linear image.

    `key_source` defaults to `rgb` itself, which is correct when you pass the base diffuse: the
    shader keys off that same texture. Pass it separately only if you have composited elsewhere.

    `height` is a scalar OR a per-texel (H, W) array from `height_map`. Pass the array whenever you
    can: the scalar is a single slice through a palette that cycles ~100 times across the body, and
    no slider position makes it agree with a render. The scalar path is kept because sweeping it is
    a genuinely useful way to see the palette's whole range.
    """
    rgb = np.asarray(rgb, dtype=np.float64)
    kb = key_blend(block, rgb if key_source is None else np.asarray(key_source, dtype=np.float64))

    a = _grade(rgb, block["hueMatrixBase"], block["brightnessBase"], block["saturationBase"])
    b = _grade(rgb, block["hueMatrixPalette"], block["brightnessPalette"],
               block["saturationPalette"])
    graded = a * (1.0 - kb) + b * kb
    out = rgb * (1.0 - colour_weight) + graded * colour_weight

    grad = _gradient_at_ts(block, ts_for_height(block, height))
    if grad is None:
        return np.clip(out, 0.0, 1.0)

    # The gradient is GATED: colourWeight x paletteStrength x keyBlend. On texels that take the
    # base grade (keyBlend ~ 0) it contributes nothing, whatever paletteStrength says.
    s = colour_weight * float(block["paletteStrength"]) * kb
    grey = 0.5 * (1.0 - s) + grad * s
    base = np.clip(out - 1.0 / 255.0, 0.0, 1.0)
    over = np.where(base < 0.5, 2.0 * base * grey, 1.0 - 2.0 * (1.0 - base) * (1.0 - grey))
    return np.clip(over, 0.0, 1.0)


def colour_weight_map(layers, mask_dir, mask_stem, layer_weights, shape):
    """Per-pixel `colourWeight` from the layer stack -- what stops teeth and tongues being painted.

    THIS IS WHY IT MATTERS. `colourWeight` gates the whole grade (`out = lerp(albedo, graded, w)`),
    and it is a PRODUCT OF TWO numbers, both needed:

      * the SWATCH's own `pGlobalColouringWeight` (from the LayerJSON's `swatch_colour_weight`).
        A hard veto: `Swatch_Bone`, `Swatch_Nail` and `Swatch_Mouth_Flesh` are **0**, which is what
        keeps beaks, horns, claws, teeth and tongues unpainted on every dinosaur;
      * the variant's per-layer weight (`layerColourWeights`).

    Using only the second one repaints every beak and tongue -- so a preview that assumes 1.0, or
    that reads only the variant's array, colours the mouth and eye. That is exactly the artefact
    this exists to remove.

    Accumulated the way the shader does, from 0:  `w = lerp(w, layerCW, smoothstep(mask))`.
    A texel no layer covers therefore stays at 0 and is never graded.

    Returns (H, W, 1), or None when the masks cannot be resolved (caller then falls back to 1.0).
    """
    from PIL import Image
    h, w = shape[0], shape[1]
    acc = np.zeros((h, w, 1), dtype=np.float64)
    found = 0
    for L in layers:
        if not L.get("used") or L.get("blend_texture") is None:
            continue
        path = os.path.join(mask_dir, "%s_[%02d]_%s.png"
                            % (mask_stem, int(L["blend_texture"]), L["blend_channel"]))
        if not os.path.isfile(path):
            continue
        im = Image.open(path).convert("L")
        if im.size != (w, h):
            im = im.resize((w, h), Image.BILINEAR)
        # The blend mask is DATA, not colour -- no sRGB decode. Decoding it here would skew every
        # layer's coverage and quietly change which parts of the animal get graded.
        m = np.asarray(im, dtype=np.float64)[:, :, None] / 255.0
        sm = m * m * (3.0 - 2.0 * m)                      # smoothstep, as the shader does
        idx = int(L.get("index", L.get("layer_no", 1) - 1))
        variant_w = float(layer_weights[idx]) if 0 <= idx < len(layer_weights) else 1.0
        cw = abs(float(L.get("swatch_colour_weight", 1.0))) * variant_w
        acc = acc * (1.0 - sm) + cw * sm
        found += 1
    return acc if found else None


def _height_slice(swatch_dir, index, shape):
    """One slice of the shared height-texture ARRAY, resized to `shape`, or None.

    THE SWATCH NAME IN THE FILENAME IS NOT THE SWATCH. `pHeightTexture` is a single texture array
    shared by every swatch, extracted under whichever swatch happened to own it -- on this machine
    all 54 slices sit under `swatch_anky_ankylo_backplates.pheighttexture_[NN].png`. A layer's
    `slices["pHeightTexture"]` is an ARRAY INDEX into that, not a per-swatch file. Matching on the
    swatch's own name finds nothing, which is the trap this function exists to avoid.
    """
    import glob as _glob
    from PIL import Image
    hits = _glob.glob(os.path.join(swatch_dir, "*.pheighttexture_[[]%02d[]].png" % int(index)))
    if not hits:
        return None
    im = Image.open(sorted(hits)[0]).convert("L")
    h, w = shape[0], shape[1]
    if im.size != (w, h):
        im = im.resize((w, h), Image.BILINEAR)
    # Height is DATA, not colour -- no sRGB decode, same rule as the blend masks. Decoding it would
    # move every texel to a different point on the palette cycle.
    return np.asarray(im, dtype=np.float64) / 255.0


def height_map(layers, mask_dir, mask_stem, shape, swatch_dir=None, layer_weights=None):
    """Per-texel composited layer height -- what actually drives the palette. (H, W) or None.

    Accumulated exactly as `colour_weight_map` does, because it is the same stack and the same
    masks: `h = lerp(h, layerHeight, smoothstep(mask))`, starting from 0 so an uncovered texel
    stays at 0. Each layer contributes `slice * pHeightScale + pHeightOffset`, the values the layer
    FGM authored -- typically 0.001..0.01, which is what puts the palette parameter in the range
    where `t = height*100*scale + offset` sweeps roughly a hundred cycles.

    Returns None when the swatch library or the masks cannot be resolved; the caller then falls
    back to the scalar slider, which is an approximation and should say so.
    """
    if not swatch_dir or not os.path.isdir(swatch_dir):
        return None
    from PIL import Image
    h, w = shape[0], shape[1]
    acc = np.zeros((h, w), dtype=np.float64)
    found = 0
    for L in layers:
        if not L.get("used") or L.get("blend_texture") is None:
            continue
        idx = (L.get("slices") or {}).get("pHeightTexture")
        if idx is None:
            continue
        mask_path = os.path.join(mask_dir, "%s_[%02d]_%s.png"
                                 % (mask_stem, int(L["blend_texture"]), L["blend_channel"]))
        if not os.path.isfile(mask_path):
            continue
        slice_img = _height_slice(swatch_dir, idx, (h, w))
        if slice_img is None:
            continue
        im = Image.open(mask_path).convert("L")
        if im.size != (w, h):
            im = im.resize((w, h), Image.BILINEAR)
        m = np.asarray(im, dtype=np.float64) / 255.0
        sm = m * m * (3.0 - 2.0 * m)                      # smoothstep, as the shader does

        p = L.get("params") or {}

        def _p(name, default=0.0):
            v = p.get(name, default)
            return float(v[0]) if isinstance(v, (list, tuple)) and v else float(v or default)

        # pHeightScale / max(pUVTile), matching `blender_layer_nodes._layer_group`.
        #
        # That reciprocal is NOT a guess: it was read straight out of eleven Lokiceratops layer
        # blocks in RenderDoc captures (block +0 w1, `%896`), worst relative error 5e-8 across
        # tiles 7..22.9, and the shader computes max(tile) at %922 for the LOD -- so the CPU is
        # uploading the reciprocal of the same quantity and displacement stays proportional to
        # feature size. Direct measurement of the uploaded value beats any fit downstream of it.
        #
        # I briefly replaced this with the raw pHeightScale after fitting the game's albedo for
        # SpinosaurusJWR variant 06. That fit was WORTHLESS: variant 06 is the near-grey one
        # (measured saturation 0.11-0.15), so every candidate lands within a few percent of neutral
        # and the target cannot discriminate between them. Fit against a SATURATED variant if this
        # ever needs re-testing.
        # max() of the tile vector, matching `blender_layer_nodes` exactly
        # (`norm = 1.0 / max(max(tile), 1e-6)`). Taking only the first component would diverge on
        # any layer whose U and V tiles differ, and the whole point of this function is to compute
        # the SAME height the node graph does so the two previews are comparable.
        raw_tile = p.get("pUVTile", [1.0, 1.0])
        if not isinstance(raw_tile, (list, tuple)) or not raw_tile:
            raw_tile = [1.0]
        tile = max(max(float(x) for x in raw_tile), 1e-6)
        layer_h = slice_img * (_p("pHeightScale", 0.0) / tile) + _p("pHeightOffset", 0.0)
        acc = acc * (1.0 - sm) + layer_h * sm
        found += 1
    return acc if found else None


def srgb_to_linear(a):
    a = np.asarray(a, dtype=np.float64)
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(a):
    a = np.clip(np.asarray(a, dtype=np.float64), 0.0, 1.0)
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * (a ** (1.0 / 2.4)) - 0.055)


def load_texture(path, max_side=512):
    """Load an sRGB PNG as a LINEAR float array, downscaled so a live preview stays interactive."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if max_side and max(im.size) > max_side:
        scale = max_side / float(max(im.size))
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                       Image.BILINEAR)
    return srgb_to_linear(np.asarray(im, dtype=np.float64) / 255.0)


def to_qimage(linear_rgb):
    """Linear float (H, W, 3) -> a QImage ready to paint (sRGB-encoded, contiguous RGB888)."""
    from PyQt5 import QtGui
    buf = np.ascontiguousarray((linear_to_srgb(linear_rgb) * 255.0 + 0.5).astype(np.uint8))
    h, w, _ = buf.shape
    img = QtGui.QImage(buf.data, w, h, 3 * w, QtGui.QImage.Format_RGB888)
    return img.copy()          # detach from the numpy buffer before it is collected


def selftest():
    import palette_preview as pp
    import material_block as mb

    rng = np.random.default_rng(7)
    block = {
        "keyColour": [1.0, 1.0, 1.0], "keyThreshold": 1.65, "keyTolerance": 0.10, "keyType": 1,
        "hueMatrixBase": (511, 7, -6), "hueMatrixPalette": (500, -20, 31),
        "brightnessBase": 2.68, "brightnessPalette": 0.80,
        "saturationBase": 0.897, "saturationPalette": 0.831,
        "instancePaletteScale": 1.4, "instancePaletteOffset": 0.8, "paletteStrength": 0.5,
        "gradientEnabled": True,
        "gradOffset": (307, 341, 221), "gradAmplitude": (181, 223, 163),
        "gradFreq": (4, 4, 4), "gradPhase": (19, 8, 40),
    }

    px = rng.random((9, 5, 3))

    # 1. the key mask must match material_block's scalar version
    ours = key_blend(block, px)
    for j in range(px.shape[0]):
        for i in range(px.shape[1]):
            want = mb.key_blend(block, list(px[j, i]))
            assert abs(ours[j, i, 0] - want) < 1e-9, (ours[j, i, 0], want)

    # 2. the whole grade must match palette_preview's scalar version, pixel for pixel
    for height in (0.0, 0.37, 1.0):
        got = grade_image(px, block, height=height)
        ts = pp.ts_for_height(block, height)
        for j in range(px.shape[0]):
            for i in range(px.shape[1]):
                kb = mb.key_blend(block, list(px[j, i]))
                want = pp.predicted_at_ts(block, ts, albedo=list(px[j, i]), key_blend=kb)
                assert np.allclose(got[j, i], want, atol=1e-6), (height, got[j, i], want)

    # 3. a flat (unharvested) palette must still grade -- it just loses the gradient
    flat = dict(block, gradientEnabled=False)
    assert np.isfinite(grade_image(px, flat)).all()

    # 4. round-trip the encoders
    assert np.allclose(srgb_to_linear(linear_to_srgb(px)), px, atol=1e-9)

    # 5. colour_weight 0 must be a no-op (the grade is lerped in by it)
    assert np.allclose(grade_image(px, flat, colour_weight=0.0), px, atol=1e-12)

    print("selftest ok")


if __name__ == "__main__":
    selftest()
