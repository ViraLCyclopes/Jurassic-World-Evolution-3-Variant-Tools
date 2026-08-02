"""Build the feathers/quills material -- `DinosaurFeathers_Clip{Single,Double}Sided`.

FEATHERS AND QUILLS ARE ONE TIER. `psittacosaurus_female_quills.fgm` is the same shader as
`pyroraptor_feathers.fgm`, and both carry their own 144-attribute variant paired 1:1 with the body.
Only the part token differs, so one code path serves both.

Textures resolve by `<dependency_name>`: the model's own folder first, the shared `DinosaurFur/`
library second. Psittacosaurus' quills FGM names THREE of each, which is why it is the test case --
Pyroraptor only exercises the library branch.

Iridescence is not implemented. `pIridescenceTexture`, `pIridescenceMaskTexture` and
`pFeathers_EmissiveTexture` are inline RGBA placeholders on both species, so nothing in the
validation set exercises them; the slots are reported, not shaded.
"""
import os
import sys
import xml.etree.ElementTree as ET

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import blender_parts
from part_manifest import resolve_texture

MAT_PREFIX = "JWE3_Feathers"

# Channel unpacking, adopted from cobra-tools' own import because it agrees with the textures' own
# names -- corroboration, not a guess. See PATTERNS.md 4.5.3.
#
#   pFeathers_RoughnessPackedTexture              R Metalness  G Roughness  B Specular
#   pFeathers_AOHeightOpacityTransmission_Packed  R AO         G height     B Opacity  A Transmission
PACKED = {
    "pFeathers_RoughnessPackedTexture": {"R": "Metallic", "G": "Roughness", "B": "Specular"},
    "pFeathers_AOHeightOpacityTransmission_PackedTexture": {
        "R": "AO", "G": "Height", "B": "Alpha", "A": "Transmission"},
}

# Blender 4.x renamed several Principled sockets. Mapping them explicitly, because a name that is
# merely absent from bsdf.inputs gets skipped SILENTLY -- which is how Specular and Transmission
# went missing from the first build with every test still passing.
SOCKET_ALIASES = {
    "Specular": ("Specular IOR Level", "Specular"),
    "Transmission": ("Transmission Weight", "Transmission"),
    "Emission": ("Emission Color", "Emission"),
}

# targets handled outside the Principled inputs.
#
#   AO      -> multiplied into Base Color (Principled has no AO socket)
#   Height  -> drives the PALETTE GRADE, not the surface. A feathers variant carries its own seed
#              and paletteScale/Offset (Pyroraptor v00: body seed 26, feathers seed 195), so the
#              gradient needs a height to walk -- and feathers have no 16-layer stack to composite
#              one from. The only height in the feathers texture set is this channel, which the
#              texture's own name calls Height. HYPOTHESIS, not read from container 202's IR.
NON_BSDF_TARGETS = ("AO", "Height")

# Slots we deliberately do not shade, listed so that an UNEXPECTED drop still fails loudly.
#
#   pFeathers_Aniso_PackedTexture -- a per-texel anisotropy DIRECTION (R,G). Principled's
#     "Anisotropic Rotation" is a scalar, not a tangent map, so there is nowhere to put it without
#     a custom tangent setup. It affects specular streaking along the barbs, not base colour.
KNOWN_UNUSED = {
    "pFeathers_Aniso_PackedTexture": "anisotropy direction needs a tangent map, not a scalar",
}

# Individual channels we map (so the mapping stays documented) but deliberately do NOT wire,
# keyed "<slot>.<channel>". Same contract as KNOWN_UNUSED: declared, never silently dropped.
#
#   ...Transmission_PackedTexture.A -- the game's "transmission" is thin-sheet TRANSLUCENCY, the
#     light that scatters through a backlit feather. Principled's "Transmission Weight" is
#     refractive glass, and at weight 1 it replaces the diffuse lobe entirely: base colour stops
#     reaching the camera and the plumage renders as dark grey glass. That is exactly what it did.
#     The albedo was correct the whole time -- probing the grade output showed the right deep green
#     with yellow tips while the beauty render was charcoal.
#     The channel is also a CONSTANT 1.0 across the whole texture (mean 1.0000, max 1.0000), so it
#     carries no spatial information to reconstruct from even if there were a socket for it.
#     Blender 4.x has no thin-sheet translucency on Principled; Subsurface is volumetric and wrong
#     here. Leaving it out is closer to the game than any available socket.
KNOWN_UNUSED_CHANNELS = {
    "pFeathers_AOHeightOpacityTransmission_PackedTexture.A":
        "thin-sheet translucency; Principled's Transmission is glass and blanks the albedo",
}


def _socket(bsdf, target):
    """The real socket for a logical target name, or None. Never guesses silently."""
    for cand in SOCKET_ALIASES.get(target, (target,)):
        if cand in bsdf.inputs:
            return bsdf.inputs[cand]
    return None

# only these are colour; everything else is data and MUST be Non-Color
ALBEDO_SLOTS = ("pDinosaurFeathers_BaseDiffuseTexture", "pFeathers_BaseColourTexture")

# A feathers mesh carries TWO UV layers and they are not interchangeable. UV0 is the per-animal
# layout; UV1 is the feather-card atlas the shared DinosaurFur library is authored against.
#
# Taken verbatim from cobra-tools `plugin/modules_import/material.py: BaseShader.uv_map`. The rule
# is BY SLOT, not by which folder the file came from -- Psittacosaurus overrides
# pFeathers_NormalTexture and pFeathers_RoughnessPackedTexture with local files that are still
# card-atlas textures, so a source-folder rule would put them on the wrong layer.
#
# MEASURED from 0202's IR. Reading `loadInput`: the SECOND argument is the signature ID, not a row
# index. Against the interpolation-mode table, id 6 = M_TEXCOORD 0 and id 7 = M_TEXCOORD 1
# (id 0 = CLUSTER_BINDLESSOFFSET, which independently confirms the numbering).
#
# Every texture the shader reads out of the material record at `%50 + 1` is sampled at TEXCOORD1.
# Exactly one texture is sampled at TEXCOORD0: `%767`, from record `%50 + 0` offset 28 -- the only
# three-channel colour on that UV in the whole shader. That is
# `pDinosaurFeathers_BaseDiffuseTexture`, and three independent facts say so:
#
#   * it is the only slot in feathers.fgm named for the DINOSAUR's diffuse rather than the feather
#     cards, every other one being pFeathers_* / pIridescence* / pPatterning_*;
#   * the shader keys off it -- `%954 = %767 - keyColour` -- exactly as the body shader keys off
#     pBaseDiffuseTexture, and a key colour is only ever compared against a base diffuse;
#   * its file (pyroraptor_feathers.pdinosaurfeathers_basediffusetexture) is visibly a body-UV
#     layout of flat per-island colours, where pFeathers_BaseColourTexture is visibly a card atlas.
#
# This used to say UV1, on the reasoning that record `%50 + 0` belonged to a DIFFERENT material.
# It does not: `%50` is CLUSTER_BINDLESSOFFSET, the base of this material's own parameter block,
# and the block simply spans several 64-byte records (%struct.fMaterialParameterBytes).
#
# The mesh's UV0 is its active_render layer, so slot 0 correctly needs no explicit UVMap node.
UV_LAYER = {
    "pDinosaurFeathers_BaseDiffuseTexture": 0,
    "pFeathers_AOHeightOpacityTransmission_PackedTexture": 1,
    "pFeathers_Aniso_PackedTexture": 1,
    "pFeathers_NormalTexture": 1,
    "pFeathers_BaseColourTexture": 1,
    "pFeathers_RoughnessPackedTexture": 1,
}


def fgm_textures(fgm_path):
    """{slot: dependency_name} for the real textures; inline RGBA placeholders are skipped.

    The FGM is plain XML, so cobra-tools is not needed to read a dependency name -- which keeps
    this module importable inside Blender without dragging a second copy of cobra-tools in.
    """
    root = ET.parse(fgm_path).getroot()
    out, placeholders = {}, []
    for t in root.iter("textureinfo"):
        dep = t.find("dependency_name")
        if dep is not None and (dep.text or "").strip():
            out[t.get("name")] = dep.text.strip()
        else:
            placeholders.append(t.get("name"))
    return out, placeholders


def _img(path, noncolor=True):
    """Load (or reuse) an image, re-asserting its colour space every time.

    Image datablocks are SHARED. cobra-tools assigns split channels of the SAME packed texture
    inconsistently -- aoheight _R/_G and aniso _R/_G arrive as sRGB while their siblings arrive as
    Non-Color -- and a stale colour space on a reused datablock is invisible and poisons every
    material using it. Re-assert on reuse, never only on create.
    """
    name = os.path.basename(path)
    img = bpy.data.images.get(name)
    if img is None or not img.filepath:
        img = bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = "Non-Color" if noncolor else "sRGB"
    return img


def _channel_files(resolved):
    """cobra-tools extracts a packed .tex to one PNG per channel: <stem>_R.png, _G.png ...

    Returns {"R": path, ...}, or {"": path} when the texture is a single unsuffixed file.
    """
    d, base = os.path.dirname(resolved), os.path.basename(resolved)
    stem = os.path.splitext(base)[0]
    root = stem.rsplit("_", 1)[0] if stem.rsplit("_", 1)[-1] in tuple("RGBA") else stem
    out = {}
    for f in sorted(os.listdir(d)):
        s, e = os.path.splitext(f)
        if e.lower() != ".png":
            continue
        if s == root:
            out[""] = os.path.join(d, f)
        elif s.startswith(root + "_"):
            tail = s[len(root) + 1:]
            if tail in tuple("RGBA") or tail == "RG":
                out[tail] = os.path.join(d, f)
    return out


def _sub_clamped(nt, colour, amount):
    """saturate(colour - amount), per channel. The shader's -1/255 before an overlay.

    One Mix node, not the Separate/3x Math/Combine sandwich this used to build: an RGBA Mix in
    SUBTRACT mode with `clamp_result` IS a per-channel saturate(A - B), and five nodes in the
    middle of the albedo chain were the single biggest source of clutter in the tree.
    """
    n = nt.nodes.new("ShaderNodeMix")
    n.data_type, n.blend_type = "RGBA", "SUBTRACT"
    n.clamp_result = True
    n.inputs["Factor"].default_value = 1.0
    n.label = f"- {amount:.6g} (clamped)"
    nt.links.new(colour, n.inputs[6])
    n.inputs[7].default_value = (amount, amount, amount, 1.0)
    return n.outputs[2]


def build_feathers(obj, fgm_path, library_dir, mat_name=None):
    """Build and assign the feathers/quills material for `obj`. Returns the material."""
    local_dir = os.path.dirname(os.path.abspath(fgm_path))
    slots, placeholders = fgm_textures(fgm_path)

    # REUSE by name, as blender_layer_nodes.build does. An unconditional `materials.new()` returns
    # "<name>.001", ".002", ... on every rebuild and reassigns the object to the newest, orphaning
    # the previous one WITH its jwe3_* bookkeeping still on it. Thirteen dead copies accumulated in
    # one session that way, and inspecting the stale one reported the wrong variant -- so "which
    # variant am I looking at?" could not be answered from the material.
    name = mat_name or f"{MAT_PREFIX}_{os.path.basename(local_dir)}"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.blend_method = "CLIP"          # DinosaurFeathers_Clip*Sided is alpha-tested
    nt = mat.node_tree
    for n in list(nt.nodes):
        if n.type != "OUTPUT_MATERIAL":
            nt.nodes.remove(n)
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    report = {"resolved": {}, "missing": [], "placeholders": placeholders,
              "wired": [], "skipped": [], "unused": []}
    extras = {}          # "Detail" / "AO" -> socket, composited into Base Color below

    uv_nodes = {}

    def uv_for(slot):
        """One shared UVMap node per layer; layer 0 is the default and needs no node."""
        i = UV_LAYER.get(slot, 0)
        if i == 0:
            return None
        if i not in uv_nodes:
            n = nt.nodes.new("ShaderNodeUVMap")
            n.uv_map = f"UV{i}"
            uv_nodes[i] = n
        return uv_nodes[i]

    framed = {}          # slot -> [nodes], collapsed into a labelled NodeFrame at the end

    def tex(path, noncolor=True, slot=None, label=None):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = _img(path, noncolor)
        n.label = label or os.path.splitext(os.path.basename(path))[0].rsplit(".", 1)[-1]
        uv = uv_for(slot) if slot else None
        if uv is not None:
            nt.links.new(uv.outputs["UV"], n.inputs["Vector"])
        if slot:
            framed.setdefault(slot, []).append(n)
        return n

    for slot, dep in sorted(slots.items()):
        found = resolve_texture(dep, local_dir, library_dir)
        if not found:
            report["missing"].append((slot, dep))
            continue
        report["resolved"][slot] = found
        chans = _channel_files(found)

        if slot in ALBEDO_SLOTS:
            path = chans.get("", found)
            n = tex(path, noncolor=False, slot=slot)
            if slot == "pDinosaurFeathers_BaseDiffuseTexture":
                extras["Base"] = n.outputs["Color"]
            else:
                # the shared library colour map is the DETAIL layer, multiplied over the base --
                # this is what cobra-tools' MainShader does, and leaving it out loses the feather
                # barb detail entirely
                extras["Detail"] = n.outputs["Color"]
            report["wired"].append(slot)
            continue

        if slot == "pFeathers_NormalTexture":
            rg = chans.get("RG") or chans.get("")
            if rg:
                n = tex(rg, slot=slot)
                # _RG is two-channel; Z is rebuilt inside the shared JWE3_NormalZ group
                gz = nt.nodes.new("ShaderNodeGroup")
                gz.node_tree = normal_z_group()
                gz.label = "rebuild Z"
                nt.links.new(n.outputs["Color"], gz.inputs["RG"])
                nt.links.new(gz.outputs["Normal"], bsdf.inputs["Normal"])
                report["wired"].append(slot)
            continue

        if slot in KNOWN_UNUSED:
            report["unused"].append((slot, KNOWN_UNUSED[slot]))
            continue
        mapping = PACKED.get(slot)
        if not mapping:
            report["skipped"].append((slot, "no channel mapping"))
            continue
        for ch, target in mapping.items():
            path = chans.get(ch)
            if not path:
                report["skipped"].append((f"{slot}.{ch}", "channel file missing"))
                continue
            why = KNOWN_UNUSED_CHANNELS.get(f"{slot}.{ch}")
            if why:
                report["unused"].append((f"{slot}.{ch}", why))
                continue
            n = tex(path, slot=slot)
            if target in NON_BSDF_TARGETS:
                extras[target] = n.outputs["Color"]
                report["wired"].append(f"{slot}.{ch}->{target}")
                continue
            sock = _socket(bsdf, target)
            if sock is None:
                report["skipped"].append((f"{slot}.{ch}", f"no socket for {target!r}"))
                continue
            nt.links.new(n.outputs["Color"], sock)
            report["wired"].append(f"{slot}.{ch}->{sock.name}")

    # Base Color, MEASURED from 0202_ps_DinosaurFeathers_ClipDoubleSided (%861-%903):
    #
    #     albedo = overlay( base = saturate(baseDiffuse - 1/255),  blend = (1 - AO) * detail )
    #
    # reading, in the IR's own registers:
    #
    #     %864..866 = saturate(%767 - 1/255)     %767 = pDinosaurFeathers_BaseDiffuseTexture, UV0
    #     %303      = 1 - %300                   %300 = AO,                                   UV1
    #     %304..306 = %303 * %184..186           %184 = the feather-card colour,              UV1
    #     %870..903 = the textbook overlay of %864 over %304
    #
    # It is an OVERLAY, not a chain of multiplies -- the same blend (and the same -1/255 term) the
    # body uses to put its layer stack over the base diffuse. This used to be `base x AO x detail`,
    # copied from cobra-tools' generic MainShader convention rather than read from the shader.
    # Multiply can only ever darken, which is why the feathers rendered far too dark against every
    # in-game reference while their grade parameters were correct.
    #
    # Two things here were previously the wrong way round, both now read off the IR above:
    #   * the species-local base diffuse is the overlay BASE (it is the operand carrying -1/255),
    #     and the card atlas is the BLEND layer -- not the reverse;
    #   * (1 - AO) multiplies the CARD COLOUR, not the base diffuse. AO and the card colour are both
    #     card-space (UV1) and the base diffuse is body-space (UV0), so pairing AO with the base
    #     diffuse was also mixing two different UV layers into one product.
    # THE MASK ENTERS UN-INVERTED: `AO x detail`, NOT `(1 - AO) x detail`.
    #
    # This is a KNOWING DEVIATION from the IR above, which plainly reads `%303 = 1 - %300`. It is
    # made on measurement, against the game's own GBuffer albedo captured unlit with the ReShade
    # add-on (see the JWE3_ReShadeAddon project) and decoded from sRGB. Ratios ours/game, sampled
    # region-for-region on Pyroraptor v00:
    #
    #     region     (1 - AO)            AO                  <- what we now use
    #     big fan    0.43 / 0.43 / 0.45  0.88 / 0.90 / 0.94
    #     coverts    0.34 / 0.38 / 0.36  1.04 / 1.17 / 1.15
    #     tail       0.29 / 0.29 / 0.29  0.71 / 0.71 / 0.71
    #
    # The errors are UNIFORM across R/G/B in both cases -- a pure scale, no hue component -- and the
    # overlay itself is exact (`2*base*blend`: big fan 2*0.292*0.263 = 0.1535 vs measured 0.1522).
    # So the mask term was the entire error.
    #
    # WHY THIS IS PROBABLY RIGHT FOR THE WRONG REASON, and what to check next: if `AO x card` is
    # what matches, then `%300` is NOT the channel we feed here. We supply `aoht_R` (mean 0.772) and
    # invert it to 0.228, when the multiplier that matches is ~0.77. The likely truth is that `%299`
    # samples a DIFFERENT texture and inverting our node merely compensates. No shipped channel has
    # a mean near 0.228, so the real source is unidentified -- see the memory note. Resolve `%299`
    # from a capture and this should become `1 - <that texture>` again.
    base, detail, ao = extras.get("Base"), extras.get("Detail"), extras.get("AO")
    blend = detail
    if detail is not None and ao is not None:
        inv = nt.nodes.new("ShaderNodeMix")
        inv.data_type, inv.blend_type = "RGBA", "MULTIPLY"
        inv.inputs["Factor"].default_value = 1.0
        inv.label = "AO x detail"
        nt.links.new(detail, inv.inputs[6])
        nt.links.new(ao, inv.inputs[7])
        blend = inv.outputs[2]
        report["wired"].append("detail x AO")
    chain = base
    if base is not None and blend is not None:
        ov = nt.nodes.new("ShaderNodeMix")
        ov.data_type, ov.blend_type = "RGBA", "OVERLAY"
        ov.inputs["Factor"].default_value = 1.0
        ov.label = "overlay(baseDiffuse, AO x detail)"
        nt.links.new(_sub_clamped(nt, base, 1.0 / 255.0), ov.inputs[6])   # base of the overlay
        nt.links.new(blend, ov.inputs[7])                                 # blend layer
        chain = ov.outputs[2]
        report["wired"].append("overlay(base, detail)")
    elif blend is not None:
        chain = blend
    if chain is not None:
        nt.links.new(chain, bsdf.inputs["Base Color"])

    # Stamp the sockets the grade needs, by NODE NAME -- a socket reference cannot survive being
    # stored on the material, and apply_feather_grade may run much later.
    if chain is not None:
        mat["jwe3_albedo_node"] = chain.node.name
        mat["jwe3_albedo_socket"] = chain.name
    if "Base" in extras:
        mat["jwe3_rawdiffuse_node"] = extras["Base"].node.name
    if "Height" in extras:
        mat["jwe3_height_node"] = extras["Height"].node.name

    # ORDER MATTERS: parenting a node makes its `location` relative to the frame, so laying out
    # first and framing second silently scrambles every framed position.
    _frame_slots(nt, framed)
    _layout(nt)
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    mat["jwe3_feathers_fgm"] = os.path.basename(fgm_path)
    mat["jwe3_resolved"] = len(report["resolved"])
    mat["jwe3_missing"] = ", ".join(s for s, _ in report["missing"])
    return mat, report


NORMAL_Z_GROUP = "JWE3_NormalZ"


def normal_z_group():
    """A reusable group that rebuilds a two-channel normal's Z as sqrt(1 - x^2 - y^2).

    Kept as a GROUP rather than eight loose nodes for two reasons: it is shared by every material
    that needs it instead of being rebuilt each time, and inline it is a six-deep serial math chain
    that stretched the tree past 2000 px on its own.

    `pFeathers_NormalTexture` extracts as `_RG` -- x and y only. Feeding that straight to a Normal
    Map node treats blue as z and flattens the surface.
    """
    g = bpy.data.node_groups.get(NORMAL_Z_GROUP)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(NORMAL_Z_GROUP, "ShaderNodeTree")
    g.interface.new_socket("RG", in_out="INPUT", socket_type="NodeSocketColor")
    g.interface.new_socket("Normal", in_out="OUTPUT", socket_type="NodeSocketVector")
    gi = g.nodes.new("NodeGroupInput")
    go = g.nodes.new("NodeGroupOutput")
    sep = g.nodes.new("ShaderNodeSeparateColor")
    comb = g.nodes.new("ShaderNodeCombineColor")
    nm = g.nodes.new("ShaderNodeNormalMap")
    g.links.new(gi.outputs["RG"], sep.inputs["Color"])

    def m(op, a, b=None):
        n = g.nodes.new("ShaderNodeMath")
        n.operation = op
        n.hide = True
        if hasattr(a, "is_output"):
            g.links.new(a, n.inputs[0])
        else:
            n.inputs[0].default_value = a
        if b is not None:
            if hasattr(b, "is_output"):
                g.links.new(b, n.inputs[1])
            else:
                n.inputs[1].default_value = b
        return n

    x2 = m("MULTIPLY", sep.outputs["Red"], sep.outputs["Red"])
    y2 = m("MULTIPLY", sep.outputs["Green"], sep.outputs["Green"])
    s = m("ADD", x2.outputs[0], y2.outputs[0])
    inv = m("SUBTRACT", 1.0, s.outputs[0])
    cl = m("MAXIMUM", inv.outputs[0], 0.0)
    z = m("SQRT", cl.outputs[0])
    g.links.new(sep.outputs["Red"], comb.inputs["Red"])
    g.links.new(sep.outputs["Green"], comb.inputs["Green"])
    g.links.new(z.outputs[0], comb.inputs["Blue"])
    g.links.new(comb.outputs["Color"], nm.inputs["Color"])
    g.links.new(nm.outputs["Normal"], go.inputs["Normal"])

    for i, n in enumerate((gi, sep, x2, y2, s, inv, cl, z, comb, nm, go)):
        n.location = (i * 150, 0 if n.type != "MATH" else -120)
    return g


GRADE_PREFIX = "JWE3_Grade"


def apply_feather_grade(mat, block, gradient=False):
    """Splice the palette grade into a feathers/quills material. Returns the group node.

    The body's `blender_palette_nodes.apply_to` cannot be reused: it requires the 16-layer stack's
    `jwe3_last_layer` node for Height and ColourWeight, and a feathers material has neither.

    Differences from the body path, each with its reason:

    * **The cosine gradient is OFF.** An earlier build fed
      `pFeathers_AOHeightOpacityTransmission_PackedTexture.G` in as the palette Height on the
      strength of the channel's name. That was wrong, and measurement killed it twice over:

        - the channel spans 0.482..0.518 on both `feathers` and `hair`, centred on 0.5 -- a NEUTRAL
          BUMP map, not a palette coordinate. Pushed through `t = h*100*scale + offset` it wraps the
          cosine ~30 times inside that hairline band, so the gradient came out as a near-uniform
          tint with high-frequency noise rather than a gradient;
        - `DinosaurFeathers_Clip*Sided` declares neither `pHeightScale` nor `pHeightOffset` (39
          attrs, checked). Those two ARE the body's palette height -- per-layer `pHeightOffset`
          constants are what spread the 16 layers along the gradient. The feathers shader has no
          such input, so there is nothing to parameterise a gradient WITH.

      Corroboration: 11 of Pyroraptor's 12 feather variants name the same seed (191), and the seed
      feeds ONLY the gradient. A parameter the shader ignores is exactly what artists leave
      constant. The `feathersvariant` FGM still carries seed/complexity because its schema is
      byte-identical to the body's 144-attr one, not because feathers use them.

      So feathers get the base/palette hue grade and no cosine term. Pass `gradient=True` to force
      it back on -- kept as an escape hatch for when container 202's IR is finally read, which is
      what would settle this properly.
    * **ColourWeight is a constant 1.0.** The per-layer `pGlobalColouringWeight` veto that keeps
      beaks and claws unpainted lives in the swatch library, which feathers do not use.
    * **KeySource** is the species-local base diffuse, matching the body path's use of the RAW base
      diffuse rather than the composited albedo (PALETTE.md: the key mask measures against %2240).
      Note it must be compared in LINEAR space -- `image.pixels` hands back raw sRGB bytes/255, and
      measuring the key mask on those understates it wildly (0.008% of texels passing vs 76%).
    """
    import blender_palette_nodes as bpn

    nt = mat.node_tree
    blender_parts.unsplice(mat, GRADE_PREFIX)     # replace, never stack

    if not gradient:
        block = dict(block)
        block["gradientEnabled"] = False

    alb = nt.nodes.get(mat.get("jwe3_albedo_node", ""))
    if alb is None:
        raise ValueError("build the material with build_feathers first")
    alb_sock = alb.outputs[mat.get("jwe3_albedo_socket", "Color")]

    pg = nt.nodes.new("ShaderNodeGroup")
    # The group name MUST carry the part. palette_group defaults to
    # "JWE3_Palette_<species>_v<NN>" and _new_group REMOVES any existing group of that name --
    # so grading the body and then the feathers of the same variant destroys the body's group out
    # from under it, leaving a tree-less node, a severed albedo chain and a WHITE body.
    pg.node_tree = bpn.palette_group(
        block, name=f"JWE3_Palette_{block['species']}_v{block['variant']:02d}_feathers")
    pg.name = f"{GRADE_PREFIX}_feathers"
    pg.width = 240
    # the two reasons the gradient can be absent are NOT the same and must not read the same:
    # "no gradient" is by design on feathers, "NO COEFFS" means this seed was never harvested.
    if block.get("gradientEnabled", True):
        why = ""
    elif gradient:
        why = "  (NO COEFFS - base grade only)"
    else:
        why = "  (hue grade only - feathers have no palette height)"
    pg.label = (f"{block['species']} feathers v{block['variant']:02d} "
                f"seed {block['seed']}/{block['complexity']}{why}")

    # Take over whatever the albedo currently feeds -- falling back to Base Color when it feeds
    # NOTHING, which is a severed chain, not an absence of work to do.
    sinks = blender_parts.albedo_sinks(mat, [(l.to_node.name, l.to_socket.name)
                                             for l in alb_sock.links])
    nt.links.new(alb_sock, pg.inputs["Albedo"])

    raw = nt.nodes.get(mat.get("jwe3_rawdiffuse_node", ""))
    nt.links.new((raw or alb).outputs["Color"], pg.inputs["KeySource"])

    # Height is left unlinked unless the gradient is forced on -- wiring the neutral bump channel in
    # would misrepresent it as a palette coordinate even though nothing downstream reads it.
    h = nt.nodes.get(mat.get("jwe3_height_node", "")) if gradient else None
    if h is not None:
        nt.links.new(h.outputs["Color"], pg.inputs["Height"])
    else:
        pg.inputs["Height"].default_value = 0.0
    pg.inputs["ColourWeight"].default_value = 1.0

    for node_name, sock_name in sinks:
        nt.links.new(pg.outputs["Color"], nt.nodes[node_name].inputs[sock_name])
    if not pg.outputs["Color"].links:
        raise ValueError(f"{mat.name}: the feather grade's output reached nothing -- it would "
                         f"render at Blender's default grey, not this variant's colour")

    # Put it IN the flow. A new node lands wherever Blender feels like -- in practice on top of
    # whatever is already there -- and this one is spliced in long after `_layout` ran, so nothing
    # else will ever move it.
    #
    # `layout_chain` recomputes the tail from an anchor rather than nudging neighbours aside, which
    # matters because this runs on EVERY re-apply: the nudging version pushed the BSDF and Material
    # Output one node-width further right each time, and the feathers material ended up with an
    # 1800 px gap between the grade and the BSDF.
    blender_parts.layout_chain(mat)
    return pg




def _frame_slots(nt, framed):
    """One labelled NodeFrame per texture slot, channels collapsed inside it.

    Same convention as cobra-tools' `BaseShader.put_in_frame`: frame label is the FGM slot name at
    label_size 14, and every channel node is `hide = True` so a packed texture reads as one small
    labelled block instead of four full-size image nodes.
    """
    for slot, nodes in framed.items():
        if not nodes:
            continue
        frame = nt.nodes.new("NodeFrame")
        frame.label = slot
        frame.label_size = 14
        frame.shrink = True
        for n in nodes:
            n.hide = True
            n.parent = frame


def _layout(nt, dx=200, dy=150):
    """Compact, frame-aware layout.

    Two passes, because the two kinds of node want very different spacing: a COLLAPSED image node
    is about 30 px tall, so the 240 px row pitch that suits full-size nodes leaves the texture
    block absurdly tall and sparse. Framed textures therefore stack tightly in a left-hand column
    and everything else is placed by dependency depth to the right of them.

    Frames themselves are never positioned -- they auto-fit around their children, and setting a
    location fights the parent offset Blender applies.
    """
    ROW = 32                    # pitch for collapsed nodes inside a frame
    PAD = 62                    # frame header + margin between stacked frames

    frames = {n.name: n for n in nt.nodes if n.type == "FRAME"}
    framed, loose = {}, []
    for n in nt.nodes:
        if n.type == "FRAME":
            continue
        if n.parent is not None:
            framed.setdefault(n.parent.name, []).append(n)
        else:
            loose.append(n)

    # Left column: one tight stack per frame, frames stacked top to bottom.
    # Child locations are RELATIVE to their frame, so they all start at 0 and the frame itself
    # carries the absolute position.
    y = 0.0
    for fname in sorted(framed, key=lambda k: frames[k].label):
        kids = framed[fname]
        for i, n in enumerate(kids):
            n.location = (0.0, -i * ROW)
        frames[fname].location = (0.0, y)
        y -= len(kids) * ROW + PAD

    # UV Map nodes belong to the LEFT of the texture column -- they feed it. Depth-based placement
    # puts them at depth 0 alongside the textures, which lands them in the middle of the chain.
    uvs = [n for n in loose if n.type == "UVMAP"]
    for i, n in enumerate(uvs):
        n.location = (-220.0, -i * 120.0)
    loose = [n for n in loose if n.type != "UVMAP"]

    # right of the textures: everything else by dependency depth
    depth = {}

    def d(n, seen=()):
        if n.name in depth:
            return depth[n.name]
        if n.name in seen:
            return 0
        ups = [l.from_node for l in nt.links
               if l.to_node.name == n.name and l.from_node.type != "FRAME"]
        depth[n.name] = 1 + max([d(u, seen + (n.name,)) for u in ups], default=-1)
        return depth[n.name]

    for n in loose:
        d(n)
    # Normalise: depth is counted from the UV node through the textures, so the shallowest LOOSE
    # node is already at depth 2. Without this the first downstream column sits 700 px right of
    # the texture stack with nothing in between.
    base_depth = min((depth[n.name] for n in loose), default=0)
    base_x = 330.0              # clear of the collapsed image nodes
    rows = {}
    for n in sorted(loose, key=lambda n: (depth[n.name], n.name)):
        c = depth[n.name] - base_depth
        rows[c] = rows.get(c, 0) + 1
        n.location = (base_x + c * dx, -(rows[c] - 1) * dy)
        # Scalar helpers are collapsed but stay ON the flow. They used to be banished to
        # `tex_bottom`, ~700 px below everything else, which sent the albedo chain diving off the
        # bottom of the screen and back -- the opposite of readable. Nothing here is a long
        # single-file math run any more: the sqrt(1 - x^2 - y^2) that motivated the exile lives
        # inside the JWE3_NormalZ group, and _sub_clamped is one Mix node.
        if n.type == "MATH":
            n.hide = True
            n.location = (base_x + c * dx, -(rows[c] - 1) * dy)


def selftest():
    """Run INSIDE Blender with Pyroraptor imported and JWE3_DINO_ROOTS reachable."""
    import bpy
    parts = blender_parts.discover_parts(lod=0)
    objs = parts.get("Feathers") or parts.get("Quills")
    assert objs, "no feathers/quills part in the scene"
    obj = objs[0]

    fgm = os.environ["JWE3_FEATHERS_FGM"]
    lib = os.environ["JWE3_FUR_LIBRARY"]

    shared_before = bpy.data.node_groups["MainShader"].users if \
        "MainShader" in bpy.data.node_groups else None

    mat, report = build_feathers(obj, fgm, lib)
    assert not report["missing"], f"unresolved textures: {report['missing']}"
    assert len(report["resolved"]) >= 5, report["resolved"]
    # the shared library must actually have been used, not just the local folder
    assert any(os.path.basename(p).startswith("feathers.") for p in report["resolved"].values()), \
        "nothing resolved from the shared DinosaurFur library"

    # NOTHING may be dropped silently. Blender 4.x renamed Specular -> "Specular IOR Level" and
    # Transmission -> "Transmission Weight"; the first build skipped both and every count-based
    # assertion still passed. Assert on what was WIRED, by name.
    assert not report["skipped"], f"channels dropped: {report['skipped']}"
    # the deliberate omission must be DECLARED, not merely absent
    assert sorted(s for s, _ in report["unused"]) == [
        "pFeathers_AOHeightOpacityTransmission_PackedTexture.A",
        "pFeathers_Aniso_PackedTexture"], report["unused"]
    wired = " ".join(report["wired"])
    # NOTE "detail x AO", not "(1 - AO)": a knowing deviation from the IR, measured against the
    # game's GBuffer albedo. See the table in the build function before changing this back.
    for expect in ("Metallic", "Roughness", "Specular", "Alpha",
                   "detail x AO", "overlay(base, detail)", "pFeathers_NormalTexture"):
        assert expect in wired, f"{expect!r} was not wired. wired = {report['wired']}"
    # Transmission must NOT be wired: at weight 1 it turns the plumage into clear glass and the
    # graded albedo stops reaching the camera. This is the single defect that made every feathers
    # render read as charcoal while the grade itself was correct, so pin it.
    bsdf_ = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert not bsdf_.inputs["Transmission Weight"].links, \
        "Transmission Weight is wired again -- see KNOWN_UNUSED_CHANNELS"
    assert bsdf_.inputs["Transmission Weight"].default_value == 0.0

    # The albedo must be an OVERLAY, not the multiply chain this used to build. Measured from
    # 0202 (%861-%903); multiply can only darken, and it made the feathers far too dark against
    # every in-game reference while the grade parameters themselves were correct.
    ov = [n for n in mat.node_tree.nodes if n.type == "MIX" and n.blend_type == "OVERLAY"]
    assert len(ov) == 1, f"expected exactly one OVERLAY mix in the albedo, found {len(ov)}"
    assert ov[0].inputs[6].links and ov[0].inputs[7].links, "overlay is missing an input"
    # ...and the operands must not be swapped. The -1/255 in the IR sits on %767, the BASE DIFFUSE,
    # so the base-side input of the overlay is the one reached through the subtract. Both operands
    # are colours of the same shape, so a swap renders perfectly happily and just looks wrong.
    sub = ov[0].inputs[6].links[0].from_node
    assert sub.type == "MIX" and sub.blend_type == "SUBTRACT" and sub.clamp_result, (
        f"overlay base comes from {sub.type}/{getattr(sub, 'blend_type', '-')}, expected the"
        " clamped -1/255 subtract -- the operands look swapped")
    assert abs(sub.inputs[7].default_value[0] - 1.0 / 255.0) < 1e-6, sub.inputs[7].default_value[0]

    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    linked = {l.to_socket.name for l in mat.node_tree.links if l.to_node.name == bsdf.name}
    for sock in ("Base Color", "Normal", "Alpha", "Metallic", "Roughness"):
        assert sock in linked, f"Principled.{sock} left unconnected: {sorted(linked)}"

    # --- UV LAYERS. A feathers mesh has two and they are not interchangeable; getting this wrong
    #     renders without error, just with the textures sampled in the wrong space.
    #
    #     Assert BY SLOT against UV_LAYER, never by filename. This used to test
    #     `img.startswith("feathers.")` -- a source-FOLDER rule, which is precisely what the comment
    #     on UV_LAYER warns against. Every texture node is inside a frame labelled with its slot,
    #     so the slot is recoverable here.
    #
    #     The split must be BOTH ways round: the card-atlas textures on UV1 and the body-space base
    #     diffuse on UV0. Asserting only "something reached UV1" passed happily while all six slots
    #     were on UV1 together. ---
    assert len(obj.data.uv_layers) >= 2, f"{obj.name} has {len(obj.data.uv_layers)} UV layers"
    nt = mat.node_tree
    by_layer = {}
    for n in nt.nodes:
        if n.type != "TEX_IMAGE" or not n.image:
            continue
        src = next((l.from_node for l in nt.links
                    if l.to_node.name == n.name and l.to_socket.name == "Vector"), None)
        layer = src.uv_map if src is not None and src.type == "UVMAP" else "UV0"
        slot = n.parent.label if n.parent is not None else None
        want = f"UV{UV_LAYER.get(slot, 0)}"
        assert layer == want, f"{n.image.name} (slot {slot}) is on {layer}, expected {want}"
        by_layer.setdefault(layer, set()).add(slot)
    assert "UV1" in by_layer, "nothing was put on UV1 -- the UVMap node was never created"
    assert by_layer.get("UV0") == {"pDinosaurFeathers_BaseDiffuseTexture"}, (
        f"UV0 should carry the body-space base diffuse and nothing else: {by_layer.get('UV0')}")

    # --- node organisation, same convention as cobra-tools' put_in_frame ---
    frames = [n for n in nt.nodes if n.type == "FRAME"]
    assert frames, "no NodeFrames created"
    assert all(f.label for f in frames), "a frame was left unlabelled"
    for n in nt.nodes:
        if n.type == "TEX_IMAGE":
            assert n.parent is not None, f"{n.image.name} is not inside a frame"
            assert n.hide, f"{n.image.name} was not collapsed"
    # a packed texture's channels share ONE frame rather than getting one each
    per_frame = {}
    for n in nt.nodes:
        if n.type == "TEX_IMAGE":
            per_frame[n.parent.label] = per_frame.get(n.parent.label, 0) + 1
    assert max(per_frame.values()) > 1, f"channels were not grouped: {per_frame}"

    # --- COMPACTNESS. The tree must fit on a screen without zooming out. Measured in ABSOLUTE
    #     coordinates: a framed node's .location is relative to its frame, so mixing the two
    #     silently doubled the apparent height and hid two frames overlapping exactly. ---
    def _abs(n):
        x, y, p = n.location.x, n.location.y, n.parent
        while p is not None:
            x, y, p = x + p.location.x, y + p.location.y, p.parent
        return x, y

    pts = [_abs(n) for n in nt.nodes if n.type != "FRAME"]
    w = max(p[0] for p in pts) - min(p[0] for p in pts)
    h = max(p[1] for p in pts) - min(p[1] for p in pts)
    assert w < 1800 and h < 900, f"node tree is {w:.0f}x{h:.0f}; too spread out to navigate"
    # no two frames may sit at the same height -- that is what the ordering bug produced
    fys = [round(f.location.y) for f in frames]
    assert len(fys) == len(set(fys)), f"frames overlap vertically: {sorted(fys)}"

    imgs = [n.image for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image]
    assert len(imgs) >= 5, len(imgs)
    for i in imgs:
        want = "sRGB" if any(k in i.name.lower() for k in ("basecolour", "basediffuse")) \
            else "Non-Color"
        assert i.colorspace_settings.name == want, f"{i.name} is {i.colorspace_settings.name}"

    # MainShader is shared by all four part materials -- mutating it corrupts fur, fin and shell
    if shared_before is not None:
        assert bpy.data.node_groups["MainShader"].users == shared_before, \
            "the shared MainShader group was mutated"

    assert obj.data.materials[0].name == mat.name
    print("selftest ok")


if __name__ == "__main__":
    print("imports cleanly; run selftest() inside Blender")
