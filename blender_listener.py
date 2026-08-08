r"""JWE3 Variant Preview Listener -- a Blender add-on socket server (Task 4 of the Variant Editor plan).

WHAT THIS IS. A small Blender add-on that opens a localhost TCP server so the standalone
VariantEditor tool (a separate PyQt5 process, outside Blender) can drive a live material preview
on a JWE dinosaur mesh the user has already imported with cobra-tools' Blender plugin. This add-on
does NOT create geometry -- it only builds/assigns a material node graph (`blender_layer_nodes`)
and re-grades it (`blender_palette_nodes`) on an object that must already exist in the scene.

WHY A SOCKET AND NOT A DIRECT CALL. The editor is a plain `python` process; Blender's `bpy` only
exists inside Blender's own process and can only be touched from Blender's main thread. So the
editor talks over a socket, and a background thread inside Blender receives bytes off the wire and
hands decoded commands to the main thread via `bpy.app.timers` -- the only supported way to get work
onto Blender's main thread from outside it (see the `jwe3-blender-bridge` memory).

MESSAGE FRAMING (exact -- Task 5's `preview_bridge.py` client MUST match this):

    Every message, in BOTH directions (editor -> listener request, listener -> editor reply), is:

        [4 bytes: big-endian unsigned length N]  [N bytes: UTF-8 JSON]

    i.e. `struct.pack(">I", len(body)) + body` where `body = json.dumps(obj).encode("utf-8")`.
    Decode the reply the same way: read exactly 4 bytes, unpack with `struct.unpack(">I", ...)`,
    then read exactly that many more bytes and `json.loads` them.

    One request per connection: the client opens a TCP connection to 127.0.0.1:8990, sends one
    framed JSON request, reads one framed JSON reply, then the listener closes the connection.
    The client should open a fresh connection for the next command.

COMMANDS:

    {"cmd": "build", "object": <mesh object name>, "mask_dir": ..., "mask_prefix": ...,
     "layers_json": ...}
        -> builds the 16-layer node material via `blender_layer_nodes.build_from_json(layers_json,
           mask_dir, mask_prefix)` and assigns it onto the NAMED, ALREADY-IMPORTED mesh object
           (`bpy.data.objects[object]`). Does not create geometry. If the object does not exist,
           replies {"ok": false, "error": "import the JWE model (.ms2) first"}. On success, the
           built material is remembered module-side (`_current_mat`) so a later `grade` can find it,
           and replies {"ok": true}.

    {"cmd": "grade", "block": {...}}
        -> re-grades `_current_mat` in place via `blender_palette_nodes.apply_to(_current_mat,
           block)`. Replies {"ok": true} on success, or {"ok": false, "error": "build first"} if no
           material has been built yet in this Blender session.

    Any exception raised while executing a command is caught and turned into
    {"ok": false, "error": str(e)} rather than killing the drain-queue timer.

MANUAL SMOKE TEST (there is no automated `selftest()` for this file -- it needs a running Blender):

    1. In Blender: Edit > Preferences > Add-ons > Install..., pick this file, enable
       "JWE3 Variant Preview Listener". (Or for iteration: Scripting tab, open this file, Run
       Script, then call `register()` once in the Python console.)
    2. Import a JWE dinosaur mesh with cobra-tools' Blender plugin (e.g. Spino Female's
       `models.ms2`) so a mesh object exists in the scene, and note its object name (e.g.
       "models" or whatever cobra-tools names it -- check the Outliner).
    3. From a SEPARATE `python` process (not inside Blender), send a length-prefixed "build"
       message using that object name, a real `mask_dir`/`mask_prefix` from
       `Variant Research/Textures`, and a `layers_json` path (the same JSON shape
       `blender_layer_nodes.build_from_json` expects). Example client snippet:

           import socket, struct, json
           def send(obj):
               body = json.dumps(obj).encode("utf-8")
               s = socket.create_connection(("127.0.0.1", 8990), timeout=10)
               s.sendall(struct.pack(">I", len(body)) + body)
               n = struct.unpack(">I", s.recv(4))[0]
               reply = b""
               while len(reply) < n:
                   reply += s.recv(n - len(reply))
               s.close()
               return json.loads(reply.decode("utf-8"))

           print(send({"cmd": "build", "object": "models", "mask_dir": r"...\Textures",
                       "mask_prefix": "bary_v00", "layers_json": r"...\layers.json"}))
           # expect {"ok": True}

    4. Then send a "grade" message with a real block (e.g. from `export_palette`):

           print(send({"cmd": "grade", "block": {...}}))
           # expect {"ok": True}, and the mesh's material re-grades in the Blender viewport.

    5. Sending "grade" before any "build" in a fresh Blender session should reply
       {"ok": False, "error": "build first"}. Sending "build" with a wrong object name should
       reply {"ok": False, "error": "import the JWE model (.ms2) first"}.

Import discipline: `bpy` and the two node-graph modules (which themselves import `bpy` at module
scope) are imported LAZILY, inside functions, never at module top level. That keeps this file
importable by plain `python` outside Blender for the headless sanity check at the bottom.
"""

import json
import os
import queue
import shutil
import socket
import struct
import sys
import threading

HOST = "127.0.0.1"
PORT = 8990
DRAIN_INTERVAL = 0.1  # seconds between bpy.app.timers drain-queue re-fires

# NO bl_info here on purpose. The add-on is the whole folder and `__init__.py` carries it; a second
# bl_info in a submodule makes Blender offer the add-on twice, which is how you end up with two
# "JWE3 Variant (.fgm)" entries in the File > Import menu.

# Module-level state (Blender add-ons are singletons per session; no class needed).
_server_sock = None
_server_thread = None
_stop_event = threading.Event()
_command_queue = queue.Queue()
def _on_load_post(*_args):
    """Drop cached datablocks when Blender loads a file (File > New / Open / revert).

    `_current_mat` holds a Material. Loading a file frees every datablock, and a freed one is NOT
    None -- it is a dead StructRNA that raises `ReferenceError: StructRNA has been removed` on
    first touch, so `if _current_mat is None` waves it straight through. Clearing here makes the
    next `grade` report "nothing built yet", which is true and recoverable.
    """
    global _current_mat, _current_grade
    _current_mat = None
    _current_grade = None


# Equivalent to decorating with @bpy.app.handlers.persistent, which we cannot do: this module is
# imported headlessly by the selftests and has no bpy at module scope. Blender only looks for this
# attribute. Without it the handler is REMOVED by the very first file load -- the one load it
# exists to handle.
_on_load_post._bpy_persistent = True

_current_mat = None  # the last material built by a "build" command; "grade" re-grades this
_current_grade = None  # NAME of the grade node the last "grade" spliced in, removed by the next
                       # one (see _unsplice_grade). A name, not a reference: node references go
                       # stale across undo/file-reload, names survive.
# What File > Import last loaded, so the standalone editor can follow along (it polls "state").
# `serial` increments on every import, which is how the editor spots a RE-import of the same file.
_last_import = {"path": None, "object": None, "species": None, "sex": None, "serial": 0}

# The ADD-ON's module name, set by __init__.py before register(). Blender matches
# AddonPreferences.bl_idname against it, and this module's own __name__ is NOT it -- we are
# imported flat ("blender_listener"), so using __name__ meant the preferences panel never
# rendered and the texture-folder settings were unreachable from inside Blender.
ADDON_ID = None


def _here():
    """This add-on's own folder. Everything it needs is inside it."""
    return os.path.dirname(os.path.abspath(__file__))


def _parent_dir():
    """The vendored node modules (`blender_layer_nodes`, `blender_palette_nodes`, ...).

    They live in `vendor/` INSIDE the add-on, so there is nothing to configure and nothing outside
    the add-on has to exist. (This used to point at an external "Variant Research" folder, set via
    an add-on preference -- which meant an installed copy could not work until the user found and
    entered that path.)
    """
    return os.path.join(_here(), "vendor")


def _assets_reachable():
    """True if the add-on is a complete install rather than a lone .py file.

    Installing just `blender_listener.py` copies one file into Blender's addons folder, where none
    of its siblings exist and every import fails at first use. Install the whole folder/zip.
    """
    return (os.path.isfile(os.path.join(_here(), "preview_assets.py"))
            and os.path.isfile(os.path.join(_parent_dir(), "blender_layer_nodes.py")))


def _recv_exact(conn, n):
    """Read exactly n bytes from conn, or return None if the peer closed early."""
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _recv_message(conn):
    """Read one 4-byte-big-endian-length-prefixed UTF-8 JSON message. None on early close/bad JSON."""
    hdr = _recv_exact(conn, 4)
    if hdr is None:
        return None
    (length,) = struct.unpack(">I", hdr)
    body = _recv_exact(conn, length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def _send_message(conn, obj):
    """Write one 4-byte-big-endian-length-prefixed UTF-8 JSON message."""
    body = json.dumps(obj).encode("utf-8")
    conn.sendall(struct.pack(">I", len(body)) + body)


def _server_loop():
    """Background thread: accept connections, decode one framed request each, queue for main thread.

    Runs entirely off Blender's main thread, so it must never touch bpy directly.
    """
    global _server_sock
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(5)
    sock.settimeout(0.5)  # so the loop can notice _stop_event without blocking forever
    _server_sock = sock

    while not _stop_event.is_set():
        try:
            conn, _addr = sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break  # socket was closed out from under us (unregister)

        try:
            msg = _recv_message(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            continue

        if msg is None:
            try:
                conn.close()
            except Exception:
                pass
            continue

        _command_queue.put((conn, msg))


def _norm_obj_name(name):
    """Normalise an object name for matching: drop cobra-tools' `: <material>` suffix, fold case.

    cobra-tools names imported objects `"<name>: <material>"`, and when the material part is empty
    that leaves a TRAILING COLON AND SPACE -- the real object is `'lokiceratops_female_L0: '` while
    the Outliner shows `lokiceratops_female_L0:`. So anyone reading the name off the screen types
    something that never matches exactly, and the build fails for no visible reason.
    """
    return (name or "").split(":")[0].strip().lower()


def _resolve_object(name):
    """(object, candidates) -- find a mesh object from a name a human typed, tolerantly."""
    import bpy
    obj = bpy.data.objects.get(name or "")
    if obj is not None:
        return obj, []

    want = _norm_obj_name(name)
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not want:
        return None, [o.name for o in meshes[:10]]

    exact = [o for o in meshes if _norm_obj_name(o.name) == want]
    if len(exact) == 1:
        return exact[0], []
    if exact:                                   # several LODs/duplicates: take the densest mesh
        exact.sort(key=lambda o: len(o.data.polygons), reverse=True)
        return exact[0], []

    partial = [o for o in meshes if want in _norm_obj_name(o.name)]
    if len(partial) == 1:
        return partial[0], []
    return None, [o.name for o in (partial or meshes)][:10]


def _build_on_object(object_name, mask_dir, mask_prefix, layers_json, base_diffuse=None,
                     skin_name=None):
    """Build the layer material and assign it to `object_name`. Returns the material, or None if
    that object does not exist. Shared by the socket `build` command and the menu importer.

    `base_diffuse` swaps the species base diffuse for a VARIANTSET's (a cosmetic skin). Everything
    else -- layers, masks, height, roughness -- is identical, which is the whole of what a
    variantset does. `skin_name` only tags the material so which skin is loaded is visible rather
    than inferred; picking the wrong one is otherwise a silent wrong-texture render.
    """
    import bpy
    sys.path.insert(0, _parent_dir())
    from blender_layer_nodes import build_from_json

    global _current_mat, _current_grade
    obj, _candidates = _resolve_object(object_name)
    if obj is None:
        return None

    kw = {}
    if base_diffuse:
        kw["base_diffuse_override"] = base_diffuse
    mat = build_from_json(layers_json, mask_dir, mask_prefix, **kw)
    if skin_name:
        mat["jwe3_variantset"] = skin_name

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    obj.active_material = mat

    _current_mat = mat
    _current_grade = None      # fresh material: no grade spliced into it yet

    # Replacing the material orphans the previous one's grade tree (one datablock per rebuild, and
    # browsing variants rebuilds constantly). Only ever touch OUR prefix, and only at zero users.
    for tree in [t for t in bpy.data.node_groups
                 if t.name.startswith("JWE3_Palette_") and t.users == 0]:
        bpy.data.node_groups.remove(tree)
    return mat


def _cmd_build(cmd):
    """Handle {"cmd": "build", ...} -- must run on Blender's main thread."""
    mat = _build_on_object(cmd["object"], cmd["mask_dir"], cmd["mask_prefix"], cmd["layers_json"],
                           base_diffuse=cmd.get("base_diffuse"),
                           skin_name=cmd.get("skin_name"))
    if mat is None:
        # Say WHICH names exist. "build failed" alone is useless when the cause is a name that
        # looks right on screen but differs by cobra-tools' trailing ": " suffix.
        _obj, candidates = _resolve_object(cmd["object"])
        if candidates:
            return {"ok": False,
                    "error": "no mesh object matching %r. Did you mean one of: %s"
                             % (cmd["object"], ", ".join(repr(c) for c in candidates[:5]))}
        return {"ok": False, "error": "no mesh objects in the scene -- import the JWE model "
                                      "(.ms2) with cobra-tools first"}
    return {"ok": True}


def _unsplice_grade(mat, node_name):
    """Remove a previously spliced grade node and reconnect the albedo straight to what it fed.

    WHY THIS EXISTS (found in the Task 7 live smoke test, 2026-07-26): `apply_to` *inserts* a new
    grade node on every call -- it does not update in place. Worse, `palette_group` names its
    datablock deterministically (`JWE3_Palette_<species>_v<NN>`) and `_new_group` deletes any
    existing group of that name first. So a second grade both (a) spliced itself into the first
    grade's input, double-applying the whole grade, and (b) deleted the first node's `node_tree`
    out from under it, leaving a group node with `node_tree = None` that passes nothing through --
    the mesh rendered white. With a debounced slider drag sending a push every 100 ms, that is
    every drag. So each grade must remove its predecessor before applying.
    """
    import bpy
    nt = mat.node_tree
    pg = nt.nodes.get(node_name or "")
    if pg is None:
        return
    src = nt.nodes.get(mat.get("jwe3_albedo_node", ""))
    tree = pg.node_tree
    # where the grade currently feeds (BSDF Base Color, or the AO multiply) -- restore those
    sinks = [l.to_socket for l in pg.outputs["Color"].links] if tree is not None else []
    nt.nodes.remove(pg)
    if src is not None:
        for sock in sinks:
            nt.links.new(src.outputs[2], sock)
    if tree is not None and tree.users == 0:
        bpy.data.node_groups.remove(tree)


def _grade_current(block):
    """Re-grade the current material, replacing any previous grade. Returns False if nothing built."""
    sys.path.insert(0, _parent_dir())
    from blender_palette_nodes import apply_to

    global _current_grade
    if _current_mat is None:
        return False

    _unsplice_grade(_current_mat, _current_grade)   # replace, never stack (see _unsplice_grade)
    pg = apply_to(_current_mat, block)
    _current_grade = pg.name if pg is not None else None

    # apply_to writes its own generic label ("Baryonyx v00 seed 36/2"), which would wipe the source
    # filename every time the editor pushes a slider change. Re-stamp it from the material.
    stem = _current_mat.get("jwe3_variant_fgm")
    if pg is not None and stem:
        pg.label = "%s   seed %d/%d%s" % (stem, block["seed"], block["complexity"],
                                          "" if block["coeffExact"] else
                                          "   (NO COEFFS - base grade only)")
    return True


def _cmd_grade(cmd):
    """Handle {"cmd": "grade", "block": {...}} -- must run on Blender's main thread."""
    if not _grade_current(cmd["block"]):
        return {"ok": False, "error": "build first"}
    return {"ok": True}


def _model_texture_dir(object_name):
    """The folder the mesh's OWN textures were imported from, or None.

    cobra-tools imports a model out of one folder (`in_dir` in its `create_material`) that holds the
    .ms2, the .fgm files and the .png textures together, and it points the loaded images at files in
    that folder. So the images already on the mesh tell us where its textures live -- which is the
    right place to take masks from, no curated per-species folder needed. Only returns a folder that
    genuinely holds blend-weight masks.
    """
    import bpy
    sys.path.insert(0, _here())
    import preview_assets

    obj, _cands = _resolve_object(object_name)
    if obj is None or obj.type != "MESH":
        return None
    seen = set()
    for mat in list(obj.data.materials) + [obj.active_material]:
        if mat is None or not mat.use_nodes or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            img = getattr(node, "image", None)
            if img is None or not img.filepath:
                continue
            folder = os.path.dirname(bpy.path.abspath(img.filepath))
            if folder in seen:
                continue
            seen.add(folder)
            if preview_assets.detect_mask_prefix(folder):
                return folder
    return None


# -- File > Import > JWE3 Variant (.fgm) -----------------------------------
def import_pattern(fgm_path, object_name=None):
    """Load a pattern .fgm and splice it over whatever the target part already shows.

    Patterns and variants are SEPARATE cosmetic axes in game -- either can be applied without the
    other -- so this deliberately does not require a variant to be present. `blender_parts.splice_at`
    inserts by CHAIN_POS rather than at the end of the chain, so applying a pattern before or after
    a variant gives the same node tree (asserted in blender_pattern_nodes.selftest).

    The pattern's per-texel index comes from the species pattern SET, not the pattern itself:
    `<species>_patternset_01.u_basePatternMap` for the body and `u_feathersBasePatternMap` for the
    plumage. Without it the whole mesh reads one LUT entry, which is a flat tint.
    """
    for p in (_here(), _parent_dir()):
        if p not in sys.path:
            sys.path.insert(0, p)
    import blender_parts
    import blender_pattern_nodes as bpn
    import export_pattern
    import part_manifest

    species_dir = os.path.dirname(os.path.abspath(fgm_path))
    stem = os.path.splitext(os.path.basename(fgm_path))[0]
    try:
        _core, part = part_manifest.split_part(stem)
    except ValueError:
        part = "Feathers" if "featherspattern" in stem.lower() else ""

    # A SELECTED mesh wins over name-based discovery. Object names are user-editable and several
    # animals can share a scene, so a naming convention can only ever be the fallback -- "apply it
    # to the thing I have selected" is unambiguous and needs no convention at all.
    import bpy
    selected = [o for o in bpy.context.selected_objects
                if o.type == "MESH" and "joint_physics" not in o.name]
    if selected:
        objs = selected
    else:
        parts = blender_parts.discover_parts(lod=0)
        objs = list(parts.get(part) or [])
        if part == "":
            # fur_shell and fur_fin are the BODY's surface, shelled outwards -- they carry no
            # cosmetic of their own and inherit the body's, exactly as `variant_parts` makes them
            # inherit its grade. Leaving them out meant the shell kept showing an unpatterned body
            # underneath the patterned one, which reads as the pattern "not rendering right".
            objs += list(parts.get("__derived__") or [])
    if not objs:
        parts = blender_parts.discover_parts(lod=0)
        return False, ("%s is a %s pattern, but no matching mesh part is in the scene (found: %s). "
                       "Select the mesh you want it applied to and try again -- a selected mesh is "
                       "used directly, whatever it is named."
                       % (os.path.basename(fgm_path), part or "body",
                          ", ".join(sorted(p or "body" for p in parts
                                           if not p.startswith("__"))) or "none"))
    try:
        data = export_pattern.export(fgm_path)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)

    index_map = _pattern_index_map(species_dir, part)
    patchwork_map = _pattern_patchwork_map(species_dir, part)
    done = []
    for obj in objs:
        if not obj.data.materials or obj.data.materials[0] is None:
            return False, "%s has no material yet -- import a variant first" % obj.name
        mat = obj.data.materials[0]
        tag = (blender_parts.mesh_part_name(obj) or part or "body").lower()
        bpn.apply_pattern(mat, data, index_map=index_map, tag=tag,
                          patchwork_map=patchwork_map)
        mat["jwe3_pattern_fgm"] = os.path.basename(fgm_path)
        mat["jwe3_pattern_path"] = os.path.abspath(fgm_path)   # so Reload can find it again
        done.append("%s (%s)" % (obj.name, mat.name))

    return True, "%s -> %s: %s%s" % (
        os.path.basename(fgm_path), part or "body", ", ".join(done),
        "" if index_map else "  -- NO INDEX MAP FOUND, the pattern will read as a flat tint")


def reload_patterns():
    """Re-import every material's recorded pattern .fgm. Returns (ok, message).

    The LUT is BAKED at import -- a 32x3 image built from the FGM's keys -- so editing the .fgm in
    cobra-tools' FGM editor does not move the preview on its own. This re-reads whatever each
    material already recorded, which is the whole edit loop: save in cobra-tools, hit Reload.

    Re-importing is safe and in-place: `apply_pattern` unsplices the old group first, and
    `lut_image` reuses the image datablock by name, so nothing duplicates.
    """
    import bpy
    done, failed = [], []
    for mat in bpy.data.materials:
        path = mat.get("jwe3_pattern_path")
        if not path:
            continue
        if not os.path.isfile(path):
            failed.append("%s: %s is gone" % (mat.name, os.path.basename(path)))
            continue
        try:
            ok, msg = import_pattern(path)
            (done if ok else failed).append(msg if ok else "%s: %s" % (mat.name, msg))
        except Exception as e:
            failed.append("%s: %s: %s" % (mat.name, type(e).__name__, e))
    if not done and not failed:
        return False, "no pattern has been imported yet -- File > Import > JWE3 Pattern first"
    return not failed, "reloaded %d pattern(s)%s" % (
        len(done), ("; FAILED: " + "; ".join(failed)) if failed else "")


def save_pattern(object_name=None, backup=True):
    """Write the active material's edited ColorRamp back into its pattern .fgm. (ok, message).

    THE AUTHORING LOOP, closed. Until now the ramp was editable but the edits died with the .blend:
    the FGM was read-only and only cobra-tools could change it.

    Deliberately single-material. One pattern is applied to several parts at once (body, fur_shell,
    fur_fin each get their own group with its own ramp), so "save every pattern in the scene" would
    have to pick a winner among ramps that may disagree. The ACTIVE object is an unambiguous answer,
    and the reload afterwards pushes the saved result back out to all of them.

    The original is re-read from disk rather than taken from the group, so untouched keys keep their
    exact raw floats and the emissive keys -- which the ramp does not carry at all -- survive.
    """
    for p in (_here(), _parent_dir()):
        if p not in sys.path:
            sys.path.insert(0, p)
    import bpy
    import blender_pattern_nodes as bpn
    import pattern_io
    import pattern_writeback

    obj = bpy.context.active_object if object_name is None else bpy.data.objects.get(object_name)
    if obj is None:
        return False, "no active object -- select the mesh whose pattern you edited"
    if not obj.data or not getattr(obj.data, "materials", None) or not obj.data.materials[0]:
        return False, "%s has no material" % obj.name
    mat = obj.data.materials[0]

    path = mat.get("jwe3_pattern_path")
    if not path:
        return False, "%s has no imported pattern -- File > Import > JWE3 Pattern first" % mat.name
    if not os.path.isfile(path):
        return False, "%s is gone; cannot save" % path

    stops = bpn.read_ramp(mat)
    if not stops:
        return False, ("%s has no editable ramp. The group only builds one when the pattern has "
                       "keys, and the Source input must be 1 for it to be what renders." % mat.name)

    try:
        original = pattern_io.load_pattern_fgm(path)
        model, report = pattern_writeback.model_from_stops(stops, original)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)

    if backup:
        bak = path + ".bak"
        if not os.path.isfile(bak):        # keep the PRISTINE original, never overwrite it
            shutil.copy2(path, bak)

    try:
        pattern_io.save_pattern_fgm(model, path)
    except Exception as e:
        return False, "save failed: %s: %s" % (type(e).__name__, e)

    # Re-import so the BAKED image path matches the keys just written. Without this the two Source
    # paths disagree until the next manual reload, and flipping Source would look like a bug.
    ok, msg = import_pattern(path)

    warn = ""
    if max(report["colour_error"], report["opacity_error"]) > pattern_writeback.EPS:
        warn = ("  -- LOSSY: too many stops for the FGM's %d colour / %d opacity slots, worst "
                "deviation %.4f" % (12, 8, max(report["colour_error"], report["opacity_error"])))
    return ok, "saved %s (%d colour, %d opacity keys)%s%s" % (
        os.path.basename(path), report["colour_keys"], report["opacity_keys"], warn,
        "" if ok else "; reload after save failed: %s" % msg)


def _pattern_index_map(species_dir, part):
    """`u_basePatternMap` (body) or `u_feathersBasePatternMap` (plumage) PNG, or None."""
    want = "u_feathersbasepatternmap" if part in ("Feathers", "Quills") else "u_basepatternmap"
    for f in sorted(os.listdir(species_dir)):
        low = f.lower()
        if low.endswith(".png") and low.endswith(want + ".png"):
            return os.path.join(species_dir, f)
    return None


def _pattern_patchwork_map(species_dir, part):
    """`u_basePatchworkMap` (body) or `u_feathersBasePatchworkMap` (plumage) PNG, or None.

    100 of the game's patternsets ship one, across 63 species, so it is common -- but many
    still have none, and None is a normal case rather than a fault.
    """
    want = ("u_feathersbasepatchworkmap" if part in ("Feathers", "Quills")
            else "u_basepatchworkmap")
    for f in sorted(os.listdir(species_dir)):
        low = f.lower()
        if low.endswith(want + ".png"):
            return os.path.join(species_dir, f)
    return None


def _part_of_variant(fgm_path):
    """`(part, slot_index)` if this cosmetic belongs to a NON-body mesh part, else `("", None)`.

    The species folder is the .fgm's own folder -- for a loose file the user picked, that is exactly
    where its manifest lives. Never raises: an unreadable manifest just means "treat as body", which
    is the old behaviour.
    """
    try:
        for p in (_here(), _parent_dir(), os.path.join(_here(), "vendor")):
            if p not in sys.path:
                sys.path.insert(0, p)
        import variant_parts
        part, index = variant_parts.part_for_fgm(os.path.dirname(os.path.abspath(fgm_path)),
                                                 fgm_path)
        return (part or ""), index
    except Exception:
        return "", None


def _import_part_variant(fgm_path, model, part, slot_index):
    """Grade a NON-body part (feathers/quills) from its own cosmetic .fgm. Returns (ok, message).

    BUILDS the part's material when it is missing, exactly as the body path does -- an import that
    refuses to build is not an import. What made this awkward before was locating the two inputs
    `build_feathers` needs; both are now derivable:

      * the part's base .fgm  -> `part_manifest.part_base_fgm` (`<species>_feathers.fgm`, preferring
        the species-prefixed file over the bare shared one, which leaves the body-space base diffuse
        as an inline placeholder);
      * the shared card library -> `part_manifest.fur_library`, the `DinosaurFur` folder found by
        walking up from the species folder.

    A material that exists but was not built by `build_feathers` is REBUILT rather than graded:
    cobra-tools' own FGM import leaves a flat stub (`use_nodes = False`), and grading that raised
    a bare "build the material with build_feathers first".
    """
    import variant_parts
    import part_manifest
    import blender_feather_nodes as bfn
    import blender_parts
    import preview_assets
    from preview_bridge import model_to_block

    species_dir = os.path.dirname(os.path.abspath(fgm_path))
    parts = blender_parts.discover_parts(lod=0)
    objs = parts.get(part) or []
    if not objs:
        return False, ("%s is a %s cosmetic, but no %s mesh is in the scene (parts found: %s)"
                       % (os.path.basename(fgm_path), part, part,
                          ", ".join(sorted(p for p in parts if not p.startswith("__"))) or "none"))

    graded, built, unresolved = [], [], []
    for obj in objs:
        mat = obj.data.materials[0] if obj.data.materials else None
        if mat is None or "jwe3_albedo_node" not in mat.keys():
            part_fgm = part_manifest.part_base_fgm(species_dir, part)
            if not part_fgm:
                return False, ("%s needs the part's base .fgm (e.g. <species>_%s.fgm) next to the "
                               "cosmetic in %s, and there is none"
                               % (obj.name, part_manifest.PART_FGM_TOKEN.get(part, part.lower()),
                                  species_dir))
            mat, report = bfn.build_feathers(obj, part_fgm,
                                             part_manifest.fur_library_dirs(species_dir))
            built.append(os.path.basename(part_fgm))
            # BUILD WHAT WE CAN. `build_feathers` already skips a texture it cannot find and keeps
            # going, so an unresolved slot must not throw the rest away -- a material with five of
            # its six maps is far more useful than an error. Report the gap instead, naming the
            # files, so it is obvious which folder is missing rather than silently wrong.
            if report.get("missing"):
                unresolved.extend(d for _slot, d in report["missing"])
        species = preview_assets.species_from_object_name(obj.name) or "Preview"
        block = model_to_block(model, species=species, sex=None,
                               variant=slot_index if slot_index is not None else 0)
        if part in variant_parts.FEATHER_PARTS:
            bfn.apply_feather_grade(mat, block)
        else:
            return False, "no grade path for part %r yet" % part
        mat["jwe3_variant_fgm"] = os.path.basename(fgm_path)
        mat["jwe3_variant_path"] = fgm_path
        mat["jwe3_seed"] = int(block["seed"])
        mat["jwe3_complexity"] = int(block["complexity"])
        mat["jwe3_gradient"] = "exact" if block["coeffExact"] else "approximate"
        if slot_index is not None:
            mat["jwe3_variant_index"] = slot_index
        graded.append("%s (%s)" % (obj.name, mat.name))

    msg = "%s -> %s part: %s -- seed %d/%d, gradient %s%s" % (
        os.path.basename(fgm_path), part, ", ".join(graded),
        int(model.seed), int(model.complexity),
        "exact" if block["coeffExact"] else "APPROXIMATE (seed not harvested)",
        "  [built from %s]" % ", ".join(sorted(set(built))) if built else "")
    if unresolved:
        # Partial build, reported rather than hidden: name the files and where we looked, so the
        # fix (add a folder in the add-on preferences) is obvious.
        miss = sorted(set(unresolved))
        msg += ("  -- %d texture(s) NOT FOUND, built without them: %s. Searched %s"
                % (len(miss), ", ".join(miss[:4]) + (" ..." if len(miss) > 4 else ""),
                   "; ".join([species_dir] + part_manifest.fur_library_dirs(species_dir))))
    return True, msg


def _remirror_derived(block, species_dir):
    """Re-grade fur_shell/fur_fin after the body's layer stack was rebuilt. Returns (done, failed).

    WHY THIS IS NOT OPTIONAL. `fur_shell` and `fur_fin` do not own a layer stack: `mirror_layer_chain`
    fills them with COPIES of the body's per-layer group nodes. Rebuilding the body replaces those
    datablocks, so every `JWE3_Mirror_*` node in the two derived materials is left with
    `node_tree = None`.

    A group node with no node_tree has NO SOCKETS, so the chain through it is severed -- and these
    are the two surfaces that OCCLUDE the body almost completely (see `apply_derived_grade`). The
    animal then renders from raw base diffuse and reads as a colour-model bug. Measured live: an
    `import_variant` of a body FGM left 9 dead mirror groups on each of the two materials.

    `variant_parts.apply_variant_all` has always re-mirrored, and the docstrings say to run it after
    any body rebuild -- but nothing enforced that, and the failure is silent. This closes it at the
    source, so importing a body variant is self-consistent on its own.
    """
    import blender_parts
    import variant_parts

    done, failed = [], []
    for obj in blender_parts.discover_parts().get("__derived__", []):
        if not obj.data.materials or obj.data.materials[0] is None:
            continue
        mat = obj.data.materials[0]
        tag = blender_parts.mesh_part_name(obj) or "derived"
        try:
            variant_parts.apply_derived_grade(mat, block, species_dir, tag, body_mat=_current_mat)
            done.append(tag)
        except ValueError as e:
            # Never fatal: the body import itself succeeded, and a derived part with no material
            # built yet is a normal state, not an error. Report it instead of hiding it.
            failed.append("%s: %s" % (tag, e))
    return done, failed


def import_variant(fgm_path, object_name=None):
    """Load a loose variant .fgm and put it on a mesh, in one call. Returns (ok, message).

    This is the Blender-side equivalent of the whole standalone editor's Open+Build: read the .fgm,
    resolve the species' masks/LayerJSON, build the layer material onto the mesh, and grade it with
    the variant's own colour block. `object_name=None` picks the best-matching imported body mesh.

    NOTE ON COBRA-TOOLS: its own FGM import creates a *stub* material (`use_nodes = False`, flat
    grey) -- it does not reproduce the JWE3 layer stack or the palette grade. So this does not
    extend or hijack that importer; it is a separate path that builds the real thing.
    """
    import bpy
    global _last_import
    for p in (_here(), _parent_dir()):
        if p not in sys.path:
            sys.path.insert(0, p)
    import fgm_io
    import preview_assets
    from preview_bridge import model_to_block

    model = fgm_io.load_fgm(fgm_path)       # raises a clear error on a layer/base FGM
    fgm_species, fgm_sex = fgm_io.species_sex_from_filename(fgm_path)

    # WHICH PART is this cosmetic for? Ask before doing anything, because a feathers variant and a
    # body variant are byte-identical in shape (same DinosaurLayered_Variant shader, 144 attributes,
    # no textures) and everything below assumes the body. Handing this a
    # `<species>_feathersvariant_NN_NN.fgm` used to build a full 16-layer body stack from it,
    # overwrite the body material and assign that onto the feathers mesh.
    part, slot_index = _part_of_variant(fgm_path)
    if part:
        return _import_part_variant(fgm_path, model, part, slot_index)

    # 1. Pick the target mesh. An explicit name wins; then the active selection if it is a JWE mesh
    #    we recognise (which is what makes "select Baryonyx, import a Spino variant" work); then the
    #    best match for the .fgm's own species.
    if not object_name:
        obj = getattr(bpy.context, "active_object", None)
        if obj is not None and obj.type == "MESH" and preview_assets.species_from_object_name(obj.name):
            object_name = obj.name
        else:
            names = [o.name for o in bpy.data.objects
                     if o.type == "MESH" and "joint" not in o.name.lower()]
            object_name = preview_assets.pick_target_object(names, fgm_species, fgm_sex)
    if not object_name:
        return False, ("no %s mesh found -- import the model (.ms2) first, or select the mesh you "
                       "want this variant applied to" % fgm_species)

    # 2. TEXTURES FOLLOW THE MODEL, COLOUR FOLLOWS THE FGM. The masks/LayerJSON belong to the mesh
    #    in front of you (its UVs, its blend weights); using the .fgm's species here would paint
    #    Spinosaurus masks onto a Baryonyx and produce garbage.
    model_species = preview_assets.species_from_object_name(object_name) or fgm_species
    model_sex = preview_assets.sex_from_object_name(object_name) or fgm_sex
    try:
        mask_dir, mask_prefix, layers_json = preview_assets.assets_for(
            model_species, model_sex, fgm_path=fgm_path, fgm_species=fgm_species,
            model_dir=_model_texture_dir(object_name))
    except preview_assets.AssetError as e:
        return False, str(e)

    if _build_on_object(object_name, mask_dir, mask_prefix, layers_json) is None:
        return False, "object %r not found" % object_name
    block = model_to_block(model, model_species or "Preview", model_sex)
    _grade_current(block)

    # Stamp the source .fgm onto the material and the grade node. Without this every import looks
    # identical in the shader editor ("Baryonyx v00 seed 36/2" says nothing about WHICH file), and
    # after a few variants you cannot tell what you are looking at. The custom properties survive
    # saving the .blend, so the material still knows its origin in a later session.
    stem = os.path.basename(fgm_path)
    _current_mat["jwe3_variant_fgm"] = stem
    _current_mat["jwe3_variant_path"] = fgm_path
    _current_mat["jwe3_seed"] = int(model.seed)
    _current_mat["jwe3_complexity"] = int(model.complexity)
    _current_mat["jwe3_gradient"] = "exact" if block["coeffExact"] else "approximate"
    grade_node = _current_mat.node_tree.nodes.get(_current_grade or "")
    if grade_node is not None:
        grade_node.label = "%s   seed %d/%d%s" % (
            stem, model.seed, model.complexity,
            "" if block["coeffExact"] else "   (NO COEFFS - base grade only)")

    # Rebuilding the body above orphaned the mirrored layer chains in fur_shell/fur_fin. Put them
    # back before returning -- see _remirror_derived. Textures come from the same folder the masks
    # did, which is the mesh's own import folder, so this needs nothing the caller has to supply.
    remirrored, remirror_failed = _remirror_derived(block, mask_dir)

    # Publish it so an open editor window can follow this import (it polls "state").
    _last_import = {"path": fgm_path, "object": object_name, "species": model_species,
                    "sex": model_sex, "serial": _last_import["serial"] + 1}

    exact = "exact" if block["coeffExact"] else \
            "APPROXIMATE (seed not harvested: grade exact, gradient flat)"
    cross = "" if (fgm_species or "").lower() == (model_species or "").lower() else \
            "  [%s colours on the %s model]" % (fgm_species, model_species)
    derived = ""
    if remirrored:
        derived = "  [re-mirrored %s]" % ", ".join(remirrored)
    if remirror_failed:
        derived += "  [derived parts NOT regraded: %s]" % "; ".join(remirror_failed)
    return True, "%s seed %d/%d -> %s (%s textures) -- gradient %s%s%s" % (
        os.path.basename(fgm_path), model.seed, model.complexity, object_name,
        model_species, exact, cross, derived)


def _cmd_objects(cmd):
    """Handle {"cmd": "objects", "contains": <optional substring>} -- list mesh objects.

    Lets the editor offer the user's actual imported meshes instead of making them type a name.
    Sorted by polygon count descending, so the body mesh (the one you want) comes first and the
    LOD/prop/physics-joint meshes come after.
    """
    import bpy
    contains = (cmd.get("contains") or "").lower()
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and "joint" not in o.name.lower()]
    if contains:
        meshes = [o for o in meshes if contains in o.name.lower()]
    meshes.sort(key=lambda o: len(o.data.polygons), reverse=True)
    return {"ok": True, "objects": [o.name for o in meshes]}


def _cmd_ping(cmd):
    """Handle {"cmd": "ping"} -- PreviewBridge.connect()'s liveness probe."""
    return {"ok": True}


def _cmd_state(cmd):
    """Handle {"cmd": "state"} -- what File > Import last loaded.

    The editor polls this so that importing a variant in Blender pulls that variant's settings into
    the open editor window. Cheap by design: a dict of plain values, no bpy access at all.
    """
    return {"ok": True, "last_import": dict(_last_import)}


def _cmd_selected(cmd):
    """Handle {"cmd": "selected"} -- describe the mesh currently selected in Blender.

    Lets the editor start from what is already in the viewport instead of requiring the user to
    find the .fgm on disk again. Materials record where they came from (`jwe3_variant_path`), so
    the editor can simply open that file.

    Reports the object even when it carries no recorded variant: adopting the object name alone is
    still useful, because a later Build then targets the right mesh.
    """
    import bpy
    sel = [o for o in bpy.context.selected_objects
           if o.type == "MESH" and "joint_physics" not in o.name]
    if not sel:
        return {"ok": False, "error": "nothing selected in Blender -- click the model first"}
    obj = bpy.context.active_object
    if obj not in sel:
        obj = sel[0]
    mat = None
    for slot in obj.material_slots:
        if slot.material is not None:
            mat = slot.material
            break
    # Patterns are reported alongside the variant because they are a SEPARATE cosmetic axis: a
    # material can carry a pattern with no variant, or the reverse. `import_pattern` stamps
    # jwe3_pattern_path/_fgm exactly so "what is on this mesh" can be answered later.
    info = {"object": obj.name, "material": mat.name if mat else None,
            "variant_path": None, "variant_fgm": None, "seed": None, "variant_index": None,
            "pattern_path": None, "pattern_fgm": None}
    if mat is not None:
        for key, prop in (("variant_path", "jwe3_variant_path"), ("variant_fgm", "jwe3_variant_fgm"),
                          ("seed", "jwe3_seed"), ("variant_index", "jwe3_variant_index"),
                          ("pattern_path", "jwe3_pattern_path"), ("pattern_fgm", "jwe3_pattern_fgm")):
            v = mat.get(prop)
            if v is not None:
                info[key] = v
    return {"ok": True, "selected": info}


def _cmd_pattern(cmd):
    """Handle {"cmd": "pattern", "data": {...}} -- apply a pattern from data, with NO file on disk.

    This is the LIVE-PREVIEW path and it deliberately does not take a path. `import_pattern` reads
    a .fgm, which is right for File > Import but useless while someone is dragging a key in the
    editor: the edit they are looking at has not been saved and may never be. `data` is exactly
    what `export_pattern.export()` produces -- {source, model, lut, interp, threshold} -- so the
    editor can build it from its in-memory model and the node builder is none the wiser.

    Must run on Blender's main thread; the queue drain guarantees that.
    """
    data = cmd.get("data")
    if not isinstance(data, dict) or "lut" not in data:
        return {"ok": False, "error": "pattern: 'data' must be an export_pattern-style dict"}

    for p in (_here(), _parent_dir()):
        if p not in sys.path:
            sys.path.insert(0, p)
    import bpy
    import blender_parts
    import blender_pattern_nodes as bpn

    part = cmd.get("part") or ""
    name = cmd.get("object")
    # Same precedence as import_pattern: an explicit object, else the SELECTION, else discovery.
    # Object names are user-editable, so a naming convention can only ever be the fallback.
    if name:
        obj, candidates = _resolve_object(name)
        objs = [obj] if obj is not None else []
        if not objs:
            suffix = (" (candidates: %s)" % ", ".join(candidates)) if candidates else ""
            return {"ok": False, "error": "no mesh object named %r%s" % (name, suffix)}
    else:
        objs = [o for o in bpy.context.selected_objects
                if o.type == "MESH" and "joint_physics" not in o.name]
        if not objs:
            parts = blender_parts.discover_parts(lod=0)
            objs = list(parts.get(part) or [])
            if part == "":
                objs += list(parts.get("__derived__") or [])
    if not objs:
        return {"ok": False, "error": "no target mesh: select one, or pass 'object'"}

    index_map = cmd.get("index_map") or None
    if index_map and not os.path.isfile(index_map):
        index_map = None
    patchwork_map = cmd.get("patchwork_map") or None
    if patchwork_map and not os.path.isfile(patchwork_map):
        patchwork_map = None
    done = []
    for obj in objs:
        if not obj.data.materials or obj.data.materials[0] is None:
            return {"ok": False,
                    "error": "%s has no material yet -- import a variant first" % obj.name}
        mat = obj.data.materials[0]
        tag = (blender_parts.mesh_part_name(obj) or part or "body").lower()
        bpn.apply_pattern(mat, data, index_map=index_map, tag=tag,
                          patchwork_map=patchwork_map)
        done.append("%s (%s)" % (obj.name, mat.name))
    return {"ok": True, "applied": done,
            "index_map": os.path.basename(index_map) if index_map else None,
            "note": None if index_map else "no index map -- the pattern reads as a flat tint"}


_HANDLERS = {
    "build": _cmd_build,
    "grade": _cmd_grade,
    "objects": _cmd_objects,
    "ping": _cmd_ping,
    "state": _cmd_state,
    "selected": _cmd_selected,
    "pattern": _cmd_pattern,
}


def _execute_command(cmd):
    """Dispatch one decoded command dict; never raises -- always returns an {"ok": ...} dict."""
    try:
        action = cmd.get("cmd")
        handler = _HANDLERS.get(action)
        if handler is None:
            return {"ok": False, "error": f"unknown cmd {action!r}"}
        return handler(cmd)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _drain_queue():
    """bpy.app.timers callback: runs on the main thread, executes queued commands, replies.

    Returns DRAIN_INTERVAL so Blender re-fires this timer; unregister() cancels it by returning
    None once (see unregister below) or by bpy.app.timers.unregister.
    """
    while True:
        try:
            conn, cmd = _command_queue.get_nowait()
        except queue.Empty:
            break
        reply = _execute_command(cmd)
        try:
            _send_message(conn, reply)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return DRAIN_INTERVAL


_ui_classes = []      # operator classes registered with Blender, torn down by unregister()


def _menu_func(self, context):
    self.layout.operator("jwe3.import_variant", text="JWE3 Variant (.fgm)")
    self.layout.operator("jwe3.import_pattern", text="JWE3 Pattern (.fgm)")
    self.layout.operator("jwe3.reload_patterns", text="JWE3 Patterns — Reload from disk")


def _export_menu_func(self, context):
    self.layout.operator("jwe3.save_pattern", text="JWE3 Pattern → .fgm (active material)")


def _strip_menu(menu, fn):
    """Strip EVERY copy of `fn` from `menu`. `remove` takes one at a time, and a stale duplicate
    from an earlier registration would otherwise survive and keep showing a second menu item."""
    for _ in range(8):                       # bounded: nothing legitimately appends this 8 times
        try:
            menu.remove(fn)
        except Exception:
            break
    # a reloaded module leaves behind a DIFFERENT function object with the same name, which
    # `remove` cannot match -- find those by name and drop them too
    for other in list(getattr(menu, "_dyn_ui_initialize", lambda: [])()):
        if getattr(other, "__name__", "") == fn.__name__ and other is not fn:
            try:
                menu.remove(other)
            except Exception:
                pass


def _remove_menu_func():
    import bpy
    _strip_menu(bpy.types.TOPBAR_MT_file_import, _menu_func)


def _remove_export_menu_func():
    import bpy
    _strip_menu(bpy.types.TOPBAR_MT_file_export, _export_menu_func)


def _register_ui():
    """Register the add-on preferences and the File > Import entry.

    The classes are defined HERE, inside a function, not at module scope: subclassing
    `bpy.types.Operator` needs bpy, and this module must stay importable by plain `python` outside
    Blender (same rule as the lazy bpy imports elsewhere in this file).
    """
    import bpy

    class JWE3VariantPrefs(bpy.types.AddonPreferences):
        bl_idname = ADDON_ID or __name__

        def _save(self, key, value):
            """Mirror a picked folder into the shared config, so the desktop editor and the
            harvesting tools use the same one rather than each keeping its own idea."""
            try:
                sys.path.insert(0, _here())
                import jwe3_config
                jwe3_config.write(**{key: value or None})
            except Exception as e:
                print("JWE3 Variant Tools: could not save %s:" % key, e)

        def _write_swatch(self, _context):
            self._save("swatch_dir", bpy.path.abspath(self.swatch_dir) or None)

        def _write_fur(self, _context):
            """Both fields feed the ONE `fur_library` setting, which is os.pathsep-separated.

            The picker can only choose a single folder, so it writes the first entry and the free
            text field supplies any others. Joining here rather than adding a second config key
            keeps one list for every consumer (`jwe3_config.get_dirs("fur_library")`) -- the desktop
            editor and the harvesting tools read the same setting.

            The text field must NOT go through `bpy.path.abspath`: it is a LIST, and that would
            mangle the separators into one nonsense path.
            """
            first = bpy.path.abspath(self.fur_library) if self.fur_library else ""
            rest = (self.extra_dirs or "").strip()
            joined = os.pathsep.join([p for p in (first.strip(), rest) if p])
            self._save("fur_library", joined or None)

        _write_extra = _write_fur

        def _write_layerjson(self, _context):
            """Point the add-on at the folder LayerJSONs are generated into.

            Writes to the SHARED config, like every other field here, so the desktop editor writes
            new species where Blender will look for them. A Blender-only preference would put the
            two back out of step, which is the exact failure this setting exists to end.
            """
            self._save("layerjson_dir", bpy.path.abspath(self.layerjson_dir) or None)
            # The species index is cached; a repointed folder must take effect without a restart.
            try:
                import preview_assets
                preview_assets._LJ_CACHE = None
            except Exception:
                pass

        layerjson_dir: bpy.props.StringProperty(
            name="LayerJSON folder",
            description="Where generated LayerJSONs live. Searched BEFORE the copy shipped inside "
                        "the add-on, so a species you generate overrides one we ship. Leave blank "
                        "for the default beside the config",
            subtype="DIR_PATH",
            default="",
            update=_write_layerjson)

        swatch_dir: bpy.props.StringProperty(
            name="Swatch Library folder",
            description="Where you unpacked SwatchLibrary.ovl's PNGs. Game data, so it is never "
                        "shipped with the add-on. Leave blank to auto-detect",
            subtype="DIR_PATH",
            default="",
            update=_write_swatch)

        fur_library: bpy.props.StringProperty(
            name="DinosaurFur folder",
            description="The shared feather/fur card textures (feathers.pfeathers_*). Normally "
                        "found automatically by looking for a DinosaurFur folder above the "
                        "species you are importing -- set this only if yours lives elsewhere",
            subtype="DIR_PATH",
            default="",
            update=_write_fur)

        extra_dirs: bpy.props.StringProperty(
            name="Extra texture folders",
            description="Additional folders to search for shared textures, separated by "
                        "'%s'. Searched after the species folder and the DinosaurFur folder, in "
                        "the order given" % os.pathsep,
            default="",
            update=_write_extra)

        def draw(self, context):
            col = self.layout.column()
            if _assets_reachable():
                col.label(text="Add-on files: OK", icon="CHECKMARK")
            else:
                col.label(text="Incomplete install — install the whole folder/zip, not a single "
                               ".py file", icon="ERROR")
            col.prop(self, "layerjson_dir")
            col.prop(self, "swatch_dir")
            col.prop(self, "fur_library")
            col.prop(self, "extra_dirs")
            # Which species the add-on can actually SEE. A species missing here builds from another
            # animal's layer stack without erroring, so it belongs on screen, not in a log.
            try:
                import preview_assets
                species = preview_assets.previewable_species()
                col.label(text="Species with a LayerJSON: %d" % len(species),
                          icon="CHECKMARK" if species else "ERROR")
                col.label(text="        " + (", ".join(species) if species else "none found"))
            except Exception as e:
                col.label(text="LayerJSON scan failed: %s" % e, icon="ERROR")
            try:
                sys.path.insert(0, _here())
                import jwe3_config
                for key in jwe3_config.KEYS:
                    src = jwe3_config.source(key)
                    # a MULTI setting can name several folders; show them all, one per line, or the
                    # single value collapses to "the first one" and a second folder looks ignored
                    if key in jwe3_config.MULTI:
                        dirs = jwe3_config.get_dirs(key)
                        col.label(text="%s: %d folder(s)  [%s]" % (key, len(dirs), src),
                                  icon="CHECKMARK" if dirs else "ERROR")
                        for d in dirs:
                            col.label(text="        " + d)
                        continue
                    value = jwe3_config.get(key)
                    col.label(text="%s: %s  [%s]" % (key, value or "not found", src),
                              icon="CHECKMARK" if value else "ERROR")
                games = jwe3_config.detect_game_dirs()
                if len(games) > 1:
                    col.label(text="%d game installs found — the most recently updated is used; "
                                   "run setup_gui.py to choose" % len(games), icon="INFO")
            except Exception as e:
                col.label(text="config unavailable: %s" % e, icon="ERROR")

    # Drop any JWE3VariantPrefs left over from an earlier registration before adding this one.
    # Re-running register() without a matching unregister -- a reload during development, or an
    # enable while a previous registration lingers -- leaves the OLD class registered, and Blender
    # resolves preferences by bl_idname against whatever it finds. That is how the panel ended up
    # drawing nothing: three stale classes all carrying the pre-fix bl_idname ("blender_listener")
    # shadowed the correct one, and `addons["VariantEditor"].preferences` stayed None.
    for _old in [c for c in bpy.types.AddonPreferences.__subclasses__()
                 if c.__name__ == "JWE3VariantPrefs" and c is not JWE3VariantPrefs]:
        try:
            bpy.utils.unregister_class(_old)
        except Exception:
            pass        # already dead: a stale class object with no bl_rna, nothing to remove

    bpy.utils.register_class(JWE3VariantPrefs)
    _ui_classes.append(JWE3VariantPrefs)

    class JWE3_OT_import_variant(bpy.types.Operator):
        """Load a JWE3 variant .fgm and build its material onto the imported dinosaur mesh"""
        bl_idname = "jwe3.import_variant"
        bl_label = "Import JWE3 Variant"
        bl_options = {"REGISTER", "UNDO"}

        filepath: bpy.props.StringProperty(subtype="FILE_PATH")
        filter_glob: bpy.props.StringProperty(default="*.fgm", options={"HIDDEN"})

        def invoke(self, context, event):
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            try:
                ok, msg = import_variant(self.filepath)
            except Exception as e:
                self.report({"ERROR"}, "%s: %s" % (type(e).__name__, e))
                return {"CANCELLED"}
            self.report({"INFO"} if ok else {"ERROR"}, msg)
            return {"FINISHED"} if ok else {"CANCELLED"}

    bpy.utils.register_class(JWE3_OT_import_variant)
    _ui_classes.append(JWE3_OT_import_variant)

    class JWE3_OT_import_pattern(bpy.types.Operator):
        """Load a JWE3 pattern .fgm and splice it over the target part's material"""
        bl_idname = "jwe3.import_pattern"
        bl_label = "Import JWE3 Pattern"
        bl_options = {"REGISTER", "UNDO"}

        filepath: bpy.props.StringProperty(subtype="FILE_PATH")
        filter_glob: bpy.props.StringProperty(default="*.fgm", options={"HIDDEN"})

        def invoke(self, context, event):
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}

        def execute(self, context):
            try:
                ok, msg = import_pattern(self.filepath)
            except Exception as e:
                self.report({"ERROR"}, "%s: %s" % (type(e).__name__, e))
                return {"CANCELLED"}
            self.report({"INFO"} if ok else {"ERROR"}, msg)
            return {"FINISHED"} if ok else {"CANCELLED"}

    bpy.utils.register_class(JWE3_OT_import_pattern)
    _ui_classes.append(JWE3_OT_import_pattern)

    class JWE3_OT_reload_patterns(bpy.types.Operator):
        """Re-read every imported pattern .fgm from disk -- use after editing one in cobra-tools"""
        bl_idname = "jwe3.reload_patterns"
        bl_label = "Reload JWE3 Patterns"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            try:
                ok, msg = reload_patterns()
            except Exception as e:
                self.report({"ERROR"}, "%s: %s" % (type(e).__name__, e))
                return {"CANCELLED"}
            self.report({"INFO"} if ok else {"ERROR"}, msg)
            return {"FINISHED"} if ok else {"CANCELLED"}

    bpy.utils.register_class(JWE3_OT_reload_patterns)
    _ui_classes.append(JWE3_OT_reload_patterns)

    class JWE3_OT_save_pattern(bpy.types.Operator):
        """Write the active material's edited pattern ramp back into its .fgm (keeps a .bak)"""
        bl_idname = "jwe3.save_pattern"
        bl_label = "Save JWE3 Pattern to .fgm"
        bl_options = {"REGISTER"}      # NOT UNDO: this writes a file, which undo cannot reverse

        def execute(self, context):
            try:
                ok, msg = save_pattern()
            except Exception as e:
                self.report({"ERROR"}, "%s: %s" % (type(e).__name__, e))
                return {"CANCELLED"}
            self.report({"INFO"} if ok else {"ERROR"}, msg)
            return {"FINISHED"} if ok else {"CANCELLED"}

    bpy.utils.register_class(JWE3_OT_save_pattern)
    _ui_classes.append(JWE3_OT_save_pattern)

    # ---------------------------------------------------------------- variant panel
    #
    # `variant_parts.apply_variant_all` grades EVERY part (body, fur_shell, fur_fin, feathers) from
    # each part's own variant FGM. It was previously only reachable by typing a call with a
    # hard-coded species path into the Python console, which is not something a second user can do.
    #
    # A sidebar panel rather than a File > Import entry, because the common action is flipping
    # BETWEEN variants to compare them -- a one-shot file dialog makes you re-pick the folder every
    # time. The folder is remembered on the Scene, so it survives save/reload.

    def _variant_items(self, context):
        """Scan the species folder for `*_variant_01_NN.fgm` and offer those indices.

        Falls back to 0-11 when the folder is unset or unreadable: an empty enum makes the whole
        panel undraggable and looks like a broken add-on, which is worse than offering too many.
        """
        import glob
        d = (context.scene.jwe3_species_dir or "").strip()
        found = []
        if d and os.path.isdir(bpy.path.abspath(d)):
            for p in glob.glob(os.path.join(bpy.path.abspath(d), "*_variant_??_??.fgm")):
                stem = os.path.splitext(os.path.basename(p))[0]
                tail = stem.rsplit("_", 1)[-1]
                if tail.isdigit():
                    found.append(int(tail))
        found = sorted(set(found)) or list(range(12))
        return [(str(i), "%02d" % i, "Cosmetic slot %d" % i) for i in found]

    bpy.types.Scene.jwe3_species_dir = bpy.props.StringProperty(
        name="Species", subtype="DIR_PATH",
        description="Folder holding the variant FGMs and the .dinosaurmaterialvariants manifest. "
                    "Use the copy WITH the manifest -- a pristine extraction has none, and the "
                    "part each variant belongs to cannot be resolved without it")
    bpy.types.Scene.jwe3_variant_index = bpy.props.EnumProperty(
        name="Variant", items=_variant_items, description="Cosmetic slot to apply")
    bpy.types.Scene.jwe3_seed_override = bpy.props.StringProperty(
        name="Seed", default="",
        description="Optional. Substitute this seed for the FGM's own. An unharvested seed has no "
                    "coefficients and grades FLAT, which looks identical to ungraded -- "
                    "substituting a harvested one makes the palette visible. NOT what the game "
                    "renders; both seeds are recorded on the material")
    bpy.types.Scene.jwe3_last_report = bpy.props.StringProperty(name="Last result", default="")

    class JWE3_OT_apply_variant_all(bpy.types.Operator):
        """Grade every part (body, fur shell, fur fin, feathers) from its own variant FGM"""
        bl_idname = "jwe3.apply_variant_all"
        bl_label = "Apply to All Parts"
        bl_options = {"REGISTER", "UNDO"}

        @classmethod
        def poll(cls, context):
            return bool((context.scene.jwe3_species_dir or "").strip())

        def execute(self, context):
            sc = context.scene
            d = bpy.path.abspath(sc.jwe3_species_dir).rstrip("\\/")
            if not os.path.isdir(d):
                self.report({"ERROR"}, "Not a folder: %s" % d)
                return {"CANCELLED"}
            seed = None
            raw = (sc.jwe3_seed_override or "").strip()
            if raw:
                if not raw.isdigit():
                    self.report({"ERROR"}, "Seed override must be a whole number, got %r" % raw)
                    return {"CANCELLED"}
                seed = int(raw)
            try:
                import variant_parts
                rep = variant_parts.apply_variant_all(d, int(sc.jwe3_variant_index),
                                                      seed_override=seed)
            except Exception as e:
                sc.jwe3_last_report = "%s: %s" % (type(e).__name__, e)
                self.report({"ERROR"}, sc.jwe3_last_report)
                return {"CANCELLED"}
            graded = rep.get("graded") or []
            skipped = rep.get("skipped") or []
            sc.jwe3_last_report = "%d graded, %d skipped" % (len(graded), len(skipped))
            # A skipped part is NOT cosmetic: fur_shell and fur_fin OCCLUDE the body almost
            # completely, so an ungraded one silently shows the raw base texture and every colour
            # judgement made against it is wrong. Say so loudly rather than reporting success.
            if skipped:
                self.report({"WARNING"}, "%s -- skipped: %s" % (sc.jwe3_last_report, skipped))
            else:
                self.report({"INFO"}, sc.jwe3_last_report)
            return {"FINISHED"}

    bpy.utils.register_class(JWE3_OT_apply_variant_all)
    _ui_classes.append(JWE3_OT_apply_variant_all)

    class JWE3_PT_variants(bpy.types.Panel):
        bl_label = "JWE3 Variants"
        bl_idname = "VIEW3D_PT_jwe3_variants"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "JWE3"

        def draw(self, context):
            sc = context.scene
            col = self.layout.column(align=True)
            col.prop(sc, "jwe3_species_dir")
            row = col.row(align=True)
            row.prop(sc, "jwe3_variant_index")
            row.prop(sc, "jwe3_seed_override")
            col.separator()
            col.operator("jwe3.apply_variant_all", icon="MATERIAL")
            if sc.jwe3_last_report:
                col.separator()
                col.label(text=sc.jwe3_last_report, icon="INFO")

    bpy.utils.register_class(JWE3_PT_variants)
    _ui_classes.append(JWE3_PT_variants)

    _remove_export_menu_func()
    bpy.types.TOPBAR_MT_file_export.append(_export_menu_func)

    # Append ONCE. `append` does not deduplicate, so registering twice -- a reload during
    # development, or an enable while a previous registration lingers -- puts two identical
    # "JWE3 Variant (.fgm)" entries in the File > Import menu. Drop any existing copy first.
    _remove_menu_func()
    bpy.types.TOPBAR_MT_file_import.append(_menu_func)


def _unregister_ui():
    import bpy
    _remove_menu_func()
    _remove_export_menu_func()
    # Scene properties are NOT owned by a class, so unregistering the panel leaves them behind.
    # A stale EnumProperty whose items callback has been unloaded throws on every redraw of the
    # Properties editor, which looks like Blender itself breaking.
    for _prop in ("jwe3_species_dir", "jwe3_variant_index", "jwe3_seed_override",
                  "jwe3_last_report"):
        try:
            delattr(bpy.types.Scene, _prop)
        except Exception:
            pass
    while _ui_classes:
        try:
            bpy.utils.unregister_class(_ui_classes.pop())
        except Exception:
            pass


def register():
    global _server_thread
    _stop_event.clear()
    _server_thread = threading.Thread(target=_server_loop, name="jwe3-variant-listener", daemon=True)
    _server_thread.start()

    import bpy
    # persistent=True is REQUIRED. Blender unregisters non-persistent timers whenever a file is
    # loaded -- including File > New. The socket thread survives (it is a plain daemon thread and
    # knows nothing about scenes), so it carries on accepting connections and queueing commands
    # that nothing ever drains: the editor then hangs on connect with no error anywhere. Symptom
    # is "the bridge stops working after File > New", cause is one missing keyword.
    if not bpy.app.timers.is_registered(_drain_queue):
        bpy.app.timers.register(_drain_queue, first_interval=DRAIN_INTERVAL, persistent=True)

    # Cached datablocks do NOT survive a file load, and a freed one is not None -- it is a dead
    # StructRNA that raises ReferenceError on first touch, sailing straight past `is None` guards.
    # Clear them when a new file is loaded. @persistent or the handler removes itself on the very
    # first load, which is the one that matters.
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)

    _register_ui()      # preferences + File > Import > JWE3 Variant (.fgm)

    print(f"JWE3 Variant Tools: listening on {HOST}:{PORT}")
    print("JWE3 Variant Tools: File > Import > JWE3 Variant (.fgm)")
    if not _assets_reachable():
        print("JWE3 Variant Tools: WARNING -- INCOMPLETE INSTALL.\n"
              "  Cannot see preview_assets.py in %r or vendor\\blender_layer_nodes.py in %r.\n"
              "  This add-on is a FOLDER, not a single file: remove this install and install the "
              "zip built by build_addon.py." % (_here(), _parent_dir()))


def unregister():
    global _server_sock, _server_thread, _current_mat, _current_grade

    import bpy
    _unregister_ui()
    if bpy.app.timers.is_registered(_drain_queue):
        bpy.app.timers.unregister(_drain_queue)

    try:
        if _on_load_post in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(_on_load_post)
    except Exception:
        pass

    _stop_event.set()
    if _server_sock is not None:
        try:
            _server_sock.close()
        except Exception:
            pass
        _server_sock = None

    if _server_thread is not None:
        _server_thread.join(timeout=2.0)
        _server_thread = None

    _current_mat = None
    _current_grade = None
    print("JWE3 Variant Preview Listener: stopped")


if __name__ == "__main__":
    # Headless sanity check ONLY -- confirms the module imports cleanly outside Blender (no bpy
    # at module scope) and that the stdlib pieces the framing depends on are present. Does not
    # start the server or touch bpy: run `python blender_listener.py`.
    import json as _json          # noqa: F401  (re-import here is deliberate: proves these are
    import socket as _socket      # noqa: F401  importable standalone, matching the lazy-bpy design)
    import struct as _struct      # noqa: F401
    import threading as _threading  # noqa: F401
    import queue as _queue        # noqa: F401
    print("listener module loads ok")
