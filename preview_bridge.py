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

    Same key set as `export_palette.block`. `keyType` is True (bit-16 finding). The gradient is
    taken from `coefficients_for`; a non-harvested seed yields a flat gradient and coeffExact False.
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
        "keyType": True,
        "brightnessBase": float(model.brightnessBase),
        "brightnessPalette": float(model.brightnessPalette),
        "saturationBase": float(model.saturationBase),
        "saturationPalette": float(model.saturationPalette),
        "hueMatrixBase": list(mb.hue_matrix_from_rotation(float(model.hueRotationBase))),
        "hueMatrixPalette": list(mb.hue_matrix_from_rotation(float(model.hueRotationPalette))),
        "instancePaletteScale": float(model.paletteScale),
        "instancePaletteOffset": float(model.paletteOffset),
        "paletteStrength": float(model.paletteStrength),
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

    def build_material(self, object_name, mask_dir, mask_prefix, layers_json):
        """Build+assign the layer material onto the user's imported mesh object `object_name`."""
        self._object = object_name
        r = self._request({"cmd": "build", "object": object_name, "mask_dir": mask_dir,
                           "mask_prefix": mask_prefix, "layers_json": layers_json})
        return bool(r and r.get("ok"))

    def push(self, model):
        """Re-grade the current material from the model."""
        r = self._request({"cmd": "grade", "block": model_to_block(model)})
        return bool(r and r.get("ok"))

    def last_import(self):
        """What Blender's File > Import last loaded, or None if unreachable.

        `{"path":..., "object":..., "species":..., "sex":..., "serial": N}`. `serial` increments on
        every import, so a caller can spot a re-import of the same file by comparing it.
        """
        r = self._request({"cmd": "state"})
        return r.get("last_import") if r and r.get("ok") else None

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
    assert blk["keyType"] is True
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
    # a bridge with no listener returns a clean failure, never raises
    assert PreviewBridge(port=59999).connect() is False
    print("selftest ok")


if __name__ == "__main__":
    selftest()
