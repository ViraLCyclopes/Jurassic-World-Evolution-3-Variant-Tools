"""Build the JWE3 16-layer skin stack as a Blender NODE graph. Run inside Blender.

WHY NODES AND NOT A BAKE. JWE3 pairs low-resolution per-layer masks with a 512x512 swatch tiled up
to ~32x, so surface detail is resolution-independent by design. Baking the composite to a fixed
texture throws that away: at a 1024 bake each pixel swallows ~16 swatch texels and the scales
disappear into grey. Tiling in nodes lets every layer resolve at render resolution.

TRANSCRIBED FROM shader container 300, the per-layer loop at %858-%2187. Five things this file
gets right that the earlier numpy bake did not:

1.  **The visible relief comes from the HEIGHT texture, not from `pPackedTexture`.** At %1915-%1954
    the shader takes four extra height samples at +-1 texel and forms a central difference, so the
    per-layer normal is literally the gradient of that layer's height slice. `pPackedTexture.r` is
    ROUGHNESS (its default when a layer has no packed slice is 0.3, matching the accumulator's
    initial value). An earlier note in this project claimed the opposite; it was wrong.

2.  **The gradients are blended, the offsets are not.** The shader blends per-layer *gradients*
    (%1904/%1905), and a gradient cannot see `pHeightOffset` because the offset is constant. Our
    bake blended the offset-carrying heights and then differentiated, which turns every mask seam
    into a cliff -- exactly the "harsh transitions" artefact seen on Spinosaurus. So this file
    carries TWO height accumulators through the same blend factor:
        Height   = with pHeightOffset   -> drives the palette (t = height*100*scale + offset)
        Bump     = without              -> drives the Bump node
    Differentiating the offset-free blend is not identical to blending the gradients (it keeps a
    `(h_new - h_prev) * grad(blend)` term the shader drops) but the term is small once the offsets
    are gone, and Blender's Bump node uses the same screen-space derivatives the shader does.

3.  **Four array slices per layer, one byte each**, packed in block +32 w3:
    `&255` diffuse, `>>8 &255` packed/roughness, `>>16 &255` height, `>>24` remap LUT. 255 means
    "this layer has no such texture" -- not "skip the layer". A layer with no diffuse still blends,
    contributing a flat 0.5.

4.  **Two weight curves off one blend factor** (%1899-%1914). Height, bump and roughness use the
    raw `blend`; albedo uses `smoothstep(blend)`.

5.  `pDiffuseSaturation` / `pDiffuseContrast` are applied per layer around a *perceptual* luma,
    `sqrt(dot(rgb, rgb*(0.2126,0.7152,0.0722)))` (%2077-%2100), not the usual linear dot.

6.  **Nearly all of the colour comes from the per-layer remap LUT**, not from the diffuse slices,
    which are close to greyscale. `pRemapTexture` is a 32x16 array slice read at
    (luma*0.96875 + 0.015625, pRemapLutIndex*0.0625 + 0.03125) -- see `_remap`.

STRUCTURE: each layer is its own node group, and the material is a left-to-right chain of them
carrying five sockets. The first version put all 293 nodes in one flat strip and was unreadable.

NOT IMPLEMENTED YET: the palette itself (`jwe3_palette.py`), which grades the final albedo using
the composited Height and the 16 `pGlobalColouringWeight` values.

Usage, from outside Blender:  python export_layers.py Lokiceratops
Then inside Blender:          build_from_json(".../LayerJSON/Lokiceratops_Female.json",
                                              mask_dir, "lokiceratops")
"""
import json
import math
import os

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
import _paths  # noqa: E402  (vendored: replaces a hard-coded absolute path that only ever
               # resolved on one machine -- the Swatch Library now comes from the shared config)
import part_manifest  # noqa: E402  (fgm_slots / texture_files -- name-driven texture resolution)
SWATCH_DIR = _paths.swatch_dir()


def find_base_fgm(tex_dir, prefix):
    """The species' base `<prefix>.fgm` in `tex_dir`, or None.

    This is the file that NAMES the body's textures (`pBaseDiffuseTexture ->
    pyroraptor.pbasediffusetexture.tex`, `pLayered_BlendWeights -> pyroraptor.playered_blendweights
    .tex`). Matching cobra-tools, the .fgm is the authority on what to load; the prefix-built
    filenames are only a fallback for folders that do not ship one.
    """
    if not tex_dir or not os.path.isdir(tex_dir) or not prefix:
        return None
    want = f"{prefix}.fgm".lower()
    for f in sorted(os.listdir(tex_dir)):
        if f.lower() == want:
            return os.path.join(tex_dir, f)
    return None

# All 54 slices of each shared array texture carry ONE filename prefix -- the name of whichever
# swatch the array happens to be attributed to. Only `array_index` picks the slice.
ARRAY_PREFIX = "swatch_anky_ankylo_backplates"

SHARPNESS = 2.0e4       # %1885: (h*B - prevHeight*A) * 20000

# `%97` -- a per-draw float at byte 44 of the material block (handle %12, index %60), sitting
# immediately after the float3 at 32/36/40 that scales object position. It multiplies EVERY
# layer's height, both the texture term (%977) and the offset term (%979).
#
# NOT YET READ FROM A CAPTURE. It matters far more than its size suggests: the palette gradient
# walks one full cosine cycle per 0.00112 of composited height, while Lokiceratops layer 1 only
# spans 0.000385 at %97 = 1. So at 1.0 the gradient traverses a third of a cycle over the whole
# body and lays down one near-constant pale cyan-green -- the "light green that unifies
# everything". Raising it makes the gradient cycle per scale instead.
HEIGHT_SCALE = 1.0

# Mip level to sample the height slices at. DEFAULT 0 -- mipping was tested and REJECTED.
#
# The idea was that the shader gets hardware mips (`SampleBias(..., -0.5)` at %985) while Cycles
# samples full resolution, and since the palette gradient is a cosine, averaging before it is not
# the same as averaging after it. A flatten test seemed to confirm it: replacing every height tap
# with its texture mean made the game's green patches appear and moved chroma from 0.0262 to
# 0.0414 against the game's 0.0384.
#
# But a real mip sweep, 0 through 6, moved chroma by 0.0001 -- nothing. The reason is that
# flattening is NOT what a mip does. A mip smooths detail inside each tile but barely narrows the
# DISTRIBUTION of height values, because the slice is tiled 7-26x across the body; even an 8x8
# mip still spans nearly the full 0..1 range and the gradient still sweeps the same number of
# cycles. Collapsing to a single mean is a different operation entirely, and it is that collapse,
# not mip filtering, that produced the patches.
#
# The user independently confirmed the rejection from the game side: up close, at the highest
# resolution and therefore the lowest mip, the patches are still there. A mip artefact would
# vanish there.
#
# `_mip_img` is kept because it is a correct and useful tool, but nothing should switch this on
# without new evidence.
HEIGHT_MIP = 0
MIP_TAG = "#mip"      # marks a downsampled copy so `_img` never mistakes one for the original
LUMA = (0.2126, 0.7152, 0.0722)
ROUGHNESS_DEFAULT = 0.3     # %2196 initial value, and %2116's fallback


# --------------------------------------------------------------------------- helpers

def _img(path, noncolor=True):
    """Load once and reuse; Blender will happily hold twenty copies of the same file otherwise.

    The colour space is re-asserted on the REUSE path too, not just on load. An image datablock is
    shared, so any throwaway diagnostic material that sets `colorspace_settings` mutates it for
    every material at once -- that happened on 2026-07-25 (a key-mask probe forced the Loki base
    diffuse to Non-Color) and turned the whole animal brown. Rebuilding did not fix it because the
    reuse path returned early. Now a rebuild always repairs the setting.
    """
    tgt = os.path.normcase(os.path.abspath(path))
    want = "Non-Color" if noncolor else "sRGB"
    for i in bpy.data.images:
        # `_mip_img` copies carry the SAME filepath as their base, so they would match here and be
        # handed back as if they were full resolution. Skip them by name.
        if MIP_TAG in i.name:
            continue
        if i.filepath and os.path.normcase(bpy.path.abspath(i.filepath)) == tgt:
            if i.colorspace_settings.name != want:
                i.colorspace_settings.name = want
            return i
    im = bpy.data.images.load(path)
    im.colorspace_settings.name = want
    return im


def _mip_img(path, lod, noncolor=True):
    """The height slice box-filtered down `lod` mip levels, cached as its own datablock.

    WHY THIS EXISTS. The shader samples the height texture through mips -- `SampleBias(..., -0.5)`
    for the accumulator (`%985`) and an explicit `SampleLevel(LOD=%940)` for the 4-tap normal
    (`%1077-%1087`). Cycles does no mip-mapping on an Image Texture node: it samples full
    resolution and relies on many pixel samples for antialiasing.

    That difference is not cosmetic, because the palette gradient is a COSINE of the height.
    Averaging the height and then taking the cosine (what mips do) is not the same as taking the
    cosine and then averaging (what pixel-filtering does). The first gives every layer region one
    coherent colour -- the game's patches. The second sweeps 0.3-1.7 cycles inside each region and
    averages to grey, which is what we rendered for weeks.

    `lod` is in mip levels and is ROUNDED TO AN INTEGER: the image is reduced by 2x box filtering
    `round(lod)` times, exactly as a mip chain is built. Trilinear's fractional blend between two
    levels is not modelled -- it would need two texture nodes and a mix, and the difference is far
    below what we can currently measure against a screenshot.

    NOTE ON THE IMPLEMENTATION. This builds a GENERATED image and writes pixels into it rather
    than `base.copy()` + `Image.scale()`. A copy of a FILE-source image keeps its filepath, so
    Blender reloads the full-resolution pixels from disk and silently discards the scaling -- the
    first attempt did that and produced byte-identical renders at every mip level.
    """
    import numpy as np

    base = _img(path, noncolor=noncolor)
    n = int(round(lod))
    if n <= 0:
        return base
    name = f"{base.name}{MIP_TAG}{n}"
    existing = bpy.data.images.get(name)
    if existing is not None:
        return existing

    w, h = base.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    base.pixels.foreach_get(buf)
    a = buf.reshape(h, w, 4)
    for _ in range(n):
        if a.shape[0] < 2 or a.shape[1] < 2:
            break
        hh, ww = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
        a = a[:hh, :ww].reshape(hh // 2, 2, ww // 2, 2, 4).mean(axis=(1, 3))

    img = bpy.data.images.new(name, a.shape[1], a.shape[0], alpha=True, float_buffer=True)
    img.pixels.foreach_set(np.ascontiguousarray(a, dtype=np.float32).ravel())
    img.update()        # without this the buffer is not committed and reads back as zeros
    img.colorspace_settings.name = "Non-Color" if noncolor else "sRGB"
    return img


def _sock(tree, node, key, value):
    """Set a default, accepting either an index or a socket name."""
    node.inputs[key].default_value = value
    return tree


def _new_group(name, inputs, outputs):
    """(group, group-input node, group-output node), rebuilt from scratch each run.

    An existing group of the same name is REMOVED, not reused, so an edit to this file always
    takes effect.

    That removal is destructive to anything already pointing at the group, which is fine for the
    per-material trees (they are rebuilt in the same call) but was NOT fine for the two shared
    helpers -- see `_shared`, which now adopts an in-use datablock rather than letting this run.
    """
    if name in bpy.data.node_groups:
        bpy.data.node_groups.remove(bpy.data.node_groups[name])
    g = bpy.data.node_groups.new(name, "ShaderNodeTree")
    for n, t in inputs:
        g.interface.new_socket(n, in_out="INPUT", socket_type=t)
    for n, t in outputs:
        g.interface.new_socket(n, in_out="OUTPUT", socket_type=t)
    return g, g.nodes.new("NodeGroupInput"), g.nodes.new("NodeGroupOutput")


def _mk(tree):
    """A tiny node factory: `m("ADD", a, b)` where a/b are sockets or numbers."""
    def m(op, *args, clamp=False, kind="ShaderNodeMath"):
        n = tree.nodes.new(kind)
        if kind == "ShaderNodeMath":
            n.operation = op
            n.use_clamp = clamp
        elif kind == "ShaderNodeVectorMath":
            n.operation = op
        for i, v in enumerate(args):
            if v is None:
                continue
            if hasattr(v, "is_output"):
                tree.links.new(v, n.inputs[i])
            else:
                n.inputs[i].default_value = v
        return n.outputs[0]
    return m


def layout(tree, dx=210, dy=200):
    """Spread a tree left-to-right by dependency depth. Call it after building.

    Nodes made in bulk never get a location, so without this every Math node lands on top of every
    other one at the origin and the graph is unreadable even though it is correct. Depth is the
    longest path from an unconnected input, which is what puts a node to the right of everything it
    consumes; the Group Output is forced to the last column so it never sits mid-graph.
    """
    nodes = list(tree.nodes)
    src = {n: [l.from_node for i in n.inputs for l in i.links] for n in nodes}
    depth = {}

    def d(n, seen=()):
        if n in depth:
            return depth[n]
        if n in seen:                      # shader trees are acyclic, but never hang on a cycle
            return 0
        depth[n] = 0 if not src[n] else 1 + max(d(s, seen + (n,)) for s in src[n])
        return depth[n]

    for n in nodes:
        d(n)
    last = max(depth.values(), default=0)
    for n in nodes:
        if n.type in ("GROUP_OUTPUT", "OUTPUT_MATERIAL"):
            depth[n] = last + 1
        elif n.type == "GROUP_INPUT":
            depth[n] = 0
    cols = {}
    for n in sorted(nodes, key=lambda n: (depth[n], n.name)):
        cols.setdefault(depth[n], []).append(n)
    for col, members in cols.items():
        for i, n in enumerate(members):
            n.location = (col * dx, -i * dy + (len(members) - 1) * dy * 0.5)
    return tree


_SHARED = {}


# The datablock name each shared-helper builder writes. Needed because `_shared` has to look the
# group up in `bpy.data` BEFORE calling the builder -- see below.
SHARED_GROUP_NAMES = {"blend_group": "JWE3_LayerBlend", "satcon_group": "JWE3_SatContrast"}


def _shared(fn):
    """The shared helper group, ADOPTING an existing one that other materials still use.

    This used to rebuild unconditionally whenever the in-process cache was cold, and `build()`
    clears that cache on entry. `_new_group` REMOVES the datablock of the same name -- so building
    any material silently destroyed `JWE3_LayerBlend` and `JWE3_SatContrast` out from under every
    material built earlier, leaving each of their layer groups holding a socketless GROUP node.

    The damage was invisible and severe. With the blend group gone, a layer's mask reached nothing
    and `smoothstep` ran on the literal 0.5 default, so all eight layers composited at a flat 50%
    across the whole animal instead of within their masks. Everything still rendered; it just
    rendered an average of every swatch, and regions no layer covers -- eyes, teeth -- got graded
    when the game leaves them alone. Reloading this module in Blender did it too, since the cache
    lives in module state.

    So: reuse whatever is already in `bpy.data` and in use. The cost is that an edit to
    `blend_group`/`satcon_group` no longer takes effect on a rebuild within the same session --
    delete the datablock (or restart Blender) to pick one up. That is a far smaller trap than
    silently breaking every material already in the file.
    """
    key = fn.__name__
    cached = _SHARED.get(key)
    try:
        if cached is not None and cached.name:      # raises if the datablock was removed
            return cached
    except ReferenceError:
        pass
    name = SHARED_GROUP_NAMES.get(key)
    existing = bpy.data.node_groups.get(name) if name else None
    if existing is not None and existing.users:
        _SHARED[key] = existing
    else:
        _SHARED[key] = fn()
    return _SHARED[key]


def _smoothstep(m, x):
    """`x*x*(3 - 2*x)`. Blender's Math node has no SMOOTHSTEP; Map Range has one but drags in a
    clamp and two extra sockets. Both the mask term (%859-%862) and the albedo weight (%1906-%1909)
    are already in [0,1], so the bare polynomial is exact."""
    return m("MULTIPLY", m("MULTIPLY", x, x), m("SUBTRACT", 3.0, m("MULTIPLY", x, 2.0)))


# --------------------------------------------------------------------------- node groups

def blend_group():
    """One layer's height blend. Outputs the blend factor and both height accumulators.

        delta = (h*B - prevHeight*A) * 20000          # %1880-%1885
        t     = 2*smoothstep(mask) - 1                # %859-%862, %1882-%1883
        soft  = 1 - saturate(|t|)                     # %1886-%1888
        blend = saturate(((delta - t)*soft + t)*0.5 + 0.5)

    NOTE ON `pHeightBlendScaleA == pHeightBlendScaleB == 0`, which is most of Lokiceratops and a
    third of Spinosaurus. `delta` is then identically zero and the expression collapses to
    `blend = t*|t|/2 + 0.5` -- a smooth ramp driven by the mask alone, with the later layer winning
    wherever its mask is high. That IS what the shader computes; there is no hidden fallback in the
    GPU block (`hunt_layer_block.py` reads A and B straight out of memory and they match the FGM).
    cobra-tools' importer substitutes B=1.0 in this case, which is a JWE1/JWE2-era convenience and
    is NOT what JWE3 does -- keep the zero.
    """
    g, gin, gout = _new_group(
        "JWE3_LayerBlend",
        [("PrevHeight", "NodeSocketFloat"), ("LayerHeight", "NodeSocketFloat"),
         ("PrevBump", "NodeSocketFloat"), ("LayerBump", "NodeSocketFloat"),
         ("Mask", "NodeSocketFloat"), ("ScaleA", "NodeSocketFloat"),
         ("ScaleB", "NodeSocketFloat")],
        [("Blend", "NodeSocketFloat"), ("Height", "NodeSocketFloat"),
         ("Bump", "NodeSocketFloat")])
    m = _mk(g)
    sm = _smoothstep(m, gin.outputs["Mask"])
    t = m("MULTIPLY_ADD", sm, 2.0, -1.0)
    delta = m("MULTIPLY",
              m("SUBTRACT",
                m("MULTIPLY", gin.outputs["LayerHeight"], gin.outputs["ScaleB"]),
                m("MULTIPLY", gin.outputs["PrevHeight"], gin.outputs["ScaleA"])),
              SHARPNESS)
    soft = m("SUBTRACT", 1.0, m("ABSOLUTE", t), clamp=True)
    blend = m("MULTIPLY_ADD",
              m("ADD", m("MULTIPLY", m("SUBTRACT", delta, t), soft), t),
              0.5, 0.5, clamp=True)
    g.links.new(blend, gout.inputs["Blend"])
    for src, dst, out in (("PrevHeight", "LayerHeight", "Height"),
                          ("PrevBump", "LayerBump", "Bump")):
        mix = g.nodes.new("ShaderNodeMix")
        mix.data_type = "FLOAT"
        g.links.new(blend, mix.inputs["Factor"])
        g.links.new(gin.outputs[src], mix.inputs[2])
        g.links.new(gin.outputs[dst], mix.inputs[3])
        g.links.new(mix.outputs[0], gout.inputs[out])
    return layout(g)


def satcon_group():
    """Per-layer pDiffuseSaturation / pDiffuseContrast, around the shader's perceptual luma.

        lum = sqrt(dot(rgb, rgb*(0.2126,0.7152,0.0722)))      # %2077-%2081
        out = saturate( (((rgb - lum)*sat + lum - 0.5) * contrast) + 0.5 )

    The luma is a SQRT of a weighted sum of squares, not the usual linear dot product. Using the
    linear form desaturates strongly coloured layers noticeably.
    """
    g, gin, gout = _new_group(
        "JWE3_SatContrast",
        [("Color", "NodeSocketColor"), ("Saturation", "NodeSocketFloat"),
         ("Contrast", "NodeSocketFloat")],
        [("Color", "NodeSocketColor")])
    m = _mk(g)
    sep = g.nodes.new("ShaderNodeSeparateColor")
    g.links.new(gin.outputs["Color"], sep.inputs["Color"])
    weighted = m("MULTIPLY", gin.outputs["Color"], LUMA, kind="ShaderNodeVectorMath")
    dot = m("DOT_PRODUCT", gin.outputs["Color"], weighted, kind="ShaderNodeVectorMath")
    # VectorMath DOT_PRODUCT puts its scalar on the "Value" output, which is outputs[1].
    dot = dot.node.outputs["Value"]
    lum = m("SQRT", dot)
    comb = g.nodes.new("ShaderNodeCombineColor")
    for i, ch in enumerate(("Red", "Green", "Blue")):
        v = m("SUBTRACT", sep.outputs[i], lum)
        v = m("MULTIPLY", v, gin.outputs["Saturation"])
        v = m("ADD", v, m("SUBTRACT", lum, 0.5))
        v = m("MULTIPLY", v, gin.outputs["Contrast"])
        g.links.new(m("ADD", v, 0.5, clamp=True), comb.inputs[ch])
    g.links.new(comb.outputs["Color"], gout.inputs["Color"])
    return layout(g)


# --------------------------------------------------------------------------- the stack

def _layer_uv(nt, uv_out, p, x, y):
    """Tile, offset and rotate the mesh UV into one layer's swatch space.

        a = (uv - pUVOffset) * pUVTile
        p = rotate(a - pivot, angle) + pivot,  pivot = (rotPos.x, rotPos.y - 1)

    `pUVRotationAngle` is a FRACTION OF 180 DEGREES, not radians (%1846-%1850 hold precomputed
    sin/cos; Spinosaurus layer 4 has angle -0.49 and the GPU block holds cos 0.03141 = cos(-0.49pi)).
    The -1 on the pivot's v is %1840 and is easy to miss.
    """
    off = p.get("pUVOffset", [0.0, 0.0])
    tile = p.get("pUVTile", [1.0, 1.0])
    rot = p.get("pUVRotationPosition", [0.0, 0.0])
    ang = p.get("pUVRotationAngle", [0.0])[0] * math.pi
    px, py = rot[0], rot[1] - 1.0

    pre = nt.nodes.new("ShaderNodeVectorMath")     # uv*tile - off*tile - pivot
    pre.operation = "MULTIPLY_ADD"
    pre.location = (x, y)
    nt.links.new(uv_out, pre.inputs[0])
    pre.inputs[1].default_value = (tile[0], tile[1], 1.0)
    pre.inputs[2].default_value = (-off[0] * tile[0] - px, -off[1] * tile[1] - py, 0.0)

    rotn = nt.nodes.new("ShaderNodeVectorRotate")
    rotn.rotation_type = "Z_AXIS"
    rotn.location = (x + 190, y)
    nt.links.new(pre.outputs[0], rotn.inputs["Vector"])
    rotn.inputs["Angle"].default_value = ang

    post = nt.nodes.new("ShaderNodeVectorMath")
    post.operation = "ADD"
    post.location = (x + 380, y)
    nt.links.new(rotn.outputs[0], post.inputs[0])
    post.inputs[1].default_value = (px, py, 0.0)
    return post.outputs[0]


def _slice_path(idx, slot, suffix=""):
    if idx is None or idx < 0 or idx > 254:
        return None
    p = os.path.join(SWATCH_DIR, f"{ARRAY_PREFIX}.{slot}_[{idx:02d}]{suffix}.png")
    return p if os.path.isfile(p) else None


def layer_group(L, mask_path, name):
    """One whole layer as a self-contained node group, so the material is a readable chain of 16.

    A group cannot take an image as an input socket -- images are node properties -- so each layer
    gets its OWN node tree rather than sharing one. That is the price of a tidy top level, and it
    is worth paying: the flat version was 293 nodes in one strip and unreadable.

        in:   UV, PrevHeight, PrevBump, PrevAlbedo, PrevRough, PrevWeight
        out:  Height, Bump, Albedo, Rough, Weight, Blend
    """
    p = L["params"]
    tile = p.get("pUVTile", [1.0, 1.0])
    # Block +0 w1 (`%896`). Read straight out of eleven Lokiceratops layer blocks in RenderDoc
    # captures -- worst relative error 5e-8 against the plain reciprocal, over tiles from 7 to
    # 22.9. It is not capped: an earlier `min(0.4, ...)` was fitted to Spinosaurus layer 7, whose
    # pUVTile is exactly 2.5, so 1/2.5 = 0.4 was the reciprocal all along, not a ceiling.
    # The shader itself computes max(tile) at %922 for the LOD, so this is the CPU uploading the
    # reciprocal of the same quantity: displacement stays proportional to feature size.
    norm = 1.0 / max(max(tile), 1e-6)
    g, gin, gout = _new_group(
        name,
        [("UV", "NodeSocketVector"), ("PrevHeight", "NodeSocketFloat"),
         ("PrevBump", "NodeSocketFloat"), ("PrevAlbedo", "NodeSocketColor"),
         ("PrevRough", "NodeSocketFloat"), ("PrevWeight", "NodeSocketFloat")],
        [("Height", "NodeSocketFloat"), ("Bump", "NodeSocketFloat"),
         ("Albedo", "NodeSocketColor"), ("Rough", "NodeSocketFloat"),
         ("Weight", "NodeSocketFloat"), ("Blend", "NodeSocketFloat")])
    gin.location, gout.location = (-1200, 0), (900, 0)
    m = _mk(g)
    luv = _layer_uv(g, gin.outputs["UV"], p, -1000, 500)

    mtex = g.nodes.new("ShaderNodeTexImage")
    mtex.image = _img(mask_path)
    mtex.location = (-1000, 900)
    g.links.new(gin.outputs["UV"], mtex.inputs["Vector"])
    mask = mtex.outputs["Color"]

    # ---- height, two accumulators through one blend factor
    hp = _slice_path(L["slices"].get("pHeightTexture"), "pheighttexture")
    if hp:
        htex = g.nodes.new("ShaderNodeTexImage")
        htex.image = _mip_img(hp, HEIGHT_MIP)
        htex.extension = "REPEAT"
        htex.location = (-450, 500)
        g.links.new(luv, htex.inputs["Vector"])
        # %977-%978: sample * (%896 * %97) * pHeightScale
        #
        # `norm` FEEDS BOTH ACCUMULATORS, AND THAT IS CORRECT. Do not "fix" it.
        #
        # 2026-08-07 I split them -- bump keeping norm, LayerHeight taking the raw pHeightScale --
        # on the theory that feature-size normalisation is a displacement concern with no meaning
        # for the palette. A region fit against the game's albedo seemed to support it (saturation
        # 0.047 -> 0.138 against the game's 0.143).
        #
        # IT WAS WRONG, and the fit was measuring its own artefact. Removing norm multiplies the
        # palette parameter by the UV tile (7..30x), taking the gradient to ~1700 cycles across the
        # body. Adjacent texels then land on unrelated palette colours and the mesh renders as
        # dense purple/blue SPECKLE -- which also inflates any per-region saturation statistic, so
        # the number improved while the render got obviously worse. Caught by eye in Blender, not
        # by the measurement. See [[verify-measurement-apparatus]].
        #
        # The reciprocal keeps the palette parameter in a sane range; that is what it is for.
        h_bump = m("MULTIPLY", htex.outputs["Color"],
                   p.get("pHeightScale", [0.0])[0] * norm * HEIGHT_SCALE)
        # %979-%980: %97 * 0.01 * blockHeightOffset, and the block stores pHeightOffset RAW,
        # so this term really is a hundredth of the FGM value.
        #
        # SETTLED 2026-07-25 by `hunt_height_offset.py` on Spinosaurus, which has eleven layers
        # with a nonzero offset against Lokiceratops' one. Five were found and all five read the
        # raw value to within 2.4e-4 relative (0.14001 vs 0.14, 0.23999 vs 0.24, ...). Zero votes
        # for x100. Do not "fix" the 0.01 away: an earlier attempt, based on a vacuous check that
        # had only ever run on layers whose offset is 0, turned the whole animal brown.
        h_full = m("ADD", h_bump, p.get("pHeightOffset", [0.0])[0] * 0.01 * HEIGHT_SCALE)
    else:
        h_bump = h_full = 0.0

    bn = g.nodes.new("ShaderNodeGroup")
    bn.node_tree = _shared(blend_group)
    bn.location = (200, 500)
    for key, val in (("LayerHeight", h_full), ("LayerBump", h_bump),
                     ("PrevHeight", gin.outputs["PrevHeight"]),
                     ("PrevBump", gin.outputs["PrevBump"]), ("Mask", mask)):
        if hasattr(val, "is_output"):
            g.links.new(val, bn.inputs[key])
        else:
            bn.inputs[key].default_value = val
    bn.inputs["ScaleA"].default_value = p.get("pHeightBlendScaleA", [0.0])[0]
    bn.inputs["ScaleB"].default_value = p.get("pHeightBlendScaleB", [0.0])[0]
    blend = bn.outputs["Blend"]
    g.links.new(bn.outputs["Height"], gout.inputs["Height"])
    g.links.new(bn.outputs["Bump"], gout.inputs["Bump"])
    g.links.new(blend, gout.inputs["Blend"])

    # ---- albedo: diffuse -> remap LUT -> saturation/contrast, weighted by smoothstep(blend)
    dp = _slice_path(L["slices"].get("pDiffuseTexture"), "pdiffusetexture")
    sn = g.nodes.new("ShaderNodeGroup")
    sn.node_tree = _shared(satcon_group)
    sn.location = (200, 150)
    if dp:
        dtex = g.nodes.new("ShaderNodeTexImage")
        dtex.image = _img(dp, noncolor=False)
        dtex.extension = "REPEAT"
        dtex.location = (-450, 150)
        g.links.new(luv, dtex.inputs["Vector"])
        col = _remap(g, m, dtex.outputs["Color"], L)
        g.links.new(col, sn.inputs["Color"])
    else:
        sn.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)   # %2102-%2104 fallback
    sn.inputs["Saturation"].default_value = p.get("pDiffuseSaturation", [1.0])[0]
    sn.inputs["Contrast"].default_value = p.get("pDiffuseContrast", [1.0])[0]
    amix = g.nodes.new("ShaderNodeMix")
    amix.data_type = "RGBA"
    amix.location = (550, 150)
    g.links.new(_smoothstep(m, blend), amix.inputs["Factor"])
    g.links.new(gin.outputs["PrevAlbedo"], amix.inputs[6])
    g.links.new(sn.outputs["Color"], amix.inputs[7])
    g.links.new(amix.outputs[2], gout.inputs["Albedo"])

    # ---- roughness from pPackedTexture.r, weighted by the raw blend (%1903, %2155)
    pp = _slice_path(L["slices"].get("pPackedTexture"), "ppackedtexture", "_RGB")
    rmix = g.nodes.new("ShaderNodeMix")
    rmix.data_type = "FLOAT"
    rmix.location = (550, -200)
    g.links.new(blend, rmix.inputs["Factor"])
    g.links.new(gin.outputs["PrevRough"], rmix.inputs[2])
    if pp:
        ptex = g.nodes.new("ShaderNodeTexImage")
        ptex.image = _img(pp)
        ptex.extension = "REPEAT"
        ptex.location = (-450, -200)
        g.links.new(luv, ptex.inputs["Vector"])
        sep = g.nodes.new("ShaderNodeSeparateColor")
        sep.location = (200, -200)
        g.links.new(ptex.outputs["Color"], sep.inputs["Color"])
        g.links.new(sep.outputs[0], rmix.inputs[3])
    else:
        rmix.inputs[3].default_value = ROUGHNESS_DEFAULT
    g.links.new(rmix.outputs[0], gout.inputs["Rough"])

    # ---- colour weight, accumulator %832, on the smoothstep(blend) curve like albedo.
    # This is what section 6 lerps with: out = lerp(albedo, gradedColour, colourWeight).
    #
    # It is a PRODUCT OF TWO VALUES, both packed into layer-block word 18 (`%2120 * %2148`, the
    # abs of the low f16 times the high one):
    #
    #   * the SWATCH's own pGlobalColouringWeight, from the shared SwatchLibrary. A hard veto --
    #     `Swatch_Bone`, `Swatch_Nail` and `Swatch_Mouth_Flesh` are 0, so beaks, horns, claws and
    #     tongues are NEVER repainted by the palette, on any dinosaur.
    #   * the TRANSFORM FGM's pGlobalColouringWeight, which the variant sets per layer.
    #
    # Using only the second one repaints every beak and tongue in the game. Verified against nine
    # layer blocks pulled from RenderDoc captures across two species -- see
    # `hunt_colour_weight.py` and `layer_chain.swatch_colour_weights`.
    wmix = g.nodes.new("ShaderNodeMix")
    wmix.data_type = "FLOAT"
    g.links.new(_smoothstep(m, blend), wmix.inputs["Factor"])
    g.links.new(gin.outputs["PrevWeight"], wmix.inputs[2])
    wmix.inputs[3].default_value = (abs(L.get("swatch_colour_weight", 1.0))
                                    * p.get("pGlobalColouringWeight", [1.0])[0])
    g.links.new(wmix.outputs[0], gout.inputs["Weight"])
    return layout(g)


def _remap(g, m, colour, L):
    """The per-layer remap LUT -- where nearly all of a dinosaur's colour actually comes from.

    The swatch diffuse slices are almost greyscale. %2003-%2017 turn one into colour by using its
    luminance as a lookup into `pRemapTexture`, a 32x16 array slice:

        u = dot(rgb, (0.2126,0.7152,0.0722)) * 0.96875 + 0.015625     # (luma*31 + 0.5)/32
        v = pRemapLutIndex * 0.0625 + 0.03125                         # (row + 0.5)/16

    so the LUT is 32 luminance steps across and 16 selectable rows down. This luma is the plain
    linear dot, NOT the sqrt-of-squares one used for saturation a few instructions later.

    `pRemapLutIndex == -1` means "this layer takes no remap" and the diffuse passes through
    unchanged -- it is data, not a missing value (Lokiceratops variant 5 uses -1 on eight layers).

    **V IS FLIPPED.** The extracted PNG keeps DirectX row order (row 0 at the top) but Blender's
    image V runs from the bottom, so the row must be `1 - (idx + 0.5)/16`. Getting this wrong is
    not subtle once you know what to look for and invisible until then: rows 6-15 of most LUTs are
    pure red filler, so every layer came out scarlet in blotches shaped like its mask. The red
    filler is a useful canary -- if a remap render goes red, the V flip is wrong.
    """
    idx = int(L["params"].get("pRemapLutIndex", [-1])[0])
    rp = _slice_path(L["slices"].get("pRemapTexture"), "premaptexture")
    if idx < 0 or rp is None:
        return colour
    lum = m("DOT_PRODUCT", colour, LUMA, kind="ShaderNodeVectorMath").node.outputs["Value"]
    u = m("MULTIPLY_ADD", lum, 0.96875, 0.015625)
    uvn = g.nodes.new("ShaderNodeCombineXYZ")
    uvn.location = (-150, 150)
    g.links.new(u, uvn.inputs["X"])
    uvn.inputs["Y"].default_value = 1.0 - (idx * 0.0625 + 0.03125)      # V flip, see above
    rtex = g.nodes.new("ShaderNodeTexImage")
    rtex.image = _img(rp, noncolor=False)
    rtex.extension = "EXTEND"       # a 32x16 LUT must clamp, never wrap
    rtex.location = (0, 150)
    g.links.new(uvn.outputs[0], rtex.inputs["Vector"])
    return rtex.outputs["Color"]


def _base_texture(tex_dir, prefix, slot, channel, fgm_path=None):
    """The PNG for one base-material slot: the .fgm's own dependency name first, prefix second.

    The FGM is the authority and cobra-tools reads it the same way -- `<textureinfo>` gives the slot
    and `<dependency_name>` the file, so nothing has to be reconstructed from the species name.
    `pyroraptor.fgm` really does name `pyroraptor.pbasediffusetexture.tex` and
    `pyroraptor.playered_blendweights.tex`; this used to ignore all of it and build
    `f"{prefix}.pbasediffusetexture.png"` from a prefix sniffed off whichever blend-weights file
    happened to be in the folder.

    That assumption -- one prefix per folder -- is not guaranteed. Pyroraptor's FEATHERS material
    already breaks it, naming `pyroraptor_feathers.*` and shared-library `feathers.*` side by side.
    It happens to hold for the body on all six extracted species, so this is a latent trap rather
    than a live bug, and the prefix path stays as a fallback for folders with no base .fgm.

    Returns None when the slot is absent, is an inline RGBA placeholder, or has no file on disk.
    """
    if fgm_path and os.path.isfile(fgm_path):
        try:
            dep = part_manifest.fgm_slots(fgm_path).get(slot)
        except Exception:
            dep = None
        if dep:
            hit = part_manifest.texture_files(dep, tex_dir).get(channel)
            if hit:
                return hit
    p = os.path.join(tex_dir, "%s.%s%s.png"
                     % (prefix, slot.lower(), "_" + channel if channel else ""))
    return p if os.path.isfile(p) else None


def base_group(tex_dir, prefix, levels=(0.0, 0.5, 1.0), name="JWE3_Base", fgm_path=None,
               diffuse_override=None):
    """The species' own base maps -- the part the 16 layers sit ON TOP OF.

    Leaving these out was the single biggest error in the first node build: the layer stack alone is
    near-greyscale detail, so a material without the base diffuse has no species colouring at all
    and the palette grade then blows out to white. Baryonyx is the clearest case -- most of its
    visible scale detail is in `pBaseNormalTexture`, not in any layer.

        %2522-%2549  base diffuse through a Photoshop-style Levels (in-black, mid, in-white from
                     `pBaseDiffuseLevelsInput`), then saturate(x - 1/255)
        %2549-%2588  overlay(that, layerAlbedo)   <- base is the BASE DIFFUSE, blend is the layers

    The exponent is `1 / max(mid < 0.5 ? 2*mid : 0.5/(1 - mid), 1/511)` (%2516-%2521), a constant, so
    it collapses to one Power node.

    `pBaseNormalTexture` extracts as three files: `_RG` holds x and y, and z has to be rebuilt as
    `sqrt(1 - x^2 - y^2)`. Feeding the two-channel image straight into a Normal Map node treats the
    blue channel as z and flattens everything.
    """
    g, gin, gout = _new_group(
        name,
        [("UV", "NodeSocketVector")],
        [("Diffuse", "NodeSocketColor"), ("RawDiffuse", "NodeSocketColor"),
         ("Normal", "NodeSocketVector"), ("AO", "NodeSocketFloat")])
    m = _mk(g)
    uv = gin.outputs["UV"]

    lo, mid, hi = levels
    gamma = max(2.0 * mid if mid < 0.5 else 0.5 / max(1.0 - mid, 1e-6), 1.0 / 511.0)
    inv_span = 1.0 / max(hi - lo, 1e-6)

    # A VARIANTSET overrides the species base diffuse and nothing else. `DinosaurLayered_VariantSet`
    # FGMs carry exactly two textures (pBaseDiffuseTexture, pFeathersBaseDiffuseTexture) and four
    # flags -- they are the COSMETIC SKIN, not a colour grade. The layer stack, masks, height and
    # roughness are all unchanged, so swapping this one path is the whole feature.
    dp = diffuse_override or _base_texture(tex_dir, prefix, "pBaseDiffuseTexture", "", fgm_path)
    if dp:
        dtex = g.nodes.new("ShaderNodeTexImage")
        dtex.image = _img(dp, noncolor=False)
        g.links.new(uv, dtex.inputs["Vector"])
        sep = g.nodes.new("ShaderNodeSeparateColor")
        g.links.new(dtex.outputs["Color"], sep.inputs["Color"])
        comb = g.nodes.new("ShaderNodeCombineColor")
        for i in range(3):
            n = m("MULTIPLY", m("SUBTRACT", sep.outputs[i], lo), inv_span, clamp=True)
            v = m("POWER", n, 1.0 / gamma)
            g.links.new(m("SUBTRACT", v, 1.0 / 255.0, clamp=True), comb.inputs[i])
        g.links.new(comb.outputs["Color"], gout.inputs["Diffuse"])
        # RAW, pre-levels: the palette's key-colour mask is measured against this (%2705-%2707),
        # NOT against the composited albedo. Using the composite inverts the warm/cool split.
        g.links.new(dtex.outputs["Color"], gout.inputs["RawDiffuse"])
    else:
        gout.inputs["Diffuse"].default_value = (0.5, 0.5, 0.5, 1.0)
        gout.inputs["RawDiffuse"].default_value = (0.5, 0.5, 0.5, 1.0)
        print(f"  no base diffuse for {prefix} (pBaseDiffuseTexture)")

    np_ = _base_texture(tex_dir, prefix, "pBaseNormalTexture", "RG", fgm_path)
    if np_:
        ntex = g.nodes.new("ShaderNodeTexImage")
        ntex.image = _img(np_)
        g.links.new(uv, ntex.inputs["Vector"])
        nsep = g.nodes.new("ShaderNodeSeparateColor")
        g.links.new(ntex.outputs["Color"], nsep.inputs["Color"])
        x = m("MULTIPLY_ADD", nsep.outputs[0], 2.0, -1.0)
        y = m("MULTIPLY_ADD", nsep.outputs[1], 2.0, -1.0)
        zsq = m("SUBTRACT", m("SUBTRACT", 1.0, m("MULTIPLY", x, x)), m("MULTIPLY", y, y),
                clamp=True)
        z = m("SQRT", zsq)
        ncomb = g.nodes.new("ShaderNodeCombineColor")
        g.links.new(nsep.outputs[0], ncomb.inputs[0])
        g.links.new(nsep.outputs[1], ncomb.inputs[1])
        g.links.new(m("MULTIPLY_ADD", z, 0.5, 0.5), ncomb.inputs[2])
        nm = g.nodes.new("ShaderNodeNormalMap")
        nm.uv_map = "UV0"
        g.links.new(ncomb.outputs["Color"], nm.inputs["Color"])
        g.links.new(nm.outputs["Normal"], gout.inputs["Normal"])
        g["jwe3_has_normal"] = True
    else:
        # Leave the group output UNCONNECTED and say so. An unlinked NodeSocketVector reads as
        # (0,0,0) -- a zero-length normal -- and feeding that to Bump/Principled renders the
        # whole animal a flat purple. The caller must skip the link, not paper over it here.
        g["jwe3_has_normal"] = False
        print(f"  no base normal for {prefix} (pBaseNormalTexture)")

    ap = _base_texture(tex_dir, prefix, "pBaseAOTexture", "R", fgm_path)
    if ap:
        atex = g.nodes.new("ShaderNodeTexImage")
        atex.image = _img(ap)
        g.links.new(uv, atex.inputs["Vector"])
        g.links.new(atex.outputs["Color"], gout.inputs["AO"])
    else:
        gout.inputs["AO"].default_value = 1.0
    return layout(g)


# The tail's layout, as three rules rather than fixed coordinates. Taken from an arrangement the
# user made by hand, which reads far better than the depth-sort `layout()` produces:
#
#   * the ALBEDO flow runs left to right on one raised line;
#   * the base-diffuse group sits beside its CONSUMERS at the end of the layer chain, not back at
#     the start with the UV node -- depth-sorting put it at x=340 while the Mix that reads it was at
#     x=3060, so the one link that matters crossed the whole graph;
#   * auxiliary inputs (fur mask, pattern index map) and the normal path (Bump) sit BELOW the line,
#     so the colour chain reads uninterrupted.
#
# `blender_parts.layout_chain` continues the same line for the spliced grade/pattern groups.
TAIL_LINE_Y = 120.0          # the albedo flow
TAIL_BASE_DY = 178.0         # base diffuse, above the line
TAIL_LOW_Y = -203.0          # Bump and the aux textures
TAIL_DX = 300.0


def _layout_tail(nt, prev, base):
    """Place the base group, the albedo mixes and the Bump on the tail rules above."""
    if prev is None:
        return
    x = prev.location.x
    base.location = (x - 38.0, TAIL_BASE_DY)
    # The albedo mixes are NOT placed here: `blender_parts.layout_chain` walks the actual links and
    # lays the whole tail out in link order, which is the only way to get the AO multiply on the
    # correct side of the grade. This function owns the base group and the normal path only.
    x += TAIL_DX + 208.0
    bump = next((n for n in nt.nodes if n.type == "BUMP"), None)
    if bump is not None:
        bump.location = (x, TAIL_LOW_Y)
    # bsdf/out are placed by layout_chain, at the end of the link-ordered row.


def build(layers, mask_dir, mask_prefix, mat_name="JWE3_Layered", bump_distance=1.0,
          levels=(0.0, 0.5, 1.0), use_ao=True, fgm_path=None, base_diffuse_override=None):
    """Build the material as a left-to-right chain of per-layer groups.

    Every layer that has a mask channel is included, even if it has no texture in a given slot --
    the shader still blends those, and dropping one silently changes the stack order.
    """
    _SHARED.clear()
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UV0"
    uv.location = (-400, 0)

    # The blend-weight masks: stem from the .fgm's own `pLayered_BlendWeights` dependency when it
    # names one, else the sniffed folder prefix. The `_[NN]_C` tail is an ARRAY INDEX plus a channel
    # split, added by cobra-tools' extraction -- it is not part of the name the .fgm carries, so
    # this is the one place the suffix still has to be constructed.
    mask_stem = f"{mask_prefix}.playered_blendweights"
    if fgm_path and os.path.isfile(fgm_path):
        try:
            dep = part_manifest.fgm_slots(fgm_path).get("pLayered_BlendWeights")
        except Exception:
            dep = None
        if dep:
            mask_stem = os.path.splitext(os.path.basename(dep))[0]

    prev = None
    x = 0
    used = 0
    for L in layers:
        if not L["used"] or L["blend_texture"] is None:
            continue
        mp = os.path.join(mask_dir, f"{mask_stem}_"
                                    f"[{L['blend_texture']:02d}]_{L['blend_channel']}.png")
        if not os.path.isfile(mp):
            print(f"  L{L['layer_no']:02d} skipped: no mask {os.path.basename(mp)}")
            continue
        gn = nt.nodes.new("ShaderNodeGroup")
        gn.node_tree = layer_group(L, mp, f"{mat_name}_L{L['layer_no']:02d}")
        gn.location = (x, 0)
        gn.width = 220
        gn.label = f"L{L['layer_no']:02d} {L['swatch']}"
        nt.links.new(uv.outputs["UV"], gn.inputs["UV"])
        if prev is None:
            # the shader's initial accumulator values, %2192-%2199
            gn.inputs["PrevAlbedo"].default_value = (0.5, 0.5, 0.5, 1.0)
            gn.inputs["PrevRough"].default_value = ROUGHNESS_DEFAULT
            gn.inputs["PrevWeight"].default_value = 0.0      # %2192 starts at zero
        else:
            for key in ("Height", "Bump", "Albedo", "Rough", "Weight"):
                nt.links.new(prev.outputs[key], gn.inputs["Prev" + key])
        prev = gn
        x += 300
        used += 1

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])

    base = nt.nodes.new("ShaderNodeGroup")
    base.node_tree = base_group(mask_dir, mask_prefix, levels, f"{mat_name}_Base", fgm_path,
                                diffuse_override=base_diffuse_override)
    base.width = 220
    base.label = "base diffuse / normal / AO"
    nt.links.new(uv.outputs["UV"], base.inputs["UV"])

    if prev is not None:
        # albedo: the layers OVERLAY the base diffuse (%2549-%2588), base first, layers second
        ov = nt.nodes.new("ShaderNodeMix")
        ov.data_type = "RGBA"
        ov.blend_type = "OVERLAY"
        ov.inputs["Factor"].default_value = 1.0
        nt.links.new(base.outputs["Diffuse"], ov.inputs[6])
        nt.links.new(prev.outputs["Albedo"], ov.inputs[7])
        albedo = ov.outputs[2]

        if use_ao:
            # Principled has no AO input, so it multiplies the albedo. Not what the deferred
            # renderer does (AO goes to the GBuffer) but it is the closest single-BSDF equivalent.
            ao = nt.nodes.new("ShaderNodeMix")
            ao.data_type = "RGBA"
            ao.blend_type = "MULTIPLY"
            ao.inputs["Factor"].default_value = 1.0
            nt.links.new(albedo, ao.inputs[6])
            nt.links.new(base.outputs["AO"], ao.inputs[7])
            albedo = ao.outputs[2]

        nt.links.new(albedo, bsdf.inputs["Base Color"])
        nt.links.new(prev.outputs["Rough"], bsdf.inputs["Roughness"])
        # the layer bump PERTURBS the base normal -- it does not replace it. Baryonyx carries most
        # of its scale detail in pBaseNormalTexture, so dropping this loses the whole animal.
        bn = nt.nodes.new("ShaderNodeBump")
        bn.inputs["Distance"].default_value = bump_distance
        nt.links.new(prev.outputs["Bump"], bn.inputs["Height"])
        # ONLY if the base group actually produced a normal. Its "Normal" output is left
        # unconnected when pBaseNormalTexture is missing, and an unlinked vector socket reads as
        # (0,0,0): a zero-length normal, which renders as flat purple over the entire mesh.
        # Left unlinked, Bump falls back to the true surface normal, which is what we want.
        if base.node_tree.get("jwe3_has_normal"):
            nt.links.new(base.outputs["Normal"], bn.inputs["Normal"])
        nt.links.new(bn.outputs["Normal"], bsdf.inputs["Normal"])
        mat["jwe3_albedo_node"] = ov.name          # the palette replaces what feeds Base Color
    mat["jwe3_last_layer"] = prev.name if prev else ""    # the palette hooks onto its Height
    mat["jwe3_base_node"] = base.name
    layout(nt, dx=320, dy=260)
    _layout_tail(nt, prev, base)
    print(f"{mat.name}: {used} layers wired")
    return mat


def preview_albedo(mat, on=True):
    """Route the final albedo straight to the surface as pure emission, or restore the BSDF.

    For CHECKING COLOUR against a screenshot this is the only honest comparison: it removes the
    lighting rig, the AO term and the view transform, so what you see is the albedo the shader
    computed. Judging a colour model through a Cycles sun and Filmic is how you end up chasing a
    palette bug that is really an exposure difference.

    Turn it off again before judging relief -- an emissive surface has no shading, so the bump and
    normal do nothing at all.
    """
    nt = mat.node_tree
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    em = nt.nodes.get("JWE3_AlbedoPreview")
    if not on:
        if em:
            nt.nodes.remove(em)
        bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
        nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
        return None
    if em is None:
        em = nt.nodes.new("ShaderNodeEmission")
        em.name = "JWE3_AlbedoPreview"
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    src = bsdf.inputs["Base Color"].links
    if not src:
        raise ValueError("nothing feeding Base Color -- build the material first")
    nt.links.new(src[0].from_socket, em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    layout(nt, dx=340, dy=280)
    return em


def build_from_json(json_path, mask_dir, mask_prefix, **kw):
    d = json.load(open(json_path))
    kw.setdefault("fgm_path", find_base_fgm(mask_dir, mask_prefix))
    kw.setdefault("mat_name", f"JWE3_{d['species']}_{d['sex']}")
    return build(d["layers"], mask_dir, mask_prefix, **kw)


def selftest():
    """Build both groups and a two-layer stack from synthetic data, and check the maths."""
    # Go through `_shared`, NOT the builders directly. Calling blend_group() here removes the live
    # JWE3_LayerBlend datablock, which is precisely how running this selftest used to break every
    # material already in the scene.
    g = _shared(blend_group)
    assert {s.name for s in g.interface.items_tree if s.item_type == "SOCKET"} >= \
        {"PrevHeight", "LayerHeight", "Mask", "ScaleA", "ScaleB", "Blend", "Height", "Bump"}
    _shared(satcon_group)

    # the A == B == 0 collapse, computed the way the group does
    def blend_of(mask, delta=0.0):
        sm = mask * mask * (3 - 2 * mask)
        t = 2 * sm - 1
        soft = max(0.0, 1.0 - abs(t))
        return min(1.0, max(0.0, ((delta - t) * soft + t) * 0.5 + 0.5))
    assert blend_of(0.0) == 0.0 and blend_of(1.0) == 1.0
    assert abs(blend_of(0.5) - 0.5) < 1e-12
    assert 0.94 < blend_of(0.9) < 0.95, blend_of(0.9)      # a high mask does replace, by design

    layers = [{"used": True, "layer_no": 1, "swatch": "T", "blend_texture": 0,
               "blend_channel": "R", "slices": {}, "params": {"pUVTile": [8.0, 8.0]}},
              {"used": False, "layer_no": 2, "swatch": "None", "blend_texture": 0,
               "blend_channel": "G", "slices": {}, "params": {}}]
    # --- texture resolution is NAME-DRIVEN, with the prefix only as a fallback.
    #     cobra-tools reads `<textureinfo>` + `<dependency_name>` and matches
    #     `<stem>.` / `<stem>_` on disk; we now do the same, because a prefix built from the species
    #     name cannot resolve a material that mixes prefixes -- and Pyroraptor's feathers do.
    # --- a missing base normal must leave the group's Normal output UNCONNECTED and flag it.
    #     An unlinked NodeSocketVector reads as (0,0,0); feeding that zero-length normal to
    #     Bump/Principled renders the whole animal flat purple. `build` checks the flag.
    ng = base_group("Z:/nonexistent", "none", name="JWE3_selftest_nonormal")
    assert ng.get("jwe3_has_normal") is False, ng.get("jwe3_has_normal")
    gout_ = next(n for n in ng.nodes if n.type == "GROUP_OUTPUT")
    assert not gout_.inputs["Normal"].links, "Normal was wired despite having no texture"
    bpy.data.node_groups.remove(ng)

    assert find_base_fgm("Z:/nonexistent", "anything") is None
    assert find_base_fgm(None, None) is None
    assert _base_texture("Z:/nonexistent", "none", "pBaseDiffuseTexture", "", None) is None
    # a bogus fgm_path must fall through to the prefix path, never raise
    assert _base_texture("Z:/nonexistent", "none", "pBaseDiffuseTexture", "",
                         "Z:/nonexistent/none.fgm") is None

    # --- REGRESSION: building one material must not destroy another's shared groups.
    #
    # `build()` clears the in-process cache, and `_new_group` removes any datablock of the same
    # name. So a second build used to delete JWE3_LayerBlend / JWE3_SatContrast out from under the
    # first material, leaving socketless GROUP nodes inside every one of its layer groups. Nothing
    # errored: the masks simply stopped reaching the blend, `smoothstep` ran on its 0.5 default,
    # and all the layers composited at a flat 50% over the whole animal.
    #
    # Stand in for an already-built material with a bare node group that points at the shared one.
    canary = bpy.data.node_groups.new("JWE3_selftest_canary", "ShaderNodeTree")
    ref = canary.nodes.new("ShaderNodeGroup")
    ref.node_tree = _shared(blend_group)
    shared_name = ref.node_tree.name

    mat = build(layers, mask_dir="Z:/nonexistent", mask_prefix="none",
                mat_name="JWE3_selftest")
    # no masks on disk, so nothing wires up, but the material and output must still exist
    assert any(n.type == "OUTPUT_MATERIAL" for n in mat.node_tree.nodes)

    assert ref.node_tree is not None, (
        "build() destroyed the shared group a previously-built material was using -- every layer "
        "mask in it is now disconnected. See _shared.")
    assert ref.node_tree.name == shared_name, ref.node_tree.name
    bpy.data.node_groups.remove(canary)

    bpy.data.materials.remove(mat)
    print("selftest ok")


if __name__ == "__main__":
    selftest()
