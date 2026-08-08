"""Task 5: the editor-side preview bridge.

Two responsibilities:

1. `model_to_block(model)` -- turn a VariantModel (the user's edited values) into the palette
   *block* dict that `blender_palette_nodes.apply_to` consumes. This is a pure function and is what
   the selftest covers. It mirrors the keys `export_palette.block` produces, but sourced from the
   model instead of a shipped variant. The gradient coefficients come from the harvested table via
   `export_palette.coefficients_for(seed, complexity)`; if the seed is not harvested the block is
   flagged not-exact and gets a flat gradient (amplitude 0), so the preview shows the grade with a
   neutral gradient rather than inventing one. In-game colour is always correct regardless.

2. `PreviewBridge` -- a socket client to the Blender listener add-on (Task 4) on 127.0.0.1:8990,
   using the same 4-byte big-endian length prefix + UTF-8 JSON framing, one request per connection.
   `build_material` assigns the layer material onto the user's already-imported JWE mesh object;
   `push` re-grades it. Neither the socket calls nor Blender are exercised by the selftest (that is
   the Task 7 manual smoke test) -- only `model_to_block` is unit-tested here.

Run:  python preview_bridge.py   -> selftest ok
"""
import json
import os
import socket
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.join(HERE, "vendor")                # vendored research modules, inside the package
for _p in (HERE, PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import coeff_store as ep            # noqa: E402  the LAYERED table (bundled + the user's own
                                    # captures), so a fresh harvest shows up without a restart.
                                    # Same coefficients_for contract as export_palette.
import material_block as mb          # noqa: E402  (hue_matrix_from_rotation)
from variant_model import VariantModel  # noqa: E402

HOST = "127.0.0.1"
PORT = 8990                          # must match blender_listener.py
FLAT_GRAD = dict(gradOffset=[255, 255, 255], gradAmplitude=[0, 0, 0],
                 gradFreq=[0, 0, 0], gradPhase=[0, 0, 0])


def model_to_block(model, species="Preview", sex=None, variant=0):
    """Assemble the palette block `blender_palette_nodes.apply_to` expects, from a VariantModel.

    Same key set as `export_palette.block`. The gradient is taken from `coefficients_for`; a
    non-harvested seed yields a flat gradient and coeffExact False.

    `keyType` is forced True. `u_globalKeyType` is a real FGM attribute and it VARIES, but it does
    not predict the render -- see the comment on the field below.
    """
    row, exact = ep.coefficients_for(int(model.seed), int(model.complexity))
    grad = (dict(gradOffset=row["gradOffset"], gradAmplitude=row["gradAmplitude"],
                 gradFreq=row["gradFreq"], gradPhase=row["gradPhase"])
            if row is not None else dict(FLAT_GRAD))
    return {
        "species": species, "sex": sex, "variant": variant,
        "seed": int(model.seed), "complexity": int(model.complexity),
        "keyColour": [float(c) for c in model.keyColour],
        "keyThreshold": float(model.keyThreshold),
        "keyTolerance": float(model.keyTolerance),
        # INVERTED from the FGM attribute: the GPU bit is the COMPLEMENT of `u_globalKeyType`.
        #
        # Measured 2026-08-01 against the game's own GBuffer albedo, captured unlit with the
        # ReShade add-on (see the JWE3_ReShadeAddon project) and decoded from sRGB. For each
        # variant the bare skin's measured albedo was compared against both sides of the grade,
        # computed from a measured pre-grade albedo of (0.2805, 0.1584, 0.0566):
        #
        #   variant  FGM  measured skin           base-side pred          palette-side pred
        #   v00      1.0  (0.229, 0.115, 0.049)   (0.732, 0.428, 0.193)   (0.212, 0.131, 0.063) <-
        #   v02      1.0  (0.051, 0.050, 0.042)   (0.697, 0.641, 0.594)   (0.103, 0.091, 0.080) <-
        #   v09      0.0  (0.302, 0.107, 0.051)   (0.301, 0.152, 0.028) <-(1.07,  0.38,  0.0)
        #
        # v09's red predicts 0.301 against a measured 0.302. v02 and v09 carry OPPOSITE FGM values
        # and need OPPOSITE bits, which is exactly the case that discriminates -- so the mapping is
        # the complement, not a constant.
        #
        # This REPLACES a hardcoded True justified by "v02 and v09 both need the bit SET". That was
        # judged by eye against LIT screenshots, on an apparatus that was itself broken at the time
        # (ungraded fur_shell/fur_fin, near-black feathers). Do not restore it without measuring
        # against a GBuffer capture.
        "keyType": not bool(model.keyType),
        "brightnessBase": float(model.brightnessBase),
        "brightnessPalette": float(model.brightnessPalette),
        "saturationBase": float(model.saturationBase),
        "saturationPalette": float(model.saturationPalette),
        "hueMatrixBase": list(mb.hue_matrix_from_rotation(float(model.hueRotationBase))),
        "hueMatrixPalette": list(mb.hue_matrix_from_rotation(float(model.hueRotationPalette))),
        "instancePaletteScale": float(model.paletteScale),
        "instancePaletteOffset": float(model.paletteOffset),
        "paletteStrength": float(model.paletteStrength),
        # Applied as a lerp toward `albedo * furTint` by the fur coverage mask (pBaseAOTexture.G),
        # measured in the fur shader's IR. Both grades desaturate hard on some variants (v02 runs
        # saturationBase 0.131), so on those the graded albedo is close to greyscale and the tint
        # carries the hue: v02 [1.0, 0.82, 0.545] tan, v04 [0.823, 1.0, 0.446] olive.
        #
        # NOT "the only colour parameter that differs between v02 and v04", as this used to claim --
        # brightness, saturation and keyTolerance all differ too (v02 3.5/0.131/0.06 vs
        # v04 1.8/0.297/0.15). That claim came from comparing the layer arrays alone.
        "furTint": [float(c) for c in getattr(model, "furTint", (1.0, 1.0, 1.0))],
        "gradientEnabled": row is not None,
        "coeffExact": bool(exact),
        "coeffSource": row.get("from") if row is not None else None,
        **grad,
    }


class PreviewBridge:
    """Socket client to the Blender listener add-on. One request per connection."""

    def __init__(self, host=HOST, port=PORT, timeout=10.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._object = None          # the imported mesh object name, remembered for re-grades

    # -- framing ----------------------------------------------------------
    def _request(self, obj):
        """Send one framed JSON request; return the framed JSON reply dict, or None if the
        listener is unreachable / the exchange fails. None (not a synthesized error dict) is what
        lets `connect` tell 'no listener' apart from 'listener replied ok:false'."""
        body = json.dumps(obj).encode("utf-8")
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall(struct.pack(">I", len(body)) + body)
                hdr = self._recv_exact(s, 4)
                (n,) = struct.unpack(">I", hdr)
                return json.loads(self._recv_exact(s, n).decode("utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _recv_exact(s, n):
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise OSError("connection closed early")
            buf += chunk
        return buf

    # -- public API -------------------------------------------------------
    def connect(self):
        """True if the listener replies at all (a framed reply, even ok:false, means it's alive)."""
        return self._request({"cmd": "ping"}) is not None

    def build_material(self, object_name, mask_dir, mask_prefix, layers_json,
                       base_diffuse=None, skin_name=None):
        """Build+assign the layer material onto the user's imported mesh object `object_name`.

        `base_diffuse` / `skin_name` carry a VARIANTSET (cosmetic skin): the species base diffuse is
        swapped for the set's own and the material is tagged with which one, everything else being
        identical. Omit both for the plain species build.
        """
        self._object = object_name
        msg = {"cmd": "build", "object": object_name, "mask_dir": mask_dir,
               "mask_prefix": mask_prefix, "layers_json": layers_json}
        if base_diffuse:
            msg["base_diffuse"] = base_diffuse
        if skin_name:
            msg["skin_name"] = skin_name
        r = self._request(msg)
        return bool(r and r.get("ok"))

    def push(self, model):
        """Re-grade the current material from the model."""
        r = self._request({"cmd": "grade", "block": model_to_block(model)})
        return bool(r and r.get("ok"))

    def push_pattern(self, pattern_model, source="live", index_map=None, part="",
                     object_name=None, interp="linear", patchwork_map=None):
        """Send an IN-MEMORY pattern to Blender. Returns (ok, message).

        Sends data, not a path. The editor's model may hold unsaved edits -- the whole point of a
        live preview is seeing a key you are still dragging -- so writing a temp .fgm just to have
        the listener read it back would add a round trip and a file that can be left behind.

        The payload is exactly what `export_pattern.export()` builds from a file, so the node
        builder on the far side cannot tell the difference.
        """
        import pattern_lut
        try:
            lut = pattern_lut.bake(pattern_model, interp)
            idx, byte = pattern_lut.threshold_from_model(pattern_model, interp=interp)
            data = {
                "source": source,
                "model": pattern_model.to_dict(),
                "lut": {k: v.tolist() for k, v in lut.items()},
                "interp": interp,
                "threshold": {"index": float(idx), "byte": int(byte)},
            }
        except Exception as e:
            return False, "could not bake pattern: %s: %s" % (type(e).__name__, e)

        cmd = {"cmd": "pattern", "data": data, "part": part}
        if index_map:
            cmd["index_map"] = index_map
        if patchwork_map:
            cmd["patchwork_map"] = patchwork_map
        if object_name:
            cmd["object"] = object_name
        r = self._request(cmd)
        if not r:
            return False, "no reply from Blender (is the listener add-on enabled?)"
        if not r.get("ok"):
            return False, str(r.get("error") or "unknown error")
        msg = ", ".join(r.get("applied") or []) or "applied"
        if r.get("note"):
            msg += "  -- " + r["note"]
        return True, msg

    def last_import(self):
        """What Blender's File > Import last loaded, or None if unreachable.

        `{"path":..., "object":..., "species":..., "sex":..., "serial": N}`. `serial` increments on
        every import, so a caller can spot a re-import of the same file by comparing it.
        """
        r = self._request({"cmd": "state"})
        return r.get("last_import") if r and r.get("ok") else None

    def selected(self):
        """What is selected in Blender right now: {object, material, variant_path, seed, ...}.

        Returns None when nothing is selected or Blender is unreachable -- the caller reports that
        to the user rather than this raising, because "click the model first" is a normal outcome.
        """
        rep = self._request({"cmd": "selected"})
        return (rep or {}).get("selected") if (rep or {}).get("ok") else None

    def list_objects(self, contains=None):
        """Mesh object names in the Blender scene, biggest first. Empty list if unreachable."""
        r = self._request({"cmd": "objects", "contains": contains})
        return list(r.get("objects") or []) if r and r.get("ok") else []

    @staticmethod
    def gradient_exact(seed, complexity):
        """True if this seed's gradient coefficients are harvested (preview is exact)."""
        _row, exact = ep.coefficients_for(int(seed), int(complexity))
        return bool(exact)


def selftest():
    # model_to_block is the unit under test (no Blender / socket needed)
    m = VariantModel.template(); m.seed = 9; m.complexity = 10
    blk = model_to_block(m)
    need = {"keyColour", "keyThreshold", "keyTolerance", "keyType", "brightnessBase",
            "saturationPalette", "hueMatrixBase", "instancePaletteScale", "paletteStrength",
            "gradOffset", "gradAmplitude", "gradFreq", "gradPhase", "gradientEnabled", "coeffExact"}
    assert need <= set(blk), sorted(need - set(blk))
    # The GPU bit is the COMPLEMENT of u_globalKeyType, measured against the game's GBuffer albedo
    # on v00/v02/v09 -- see the comment in model_to_block. v02 (FGM 1) and v09 (FGM 0) need
    # OPPOSITE bits, so a constant cannot satisfy both and neither can a direct mapping.
    m1 = VariantModel.template(); m1.seed = 9; m1.complexity = 10
    for kt, want in ((0, True), (1, False)):
        m1.keyType = kt
        assert model_to_block(m1)["keyType"] is want, (
            f"u_globalKeyType={kt} must give GPU bit {want} (the complement) -- see the "
            "comment in model_to_block before changing this")
    # ...but it must still ROUND-TRIP on the model, so editing and save_fgm stay faithful
    m1.keyType = 0
    assert VariantModel.from_dict(m1.to_dict()).keyType == 0
    assert abs(sum(blk["hueMatrixBase"]) - 511) <= 2, blk["hueMatrixBase"]   # circulant invariant
    # seed 9 cx 10 is harvested -> real gradient, exact
    assert blk["gradientEnabled"] and blk["coeffExact"], blk
    assert blk["gradAmplitude"] != [0, 0, 0], "harvested seed should have a real gradient"
    # an unharvested seed -> flat gradient, not exact, but still a valid block
    m.seed = 999
    b2 = model_to_block(m)
    assert b2["gradAmplitude"] == [0, 0, 0] and b2["coeffExact"] is False and not b2["gradientEnabled"]
    # model values pass through
    m2 = VariantModel.template(); m2.brightnessBase = 1.25; m2.keyColour = [0.2, 0.4, 0.6]
    b3 = model_to_block(m2)
    assert abs(b3["brightnessBase"] - 1.25) < 1e-9 and b3["keyColour"] == [0.2, 0.4, 0.6]
    # gradient_exact agrees with the block flag
    assert PreviewBridge.gradient_exact(9, 10) is True
    assert PreviewBridge.gradient_exact(999, 3) is False

    # Pattern preview sends the EDITOR'S IN-MEMORY model, not a file path. Exercise the protocol
    # boundary without Blender so a renamed key or a numpy value leaking into JSON is caught here.
    from pattern_model import PatternModel
    pm = PatternModel.template()
    pm.colourKeys[0] = (0, [0.2, 0.4, 0.6])
    pm.opacityKeys[0] = (0, 1.0)

    class _RecordingBridge(PreviewBridge):
        def __init__(self):
            super().__init__()
            self.request = None
        def _request(self, obj):
            self.request = obj
            # Prove the real socket encoder can accept the complete payload.
            json.dumps(obj)
            return {"ok": True, "applied": ["mesh (material)"], "note": None}

    rb = _RecordingBridge()
    ok, message = rb.push_pattern(pm, source="unsaved", index_map=r"C:\maps\index.png",
                                  object_name="mesh")
    assert ok and "mesh (material)" in message, (ok, message)
    assert rb.request["cmd"] == "pattern" and rb.request["object"] == "mesh", rb.request
    assert rb.request["index_map"].endswith("index.png"), rb.request
    assert rb.request["data"]["model"] == pm.to_dict(), rb.request["data"]["model"]
    assert set(rb.request["data"]["lut"]) == {"colour", "emissive", "opacity"}
    assert isinstance(rb.request["data"]["threshold"]["byte"], int)

    # a bridge with no listener returns a clean failure, never raises
    assert PreviewBridge(port=59999).connect() is False
    print("selftest ok")


if __name__ == "__main__":
    selftest()
