"""JWE3 Variant Tools -- Blender add-on entry point.

This folder IS the add-on. Install it as a folder/zip, never as a single .py: the tool needs its
siblings (`fgm_io`, `preview_assets`, `coeff_store`, ...), the vendored research modules in
`vendor/`, and the shipped data in `data/`. Installing just `blender_listener.py` copies one file
into Blender's addons folder where it can see none of that, and every import fails at the first use.

`build_addon.py` produces the installable zip.

Everything the add-on needs is inside this folder. The only two things that legitimately live
outside -- the game install and cobra-tools -- are auto-detected (Steam's registry and library list;
the cobra-tools add-on you already have) and can be overridden in one shared config via
`setup_gui.py`.
"""
import os
import sys

bl_info = {
    "name": "JWE3 Variant Tools",
    "author": "VariantEditor",
    "version": (1, 2, 0),
    "blender": (4, 5, 0),
    "location": "File > Import > JWE3 Variant (.fgm)",
    "description": "Import a JWE3 dinosaur variant .fgm onto an imported mesh (builds the real "
                   "layer material and palette grade), and serve a live preview to the standalone "
                   "JWE3 Variant Editor over localhost:8990.",
    "category": "Import-Export",
}

_HERE = os.path.dirname(os.path.abspath(__file__))
# Flat imports (`import fgm_io`) are used throughout, including by the vendored modules, so both
# folders go on sys.path rather than rewriting every module to use relative imports.
for _p in (_HERE, os.path.join(_HERE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import blender_listener  # noqa: E402


def register():
    # Tell the listener the ADD-ON's module name before it builds its preferences class.
    #
    # Blender matches `AddonPreferences.bl_idname` against the name of the add-on module -- this
    # package. `blender_listener` is imported FLAT (its folder is on sys.path, above), so its own
    # `__name__` is just "blender_listener" and never matched: the preferences panel silently did
    # not render at all, and there was no way to set the texture folders from inside Blender.
    #
    # `__name__` here is whatever Blender loaded the add-on as -- "VariantEditor" for a normal
    # install, "bl_ext.user_default.VariantEditor" when installed as an extension -- so passing it
    # through is correct for both.
    blender_listener.ADDON_ID = __name__
    blender_listener.register()


def unregister():
    blender_listener.unregister()
