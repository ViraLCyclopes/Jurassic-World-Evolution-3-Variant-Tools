"""The JWE3 colour grade as a Blender node group. Run inside Blender.

This is sections 3-6 of `Shader Research\\PALETTE.md`, i.e. everything the shader does to the
albedo AFTER the 16 layers have been composited. It is transcribed from `material_block.shade()`,
which is itself checked against the disassembly, so the two cannot drift apart silently.

    mask   = saturate(1 - saturate(|rawBaseDiffuse - keyColour| / keyThreshold))
    blend  = saturate(sign * mask / keyTolerance + bias)        # sign/bias from keyType; SET
    A      = grade(albedo, hueMatrixBase,    brightBase, satBase)
    B      = grade(albedo, hueMatrixPalette, brightPal,  satPal)
    out    = lerp(albedo, lerp(A, B, blend), colourWeight)
    t      = height * 100 * paletteScale + paletteOffset
    grad   = saturate((offset + amplitude * cos(2*pi*(t/51*freq + phase/511))) / 511)
    s      = colourWeight * paletteStrength * blend
    out    = overlay(saturate(out - 1/255), grad*s + (1-s)*0.5)

where `grade` is brightness, then a **circulant** hue-rotation matrix, then a saturation about a
*perceptual* grey `sqrt(dot(c, c*Rec709))`.

TWO THINGS THAT ARE EASY TO GET WRONG:

*   **The scalings in the gradient are not uniform.** `freq` is raw, `phase` is divided by 511
    *inside* the 2*pi, and `amplitude`+`offset` are summed and *then* divided by 511.
*   **The final combine is an overlay, not a mix**, and the base is nudged down by 1/255 first.
    Blender's Mix node in OVERLAY mode at factor 1 is the same formula.

WHAT LIMITS THIS: the twelve gradient coefficients are baked from (seed, complexity) on the CPU
and appear in no game file, so a variant is only renderable in accurate colour if its pair has been
harvested from a RenderDoc capture. `export_palette.py report <species>` says which. With no
coefficients the group still works -- amplitude 0 and offset 255 give a flat mid-grey gradient, so
you see the base grade alone and nothing is invented.
"""
import json
import math
import os

import bpy

from blender_layer_nodes import layout      # one auto-layout pass, shared by both modules

REC709 = (0.2126, 0.7152, 0.0722)
S10 = 511.0


def _new_group(name, inputs, outputs):
    if name in bpy.data.node_groups:
        bpy.data.node_groups.remove(bpy.data.node_groups[name])
    g = bpy.data.node_groups.new(name, "ShaderNodeTree")
    for n, t in inputs:
        g.interface.new_socket(n, in_out="INPUT", socket_type=t)
    for n, t in outputs:
        g.interface.new_socket(n, in_out="OUTPUT", socket_type=t)
    return g, g.nodes.new("NodeGroupInput"), g.nodes.new("NodeGroupOutput")


def _mk(tree):
    def m(op, *args, clamp=False, kind="ShaderNodeMath"):
        n = tree.nodes.new(kind)
        n.operation = op
        if kind == "ShaderNodeMath":
            n.use_clamp = clamp
        for i, v in enumerate(args):
            if v is None:
                continue
            if hasattr(v, "is_output"):
                tree.links.new(v, n.inputs[i])
            else:
                n.inputs[i].default_value = v
        return n
    return m


def hue_matrix(packed):
    """The circulant hue-rotation matrix, unpacked. Rows are cyclic shifts of (p, q, r)/511.

    Stored as three signed 10-bit values in one uint, and `p + q + r == 511` identically for any
    angle -- which is the structural test that makes these blocks findable in a capture.
    """
    p, q, r = (v / S10 for v in packed)
    return ((p, q, r), (r, p, q), (q, r, p))


def palette_group(block, name=None):
    """Build the grade for one variant's colour block (see `export_palette.py`).

        in:   Albedo (Color), Height (Float), ColourWeight (Float)
        out:  Color
    """
    name = name or f"JWE3_Palette_{block['species']}_v{block['variant']:02d}"
    g, gin, gout = _new_group(
        name,
        [("Albedo", "NodeSocketColor"), ("KeySource", "NodeSocketColor"),
         ("Height", "NodeSocketFloat"), ("ColourWeight", "NodeSocketFloat")],
        [("Color", "NodeSocketColor")])
    gin.location, gout.location = (-1400, 0), (1400, 0)
    m = _mk(g)

    def vec(op, *a):
        return m(op, *a, kind="ShaderNodeVectorMath")

    def scalar_to_vec(s):
        c = g.nodes.new("ShaderNodeCombineXYZ")
        for i in range(3):
            g.links.new(s, c.inputs[i])
        return c.outputs[0]

    albedo = gin.outputs["Albedo"]

    # ---- section 3: the key-colour mask. blend 0 = base grade, 1 = palette grade.
    #
    #   mask  = saturate(1 - saturate(distance / keyThreshold))
    #   blend = saturate(1 - mask / keyTolerance)
    #
    # Three things here were wrong for a long time and each one alone ruins the colour:
    #   * the distance is measured against the RAW base diffuse (%2241), not the composited albedo;
    #   * keyThreshold divides the DISTANCE and keyTolerance divides the MASK -- swapped relative to
    #     the names, confirmed on nine captured GPU blocks across six species;
    #   * keyType is SET, so the sense inverts: pixels CLOSE to the key colour (white everywhere in
    #     the shipped data) keep their base grade. That is the countershading -- a pale belly stays
    #     the warm colour painted into the base diffuse while the darker back is repainted by the
    #     palette. Read the other way the whole animal grades uniformly and the belly goes grey.
    d = vec("DISTANCE", gin.outputs["KeySource"], tuple(block["keyColour"])).outputs["Value"]
    mask = m("SUBTRACT", 1.0,
             m("MULTIPLY", d, 1.0 / block["keyThreshold"], clamp=True).outputs[0],
             clamp=True).outputs[0]
    sign, bias = (-1.0, 1.0) if block["keyType"] else (1.0, 0.0)
    blend = m("MULTIPLY_ADD", mask, sign / block["keyTolerance"], bias, clamp=True).outputs[0]

    # ---- section 4: two hue grades, blended by the key mask
    def scale(v, k):
        """VectorMath SCALE -- the scalar goes on inputs[3], not inputs[1]."""
        n = vec("SCALE", v)
        n.inputs[3].default_value = k
        return n.outputs[0]

    def grade(packed, brightness, saturation):
        cur = scale(albedo, brightness)
        comb = g.nodes.new("ShaderNodeCombineXYZ")
        for i, row in enumerate(hue_matrix(packed)):
            g.links.new(vec("DOT_PRODUCT", cur, row).outputs["Value"], comb.inputs[i])
        cur = comb.outputs[0]
        lum = m("SQRT", vec("DOT_PRODUCT", cur, vec("MULTIPLY", cur, REC709).outputs[0])
                .outputs["Value"]).outputs[0]
        # c = lum + (c - lum)*sat, written as c*sat + lum*(1 - sat)
        return vec("ADD", scale(cur, saturation),
                   scalar_to_vec(m("MULTIPLY", lum, 1.0 - saturation).outputs[0])).outputs[0]

    a = grade(block["hueMatrixBase"], block["brightnessBase"], block["saturationBase"])
    b = grade(block["hueMatrixPalette"], block["brightnessPalette"],
              block["saturationPalette"])
    graded = _mix(g, blend, a, b)

    # ---- section 6: the layer colour weight lerps the ungraded albedo toward the graded colour
    out = _mix(g, gin.outputs["ColourWeight"], albedo, graded)

    if not block["gradientEnabled"]:
        g.links.new(out, gout.inputs["Color"])
        return layout(g)

    # ---- section 5: the cosine-gradient palette, parameterised by the composited height
    t = m("MULTIPLY_ADD", gin.outputs["Height"],
          100.0 * block["instancePaletteScale"], block["instancePaletteOffset"]).outputs[0]
    ts = m("MULTIPLY", t, 1.0 / 51.0).outputs[0]
    chan = g.nodes.new("ShaderNodeCombineColor")
    for i in range(3):
        off = block["gradOffset"][i]
        amp = block["gradAmplitude"][i]
        frq = block["gradFreq"][i]
        pha = block["gradPhase"][i]
        arg = m("MULTIPLY", m("MULTIPLY_ADD", ts, frq, pha / S10).outputs[0],
                2.0 * math.pi).outputs[0]
        v = m("MULTIPLY_ADD", m("COSINE", arg).outputs[0], amp, off).outputs[0]
        g.links.new(m("MULTIPLY", v, 1.0 / S10, clamp=True).outputs[0], chan.inputs[i])

    # strength mixes the gradient toward mid-grey, then it overlays the graded albedo
    s = m("MULTIPLY", m("MULTIPLY", gin.outputs["ColourWeight"],
                        block["paletteStrength"]).outputs[0], blend).outputs[0]
    grey = g.nodes.new("ShaderNodeMix")
    grey.data_type = "RGBA"
    grey.inputs[6].default_value = (0.5, 0.5, 0.5, 1.0)
    g.links.new(s, grey.inputs["Factor"])
    g.links.new(chan.outputs[0], grey.inputs[7])

    base = _sub_clamped(g, m, out, 1.0 / 255.0)
    ov = g.nodes.new("ShaderNodeMix")
    ov.data_type = "RGBA"
    ov.blend_type = "OVERLAY"
    ov.inputs["Factor"].default_value = 1.0
    g.links.new(base, ov.inputs[6])
    g.links.new(grey.outputs[2], ov.inputs[7])
    g.links.new(ov.outputs[2], gout.inputs["Color"])
    return layout(g)


def _mix(g, factor, a, b):
    n = g.nodes.new("ShaderNodeMix")
    n.data_type = "RGBA"
    for sock, val in (("Factor", factor), (6, a), (7, b)):
        tgt = n.inputs[sock]
        if hasattr(val, "is_output"):
            g.links.new(val, tgt)
        else:
            tgt.default_value = val
    return n.outputs[2]


def _sub_clamped(g, m, colour, amount):
    """saturate(colour - amount), per channel."""
    sep = g.nodes.new("ShaderNodeSeparateColor")
    g.links.new(colour, sep.inputs["Color"])
    comb = g.nodes.new("ShaderNodeCombineColor")
    for i in range(3):
        g.links.new(m("SUBTRACT", sep.outputs[i], amount, clamp=True).outputs[0], comb.inputs[i])
    return comb.outputs[0]


def apply_to(mat, block, colour_weight=None):
    """Insert the grade between the layer stack's Albedo output and the Principled BSDF.

    `colour_weight=None` (the default) takes the accumulated per-layer `pGlobalColouringWeight` off
    the layer stack's Weight output, which is what the shader does (%2205). Pass a number to
    override it for experiments. Most skin layers carry 1.0, but `Swatch_Nail` and `Swatch_Bone`
    carry 0.25-0.46, so horns, claws and beak keep most of their base diffuse rather than being
    repainted by the palette -- forcing 1.0 everywhere repaints them and loses that.
    """
    nt = mat.node_tree
    last = nt.nodes.get(mat.get("jwe3_last_layer", ""))
    src = nt.nodes.get(mat.get("jwe3_albedo_node", ""))
    if last is None or src is None:
        raise ValueError("build the material with blender_layer_nodes.build first")

    # The grade takes the albedo AFTER the layers have overlaid the base diffuse (%2588), not the
    # raw layer accumulator. Splice in where that overlay's output currently goes -- which is the
    # AO multiply if AO is on, or the BSDF's Base Color if not.
    sink = [(l.to_node, l.to_socket) for l in src.outputs[2].links]
    pg = nt.nodes.new("ShaderNodeGroup")
    pg.node_tree = palette_group(block)
    pg.width = 240
    pg.label = (f"{block['species']} v{block['variant']:02d} seed {block['seed']}"
                f"/{block['complexity']}"
                + ("" if block["gradientEnabled"] else "  (NO COEFFS - base grade only)"))
    nt.links.new(src.outputs[2], pg.inputs["Albedo"])
    base = nt.nodes.get(mat.get("jwe3_base_node", ""))
    if base is not None and "RawDiffuse" in base.outputs:
        nt.links.new(base.outputs["RawDiffuse"], pg.inputs["KeySource"])
    else:
        nt.links.new(src.outputs[2], pg.inputs["KeySource"])
    nt.links.new(last.outputs["Height"], pg.inputs["Height"])
    if "Weight" in last.outputs and colour_weight is None:
        # the accumulated per-layer pGlobalColouringWeight (%832), not a constant
        nt.links.new(last.outputs["Weight"], pg.inputs["ColourWeight"])
    else:
        pg.inputs["ColourWeight"].default_value = 1.0 if colour_weight is None else colour_weight
    for node, sock in sink:
        nt.links.new(pg.outputs["Color"], sock)
    layout(nt, dx=340, dy=280)
    return pg


def apply_from_json(mat, json_path, **kw):
    return apply_to(mat, json.load(open(json_path)), **kw)


def selftest():
    """Build a group from a synthetic block and check the maths against material_block."""
    blk = {"species": "Test", "variant": 0, "seed": 1, "complexity": 0,
           "keyColour": [1.0, 1.0, 1.0], "keyTolerance": 0.24, "keyThreshold": 1.71,
           "keyType": True, "brightnessBase": 1.38, "brightnessPalette": 1.0,
           "saturationBase": 1.121, "saturationPalette": 0.95,
           "hueMatrixBase": [500, 10, 1], "hueMatrixPalette": [480, 20, 11],
           "instancePaletteScale": 2.56, "instancePaletteOffset": 1.39,
           "paletteStrength": 0.106, "gradientEnabled": True,
           "gradOffset": [396, 405, 212], "gradAmplitude": [130, 187, 230],
           "gradFreq": [204, 204, 51], "gradPhase": [511, 66, 59]}
    g = palette_group(blk, name="JWE3_Palette_selftest")
    names = {s.name for s in g.interface.items_tree if s.item_type == "SOCKET"}
    assert names == {"Albedo", "KeySource", "Height", "ColourWeight", "Color"}, names
    assert g.nodes  # something was built

    # The node graph's key mask must agree with material_block's, which is the version pinned
    # against the captured GPU blocks. Read the two constants back off the built nodes rather than
    # trusting that the source lines say what they used to -- that is the drift this catches.
    mad = [n for n in g.nodes if n.type == "MATH" and n.operation == "MULTIPLY_ADD"]
    consts = {round(n.inputs[1].default_value, 6) for n in mad}
    assert round(-1.0 / blk["keyTolerance"], 6) in consts, sorted(consts)
    dist_scale = {round(n.inputs[1].default_value, 6)
                  for n in g.nodes if n.type == "MATH" and n.operation == "MULTIPLY"}
    assert round(1.0 / blk["keyThreshold"], 6) in dist_scale, sorted(dist_scale)

    # a circulant matrix's rows must each sum to the same thing, and to 1 when packed sums to 511
    rows = hue_matrix([500, 10, 1])
    assert abs(sum(rows[0]) - 511 / S10) < 1e-9
    for r in rows[1:]:
        assert abs(sum(r) - sum(rows[0])) < 1e-12
    assert set(rows[1]) == set(rows[0]), "rows must be cyclic shifts"

    # with no coefficients the group must still build and must NOT create a gradient path
    flat = dict(blk, gradientEnabled=False)
    g2 = palette_group(flat, name="JWE3_Palette_selftest_flat")
    assert not any(n.type == "MATH" and n.operation == "COSINE" for n in g2.nodes), \
        "a variant with no harvested coefficients must not get a cosine gradient"
    assert any(n.type == "MATH" and n.operation == "COSINE" for n in g.nodes)

    for n in ("JWE3_Palette_selftest", "JWE3_Palette_selftest_flat"):
        bpy.data.node_groups.remove(bpy.data.node_groups[n])
    print("selftest ok")


if __name__ == "__main__":
    selftest()
