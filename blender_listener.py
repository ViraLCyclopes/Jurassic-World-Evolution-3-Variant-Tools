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
_current_mat = None  # the last material built by a "build" command; "grade" re-grades this
_current_grade = None  # NAME of the grade node the last "grade" spliced in, removed by the next
                       # one (see _unsplice_grade). A name, not a reference: node references go
                       # stale across undo/file-reload, names survive.
# What File > Import last loaded, so the standalone editor can follow along (it polls "state").
# `serial` increments on every import, which is how the editor spots a RE-import of the same file.
_last_import = {"path": None, "object": None, "species": None, "sex": None, "serial": 0}


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


def _build_on_object(object_name, mask_dir, mask_prefix, layers_json):
    """Build the layer material and assign it to `object_name`. Returns the material, or None if
    that object does not exist. Shared by the socket `build` command and the menu importer."""
    import bpy
    sys.path.insert(0, _parent_dir())
    from blender_layer_nodes import build_from_json

    global _current_mat, _current_grade
    obj, _candidates = _resolve_object(object_name)
    if obj is None:
        return None

    mat = build_from_json(layers_json, mask_dir, mask_prefix)

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
    mat = _build_on_object(cmd["object"], cmd["mask_dir"], cmd["mask_prefix"], cmd["layers_json"])
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

    # Publish it so an open editor window can follow this import (it polls "state").
    _last_import = {"path": fgm_path, "object": object_name, "species": model_species,
                    "sex": model_sex, "serial": _last_import["serial"] + 1}

    exact = "exact" if block["coeffExact"] else \
            "APPROXIMATE (seed not harvested: grade exact, gradient flat)"
    cross = "" if (fgm_species or "").lower() == (model_species or "").lower() else \
            "  [%s colours on the %s model]" % (fgm_species, model_species)
    return True, "%s seed %d/%d -> %s (%s textures) -- gradient %s%s" % (
        os.path.basename(fgm_path), model.seed, model.complexity, object_name,
        model_species, exact, cross)


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


_HANDLERS = {
    "build": _cmd_build,
    "grade": _cmd_grade,
    "objects": _cmd_objects,
    "ping": _cmd_ping,
    "state": _cmd_state,
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


def _remove_menu_func():
    """Strip EVERY copy of our menu entry. `remove` takes one at a time, and a stale duplicate from
    an earlier registration would otherwise survive and keep showing a second menu item."""
    import bpy
    menu = bpy.types.TOPBAR_MT_file_import
    for _ in range(8):                       # bounded: nothing legitimately appends this 8 times
        try:
            menu.remove(_menu_func)
        except Exception:
            break
    # a reloaded module leaves behind a DIFFERENT function object with the same name, which
    # `remove` cannot match -- find those by name and drop them too
    for fn in list(getattr(menu, "_dyn_ui_initialize", lambda: [])()):
        if getattr(fn, "__name__", "") == "_menu_func" and fn is not _menu_func:
            try:
                menu.remove(fn)
            except Exception:
                pass


def _register_ui():
    """Register the add-on preferences and the File > Import entry.

    The classes are defined HERE, inside a function, not at module scope: subclassing
    `bpy.types.Operator` needs bpy, and this module must stay importable by plain `python` outside
    Blender (same rule as the lazy bpy imports elsewhere in this file).
    """
    import bpy

    class JWE3VariantPrefs(bpy.types.AddonPreferences):
        bl_idname = __name__

        def _write_swatch(self, _context):
            """Mirror the picked folder into the shared config, so the desktop editor and the
            harvesting tools use the same one rather than each keeping its own idea."""
            try:
                sys.path.insert(0, _here())
                import jwe3_config
                jwe3_config.write(swatch_dir=bpy.path.abspath(self.swatch_dir) or None)
            except Exception as e:
                print("JWE3 Variant Tools: could not save swatch_dir:", e)

        swatch_dir: bpy.props.StringProperty(
            name="Swatch Library folder",
            description="Where you unpacked SwatchLibrary.ovl's PNGs. Game data, so it is never "
                        "shipped with the add-on. Leave blank to auto-detect",
            subtype="DIR_PATH",
            default="",
            update=_write_swatch)

        def draw(self, context):
            col = self.layout.column()
            if _assets_reachable():
                col.label(text="Add-on files: OK", icon="CHECKMARK")
            else:
                col.label(text="Incomplete install — install the whole folder/zip, not a single "
                               ".py file", icon="ERROR")
            col.prop(self, "swatch_dir")
            try:
                sys.path.insert(0, _here())
                import jwe3_config
                for key in jwe3_config.KEYS:
                    value, src = jwe3_config.get(key), jwe3_config.source(key)
                    col.label(text="%s: %s  [%s]" % (key, value or "not found", src),
                              icon="CHECKMARK" if value else "ERROR")
                games = jwe3_config.detect_game_dirs()
                if len(games) > 1:
                    col.label(text="%d game installs found — the most recently updated is used; "
                                   "run setup_gui.py to choose" % len(games), icon="INFO")
            except Exception as e:
                col.label(text="config unavailable: %s" % e, icon="ERROR")

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

    # Append ONCE. `append` does not deduplicate, so registering twice -- a reload during
    # development, or an enable while a previous registration lingers -- puts two identical
    # "JWE3 Variant (.fgm)" entries in the File > Import menu. Drop any existing copy first.
    _remove_menu_func()
    bpy.types.TOPBAR_MT_file_import.append(_menu_func)


def _unregister_ui():
    import bpy
    _remove_menu_func()
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
    if not bpy.app.timers.is_registered(_drain_queue):
        bpy.app.timers.register(_drain_queue, first_interval=DRAIN_INTERVAL)

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
