"""
FGM probe: verify round-trip load/save of extracted .fgm variant files.

Winning approach: Extracted `.fgm` files are cobra-tools XML (`<FgmHeader ...>`).
Load with `FgmContext(loader=None)` + `FgmHeader.from_xml_file(path, ctx)`.
Parameters are stored as parallel arrays:
  - names: `header.attributes.data[i].name`
  - values: `header.value_foreach_attributes.data[i].value` (a list)
Save with `with h.to_xml_file(h, out_path): pass` (context manager, no body needed).

Note: Saving logs a non-fatal `AttributeError: 'FgmContext' object has no attribute 'game'`.
The file writes correctly despite this. Future refinement: set ctx.game before saving to silence it.

Verified round-trip on real Baryonyx variant (native seed 36, complexity 2).
DinosaurLayered_Variant FGMs have 144 attributes.
"""

import sys
import logging
import tempfile
import shutil
import os

# Put cobra-tools on sys.path and disable WARNING logs
logging.disable(logging.WARNING)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jwe3_config as cfg

cobra_dir = cfg.get("cobra_tools")
if cobra_dir and os.path.isdir(cobra_dir) and cobra_dir not in sys.path:
    sys.path.insert(0, cobra_dir)

try:
    from modules.formats.FGM import FgmContext
    from generated.formats.fgm.structs.FgmHeader import FgmHeader
except ImportError as e:
    raise RuntimeError(
        "cobra-tools could not be found or imported.\n"
        "Please run 'python setup_gui.py' to configure your cobra-tools path."
    ) from e

_game_ovl = cfg.get("game_dir")
SAMPLE_FGM = os.environ.get("JWE3_SAMPLE_FGM") or (
    os.path.join(_game_ovl, "Content0", "Dinosaurs", "Baryonyx", "Female", "baryonyx_variant_01_00.fgm")
    if _game_ovl else os.path.join(HERE, "Textures", "Baryonyx", "baryonyx_variant_01_00.fgm")
)


def selftest():
    """
    Test round-trip load/save of an FGM file.
    """
    if not os.path.isfile(SAMPLE_FGM):
        print("probe ok (SAMPLE_FGM not present on disk, skipped probe test)")
        return

    # Create a temp copy to avoid modifying the original
    temp_copy = os.path.join(tempfile.gettempdir(), "fgm_probe_test.fgm")
    shutil.copy(SAMPLE_FGM, temp_copy)

    try:
        # Load the original and verify native seed
        ctx = FgmContext(loader=None)
        h = FgmHeader.from_xml_file(SAMPLE_FGM, ctx)

        names = [a.name for a in h.attributes.data]
        vals = h.value_foreach_attributes.data

        # Find u_globalPaletteSeed and check native value
        i = names.index("u_globalPaletteSeed")
        native_seed = vals[i].value[0]
        assert native_seed == 36, f"Expected native seed 36, got {native_seed}"

        # Now load the temp copy and modify it
        ctx2 = FgmContext(loader=None)
        h2 = FgmHeader.from_xml_file(temp_copy, ctx2)

        names2 = [a.name for a in h2.attributes.data]
        vals2 = h2.value_foreach_attributes.data

        # Find and modify seed to 123
        i2 = names2.index("u_globalPaletteSeed")
        vals2[i2].value[0] = 123

        # Save the temp copy
        with h2.to_xml_file(h2, temp_copy):
            pass

        # Reload and verify the change persisted
        ctx3 = FgmContext(loader=None)
        h3 = FgmHeader.from_xml_file(temp_copy, ctx3)

        names3 = [a.name for a in h3.attributes.data]
        vals3 = h3.value_foreach_attributes.data

        i3 = names3.index("u_globalPaletteSeed")
        reloaded_seed = vals3[i3].value[0]
        assert reloaded_seed == 123, f"Expected seed 123 after save, got {reloaded_seed}"

        print("probe ok")

    finally:
        # Clean up the temp file
        if os.path.exists(temp_copy):
            os.remove(temp_copy)


if __name__ == "__main__":
    selftest()
