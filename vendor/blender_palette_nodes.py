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
    grad   = saturate((offset + amplitude * cos(2*pi*(t/51.1*freq + phase/511))) / 511)
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

# The IR divides `t` by this before multiplying by freq: `%2864 = fmul %2824, 0x3F940A0500000000`,
# and that constant is 0.01956947 = 1/51.1. It is NOT 1/51 -- see PALETTE.md "The divisor is 51.1,
# not 51". Small, but this is a transcription of the shader and it should be exact.
T_DIVISOR = 51.1

# The body grade's node name. Every consumer looks the grade up by this prefix, so it has to be set
# where the node is created, not by whichever caller happens to remember.
GRADE_BODY = "JWE3_Grade_body"


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
         ("Height", "NodeSocketFloat"), ("ColourWeight", "NodeSocketFloat"),
         ("FurMask", "NodeSocketFloat")],
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

    def finish(socket):
        """Apply u_furTint and hand the result to the group output.

        MEASURED from 0238_ps_DinosaurFur_Vanilla_BaseLayered_GBuffer_0 (%2499-%2518):

            albedo = lerp(albedo, albedo * furTint, furMask)

        It is NOT a plain multiply. Tinting everything turns bare scaly skin the fur's colour --
        the game keeps it dark brown. `furMask` is the GREEN channel of `pBaseAOTexture` sampled on
        the base UVs (%1885 -> extractvalue 1), i.e. the fur coverage map; that texture ships with
        only its R and G channels split out, which corroborates it.

        furTint lives in a FOURTH 16-byte row of the material block, at +48. The block stride is 64
        bytes (`<<6`) but `material_block.decode` only reads +0/+16/+32 -- it was derived from
        container 300, the LAYERED shader, which never loads +48. Only the fur shaders do. Stored as
        three f16s in the low halves of the row's first three uints.
        """
        tint = tuple(block.get("furTint", (1.0, 1.0, 1.0)))
        if tuple(round(c, 6) for c in tint) != (1.0, 1.0, 1.0):
            socket = _mix(g, gin.outputs["FurMask"], socket, _mix_multiply(g, socket, tint))
        g.links.new(socket, gout.inputs["Color"])
        return layout(g)

    if not block["gradientEnabled"]:
        return finish(out)

    # ---- section 5: the cosine-gradient palette, parameterised by the composited height
    t = m("MULTIPLY_ADD", gin.outputs["Height"],
          100.0 * block["instancePaletteScale"], block["instancePaletteOffset"]).outputs[0]
    ts = m("MULTIPLY", t, 1.0 / T_DIVISOR).outputs[0]
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
    return finish(ov.outputs[2])


def _mix_multiply(g, socket, rgb):
    """socket * rgb, as a MULTIPLY Mix at full factor."""
    n = g.nodes.new("ShaderNodeMix")
    n.data_type, n.blend_type = "RGBA", "MULTIPLY"
    n.inputs["Factor"].default_value = 1.0
    g.links.new(socket, n.inputs[6])
    n.inputs[7].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    return n.outputs[2]


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
    # Fall back to Base Color when the albedo feeds nothing: a severed chain must be repaired, not
    # silently preserved. See blender_parts.albedo_sinks.
    import blender_parts
    sink = [(nt.nodes[n], nt.nodes[n].inputs[s])
            for n, s in blender_parts.albedo_sinks(
                mat, [(l.to_node.name, l.to_socket.name) for l in src.outputs[2].links])]
    pg = nt.nodes.new("ShaderNodeGroup")
    pg.node_tree = palette_group(block)
    # NAME IT HERE, not in the callers. Blender's default name is "Group.009", which nothing can
    # find again: `blender_parts.unsplice` matches on the JWE3_Grade prefix and blender_listener
    # remembers the name only in a module global that dies with the session. An unfindable grade is
    # not merely untidy -- the next apply calls palette_group(), `_new_group` deletes the node group
    # of the same name out from under the old node, and that node goes TREE-LESS: its links vanish
    # and the albedo chain is severed, so the body renders at a flat 0.5 grey. Reproduced on a
    # scene graded by the listener and then re-graded by variant_parts.apply_variant_all.
    pg.name = GRADE_BODY
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
    if not pg.outputs["Color"].links:
        raise ValueError(f"{mat.name}: the grade's output reached nothing -- it would render at "
                         f"Blender's default grey, not this variant's colour")
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
    assert names == {"Albedo", "KeySource", "Height", "ColourWeight", "FurMask", "Color"}, names

    # u_furTint is a MASKED lerp, not a multiply: lerp(albedo, albedo*tint, furMask). A neutral
    # tint must add no nodes at all, and a real one must go through a Mix driven by FurMask --
    # applying it unmasked bleaches bare skin that the game keeps dark.
    import copy
    b = copy.deepcopy(blk)
    b["furTint"] = [1.0, 1.0, 1.0]
    plain = palette_group(b, name="JWE3_SelfTest_NoTint")
    b["furTint"] = [1.0, 0.82, 0.545]
    tinted = palette_group(b, name="JWE3_SelfTest_Tint")
    assert len(tinted.nodes) > len(plain.nodes), "a non-neutral furTint added no nodes"
    fed = [l.to_node for l in tinted.nodes["Group Input"].outputs["FurMask"].links]
    assert fed, "FurMask is not wired to anything, so the tint is unmasked"
    assert any(n.type == "MIX" for n in fed), [n.type for n in fed]
    for t in (plain, tinted):
        bpy.data.node_groups.remove(t)
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

    # The gradient's `t` divisor, read back off the graph. 1/51.1 and 1/51 differ by 0.2% and both
    # render happily, so nothing but an explicit check catches a regression to the old value --
    # which is exactly how this drifted out of step with PALETTE.md in the first place.
    # `dist_scale` is rounded to 6 places, which cannot tell 1/51.1 from 1/51 apart from the 5th
    # digit -- read the constants again at full precision.
    exact_muls = {n.inputs[1].default_value
                  for n in g.nodes if n.type == "MATH" and n.operation == "MULTIPLY"}
    assert any(abs(v - 1.0 / T_DIVISOR) < 1e-9 for v in exact_muls), \
        f"gradient t-divisor is not 1/{T_DIVISOR}: {sorted(exact_muls)}"
    assert not any(abs(v - 1.0 / 51.0) < 1e-9 for v in exact_muls), "reverted to 1/51"

    # material_block is the pinned-against-captures reference; the two must not disagree.
    import material_block
    assert material_block.T_DIVISOR == T_DIVISOR, (material_block.T_DIVISOR, T_DIVISOR)

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
