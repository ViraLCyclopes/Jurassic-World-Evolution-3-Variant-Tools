"""Build the JWE3_Pattern node group and splice it over the palette grade.

The LUT crosses into Blender as a generated 32x3 image, mirroring the game: there
`pPatterning_PatternGradientMap` is an inline RGBA placeholder in the FGM, not a texture reference,
because the map is baked CPU-side from the pattern's keys and bound bindlessly (PATTERNS.md 3).

TWO PATHS, selectable by the group's `Source` input:

  Source = 0  the BAKED IMAGE -- what the hardware actually samples. 32 texels, addressed on texel
              centres. This is the game-faithful path and the default.
  Source = 1  the COLOR RAMP -- the artist's authored keys, continuous, and editable by dragging
              stops in the node editor. Strictly more precise: no 32-texel quantisation.

Keeping both is what lets the two be diffed, which is how PATTERNS.md open question 1 (what curve
the game interpolates its keys with) gets settled rather than assumed.

An earlier note here claimed a ColorRamp "cannot carry colour, emissive and opacity in one node".
That was wrong and is corrected: a ramp element is RGBA and the node has a separate `Alpha` output,
so ONE ramp carries colour and opacity. Colour and opacity are authored at different positions, so
the stops are placed on the union of the two sets -- exact for linear interpolation, and 12 stops
in the worst case against a limit of 32.

WHAT DRIVES `Index`. The per-texel index is a species-level texture, `u_basePatternMap` in
`<species>_patternset_01.fgm` (`u_feathersBasePatternMap` for the plumage), and PATTERNS.md 4.6
measures the mapping as `index = v/255 * 31`. Sampling a 32-wide LUT at that index means landing on
TEXEL CENTRES:

    u = (index + 0.5) / 32 = v01 * 31/32 + 0.5/32 = v01 * 0.96875 + 0.015625

which is the same remap `blender_layer_nodes._remap` already uses for the per-layer LUTs -- an
independent corroboration of the formula rather than a coincidence. The group therefore takes
`Index` as the map's RAW 0..1 value and does that remap internally, so feeding it the pattern map
directly is correct with nothing in between.

Run:  exec(open(__file__).read()); selftest()      # INSIDE Blender
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
# `patchwork` lives at the package root, beside fgm_io/coeff_store, and ships with the add-on.
PKG = os.path.dirname(HERE)
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import blender_parts
import patchwork

GROUP_PREFIX = "JWE3_Pattern"

# Row order in the generated image, TOP first. `lut_image` writes them reversed because Blender's
# row 0 is the bottom one.
ROWS = ("colour", "emissive", "opacity")

# index -> U, landing on texel centres of a 32-wide LUT. See the module docstring.
U_SCALE, U_OFFSET = 31.0 / 32.0, 0.5 / 32.0


def lut_image(name, lut):
    """The pattern's 32x3 LUT as a float image: row 0 colour, row 1 emissive, row 2 opacity.

    Reused by name so re-applying a pattern does not leak a datablock per apply. The colour space
    is re-asserted on the reuse path as well as on create: image datablocks are SHARED, and a stale
    `colorspace_settings` is invisible in the node editor while silently gamma-shifting every
    lookup -- the same trap `blender_layer_nodes._img` documents.

    ORDER MATTERS, and not in a way the API hints at: **assigning `colorspace_settings.name` after
    writing `pixels` WIPES the buffer back to zero.** Verified directly -- same image, same write,
    colour space set before the write keeps the data and set after leaves every texel 0. The whole
    LUT read back black, so the pattern mixed the mesh toward pure black at full opacity. So set
    the colour space (and the generated size) FIRST and write the pixels LAST.
    """
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(name, width=32, height=len(ROWS),
                                  float_buffer=True, is_data=True)
    img.generated_width, img.generated_height = 32, len(ROWS)
    img.colorspace_settings.name = "Non-Color"
    px = []
    for row in reversed(ROWS):                  # bottom row first
        vals = lut[row]
        for x in range(32):
            v = vals[x]
            px.extend([v[0], v[1], v[2], 1.0] if len(v) >= 3 else [v[0], v[0], v[0], 1.0])
    img.pixels.foreach_set(px)
    # FLUSH THE BUFFER, THEN DROP THE GPU COPY. `pixels.foreach_set` writes the CPU-side buffer
    # only. `update()` marks it dirty, but that alone was NOT enough in practice -- the viewport
    # kept showing the previous LUT until the shading mode was toggled, which is just a heavy-handed
    # way of forcing a re-upload. `gl_free()` releases the uploaded texture so the next draw has to
    # fetch the new pixels.
    #
    # This matters because the image is reused BY NAME (above), so an edited pattern writes into the
    # same datablock the GPU is already holding. The workaround people reach for -- unplug and
    # replug the node -- recompiles the shader and so appears to fix it, which hides the real cause.
    img.update()
    try:
        img.gl_free()
    except Exception:
        pass                    # not fatal: worst case the viewport needs a nudge, as before
    return img


def _row_v(i):
    """V coordinate of LUT row `i`, given Blender's bottom-up V.

    Row 0 is the TOP of our array but the LAST row Blender stores, so this counts down. Getting it
    wrong is self-announcing rather than subtle: the colour row reads as the opacity row and the
    mesh renders flat grey.
    """
    return 1.0 - (i + 0.5) / len(ROWS)


LUT_SIZE = 32
SOURCE_IMAGE, SOURCE_RAMP = 0.0, 1.0


def ramp_stops(model):
    """[(pos01, r, g, b, opacity)] -- the authored keys, on the UNION of their positions.

    Colour and opacity are authored on INDEPENDENT position sets (Pyroraptor 01_02: colour at
    6/11/14/15/16/17/31, opacity at 1/14/16/18/25), and one ColorRamp has a single set of stops.
    Evaluating both channels at the union is exact for linear interpolation -- adding a knot to a
    piecewise-linear curve does not change it -- so nothing is lost by merging them.

    A stop at `-1` means the slot is unused, not a key at position -1. Sizes are comfortable: the
    worst case across all twelve Pyroraptor patterns is 12 stops against ColorRamp's limit of 32.

    Position maps as `pos / (LUT_SIZE - 1)`, which is exactly the raw pattern-map value: the map
    gives `index = v01 * 31`, so `index / 31 == v01`. The RAMP therefore takes the map value with
    NO remap, where the image path needs the texel-centre shift. Same data, different addressing.
    """
    ck = sorted((p, v) for p, v in model.get("colourKeys", []) if p >= 0)
    ok = sorted((p, v) for p, v in model.get("opacityKeys", []) if p >= 0)
    if not ck and not ok:
        return []

    def at(keys, pos, default):
        if not keys:
            return default
        if pos <= keys[0][0]:
            return keys[0][1]
        if pos >= keys[-1][0]:
            return keys[-1][1]
        for (p0, v0), (p1, v1) in zip(keys, keys[1:]):
            if p0 <= pos <= p1:
                t = 0.0 if p1 == p0 else (pos - p0) / (p1 - p0)
                if isinstance(v0, (list, tuple)):
                    return [v0[i] + (v1[i] - v0[i]) * t for i in range(len(v0))]
                return v0 + (v1 - v0) * t
        return default

    out = []
    for pos in sorted({p for p, _ in ck} | {p for p, _ in ok}):
        col = at(ck, pos, [0.0, 0.0, 0.0])
        opa = at(ok, pos, 0.0)
        out.append((pos / float(LUT_SIZE - 1), col[0], col[1], col[2], opa))
    return out


def _build_ramp(g, stops, index_socket, label):
    """A ColorRamp carrying colour in RGB and opacity in A. Returns (colour_socket, alpha_socket).

    Opacity rides in the stop's ALPHA rather than in a second ramp: a ColorRamp element is RGBA and
    the node exposes a separate `Alpha` output, so one node carries both. (The note that used to be
    in this module's docstring -- that a ramp cannot carry colour and opacity together -- was
    simply wrong; verified against the API.)
    """
    r = g.nodes.new("ShaderNodeValToRGB")
    r.label = label
    cr = r.color_ramp
    cr.interpolation = "LINEAR"        # matches pattern_lut.bake's default; see PATTERNS.md Q1
    while len(cr.elements) > 1:
        cr.elements.remove(cr.elements[-1])
    for i, (pos, cr_, cg, cb, ca) in enumerate(stops):
        el = cr.elements[0] if i == 0 else cr.elements.new(pos)
        el.position = pos
        el.color = (cr_, cg, cb, ca)
    g.links.new(index_socket, r.inputs["Fac"])
    return r.outputs["Color"], r.outputs["Alpha"]


def _mget(model, key, default):
    """Read a field off the pattern model, which arrives as a DICT here, not a PatternModel.

    `preview_bridge.push_pattern` sends `model.to_dict()` over the wire and `export_pattern` writes
    JSON, so everything in this module sees plain dicts -- `ramp_stops` already assumes that. Using
    getattr() on a dict silently returns the default: `usePatchwork` read that way is always False,
    so the patchwork gate would never be built and nothing would report an error. Accept both.
    """
    if model is None:
        return default
    if isinstance(model, dict):
        return model.get(key, default)
    return getattr(model, key, default)


def build_group(name, lut, model=None, gated=False):
    """The `JWE3_Pattern` node tree. In: Albedo, Index, Source, Patchwork. Out: Albedo, Emissive.

    `gated` builds the patchwork zone gate. Only pass it when a patchwork map will actually be
    wired to the `Patchwork` input AND the model arms the gate -- an unconnected socket reads 0.0,
    which is zone 0, and would switch the whole mesh off for most flag values.
    """
    old = bpy.data.node_groups.get(name)
    if old is not None:
        bpy.data.node_groups.remove(old)
    g = bpy.data.node_groups.new(name, "ShaderNodeTree")
    for n, t in (("Albedo", "NodeSocketColor"), ("Index", "NodeSocketFloat"),
                 ("Source", "NodeSocketFloat"), ("Patchwork", "NodeSocketFloat")):
        g.interface.new_socket(n, in_out="INPUT", socket_type=t)
    # Emissive is sampled anyway -- row 1 of the image exists whether or not anything reads it --
    # so expose it rather than leave a texture node feeding nothing. `apply_pattern` wires only
    # Albedo into the surface chain; Emissive is there for anyone who wants it.
    for n, t in (("Albedo", "NodeSocketColor"), ("Emissive", "NodeSocketColor")):
        g.interface.new_socket(n, in_out="OUTPUT", socket_type=t)

    gin = g.nodes.new("NodeGroupInput")
    gout = g.nodes.new("NodeGroupOutput")
    img = lut_image(f"{name}_LUT", lut)

    # index (raw 0..1 from the pattern map) -> U on texel centres
    u = g.nodes.new("ShaderNodeMath")
    u.operation, u.label = "MULTIPLY_ADD", "index -> texel centre"
    u.inputs[1].default_value = U_SCALE
    u.inputs[2].default_value = U_OFFSET
    g.links.new(gin.outputs["Index"], u.inputs[0])

    taps = {}
    for i, row in enumerate(ROWS):
        xyz = g.nodes.new("ShaderNodeCombineXYZ")
        xyz.label = f"{row} row"
        g.links.new(u.outputs[0], xyz.inputs["X"])
        xyz.inputs["Y"].default_value = _row_v(i)
        tex = g.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.label = row
        # EXTEND, not the default REPEAT: at index 0 and 31 a repeating lookup wraps to the far end
        # of the LUT, which puts the first pattern colour on the last index.
        tex.extension = "EXTEND"
        g.links.new(xyz.outputs["Vector"], tex.inputs["Vector"])
        taps[row] = tex

    # --- the RAMP path: the authored keys, continuous, editable. Fed the RAW index (no remap).
    colour_src = taps["colour"].outputs["Color"]
    opacity_src = taps["opacity"].outputs["Color"]
    stops = ramp_stops(model or {})
    if stops:
        r_col, r_alpha = _build_ramp(g, stops, gin.outputs["Index"], "authored keys")
        sel_c = g.nodes.new("ShaderNodeMix")
        sel_c.data_type, sel_c.blend_type = "RGBA", "MIX"
        sel_c.label = "Source: 0 baked image / 1 ramp"
        g.links.new(gin.outputs["Source"], sel_c.inputs["Factor"])
        g.links.new(colour_src, sel_c.inputs[6])
        g.links.new(r_col, sel_c.inputs[7])
        colour_src = sel_c.outputs[2]

        sel_o = g.nodes.new("ShaderNodeMix")
        sel_o.data_type = "FLOAT"
        sel_o.label = "Source (opacity)"
        g.links.new(gin.outputs["Source"], sel_o.inputs["Factor"])
        g.links.new(opacity_src, sel_o.inputs[2])
        g.links.new(r_alpha, sel_o.inputs[3])
        opacity_src = sel_o.outputs[0]

    # PATCHWORK GATE. Multiply the pattern's opacity by 0 where the texel's zone is switched off,
    # which reproduces the shader branching past the whole pattern block. `flags` is fixed at build
    # time, so the gate is a constant-interpolation ramp rather than bit arithmetic in nodes.
    # `patchwork.gate_ramp_stops` is the same rule as `patchwork.gate_mask`, pinned in its selftest.
    if gated:
        gr = g.nodes.new("ShaderNodeValToRGB")
        gr.label = "patchwork gate"
        gcr = gr.color_ramp
        gcr.interpolation = "CONSTANT"
        stops = patchwork.gate_ramp_stops(_mget(model, "patchworkFlags", 31))
        while len(gcr.elements) > 1:
            gcr.elements.remove(gcr.elements[-1])
        for i, (pos, val) in enumerate(stops):
            el = gcr.elements[0] if i == 0 else gcr.elements.new(pos)
            el.position = pos
            el.color = (val, val, val, 1.0)
        g.links.new(gin.outputs["Patchwork"], gr.inputs["Fac"])
        gmul = g.nodes.new("ShaderNodeMath")
        gmul.operation = "MULTIPLY"
        gmul.label = "opacity x patchwork gate"
        g.links.new(opacity_src, gmul.inputs[0])
        g.links.new(gr.outputs["Color"], gmul.inputs[1])
        opacity_src = gmul.outputs[0]

    mix = g.nodes.new("ShaderNodeMix")
    mix.data_type, mix.blend_type = "RGBA", "MIX"
    mix.label = "albedo -> pattern colour, by opacity"
    g.links.new(opacity_src, mix.inputs["Factor"])
    g.links.new(gin.outputs["Albedo"], mix.inputs[6])
    g.links.new(colour_src, mix.inputs[7])
    g.links.new(mix.outputs[2], gout.inputs["Albedo"])
    # Emissive stays on the image tap in BOTH modes -- which is correct, because the baked LUT image
    # already carries the interpolation between keys.
    #
    # (The old reason given here -- "every pattern measured carries exactly ONE emissive key" -- is
    # WRONG. Surveyed 210 pattern FGMs 2026-08-07: parasaurolophus_pattern_ccpink_00 has EIGHT
    # non-zero emissive keys, indominusrex_pattern_01_07 has seven, and the dedicated
    # *_pattern_lux_00 files have five. The image tap handles all of them; only the premise was bad.)
    g.links.new(taps["emissive"].outputs["Color"], gout.inputs["Emissive"])

    _layout_group(g, gin, gout, taps)
    return g


# Interior layout, explicit rather than depth-sorted. The generic `layout()` stacked the ramp on
# top of the texture nodes (x 630 vs 676, both 240 wide) and put the three Mix nodes at
# 911/1022/1050 -- overlapping each other. Three rows of one row-tap each, the ramp on its own row
# below them, then the two Source selectors and the final mix.
_GROUP_ROW_DY = 220.0
_GROUP_COL = (0.0, 220.0, 430.0, 620.0, 900.0, 1140.0, 1380.0)


def _layout_group(g, gin, gout, taps):
    c = _GROUP_COL
    gin.location = (c[0], 0.0)
    u = next((n for n in g.nodes if n.type == "MATH"), None)
    if u is not None:
        u.location = (c[1], -60.0)
    for i, row in enumerate(ROWS):
        y = (1 - i) * _GROUP_ROW_DY
        tex = taps[row]
        tex.location = (c[3], y)
        src = next((l.from_node for inp in tex.inputs for l in inp.links), None)
        if src is not None:
            src.location = (c[2], y)
    ramp = next((n for n in g.nodes if n.type == "VALTORGB"), None)
    if ramp is not None:
        ramp.location = (c[2], -2.0 * _GROUP_ROW_DY)
    sel = [n for n in g.nodes if n.type == "MIX" and (n.label or "").startswith("Source")]
    for n in sel:
        n.location = (c[4], _GROUP_ROW_DY if n.data_type == "RGBA" else -_GROUP_ROW_DY)
    mix = next((n for n in g.nodes if n.type == "MIX" and n not in sel), None)
    if mix is not None:
        mix.location = (c[5], 0.0)
    gout.location = (c[6], 0.0)


def apply_pattern(mat, data, index_map=None, tag="", source=SOURCE_IMAGE, patchwork_map=None):
    """Splice this pattern over `mat`'s surface chain. Returns the group node.

    Unsplices any existing pattern FIRST, so re-applying replaces rather than stacks -- two grade
    groups in series is what renders a mesh white (see jwe3-palette-apply-to-stacks).

    Works on a material with NO variant grade: patterns and variants are separate cosmetic axes in
    game and either may be applied alone. `blender_parts.splice_at` inserts by CHAIN_POS rather
    than at the end of the chain, so grade-then-pattern and pattern-then-grade give the same tree.

    `index_map` is an optional path to the species' `u_basePatternMap` PNG. Without it the Index
    socket keeps its default and the whole mesh reads ONE LUT entry -- a flat colour, which is
    correct behaviour for "no index map" but looks like a bug, so the caller should pass one
    whenever it can.

    `patchwork_map` is an optional path to the species' `u_basePatchworkMap` PNG. The gate is built
    only when that map exists AND the model arms it (usePatchwork on, flags < 31) -- the same two
    conditions the game requires, both verified in game 2026-08-08. 100 patternsets ship
    a patchwork map, so no map is the normal case, not a fault.
    """
    blender_parts.unsplice(mat, GROUP_PREFIX)

    lut = data["lut"]
    name = f"{GROUP_PREFIX}_{tag}" if tag else GROUP_PREFIX
    node = mat.node_tree.nodes.new("ShaderNodeGroup")
    _model = data.get("model")
    _gated = bool(
        patchwork_map and os.path.isfile(patchwork_map)
        and _mget(_model, "usePatchwork", False)
        and int(_mget(_model, "patchworkFlags", 31)) < 31
    )
    node.node_tree = build_group(name, lut, _model, gated=_gated)
    if "Source" in node.inputs:
        node.inputs["Source"].default_value = source
    node.name = name                       # MUST start with GROUP_PREFIX: unsplice and CHAIN_POS
    node.label = f"pattern {data.get('source') or ''}".strip()
    node.width = 220

    if index_map and os.path.isfile(index_map):
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.name = f"{GROUP_PREFIX}_IndexMap"
        tex.label = "pattern index map"
        img = bpy.data.images.get(os.path.basename(index_map))
        if img is None:
            img = bpy.data.images.load(index_map, check_existing=True)
        img.colorspace_settings.name = "Non-Color"      # an index, never sRGB
        tex.image = img
        tex.location = (node.location.x - 320, node.location.y - 260)
        mat.node_tree.links.new(tex.outputs["Color"], node.inputs["Index"])

    if _gated:
        pw = mat.node_tree.nodes.new("ShaderNodeTexImage")
        pw.name = f"{GROUP_PREFIX}_PatchworkMap"
        pw.label = "patchwork map"
        pwimg = bpy.data.images.get(os.path.basename(patchwork_map))
        if pwimg is None:
            pwimg = bpy.data.images.load(patchwork_map, check_existing=True)
        pwimg.colorspace_settings.name = "Non-Color"     # a zone id, never sRGB
        pw.interpolation = "Closest"                     # zone ids: never interpolate between them
        pw.image = pwimg
        pw.location = (node.location.x - 320, node.location.y - 520)
        mat.node_tree.links.new(pw.outputs["Color"], node.inputs["Patchwork"])

    # ---- EMISSIVE ("lux") -> the BSDF's emission input.
    #
    # Row 1 of the LUT was always baked and exposed, and nothing consumed it, so a pattern with
    # emissive keys rendered exactly like one without: no glow in dark lighting, which is the whole
    # point of the CC/lux patterns. Wire it up.
    #
    # Safe for ordinary patterns: their emissive row is all zeros, and black emission adds nothing.
    # Strength is set to 1.0 only when the row actually carries signal, so a non-emissive pattern
    # cannot leave a material glowing faintly.
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None and "Emissive" in node.outputs:
        col_in = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
        str_in = bsdf.inputs.get("Emission Strength")
        if col_in is not None:
            mat.node_tree.links.new(node.outputs["Emissive"], col_in)
        if str_in is not None:
            emis_rows = (lut or {}).get("emissive") or []
            has_signal = any(any(float(c) > 1e-6 for c in v[:3]) for v in emis_rows if v)
            str_in.default_value = 1.0 if has_signal else 0.0

    # sockets, not names: splice_at links them directly
    blender_parts.splice_at(mat, node, node.inputs["Albedo"], node.outputs["Albedo"], GROUP_PREFIX)
    # A new node has no location and lands at the origin -- on top of the START of the chain.
    # layout_chain recomputes the tail from an anchor, so it is safe to call every time.
    blender_parts.layout_chain(mat)
    # Tag the tree too: rebuilding the group and rewriting the LUT changes what the material
    # evaluates to, but neither necessarily marks the material dirty, so a live preview can sit on
    # the old compile. Cheap, and it makes "apply" mean "visible" without touching a link.
    mat.node_tree.update_tag()
    mat.update_tag()
    return node


def unsplice(mat):
    """Remove the pattern group, relinking around it. True if one was there.

    Also drops the emission back to 0. Removing the group takes its link with it, but the STRENGTH
    is a plain value on the BSDF -- leaving it at 1.0 would keep whatever colour the socket falls
    back to glowing after the pattern is gone.
    """
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        s = bsdf.inputs.get("Emission Strength")
        if s is not None and not s.is_linked:
            s.default_value = 0.0
    return blender_parts.unsplice(mat, GROUP_PREFIX)


def pattern_group(mat):
    """The material's pattern group node, or None."""
    if not mat or not mat.use_nodes:
        return None
    return next((n for n in mat.node_tree.nodes
                 if n.type == "GROUP" and n.name.startswith(GROUP_PREFIX) and n.node_tree), None)


def read_ramp(mat):
    """The live ColorRamp stops as `[(pos01, r, g, b, opacity)]` -- the inverse of `ramp_stops`.

    Returns [] when the material has no pattern, or has one built from a model with no keys (in
    which case `build_group` never made a ramp). `pattern_writeback.model_from_stops` turns the
    result back into a saveable PatternModel.

    Reads the ELEMENTS, not a rendered sample: element colours are stored raw, exactly as
    `_build_ramp` wrote them, so no colour management sits between authoring and the FGM.
    """
    node = pattern_group(mat)
    if node is None:
        return []
    ramp = next((n for n in node.node_tree.nodes if n.type == "VALTORGB"), None)
    if ramp is None:
        return []
    out = []
    for el in ramp.color_ramp.elements:
        c = el.color
        out.append((float(el.position), float(c[0]), float(c[1]), float(c[2]), float(c[3])))
    return sorted(out)


def selftest():
    """Run INSIDE Blender: exec(open(__file__).read()); selftest()"""
    import numpy as np
    lut = {"colour": [[i / 31.0, 0.0, 1.0 - i / 31.0] for i in range(32)],
           "emissive": [[0.0, 0.0, 0.0]] * 32,
           "opacity": [[i / 31.0] for i in range(32)]}

    img = lut_image("JWE3_TEST_LUT", lut)
    assert tuple(img.size) == (32, 3), img.size
    assert img.colorspace_settings.name == "Non-Color"
    px = np.array(img.pixels[:]).reshape(3, 32, 4)      # (row, col, RGBA), row 0 at the BOTTOM
    assert np.isclose(px[2, 0, 0], 0.0) and np.isclose(px[2, 31, 0], 1.0), \
        "colour row is not the top row -- V flip is wrong"
    assert np.isclose(px[0, 31, 0], 1.0), "opacity row is not the bottom row"
    # ...and the middle row really is emissive, or the flip is off by one rather than reversed
    assert np.allclose(px[1, :, :3], 0.0), "emissive is not the middle row"

    mat = bpy.data.materials.new("JWE3_TEST_MAT")
    mat.use_nodes = True
    n_before = len(mat.node_tree.nodes)

    apply_pattern(mat, {"lut": lut, "source": "test"})
    groups = [n for n in mat.node_tree.nodes
              if n.type == "GROUP" and n.name.startswith(GROUP_PREFIX)]
    assert len(groups) == 1, f"expected 1 group, got {len(groups)}"
    # it must actually be IN the surface chain, not merely present
    blender_parts.verify_surface_chain(mat)
    out = next(n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL")
    reach, seen = [], set()
    stack = [out]
    while stack:
        n = stack.pop()
        if n.name in seen:
            continue
        seen.add(n.name)
        reach.append(n.name)
        for i in n.inputs:
            for l in i.links:
                stack.append(l.from_node)
    assert groups[0].name in reach, "the pattern group does not reach the Material Output"

    # --- ramp_stops: pure maths, checkable without Blender ---
    #     colour and opacity are authored at DIFFERENT positions, so stops go on the union and both
    #     channels are evaluated there. Exact for linear interpolation.
    model = {"colourKeys": [[-1, [9, 9, 9]], [0, [1.0, 0.0, 0.0]], [31, [0.0, 0.0, 1.0]]],
             "opacityKeys": [[-1, 9], [0, 0.0], [16, 1.0]]}
    st = ramp_stops(model)
    assert [round(p, 4) for p, *_ in st] == [0.0, round(16 / 31.0, 4), 1.0], st
    assert st[0][1:] == (1.0, 0.0, 0.0, 0.0), st[0]          # position 0: red, opacity 0
    mid = st[1]
    assert abs(mid[1] - (1 - 16 / 31.0)) < 1e-6, mid          # colour interpolated at 16
    assert abs(mid[4] - 1.0) < 1e-6, mid                      # opacity key lands exactly
    assert st[2][4] == 1.0, "opacity past its last key must hold, not fall to 0"
    assert ramp_stops({}) == []                               # no keys -> no ramp, not a crash
    assert len(ramp_stops({"colourKeys": [[i, [0, 0, 0]] for i in range(32)],
                           "opacityKeys": []})) <= 32, "must fit ColorRamp's 32-stop limit"

    # --- both paths present, and Source selects between them ---
    m2 = bpy.data.materials.new("JWE3_TEST_SRC"); m2.use_nodes = True
    n2 = apply_pattern(m2, {"lut": lut, "source": "t", "model": model})
    assert "Source" in n2.inputs, "the Source switch is missing"
    assert n2.inputs["Source"].default_value == SOURCE_IMAGE, "must default to the game-faithful bake"
    ramps = [n for n in n2.node_tree.nodes if n.type == "VALTORGB"]
    assert len(ramps) == 1, f"expected one ColorRamp, got {len(ramps)}"
    assert len(ramps[0].color_ramp.elements) == len(st), (len(ramps[0].color_ramp.elements), len(st))
    imgs = [n for n in n2.node_tree.nodes if n.type == "TEX_IMAGE"]
    assert len(imgs) == 3, f"the baked-image path must survive alongside the ramp: {len(imgs)}"
    bpy.data.materials.remove(m2)

    # --- read_ramp must invert ramp_stops exactly ---
    #     This is the authoring loop's foundation: if reading the stops back does not reproduce what
    #     was written, every save silently corrupts the pattern.
    m3 = bpy.data.materials.new("JWE3_TEST_RAMP"); m3.use_nodes = True
    apply_pattern(m3, {"lut": lut, "source": "t", "model": model})
    got = read_ramp(m3)
    assert len(got) == len(st), (len(got), len(st))
    for (p0, r0, g0, b0, a0), (p1, r1, g1, b1, a1) in zip(got, st):
        # ramp elements are float32, so compare at float32 precision rather than exactly
        assert abs(p0 - p1) < 1e-6 and abs(a0 - a1) < 1e-6, ((p0, a0), (p1, a1))
        assert max(abs(r0 - r1), abs(g0 - g1), abs(b0 - b1)) < 1e-6

    # and an edit made in the node editor must be visible to read_ramp
    ramp = next(n for n in pattern_group(m3).node_tree.nodes if n.type == "VALTORGB")
    ramp.color_ramp.elements[0].color = (0.125, 0.25, 0.375, 0.5)
    edited = read_ramp(m3)[0]
    assert abs(edited[1] - 0.125) < 1e-6 and abs(edited[4] - 0.5) < 1e-6, edited
    assert read_ramp(bpy.data.materials.new("JWE3_TEST_NOPAT")) == [], "no pattern must read as []"
    bpy.data.materials.remove(m3)

    # LAYOUT MUST BE IDEMPOTENT. The version this replaced nudged neighbours aside by one node
    # width per splice, so every re-apply pushed the BSDF and Material Output further right -- the
    # feathers material reached an 1800 px gap. Positions after a second apply must be identical.
    def _pos(m):
        return {n.name: tuple(round(v, 3) for v in n.location) for n in m.node_tree.nodes}
    before = _pos(mat)
    apply_pattern(mat, {"lut": lut, "source": "test"})
    assert _pos(mat) == before, "layout drifted on re-apply: %s" % [
        k for k in before if before[k] != _pos(mat).get(k)]
    # ...and nothing may sit on top of anything else in the tail row
    tail = [n for n in mat.node_tree.nodes
            if n.parent is None and n.type in ("GROUP", "BSDF_PRINCIPLED", "OUTPUT_MATERIAL")]
    xs = [round(n.location.x, 3) for n in tail]
    assert len(xs) == len(set(xs)), "two tail nodes share an x position: %s" % sorted(xs)

    # APPLYING TWICE MUST NOT STACK. A second group in series is what renders a mesh white --
    # see jwe3-palette-apply-to-stacks. Compare by .name; `is` on bpy nodes matches nothing.
    apply_pattern(mat, {"lut": lut, "source": "test"})
    groups = [n for n in mat.node_tree.nodes
              if n.type == "GROUP" and n.name.startswith(GROUP_PREFIX)]
    assert len(groups) == 1, f"applying twice stacked {len(groups)} groups"

    assert unsplice(mat) is True
    assert not [n for n in mat.node_tree.nodes
                if n.type == "GROUP" and n.name.startswith(GROUP_PREFIX)]
    assert len(mat.node_tree.nodes) == n_before, "unsplice leaked nodes"
    assert unsplice(mat) is False, "unsplice on a clean material should report False"

    bpy.data.materials.remove(mat)
    bpy.data.images.remove(img)
    for n in ("JWE3_Pattern", "JWE3_Pattern_LUT"):
        d = bpy.data.node_groups.get(n) or bpy.data.images.get(n)
        if d is not None:
            (bpy.data.node_groups if n in bpy.data.node_groups else bpy.data.images).remove(d)
    print("selftest ok")


if __name__ == "__main__":
    print("imports cleanly; run selftest() inside Blender")
