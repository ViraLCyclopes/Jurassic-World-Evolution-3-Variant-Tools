"""Apply a whole cosmetic slot across every mesh part -- the multi-part variant import.

The game picks ONE cosmetic index and dresses every part from it: on Pyroraptor variant 0 the body
takes `Pyroraptor_Variant_01_00` and the feathers take `Pyroraptor_FeathersVariant_01_00`, which are
*different files with different palette seeds* (26 vs 195). The pairing is stated explicitly in the
interleaved `.dinosaurmaterialvariants` manifest -- never re-derive it from filenames.

This module is the orchestration only. Building a part's material is
`blender_layer_nodes.build` (body) or `blender_feather_nodes.build_feathers` (feathers/quills);
grading it is `blender_palette_nodes.apply_to` or `blender_feather_nodes.apply_feather_grade`.
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (HERE, PKG):
    if p not in sys.path:
        sys.path.insert(0, p)

import part_manifest

# NB: blender_parts imports bpy, so it is imported inside apply_variant_all, not here. Everything
# above that -- reading the manifest, pairing a slot to its per-part FGMs -- is pure python and has
# to stay callable from a plain interpreter (seed scans, harvest checks, CI).

FEATHER_PARTS = ("Feathers", "Quills")


def find_variant_manifest(species_dir):
    """The base `<species>_variantset_01.dinosaurmaterialvariants` in a species folder.

    Deliberately prefers `_01`: a species may also ship film sets (`_jw`, `_lux`, `_a/_b/_c`) whose
    header is a different shape (`has_sets="1"`, no <variant_name> entries) and which this does not
    yet read.
    """
    hits = sorted(glob.glob(os.path.join(species_dir, "*.dinosaurmaterialvariants")))
    base = [h for h in hits if "variantset_01" in os.path.basename(h).lower()]
    return (base or hits or [None])[0]


def fgm_for(species_dir, name):
    """The loose `.fgm` for a manifest entry, case-insensitively. None if absent."""
    if not name:
        return None
    want = name.lower() + ".fgm"
    for f in os.listdir(species_dir):
        if f.lower() == want:
            return os.path.join(species_dir, f)
    return None


def slot_for_index(species_dir, index):
    """{part: fgm path or None} for one cosmetic index, de-interleaved by part."""
    man_path = find_variant_manifest(species_dir)
    if not man_path:
        raise FileNotFoundError(f"no .dinosaurmaterialvariants in {species_dir}")
    man = part_manifest.parse_manifest(man_path)
    if not 0 <= index < len(man.slots):
        raise IndexError(f"variant {index} out of range; {len(man.slots)} slots in {man_path}")
    return {part: fgm_for(species_dir, name) for part, name in man.slots[index].items()}, man


def part_for_fgm(species_dir, fgm_path):
    """`(part, index)` for a loose variant FGM, from the manifest. `(None, None)` if unknown.

    A body variant and a feathers variant are INDISTINGUISHABLE by content: same
    `DinosaurLayered_Variant` shader, same 144 attributes, no textures. Anything that loads one and
    assumes "body" will happily build a 16-layer stack out of a feathers cosmetic -- which is
    exactly what `blender_listener.import_variant` used to do, clobbering the body material and
    assigning it onto the feathers mesh.

    The manifest is the authority, so ask it rather than pattern-matching the filename: part "" is
    the body and "Feathers"/"Quills" are the plumage. The filename check is only a fallback for a
    species folder with no `.dinosaurmaterialvariants`.
    """
    stem = os.path.splitext(os.path.basename(fgm_path))[0].lower()
    try:
        man = part_manifest.parse_manifest(find_variant_manifest(species_dir))
    except Exception:
        man = None
    if man is not None:
        for index, slot in enumerate(man.slots):
            for part, name in slot.items():
                if name and name.lower() == stem:
                    return part, index
    # Fallback: `<species>_feathersvariant_NN_NN` vs `<species>_variant_NN_NN`.
    if "feathersvariant" in stem:
        return "Feathers", None
    if "quillsvariant" in stem:
        return "Quills", None
    return None, None


def _override_for(part, seed_override):
    """The substitute seed for one part, or None. Accepts a bare int or {part: seed}."""
    if seed_override is None:
        return None
    if isinstance(seed_override, dict):
        v = seed_override.get(part)
        return None if v is None else int(v)
    return int(seed_override)


def _fur_mask_png(species_dir):
    """`<something>_fur.pbaseaotexture_G.png` in a species folder, or None.

    The GREEN channel of pBaseAOTexture is the fur coverage map that gates u_furTint
    (measured in the fur shader's IR). The texture ships with only R and G split out.
    """
    want = "pbaseaotexture_g.png"
    for f in sorted(os.listdir(species_dir)):
        low = f.lower()
        if low.endswith(want) and "_fur." in low:
            return os.path.join(species_dir, f)
    return None


def _wire_fur_mask(pg, species_dir):
    """Feed the grade's FurMask from the fur coverage map. Leaves it at 0 when there is none.

    Zero is the safe default: with no fur there is no fur tint, and `lerp(albedo, tinted, 0)` is
    the untouched albedo. Defaulting to 1 would tint scale-only species that have no fur at all.
    """
    import bpy
    if "FurMask" not in pg.inputs:
        return None
    pg.inputs["FurMask"].default_value = 0.0
    nt = pg.id_data
    # Drop any mask node from a previous apply. `unsplice` only removes JWE3_Grade* nodes, so
    # without this every re-apply leaves another orphaned texture node behind.
    for old in [n for n in nt.nodes if n.name.startswith("JWE3_FurMask")]:
        nt.nodes.remove(old)
    path = _fur_mask_png(species_dir)
    if not path:
        return None
    img = bpy.data.images.get(os.path.basename(path))
    if img is None:
        img = bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = "Non-Color"      # a mask, never sRGB
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image, tex.label = img, "fur mask (pBaseAOTexture.G)"
    tex.name = "JWE3_FurMask"
    tex.location = (pg.location.x - 320, pg.location.y - 320)
    nt.links.new(tex.outputs["Color"], pg.inputs["FurMask"])
    return tex


MIRROR_PREFIX = "JWE3_Mirror_"

# Nodes never worth mirroring: the destination has its own surface output and shader, and the
# source's grade/mask/preview nodes are re-created per part rather than copied.
_MIRROR_SKIP_TYPES = ("OUTPUT_MATERIAL", "BSDF_PRINCIPLED")
_MIRROR_SKIP_NAMES = ("JWE3_Grade", "JWE3_FurMask", "JWE3_AlbedoPreview")


def _copy_node(src, dst_nt, name):
    """Recreate one node in another node tree, carrying the settings that matter to the chain.

    Blender has no cross-tree node copy in the Python API (`nodes.new` + attribute assignment is
    the whole story), so this is explicit about the handful of properties the layer chain uses:
    the group's tree, an image node's image and colour space, a Mix node's data/blend type, and
    every unlinked input's default value.
    """
    n = dst_nt.nodes.new(src.bl_idname)
    n.name, n.label, n.location, n.width = name, src.label, src.location, src.width
    if src.type == "GROUP":
        n.node_tree = src.node_tree
    elif src.type == "TEX_IMAGE":
        n.image = src.image
        n.interpolation, n.extension = src.interpolation, src.extension
    elif src.type == "UVMAP":
        n.uv_map = src.uv_map
    for key in ("data_type", "blend_type", "clamp_factor", "clamp_result", "operation"):
        if hasattr(src, key) and hasattr(n, key):
            try:
                setattr(n, key, getattr(src, key))
            except (AttributeError, TypeError):
                pass
    for i, sock in enumerate(src.inputs):
        if i >= len(n.inputs) or sock.is_linked:
            continue
        try:
            n.inputs[i].default_value = sock.default_value
        except (AttributeError, TypeError, ValueError):
            pass
    return n


def mirror_layer_chain(src_mat, dst_mat):
    """Copy the body's whole layer chain into `dst_mat`, returning its composited-albedo socket.

    WHY THIS EXISTS. The fur shell and fin are drawn by `DinosaurFur_Vanilla_BaseLayered` -- the
    SAME shader as the body, running the SAME 16-layer loop over the SAME per-instance layer array.
    They have no `pLayered_*` of their own because they do not need any: the layers come from the
    instance, not the material. Grading their raw base diffuse instead, which is what this used to
    do, drops the entire layer stack from the surface that covers most of the animal -- the render
    then reads as "the base texture with a white tint on it".

    Returns `(albedo_socket, key_socket, height_socket, weight_socket)`, the four things the palette
    grade wants, all `None`-safe except the albedo.
    """
    import bpy  # noqa: F401  (mirroring only touches node trees, but keep the import local)

    src_nt, dst_nt = src_mat.node_tree, dst_mat.node_tree
    for old in [n for n in dst_nt.nodes if n.name.startswith(MIRROR_PREFIX)]:
        dst_nt.nodes.remove(old)

    copies = {}
    for n in src_nt.nodes:
        if n.type in _MIRROR_SKIP_TYPES or any(n.name.startswith(s) for s in _MIRROR_SKIP_NAMES):
            continue
        copies[n.name] = _copy_node(n, dst_nt, MIRROR_PREFIX + n.name)
    for l in src_nt.links:
        a, b = copies.get(l.from_node.name), copies.get(l.to_node.name)
        if a is None or b is None:
            continue
        dst_nt.links.new(a.outputs[l.from_socket.name], b.inputs[l.to_socket.name])

    albedo_node = copies.get(src_mat.get("jwe3_albedo_node", ""))
    if albedo_node is None:
        raise ValueError(f"{src_mat.name}: no jwe3_albedo_node -- build the body material first")
    base = copies.get(src_mat.get("jwe3_base_node", ""))
    last = copies.get(src_mat.get("jwe3_last_layer", ""))
    return (albedo_node.outputs[2],
            base.outputs["RawDiffuse"] if base and "RawDiffuse" in base.outputs else None,
            last.outputs["Height"] if last and "Height" in last.outputs else None,
            last.outputs["Weight"] if last and "Weight" in last.outputs else None)


def apply_derived_grade(mat, block, species_dir, tag, body_mat=None):
    """Grade a fur shell/fin material from the BODY's colour block.

    THESE ARE THE SURFACE YOU ACTUALLY SEE. `fur_shell` and `fur_fin` sit over the body and occlude
    it almost completely -- verified by feeding the body a pure red albedo, which is invisible until
    the two are hidden. Leaving them ungraded renders the raw base diffuse, which reads as "the base
    texture with a white tint on it" and made every colour comparison meaningless.

    Pass `body_mat` to mirror the body's layer stack in first, which is what the game does (see
    `mirror_layer_chain`). Without it the grade falls back to the raw base diffuse -- still better
    than nothing, but the layer detail and its per-layer colour weights are missing.

    `tag` MUST be unique per material: palette_group names its group after species+variant and
    `_new_group` deletes any existing group of that name, so two parts sharing a tag destroy each
    other's node tree.
    """
    import blender_palette_nodes as bpn
    import blender_parts          # NOT inherited from apply_variant_all: a function-scope
                                  # `import` binds a LOCAL name, so every caller that reaches
                                  # here by another route hit NameError -- mid-way through
                                  # grading, leaving the derived parts severed and WHITE.

    nt = mat.node_tree

    # Validate BEFORE unsplicing. Removing the grade and then bailing leaves the material worse
    # than it was found -- that is how a failure here silently stripped the body's own grade.
    if any(n.name.startswith("JWE3_Grade_body") for n in nt.nodes):
        raise ValueError(f"{mat.name}: this is the body's material, already graded")

    tex = next((n for n in nt.nodes
                if n.type == "TEX_IMAGE" and n.image
                and not n.name.startswith(MIRROR_PREFIX)
                and "pbasediffusetexture" in n.image.name.lower()), None)
    if tex is None:
        raise ValueError(f"{mat.name}: no pBaseDiffuseTexture at the top level to grade")

    # Where the grade's output belongs. On a re-apply the previous grade node already holds it, and
    # that is the only reliable source: once the mirrored layer chain supplies the albedo the base
    # diffuse texture feeds nothing at all, so reading its links after an unsplice yields an empty
    # list and the grade would be left dangling.
    old = next((n for n in nt.nodes if n.name.startswith("JWE3_Grade")), None)
    sinks = [(l.to_node.name, l.to_socket.name)
             for l in (old.outputs["Color"].links if old else tex.outputs["Color"].links)]
    blender_parts.unsplice(mat, "JWE3_Grade")
    sinks = blender_parts.albedo_sinks(mat, sinks)

    albedo = key = height = weight = None
    if body_mat is not None and body_mat is not mat:
        albedo, key, height, weight = mirror_layer_chain(body_mat, mat)

    pg = nt.nodes.new("ShaderNodeGroup")
    pg.node_tree = bpn.palette_group(
        block, name=f"JWE3_Palette_{block['species']}_v{block['variant']:02d}_{tag}")
    pg.name = f"JWE3_Grade_{tag}"
    pg.width = 240
    pg.label = (f"{block['species']} {tag} v{block['variant']:02d} "
                f"seed {block['seed']}/{block['complexity']}"
                + ("" if albedo is not None else "  (NO LAYERS - base diffuse only)"))
    pg.location = (tex.location.x + 300, tex.location.y)

    nt.links.new(albedo or tex.outputs["Color"], pg.inputs["Albedo"])
    nt.links.new(key or tex.outputs["Color"], pg.inputs["KeySource"])
    if height is not None:
        nt.links.new(height, pg.inputs["Height"])
    else:
        pg.inputs["Height"].default_value = 0.0
    if weight is not None:
        nt.links.new(weight, pg.inputs["ColourWeight"])
    else:
        pg.inputs["ColourWeight"].default_value = 1.0
    _wire_fur_mask(pg, species_dir)
    for node_name, sock in sinks:
        nt.links.new(pg.outputs["Color"], nt.nodes[node_name].inputs[sock])
    # Lay the tail out here too. The derived parts were never touched: `mirror_layer_chain` copies
    # the body's node POSITIONS, but this material's own Material Output and MainShader keep the
    # coordinates cobra-tools gave them -- so the output sat at x=44 while the mirrored chain ran
    # out to x=4240, i.e. the end of the graph was drawn before the start of it.
    blender_parts.layout_chain(mat)
    return pg


def apply_variant_all(species_dir, index, objects=None, lod=0, species=None, seed_override=None):
    """Grade every discovered part from its own variant FGM for cosmetic slot `index`.

    `seed_override` substitutes a seed for the FGM's own -- an int for every part, or {part: seed}
    for one. It exists because an unharvested seed has no coefficients and grades FLAT, so a part
    can render identically to ungraded and look like a wiring fault. Pyroraptor's feathers are the
    worst case: 11 of 12 slots name seed 191, which is unharvested, so NO feather variant shows a
    gradient. Substituting a seed from `coeff_store.harvested_seeds` -- ideally an exact one at the
    same complexity -- makes the palette visible without touching the shipped assets.

    A substituted material is NOT what the game renders. Both seeds are recorded, on the report and
    on the material (`jwe3_seed` is what was drawn, `jwe3_fgm_seed` what the file says), so a
    substituted preview can never be mistaken for a faithful one.

    Returns a report dict; raises only on a genuinely broken setup, never on a part that simply has
    no material built yet -- that is reported so the caller can build it and retry.
    """
    import blender_feather_nodes as bfn
    import blender_palette_nodes as bpn
    import blender_parts
    from fgm_io import load_fgm
    from preview_bridge import model_to_block

    slots, man = slot_for_index(species_dir, index)
    parts = blender_parts.discover_parts(objects, lod=lod)
    species = species or os.path.basename(os.path.dirname(os.path.abspath(species_dir))) or "Preview"

    report = {"index": index, "parts": man.parts, "graded": [], "skipped": []}

    body_block = None
    body_mat = None
    for part, objs in sorted(parts.items()):
        if part.startswith("__"):
            continue        # __derived__ is handled after the loop, once the body block exists
        fgm = slots.get(part)
        if not fgm:
            report["skipped"].append((part, f"no variant FGM for slot {index}"))
            continue
        model = load_fgm(fgm)
        fgm_seed = int(model.seed)
        sub = _override_for(part, seed_override)
        if sub is not None:
            model.seed = sub
        block = model_to_block(model, species=species, sex=None, variant=index)
        if part == "":
            body_block = block          # fur shell/fin inherit the body's grade

        for obj in objs:
            if not obj.data.materials or obj.data.materials[0] is None:
                report["skipped"].append((part, f"{obj.name} has no material"))
                continue
            mat = obj.data.materials[0]
            try:
                if part in FEATHER_PARTS:
                    pg = bfn.apply_feather_grade(mat, block)
                else:
                    blender_parts.unsplice(mat, "JWE3_Grade")
                    pg = bpn.apply_to(mat, block)
                    # apply_to leaves the node with Blender's default name ("Group.009"), which
                    # neither the unsplice above nor blender_parts.CHAIN_POS can recognise. Left
                    # alone, every re-apply STACKS another palette grade and the mesh renders white
                    # -- the failure the palette notes warn about. Naming it here is what makes the
                    # unsplice on the next call actually find it.
                    pg.name = "JWE3_Grade_body"
                    _wire_fur_mask(pg, species_dir)
                    blender_parts.layout_chain(mat)
                    if part == "":
                        body_mat = mat     # fur shell/fin mirror this material's layer chain
                report["graded"].append({
                    "part": part, "object": obj.name, "material": mat.name,
                    "fgm": os.path.basename(fgm), "seed": block["seed"],
                    "fgm_seed": fgm_seed, "substituted": sub is not None,
                    "complexity": block["complexity"],
                    "exact": bool(block.get("gradientEnabled", True)),
                    "node": pg.name if pg is not None else None,
                })
                mat["jwe3_variant_fgm"] = os.path.basename(fgm)
                # KEEP THE PAIR IN STEP. `blender_listener.import_variant` also writes these two,
                # and this used to set only the name -- so switching variant here left the PATH
                # pointing at whatever was imported last. Reload reads the path, so a reload would
                # silently swap the material back to the old variant while the label still named
                # the new one. Caught with feathers labelled _01_00 but pathed at _01_09.
                mat["jwe3_variant_path"] = os.path.abspath(fgm)
                mat["jwe3_seed"] = block["seed"]
                mat["jwe3_fgm_seed"] = fgm_seed
                mat["jwe3_seed_substituted"] = sub is not None
                mat["jwe3_variant_index"] = index
            except ValueError as e:
                # the body needs blender_layer_nodes.build first; that is a caller problem, not a
                # failure of this orchestration
                report["skipped"].append((part, f"{obj.name}: {e}"))

    # fur shell and fin last: they have no cosmetic of their own but they OCCLUDE the body, so
    # leaving them ungraded shows the raw base diffuse and hides everything done above.
    for obj in parts.get("__derived__", []):
        if body_block is None:
            report["skipped"].append(("__derived__", f"{obj.name}: no body grade to inherit"))
            continue
        if not obj.data.materials or obj.data.materials[0] is None:
            report["skipped"].append(("__derived__", f"{obj.name} has no material"))
            continue
        mat = obj.data.materials[0]
        tag = blender_parts.mesh_part_name(obj) or "derived"
        try:
            pg = apply_derived_grade(mat, body_block, species_dir, tag, body_mat=body_mat)
        except ValueError as e:
            report["skipped"].append(("__derived__", f"{obj.name}: {e}"))
            continue
        report["graded"].append({
            "part": tag, "object": obj.name, "material": mat.name,
            "fgm": "(inherits body)", "seed": body_block["seed"],
            "fgm_seed": body_block["seed"], "substituted": False,
            "complexity": body_block["complexity"],
            "exact": bool(body_block.get("gradientEnabled", True)),
            "node": pg.name,
        })
        # Same bookkeeping the cosmetic parts get. Without it these two report None for everything
        # and the only honest record of what they are is the grade node's label.
        mat["jwe3_variant_fgm"] = "(inherits body)"
        mat["jwe3_seed"] = body_block["seed"]
        mat["jwe3_fgm_seed"] = body_block["seed"]
        mat["jwe3_seed_substituted"] = False
        mat["jwe3_variant_index"] = index
    return report


def selftest():
    """Run INSIDE Blender with a species imported. Needs JWE3_SPECIES_DIR."""
    import blender_parts
    species_dir = os.environ["JWE3_SPECIES_DIR"]

    man_path = find_variant_manifest(species_dir)
    assert man_path and "variantset_01" in os.path.basename(man_path), man_path
    man = part_manifest.parse_manifest(man_path)

    # Pyroraptor: 24 entries = 12 logical x 2 parts, body and feathers paired 1:1
    slots, _ = slot_for_index(species_dir, 0)
    assert set(slots) == set(man.parts), (set(slots), man.parts)
    for part, path in slots.items():
        assert path and os.path.isfile(path), f"{part!r} -> {path}"

    # --- part_for_fgm: the ONLY thing standing between a feathers cosmetic and the body path.
    #     A body variant and a feathers variant share the DinosaurLayered_Variant shader, all 144
    #     attributes and zero textures, so nothing about the file's CONTENT distinguishes them.
    #     Importing a feathers cosmetic used to build a 16-layer body stack from it, overwrite the
    #     body material and assign it onto the feathers mesh. ---
    for part, path in slots.items():
        got, idx = part_for_fgm(species_dir, path)
        assert got == part and idx == 0, (os.path.basename(path), got, idx, part)
    # the body must come back FALSY, which is what lets import_variant keep its original path
    assert not part_for_fgm(species_dir, slots[""])[0]
    # ...and the filename fallback has to work with no manifest at all
    assert part_for_fgm("Z:/nonexistent", "spinosaurus_feathersvariant_01_03.fgm") == \
        ("Feathers", None)
    assert part_for_fgm("Z:/nonexistent", "spinosaurus_variant_01_03.fgm") == (None, None)

    # the pairing must come from the MANIFEST, and the two files must genuinely differ
    if "Feathers" in slots:
        assert os.path.basename(slots[""]).lower() != os.path.basename(slots["Feathers"]).lower()
        from fgm_io import load_fgm
        b, f = load_fgm(slots[""]), load_fgm(slots["Feathers"])
        assert b.seed != f.seed, \
            f"body and feather seeds identical ({b.seed}) -- the wrong file was picked up"

    # a slot index past the end must raise rather than silently clamp to the last variant
    try:
        slot_for_index(species_dir, 9999)
    except IndexError:
        pass
    else:
        raise AssertionError("out-of-range variant index was accepted")

    rep = apply_variant_all(species_dir, 0)
    graded = {g["part"] for g in rep["graded"]}
    assert graded, f"nothing graded. skipped = {rep['skipped']}"
    # each graded part must have used ITS OWN seed, not the body's
    seeds = {g["part"]: g["seed"] for g in rep["graded"]}
    if "" in seeds and "Feathers" in seeds:
        assert seeds[""] != seeds["Feathers"], f"both parts graded with seed {seeds['']}"

    # --- seed substitution -------------------------------------------------------------------
    import coeff_store

    assert _override_for("Feathers", None) is None
    assert _override_for("Feathers", 29) == 29                 # bare int hits every part
    assert _override_for("Feathers", {"Feathers": 29}) == 29
    assert _override_for("", {"Feathers": 29}) is None         # dict leaves other parts alone

    feathers = [g for g in rep["graded"] if g["part"] == "Feathers"]
    if feathers:
        cx = feathers[0]["complexity"]
        exact = coeff_store.harvested_seeds(cx, exact_only=True)
        assert exact, f"no seed harvested at complexity {cx}; cannot test substitution"
        was = feathers[0]
        # Any harvested seed OTHER than the file's own. This used to require the file's own seed to
        # be unharvested, which quietly encoded the coverage of the day: harvesting seed 195 (the
        # Pyroraptor feathers seed) then broke the test even though substitution still worked. What
        # the test needs is a substitute that DIFFERS, not a base case that fails to grade.
        seed = next((s for s in exact if s != was["seed"]), None)
        assert seed is not None, \
            f"only seed {was['seed']} is harvested at complexity {cx}; nothing to substitute with"

        sub = apply_variant_all(species_dir, 0, seed_override={"Feathers": seed})
        f2 = next(g for g in sub["graded"] if g["part"] == "Feathers")
        assert f2["seed"] == seed and f2["substituted"], f2
        assert f2["fgm_seed"] == was["seed"], \
            f"the file's own seed was lost: {f2['fgm_seed']} != {was['seed']}"
        assert f2["exact"], f"substituting harvested seed {seed} still produced a flat gradient"
        # the body must be untouched by a per-part override
        b2 = next((g for g in sub["graded"] if g["part"] == ""), None)
        if b2 is not None:
            assert not b2["substituted"] and b2["seed"] == b2["fgm_seed"], b2

        # and back: no override must restore the file's own seed, not stick on the substitute
        rep = apply_variant_all(species_dir, 0)
        f3 = next(g for g in rep["graded"] if g["part"] == "Feathers")
        assert f3["seed"] == was["seed"] and not f3["substituted"], f3

    import bpy

    # Every graded part must still own a LIVE group. palette_group names its group after the
    # species and variant only, and _new_group deletes any existing group of that name -- so
    # grading a second part of the same variant used to silently strip the first part's node tree,
    # sever its albedo chain and render it white. A tree-less node has no sockets at all, which is
    # the cheap way to detect it.
    for g in rep["graded"]:
        nt = bpy.data.materials[g["material"]].node_tree
        node = nt.nodes[g["node"]]
        assert node.node_tree is not None and len(node.inputs), \
            (f"{g['part'] or '(body)'}: grade node {g['node']} lost its group -- another part's "
             f"grade almost certainly reused the name")

    # re-applying must replace, not stack -- a second grade node renders the mesh white
    before = {}
    for g in rep["graded"]:
        nt = bpy.data.materials[g["material"]].node_tree
        before[g["material"]] = len([n for n in nt.nodes if n.name.startswith("JWE3_Grade")])
    apply_variant_all(species_dir, 0)
    for mname, n in before.items():
        nt = bpy.data.materials[mname].node_tree
        now = len([n2 for n2 in nt.nodes if n2.name.startswith("JWE3_Grade")])
        assert now == n == 1, f"{mname}: {n} -> {now} grade nodes; re-apply stacked"

    # The fur shell and fin OCCLUDE the body, so if they are skipped the render shows raw base
    # diffuse and every colour judgement is worthless. This is the regression test for the session
    # that was spent comparing screenshots against geometry that was never being graded.
    derived = blender_parts.discover_parts(None, lod=0).get("__derived__", [])
    if derived:
        graded_objs = {g["object"] for g in rep["graded"]}
        missing = [o.name for o in derived
                   if o.name not in graded_objs
                   and o.data.materials and o.data.materials[0] is not None]
        assert not missing, f"fur shell/fin left ungraded -- they cover the body: {missing}"
        for o in derived:
            if not (o.data.materials and o.data.materials[0]):
                continue
            nt = o.data.materials[0].node_tree
            n = [x for x in nt.nodes if x.name.startswith("JWE3_Grade")]
            assert len(n) == 1, f"{o.name}: {len(n)} grade nodes"
            assert n[0].node_tree is not None and len(n[0].inputs), \
                f"{o.name}: grade node lost its group -- a name collision with another part"
            masks = [x for x in nt.nodes if x.name.startswith("JWE3_FurMask")]
            assert len(masks) <= 1, f"{o.name}: {len(masks)} fur-mask nodes accumulated"

            # ...and they must carry the BODY'S LAYER STACK, not the raw base diffuse. The fur
            # shader runs the same 16-layer loop over the same per-instance layer array; grading
            # the bare texture drops every layer from the surface that covers most of the animal.
            mirrored = [x for x in nt.nodes if x.name.startswith(MIRROR_PREFIX)]
            assert mirrored, f"{o.name}: no mirrored layer chain -- grading raw base diffuse"
            src = n[0].inputs["Albedo"].links
            assert src and src[0].from_node.name.startswith(MIRROR_PREFIX), \
                f"{o.name}: grade Albedo is not fed by the mirrored chain"
            # re-applying must not accumulate a second copy of the chain
            apply_variant_all(species_dir, 0)
            again = len([x for x in o.data.materials[0].node_tree.nodes
                         if x.name.startswith(MIRROR_PREFIX)])
            assert again == len(mirrored), \
                f"{o.name}: mirrored chain grew {len(mirrored)} -> {again} on re-apply"

    # EVERY graded material must record which variant it is showing, and the material must be the
    # one actually on the object. Rebuilding used to clone the feathers material ("...\.012") and
    # leave the old copy holding stale jwe3_* props, so inspecting it reported the wrong variant
    # while the render showed another -- the "which variant am I looking at?" trap.
    live = {o.data.materials[0].name
            for objs in blender_parts.discover_parts(None, lod=0).values()
            for o in objs if o.data.materials and o.data.materials[0]}
    for g in rep["graded"]:
        m = bpy.data.materials[g["material"]]
        assert g["material"] in live, \
            f"{g['part'] or '(body)'}: graded {g['material']}, which is on no object"
        # the material must actually SHADE, and the grade must reach something. A dangling grade
        # output plus a colour wired into Surface renders flat white and looks like a colour bug.
        blender_parts.verify_surface_chain(m)
        node = m.node_tree.nodes[g["node"]]
        assert node.outputs["Color"].links, \
            f"{g['part'] or '(body)'}: grade output reaches nothing -- the part is UNGRADED"
        assert m.get("jwe3_variant_index") == rep["index"], \
            (f"{g['part'] or '(body)'}: material says variant "
             f"{m.get('jwe3_variant_index')}, applied {rep['index']}")
        assert m.get("jwe3_seed") == g["seed"], g["part"]

    print("selftest ok:", {g["part"] or "(body)": g["seed"] for g in rep["graded"]},
          "skipped:", rep["skipped"])


if __name__ == "__main__":
    print("imports cleanly; run selftest() inside Blender")
