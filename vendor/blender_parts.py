"""Mesh parts, and the one place that decides node-chain order.

A JWE3 dinosaur is not one mesh with one material. Pyroraptor has `fur` (which IS the body -- see
below), `feathers`, `fur_fin` and `fur_shell`, each with its own shader; Psittacosaurus has `quills`.
Each cosmetic part carries its own variant and pattern FGM, paired 1:1 with the body's by the
interleaved manifest (see part_manifest.py).

**`DinosaurFur_Vanilla_BaseLayered` is the body.** Its texture list is identical to
`DinosaurLayered_Layered_Opaque`'s -- all the pLayered_* maps -- plus fur anisotropy. A furred
species is not a new colour system, so the existing 16-layer build covers it.

CHAIN ORDER. The grade and the pattern are INDEPENDENT cosmetic axes: the game lets you pick either
without the other, so either node group may be applied first, second, or alone. If both spliced
"just before the Material Output" the order of application would silently decide the chain order.
`splice_at` therefore inserts by CHAIN_POS, so the result is the same either way.
"""
import bpy

# Higher runs later in the chain. The pattern sitting after the grade is an ASSUMPTION -- the
# composite has not been read out of the shader yet (PATTERNS.md open question 1). What matters
# for now is that it is CONSISTENT regardless of application order.
CHAIN_POS = {
    "JWE3_Grade": 10,
    "JWE3_Pattern": 20,
}

# mesh part name (the text after "<object>: ") -> cosmetic part token used by part_manifest
PART_TOKENS = {
    "fur": "",              # the layered body on a furred species
    "body": "",
    "feathers": "Feathers",
    "quills": "Quills",
}

# parts that render but carry no cosmetic of their own -- they inherit the body's
DERIVED_PARTS = ("fur_fin", "fur_shell")


def mesh_part_name(obj):
    """The mesh part cobra-tools imported, e.g. 'pyroraptor_female_L0: fur_fin' -> 'fur_fin'."""
    return obj.name.split(":")[-1].strip() if ":" in obj.name else ""


def lod_of(obj):
    """LOD number from the object name, or None. 'pyroraptor_female_L2: fur' -> 2."""
    for chunk in obj.name.replace(":", " ").split():
        if len(chunk) > 1 and chunk[0] == "L" and chunk[1:].isdigit():
            return int(chunk[1:])
        if "_L" in chunk:
            tail = chunk.rsplit("_L", 1)[-1]
            if tail.isdigit():
                return int(tail)
    return None


def discover_parts(objects=None, lod=0):
    """{part_token: [objects]} plus {'__derived__': [...]} for fin/shell.

    Parts are read from the MESH, never guessed from the species name -- Pyroraptor would break any
    name-derived rule immediately, since its body part is called 'fur'.

    A SINGLE-PART species leaves the suffix EMPTY: Indoraptor imports as `indoraptor_L0: `, with
    nothing after the colon, because it has no fur/feather parts to disambiguate from. That is the
    body. Without this, every such species landed in `__unknown__` and anything needing a body part
    failed with "no matching mesh part is in the scene (found: none)".

    The colon is what makes this safe. `mesh_part_name` also returns "" for an object with NO colon
    at all -- a stray Cube, a backdrop, an imported prop -- and those must NOT be claimed as the
    body. Only "has a colon, nothing after it" means a real cobra-tools single-part import.
    """
    objects = objects if objects is not None else bpy.data.objects
    out = {}
    for o in objects:
        if o.type != "MESH" or "joint_physics" in o.name:
            continue
        if lod is not None and lod_of(o) not in (None, lod):
            continue
        part = mesh_part_name(o)
        if not part and ":" in o.name:
            part = "body"
        if part in DERIVED_PARTS:
            out.setdefault("__derived__", []).append(o)
        elif part in PART_TOKENS:
            out.setdefault(PART_TOKENS[part], []).append(o)
        else:
            out.setdefault("__unknown__", []).append(o)
    return out


def _surface_link(mat):
    """(node feeding Material Output.Surface, the output node), or (None, output)."""
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        return None, None
    for l in nt.links:
        # compare by NAME: bpy hands back a fresh wrapper per attribute access, so `is` between
        # separately fetched nodes is False even for the same node and silently matches nothing
        if l.to_node.name == out.name and l.to_socket.name == "Surface":
            return l.from_node, out
    return None, out


def chain_nodes(mat):
    """Our spliced GROUPS currently in `mat`, in chain order.

    Group nodes only. Auxiliary nodes share the same name prefix so `unsplice` cleans them up --
    the pattern's `JWE3_Pattern_IndexMap` texture, the grade's `JWE3_FurMask` -- but they are
    inputs to a chain member, not members themselves. Counting them as chain nodes put the index
    map in the middle of the flow, one slot after the group it feeds.
    """
    found = []
    for n in mat.node_tree.nodes:
        if n.type != "GROUP":
            continue
        for prefix, pos in CHAIN_POS.items():
            if n.name.startswith(prefix) or (n.node_tree
                                             and n.node_tree.name.startswith(prefix)):
                found.append((pos, n))
                break
    return [n for _, n in sorted(found, key=lambda pn: pn[0])]


def dead_group_nodes(mat, recurse=False):
    """Group nodes whose node_tree is gone -- they have no sockets, so they SEVER the chain.

    Two ways they appear: `palette_group` name collisions (documented in the palette notes), and
    purging orphan datablocks while a stale node still points at one. Either way the node survives
    with `node_tree = None` and zero sockets, `unsplice` cannot relink through it, and whatever it
    fed goes unlinked -- which renders as flat white/grey.

    `recurse=True` also walks INSIDE every nested group tree, returning `(tree, node)` pairs.
    The top-level-only scan is not enough and that is not hypothetical: all eight of Pyroraptor's
    per-layer groups were carrying dead `JWE3_LayerBlend` and `JWE3_SatContrast` nodes while the
    material's own tree was clean. The consequence was silent and severe -- with the blend group
    gone, each layer's mask reached nothing and `smoothstep` ran on a literal 0.5, so all eight
    layers composited at a flat 50% over the whole animal. That is why the eye was graded at all:
    a texel no layer covers is supposed to keep colour weight 0 and be left alone.
    """
    dead = [n for n in mat.node_tree.nodes if n.type == "GROUP" and n.node_tree is None]
    if not recurse:
        return dead
    out = [(mat.node_tree, n) for n in dead]
    seen, stack = set(), [n.node_tree for n in mat.node_tree.nodes
                          if n.type == "GROUP" and n.node_tree is not None]
    while stack:
        tree = stack.pop()
        if tree.name in seen:
            continue
        seen.add(tree.name)
        for n in tree.nodes:
            if n.type != "GROUP":
                continue
            if n.node_tree is None:
                out.append((tree, n))
            else:
                stack.append(n.node_tree)
    return out


def verify_surface_chain(mat):
    """Raise unless the material actually shades: a SHADER reaches Surface and no group is dead.

    A colour wired straight into `Material Output.Surface` is accepted by Blender and renders as
    a flat, unlit surface -- indistinguishable at a glance from "the grade made it pale", which is
    how it went unnoticed. Check it rather than trusting the render.
    """
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        raise ValueError(f"{mat.name}: no Material Output")
    links = out.inputs["Surface"].links
    if not links:
        raise ValueError(f"{mat.name}: nothing reaches Material Output.Surface")
    if links[0].from_socket.type != "SHADER":
        raise ValueError(
            f"{mat.name}: Material Output.Surface is fed by "
            f"{links[0].from_node.name}.{links[0].from_socket.name} "
            f"({links[0].from_socket.type}), not a shader -- it will render flat and unlit")
    # Recursive: a dead group NESTED inside a layer group leaves the material's own tree looking
    # perfectly healthy while the layer masks quietly do nothing. See dead_group_nodes.
    dead = dead_group_nodes(mat, recurse=True)
    if dead:
        raise ValueError(f"{mat.name}: group node(s) with no node_tree, which sever the chain: "
                         f"{[f'{t.name}/{n.name}' for t, n in dead]}")
    return True


def albedo_sinks(mat, found):
    """`found` if it has anything, else the material's own colour terminal. Never returns empty.

    WHY THIS EXISTS. Every splice helper re-uses "whatever the albedo currently feeds" as the place
    to put its output. That is right until something upstream severs the chain -- and then the
    breakage is PERMANENT and SILENT: with no sinks the grade output dangles, Base Color falls back
    to Blender's default 0.8 grey, and every later re-apply finds no sinks either and preserves the
    break. Pyroraptor's feathers sat like that undetected, rendering the largest surface on the
    animal as flat default grey while three sessions of colour work were judged against it.

    Raises rather than returning empty: a grade whose output goes nowhere must never be reported as
    applied.
    """
    if found:
        return found
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None and "Base Color" in bsdf.inputs:
        return [(bsdf.name, "Base Color")]
    # cobra-tools' imported materials have no Principled -- they end in a MainShader group whose
    # colour socket is spelled a dozen ways across shaders, so match on the name rather than list it
    for n in nt.nodes:
        if n.type != "GROUP":
            continue
        for s in n.inputs:
            low = s.name.lower()
            if s.type == "RGBA" and ("colour" in low or "color" in low or "diffuse" in low):
                return [(n.name, s.name)]
    raise ValueError(f"{mat.name}: albedo feeds nothing and no colour terminal was found to fall "
                     f"back on -- the material's colour chain is broken")


def unsplice(mat, prefix):
    """Remove every node whose name starts with `prefix`, relinking around it. True if any went."""
    nt = mat.node_tree
    # Sweep dead group nodes too, whatever they are named. A tree-less group has no sockets, so it
    # cannot be relinked through and it is never a legitimate part of the chain -- and the stale
    # ones are exactly the nodes `prefix` fails to match (a pre-rename grade left as "Group.009").
    for n in dead_group_nodes(mat):
        nt.nodes.remove(n)
    victims = [n for n in nt.nodes if n.name.startswith(prefix)]
    if not victims:
        return False
    for n in victims:
        # Relink from whatever fed the node's FIRST input -- that is the one carrying the signal the
        # group passes through (Albedo on a palette grade), and it is what splice_at connects. A
        # palette node also takes KeySource, Height and ColourWeight; picking an arbitrary incoming
        # link can hand a FLOAT back to a colour socket and silently recolour the mesh.
        first = n.inputs[0].name if len(n.inputs) else None
        src = next((l.from_socket for l in nt.links
                    if l.to_node.name == n.name and l.to_socket.name == first), None)
        if src is None:
            src = next((l.from_socket for l in nt.links if l.to_node.name == n.name), None)
        dsts = [l.to_socket for l in nt.links if l.from_node.name == n.name]
        nt.nodes.remove(n)
        if src is not None:
            for d in dsts:
                nt.links.new(src, d)
    return True


def splice_at(mat, node, in_socket, out_socket, prefix):
    """Insert `node` into the surface chain at its CHAIN_POS, not merely at the end.

    Everything already spliced with a LOWER position stays upstream; anything with a HIGHER
    position stays downstream. That is what makes grade-then-pattern and pattern-then-grade produce
    the same tree.
    """
    nt = mat.node_tree
    pos = CHAIN_POS[prefix]
    upstream, _ = _surface_link(mat)
    later = [n for n in chain_nodes(mat)
             if n.name != node.name and _pos_of(n) is not None and _pos_of(n) > pos]

    if later:
        # sit immediately before the earliest later node, taking over its input
        target = later[0]
        feed = next((l for l in nt.links if l.to_node.name == target.name), None)
        if feed is not None:
            src = feed.from_socket
            nt.links.remove(feed)
            nt.links.new(src, in_socket)
        nt.links.new(out_socket, target.inputs[0])
        return node

    # Nothing later: take over the material's ALBEDO terminal.
    #
    # NOT the Surface link, which is what this used to do. The node feeding Surface is the BSDF --
    # a shader -- so inserting a colour node between it and the output puts RGBA into Surface and
    # the mesh renders flat and unlit. `verify_surface_chain` catches it, which is how it was
    # found; nothing had exercised this branch before because the palette grade splices itself via
    # `albedo_sinks` rather than through here.
    #
    # Everything spliced by CHAIN_POS is an albedo transform, so the terminal is the right anchor:
    # we take whatever currently feeds Base Color as our input and feed Base Color ourselves.
    for node_name, sock_name in albedo_sinks(mat, []):
        sink = nt.nodes[node_name].inputs[sock_name]
        feed = sink.links[0].from_socket if sink.links else None
        if feed is not None:
            nt.links.new(feed, in_socket)
        nt.links.new(out_socket, sink)
    return node


def repair_surface(mat):
    """Reconnect the shader to Material Output.Surface if a non-shader is feeding it. True if fixed.

    `splice_at` used to take over the SURFACE link, so a colour node ended up wired into Surface and
    the shader's output went nowhere. Unsplicing that node then relinked its *source* straight to
    Surface, leaving the material permanently colour-into-Surface: it renders flat and unlit, and
    no later apply notices because every splice helper works on the albedo chain instead.

    Only acts on the unambiguous case -- a shader node exists, and something that is NOT a shader
    is in the Surface slot. Anything stranger is left alone for `verify_surface_chain` to report.
    """
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        return False
    links = out.inputs["Surface"].links
    if links and links[0].from_socket.type == "SHADER":
        return False
    shader = next((n for n in nt.nodes
                   if any(o.type == "SHADER" for o in n.outputs)), None)
    if shader is None:
        return False
    for l in list(links):
        nt.links.remove(l)
    nt.links.new(next(o for o in shader.outputs if o.type == "SHADER"), out.inputs["Surface"])
    return True


def _albedo_input(node):
    """The socket carrying the incoming ALBEDO for a tail node, or None.

    Blender's Mix node has THREE sockets called "A" (float, vector, colour), and `inputs["A"]`
    hands back the FIRST -- the float one -- which is never the albedo. Index 6 is the colour A.
    """
    if node.type == "MIX":
        return node.inputs[6] if node.inputs[6].links else None
    for name in ("Albedo", "Color", "Base Color"):
        if name in node.inputs and node.inputs[name].links:
            return node.inputs[name]
    return next((i for i in node.inputs if i.links and i.type == "RGBA"), None)


def albedo_chain(mat):
    """The nodes between the layer stack and the shader, in LINK order.

    Walked backwards from the albedo terminal rather than taken from `nt.nodes`, because collection
    order is arbitrary and the link order is the only thing that is true. Laying the tail out by
    collection order drew the AO multiply UPSTREAM of the grade when it is actually downstream of
    it -- the arrows ran backwards through the middle of the graph.

    Stops at the layer/base groups, the UV node and any texture: those are upstream and keep the
    positions `layout()` gave them.
    """
    nt = mat.node_tree
    try:
        sinks = albedo_sinks(mat, [])
    except ValueError:
        return []
    node_name, sock_name = sinks[0]
    cur = nt.nodes[node_name].inputs[sock_name]
    order, seen = [], set()
    while cur is not None and cur.links:
        fr = cur.links[0].from_node
        if fr.name in seen or fr.type in ("TEX_IMAGE", "UVMAP", "GROUP_INPUT"):
            break
        if fr.type == "GROUP" and not any(fr.name.startswith(p) for p in CHAIN_POS):
            break                    # a layer group or the base group -- upstream
        seen.add(fr.name)
        order.append(fr)
        cur = _albedo_input(fr)
    order.reverse()
    return order


def layout_chain(mat, dx=300, aux_dy=-260):
    """Re-lay the TAIL of the surface graph: spliced groups, then the shader, then the output.

    Computed from an anchor every time rather than nudged, so it is IDEMPOTENT. The version this
    replaces shifted everything to the right of an insertion point by one node width per splice,
    so each re-apply pushed the BSDF and Material Output further out -- the feathers material ended
    up with an 1800 px gap between the grade and the BSDF, and a node dropped with no location at
    all landed at the origin, on top of the start of the chain.

    Only unparented nodes move: a framed node's location is relative to its frame, so shifting one
    by an absolute delta slides it out of its frame.
    """
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        return
    repair_surface(mat)

    # The layered body's own tail (base group, albedo mixes, Bump) first, because
    # `blender_palette_nodes.apply_to` runs a full depth-sort of the material AFTER `build` laid
    # that tail out -- so anything done at build time is undone by the first grade. Doing it here,
    # from the node names the material records, means the LAST thing to touch the graph is the
    # thing that arranges it.
    try:
        import blender_layer_nodes
        prev = nt.nodes.get(mat.get("jwe3_last_layer") or "")
        base = nt.nodes.get(mat.get("jwe3_base_node") or "")
        if prev is not None and base is not None:
            blender_layer_nodes._layout_tail(nt, prev, base)
    except Exception:
        pass                    # layout is cosmetic; never let it break an apply

    shader = None
    for l in nt.links:
        if l.to_node.name == out.name and l.to_socket.name == "Surface":
            shader = l.from_node
            break

    tail = albedo_chain(mat) or chain_nodes(mat)
    tail_names = {n.name for n in tail} | {out.name} | ({shader.name} if shader else set())
    # aux nodes belong to a chain node (the pattern's index map, the grade's fur mask) -- they are
    # placed relative to their owner, not in the row
    aux = [n for n in nt.nodes
           if n.parent is None and n.name not in tail_names
           and any(n.name.startswith(p) for p in CHAIN_POS)]
    aux_names = {n.name for n in aux}

    upstream = [n for n in nt.nodes
                if n.parent is None and n.name not in tail_names and n.name not in aux_names]
    x = (max((n.location.x + n.width for n in upstream), default=0.0) + 60.0) if upstream else 0.0

    def _prefix(name):
        return next((p for p in CHAIN_POS if name.startswith(p)), None)

    for n in tail:
        n.location = (x, 0.0)
        # Aux belongs to the chain node sharing its CHAIN_POS prefix -- match on THAT, not on the
        # full node name: the pattern's index map is "JWE3_Pattern_IndexMap" while its group is
        # "JWE3_Pattern_feathers", so neither name is a prefix of the other and a name-based test
        # silently never fires, leaving the texture stranded at the far left of the tree.
        for a in aux:
            if _prefix(a.name) == _prefix(n.name):
                a.location = (x - 320.0, aux_dy)
        x += dx
    if shader is not None and shader.name not in {n.name for n in tail}:
        shader.location = (x, 0.0)
        x += dx
    out.location = (x, 0.0)


def _pos_of(node):
    for prefix, pos in CHAIN_POS.items():
        if node.name.startswith(prefix):
            return pos
        if node.type == "GROUP" and node.node_tree and node.node_tree.name.startswith(prefix):
            return pos
    return None


def single_user_group(node):
    """Give `node` its own copy of its node group.

    MainShader is ONE group shared by all four Pyroraptor part materials (feathers, fur, fur_fin,
    fur_shell). Editing it in place changes all four at once -- the same shape of bug as the shared
    image datablock that once turned Lokiceratops brown.
    """
    if node.type == "GROUP" and node.node_tree and node.node_tree.users > 1:
        node.node_tree = node.node_tree.copy()
    return node.node_tree


def selftest():
    """Run INSIDE Blender: exec(open(__file__).read()); selftest()"""
    # --- pure-python helpers first, no scene needed ---
    class FakeObj:
        def __init__(self, name):
            self.name, self.type = name, "MESH"

    assert mesh_part_name(FakeObj("pyroraptor_female_L0: fur_fin")) == "fur_fin"
    assert mesh_part_name(FakeObj("pyroraptor_female_L0: feathers")) == "feathers"
    assert mesh_part_name(FakeObj("models")) == ""
    assert lod_of(FakeObj("pyroraptor_female_L2: fur")) == 2
    assert lod_of(FakeObj("pyroraptor_female_L0: feathers")) == 0
    assert lod_of(FakeObj("models")) is None

    objs = [FakeObj(f"pyroraptor_female_L{n}: {p}")
            for n in (0, 1) for p in ("fur", "feathers", "fur_fin", "fur_shell")]
    objs.append(FakeObj("pyroraptor_female_joints_def_c_head_joint_physics"))
    parts = discover_parts(objs, lod=0)
    assert set(parts) == {"", "Feathers", "__derived__"}, set(parts)
    assert len(parts[""]) == 1 and len(parts["Feathers"]) == 1, parts
    assert len(parts["__derived__"]) == 2, parts          # fin + shell
    # LOD1 must be excluded, and physics joints must never appear
    assert all("L0" in o.name for v in parts.values() for o in v), parts

    # the body of a FURRED species is the 'fur' part -- a name-derived rule would miss it
    assert PART_TOKENS["fur"] == "" and PART_TOKENS["feathers"] == "Feathers"

    import bpy
    mat = bpy.data.materials.new("JWE3_PARTS_TEST")
    mat.use_nodes = True
    nt = mat.node_tree
    base = _surface_link(mat)[0]
    assert base is not None, "fresh material had nothing feeding Surface"

    def add(prefix):
        g = nt.nodes.new("ShaderNodeGroup")
        tree = bpy.data.node_groups.new(prefix, "ShaderNodeTree")
        tree.interface.new_socket("In", in_out="INPUT", socket_type="NodeSocketShader")
        tree.interface.new_socket("Out", in_out="OUTPUT", socket_type="NodeSocketShader")
        g.node_tree = tree
        g.name = prefix
        return g

    def order(m):
        return [n.name for n in chain_nodes(m)]

    # grade then pattern
    g = add("JWE3_Grade")
    splice_at(mat, g, g.inputs[0], g.outputs[0], "JWE3_Grade")
    p = add("JWE3_Pattern")
    splice_at(mat, p, p.inputs[0], p.outputs[0], "JWE3_Pattern")
    forward = order(mat)

    # pattern then grade, from scratch -- MUST give the same chain
    unsplice(mat, "JWE3_Grade")
    unsplice(mat, "JWE3_Pattern")
    assert order(mat) == [], order(mat)
    p = add("JWE3_Pattern")
    splice_at(mat, p, p.inputs[0], p.outputs[0], "JWE3_Pattern")
    g = add("JWE3_Grade")
    splice_at(mat, g, g.inputs[0], g.outputs[0], "JWE3_Grade")
    assert order(mat) == forward, f"application order changed the chain: {order(mat)} vs {forward}"

    # a pattern applies with NO grade present -- the two axes are independent
    unsplice(mat, "JWE3_Grade")
    assert order(mat) == ["JWE3_Pattern"], order(mat)
    assert unsplice(mat, "JWE3_Pattern") is True
    assert unsplice(mat, "JWE3_Pattern") is False, "unsplice on a clean material reported True"
    assert _surface_link(mat)[0] is not None, "unsplice left Surface unconnected"

    # a MULTI-INPUT grade must relink from its first input. The palette group takes Albedo,
    # KeySource, Height and ColourWeight; relinking from an arbitrary one hands a float to a colour
    # socket. Wire Height FIRST so a naive "first link found" implementation picks the wrong one.
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tree = bpy.data.node_groups.new("JWE3_Grade", "ShaderNodeTree")
    for nm, ty in (("Albedo", "NodeSocketColor"), ("Height", "NodeSocketFloat")):
        tree.interface.new_socket(nm, in_out="INPUT", socket_type=ty)
    tree.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    grp = nt.nodes.new("ShaderNodeGroup")
    grp.node_tree, grp.name = tree, "JWE3_Grade"
    rgb, val = nt.nodes.new("ShaderNodeRGB"), nt.nodes.new("ShaderNodeValue")
    nt.links.new(val.outputs[0], grp.inputs["Height"])        # deliberately linked first
    nt.links.new(rgb.outputs[0], grp.inputs["Albedo"])
    nt.links.new(grp.outputs["Color"], bsdf.inputs["Base Color"])
    assert unsplice(mat, "JWE3_Grade") is True
    feed = next(l.from_node.name for l in nt.links
                if l.to_node.name == bsdf.name and l.to_socket.name == "Base Color")
    assert feed == rgb.name, f"unsplice relinked Base Color from {feed}, not the Albedo source"

    bpy.data.materials.remove(mat)
    print("selftest ok")


if __name__ == "__main__":
    print("imports cleanly; run selftest() inside Blender")
