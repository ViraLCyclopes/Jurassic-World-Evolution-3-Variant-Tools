"""
FGM I/O: Load and save JWE3 dinosaur variant FGM files.

Maps FGM attributes to/from VariantModel via cobra-tools' FgmHeader.
Handles variant FGM XML files (extracted, not packed in OVLs).

Attribute mapping (from plan):
- seed: u_globalPaletteSeed [int]
- complexity: u_globalPaletteMaximumComplexity [int]
- keyColour: u_globalKeyColour [r,g,b]
- keyThreshold: u_globalKeyThreshold [float]
- keyTolerance: u_globalKeyTolerance [float]
- brightnessBase: u_globalColourBrightnessBase [float]
- brightnessPalette: u_globalColourBrightnessPalette [float]
- saturationBase: u_globalColourSaturationBase [float]
- saturationPalette: u_globalColourSaturationPalette [float]
- hueRotationBase: u_globalColourRotationOffsetBase [float]
- hueRotationPalette: u_globalColourRotationOffsetPalette [float]
- paletteScale: u_instancePaletteScale [float]
- paletteOffset: u_instancePaletteOffset [float]
- paletteStrength: u_instancePaletteStrength [float]
- layerColourWeights[n]: u_globalColourWeight{n} (n=1..16) [float]
(layerSaturation/Contrast are deferred - in layer FGMs, not variant FGM)
"""

import sys
import logging
import os
import re
from pathlib import Path

# Set up logging before importing cobra-tools
logging.disable(logging.WARNING)

# Add cobra-tools to path
sys.path.insert(0, r"d:\JWE2 Stuff\Cobra Tool Versions\Main Mod Kit\cobra-tools-master")

from modules.formats.FGM import FgmContext
from generated.formats.fgm.structs.FgmHeader import FgmHeader

# Add parent directory to path for variant_model import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from variant_model import VariantModel


# FGM attribute name mapping
ATTRIBUTE_MAP = {
    "seed": "u_globalPaletteSeed",
    "complexity": "u_globalPaletteMaximumComplexity",
    "keyColour": "u_globalKeyColour",
    "keyThreshold": "u_globalKeyThreshold",
    "keyTolerance": "u_globalKeyTolerance",
    "brightnessBase": "u_globalColourBrightnessBase",
    "brightnessPalette": "u_globalColourBrightnessPalette",
    "saturationBase": "u_globalColourSaturationBase",
    "saturationPalette": "u_globalColourSaturationPalette",
    "hueRotationBase": "u_globalColourRotationOffsetBase",
    "hueRotationPalette": "u_globalColourRotationOffsetPalette",
    "paletteScale": "u_instancePaletteScale",
    "paletteOffset": "u_instancePaletteOffset",
    "paletteStrength": "u_instancePaletteStrength",
}

# Layer colour weight attributes (n=1..16)
LAYER_COLOUR_WEIGHT_PREFIX = "u_globalColourWeight"

# What makes an FGM a *variant* FGM. Observed in the Spino Female folder: variant FGMs use the
# DinosaurLayered_Variant shader with 144 attributes; layer FGMs use DinosaurLayered_Layer with 13,
# and the bare <species>.fgm uses DinosaurLayered_Layered_Opaque with 29. Only the first carries
# palette parameters, so load_fgm tests for the seed attribute rather than trusting the filename.
VARIANT_SHADER = "DinosaurLayered_Variant"
VARIANT_MARKER_ATTR = "u_globalPaletteSeed"


def is_variant_fgm(path: str) -> bool:
    """True if this .fgm carries palette parameters (i.e. load_fgm will accept it)."""
    try:
        ctx = FgmContext(loader=None)
        h = FgmHeader.from_xml_file(path, ctx)
        return VARIANT_MARKER_ATTR in [a.name for a in h.attributes.data]
    except Exception:
        return False


def load_fgm(path: str) -> VariantModel:
    """
    Load a JWE3 dinosaur variant FGM file and return a VariantModel.

    Args:
        path: Absolute path to the .fgm file (XML format)

    Returns:
        VariantModel with attributes read from the FGM
    """
    ctx = FgmContext(loader=None)
    h = FgmHeader.from_xml_file(path, ctx)

    # Build attribute name -> index map
    names = [a.name for a in h.attributes.data]
    name_to_idx = {name: i for i, name in enumerate(names)}

    # A non-variant FGM has NONE of these attributes, so every field below would silently keep its
    # template default -- seed 0, everything 1.0 -- and the caller would get a valid-looking model
    # that is pure defaults. Loading a dozen different layer FGMs would produce a dozen identical
    # models and nothing would appear to change. Fail loudly instead.
    if VARIANT_MARKER_ATTR not in name_to_idx:
        raise ValueError(
            "%s is not a variant FGM: shader %r with %d attributes, no %s.\n"
            "Variant FGMs are named <species>[_<sex>]_variant_<NN>_<NN>.fgm and use the %r shader. "
            "Layer FGMs (%s_layer_NN), pattern FGMs and the bare <species>.fgm carry no palette "
            "parameters and cannot be edited or previewed here."
            % (os.path.basename(path), h.shader_name, len(names), VARIANT_MARKER_ATTR,
               VARIANT_SHADER, os.path.basename(path).split("_")[0]))

    # Get values array (parallel to names)
    vals = h.value_foreach_attributes.data

    # Create model with template defaults
    model = VariantModel.template()

    # Map scalar attributes
    for model_field, fgm_attr_name in ATTRIBUTE_MAP.items():
        if fgm_attr_name in name_to_idx:
            idx = name_to_idx[fgm_attr_name]
            value_list = vals[idx].value

            if model_field == "keyColour":
                # keyColour is [r, g, b]
                model.keyColour = list(value_list)
            elif model_field in ("seed", "complexity"):
                # seed and complexity are ints
                setattr(model, model_field, int(value_list[0]))
            else:
                # All others are floats
                setattr(model, model_field, float(value_list[0]))

    # Map layer colour weights (u_globalColourWeight1..16)
    layer_weights = [1.0] * 16
    for n in range(1, 17):
        attr_name = f"{LAYER_COLOUR_WEIGHT_PREFIX}{n}"
        if attr_name in name_to_idx:
            idx = name_to_idx[attr_name]
            layer_weights[n - 1] = float(vals[idx].value[0])
    model.layerColourWeights = layer_weights

    # layerSaturation and layerContrast remain at template defaults (they're in layer FGMs, not variant)

    return model


def save_fgm(model: VariantModel, path: str) -> None:
    """
    Save a VariantModel to an existing FGM file, overwriting attribute values in place.

    Args:
        model: VariantModel with updated values
        path: Absolute path to the .fgm file (must exist)
    """
    ctx = FgmContext(loader=None)
    h = FgmHeader.from_xml_file(path, ctx)

    # Set game to avoid AttributeError in context_to_xml (quirk from plan)
    if not hasattr(ctx, 'game') or ctx.game is None:
        ctx.game = "Jurassic World Evolution 3"

    # Build attribute name -> index map
    names = [a.name for a in h.attributes.data]
    name_to_idx = {name: i for i, name in enumerate(names)}

    # Get values array (parallel to names)
    vals = h.value_foreach_attributes.data

    # Update scalar attributes
    for model_field, fgm_attr_name in ATTRIBUTE_MAP.items():
        if fgm_attr_name in name_to_idx:
            idx = name_to_idx[fgm_attr_name]
            value = getattr(model, model_field)

            if model_field == "keyColour":
                # keyColour is [r, g, b] - update each element in place
                for j, v in enumerate(value):
                    vals[idx].value[j] = float(v)
            elif model_field in ("seed", "complexity"):
                # seed and complexity are ints, stored as single-element list
                vals[idx].value[0] = int(value)
            else:
                # All others are floats
                vals[idx].value[0] = float(value)

    # Update layer colour weights (u_globalColourWeight1..16)
    for n in range(1, 17):
        attr_name = f"{LAYER_COLOUR_WEIGHT_PREFIX}{n}"
        if attr_name in name_to_idx:
            idx = name_to_idx[attr_name]
            vals[idx].value[0] = float(model.layerColourWeights[n - 1])

    # Save the FGM file
    with h.to_xml_file(h, path):
        pass


def save_fgm_from_template(model: VariantModel, template_fgm_path: str, out_path: str) -> None:
    """
    Save a VariantModel to a new FGM file by copying a template and writing model values.

    Creates a brand-new .fgm file from a template (for "New from template" workflow).

    Args:
        model: VariantModel with values to write
        template_fgm_path: Path to the template .fgm file (source, untouched)
        out_path: Path where the new .fgm will be written
    """
    import shutil
    # Copy the template to the output location
    shutil.copy(template_fgm_path, out_path)
    # Write the model onto the output file
    save_fgm(model, out_path)


def species_sex_from_filename(path: str) -> tuple:
    """
    Parse species and sex from a variant FGM filename.

    Pattern: <species>[_<sex>]_variant_...
    Sex tokens: female, male, juvenile (optional)

    Examples:
        "spinosaurus_female_variant_01_07.fgm" -> ("Spinosaurus", "Female")
        "apatosaurus_variant_01_00.fgm" -> ("Apatosaurus", None)

    Args:
        path: File path or filename

    Returns:
        Tuple of (species: str, sex: str|None) with species capitalized
    """
    # Extract just the filename
    filename = os.path.basename(path).lower()

    # Split on _variant_ to get the prefix
    parts = filename.split("_variant_")
    if not parts:
        return (None, None)

    prefix = parts[0]  # e.g. "spinosaurus_female", "apatosaurus", "indominus_rex_female"

    tokens = [t for t in prefix.split("_") if t]
    if not tokens:
        return (None, None)

    # Sex is an optional TRAILING token.
    sex = None
    if len(tokens) >= 2 and tokens[-1].lower() in ("female", "male", "juvenile"):
        sex = tokens.pop().capitalize()

    # Everything left is the species -- which is NOT always one token. `indominus_rex_female_...`
    # must give "IndominusRex" (the game's own folder name), not "Indominus": taking only the first
    # token left every multi-word species with no LayerJSON and no texture folder, so importing one
    # failed outright.
    species = "".join(t.capitalize() for t in tokens)
    return (species or None, sex)


# Sample FGM for testing
SAMPLE_FGM = r"D:\JWE2 Stuff\Cobra Tool Versions\Main Mod Kit\JWE 3 Luas\Base Game\Dinosaur Files\Base Game Dinos\Baryonyx\Female\baryonyx_variant_01_00.fgm"


def selftest():
    """
    Test load/save round-trip, filename parsing, and save_fgm_from_template.
    """
    # Test filename parsing
    assert species_sex_from_filename("spinosaurus_female_variant_01_07.fgm") == ("Spinosaurus", "Female"), \
        f"Failed: spinosaurus_female parsing"
    assert species_sex_from_filename("apatosaurus_variant_01_00.fgm") == ("Apatosaurus", None), \
        f"Failed: apatosaurus parsing"
    # multi-word species: the game's folder is "IndominusRex", so the parse must produce that and
    # not "Indominus" -- otherwise nothing resolves and the import fails
    assert species_sex_from_filename("indominus_rex_female_variant_01_00.fgm") == \
        ("IndominusRex", "Female"), species_sex_from_filename("indominus_rex_female_variant_01_00.fgm")
    assert species_sex_from_filename("indominus_rex_variant_01_00.fgm") == ("IndominusRex", None)
    assert species_sex_from_filename(r"C:\x\tyrannosaurus_rex_male_variant_02_03.fgm") == \
        ("TyrannosaurusRex", "Male")
    # a species literally called "male"/"female" is not a thing, but a lone sex token must not
    # swallow the whole name
    assert species_sex_from_filename("female_variant_01_00.fgm") == ("Female", None)

    # Test load/save round-trip
    import shutil
    import tempfile

    work = os.path.join(tempfile.gettempdir(), "fgmio_test.fgm")

    # Copy SAMPLE_FGM to temp location (never overwrite original)
    shutil.copy(SAMPLE_FGM, work)

    # Load, edit, save, reload
    m = load_fgm(work)
    original_seed = m.seed
    original_brightness = m.brightnessBase

    m.seed = 200
    m.brightnessBase = 1.25
    save_fgm(m, work)

    m2 = load_fgm(work)
    assert m2.seed == 200, f"Seed mismatch: expected 200, got {m2.seed}"
    assert abs(m2.brightnessBase - 1.25) < 1e-4, f"Brightness mismatch: expected 1.25, got {m2.brightnessBase}"

    # Test save_fgm_from_template
    template_path = os.path.join(tempfile.gettempdir(), "fgmio_template.fgm")
    out_path = os.path.join(tempfile.gettempdir(), "fgmio_from_template.fgm")

    # Copy SAMPLE_FGM to template location
    shutil.copy(SAMPLE_FGM, template_path)

    # Create a model with seed=77
    m_template = VariantModel.template()
    m_template.seed = 77
    m_template.brightnessBase = 1.5

    # Save from template to a different output path
    save_fgm_from_template(m_template, template_path, out_path)

    # Verify the output file has the expected values
    m3 = load_fgm(out_path)
    assert m3.seed == 77, f"Template seed mismatch: expected 77, got {m3.seed}"
    assert abs(m3.brightnessBase - 1.5) < 1e-4, f"Template brightness mismatch: expected 1.5, got {m3.brightnessBase}"

    # Verify template and output are different files
    assert out_path != template_path, "Output path should differ from template path"

    # A non-variant FGM must RAISE, not quietly hand back template defaults. Layer FGMs carry no
    # palette attributes, so silently defaulting made every one of them load as an identical
    # seed-0 model -- the "imports once and never updates" bug.
    spino = os.path.join(os.path.dirname(os.path.dirname(SAMPLE_FGM)),
                         "..", "Spino Female")
    layer = os.path.normpath(os.path.join(spino, "spinosaurus_female_layer_01.fgm"))
    if os.path.isfile(layer):
        assert not is_variant_fgm(layer), layer
        try:
            load_fgm(layer)
        except ValueError as e:
            assert "not a variant FGM" in str(e), str(e)
        else:
            raise AssertionError("load_fgm accepted a layer FGM instead of raising")
        # and a real variant still loads, including a legitimately all-zero one
        variant = os.path.normpath(os.path.join(spino, "spinosaurus_female_variant_01_01.fgm"))
        if os.path.isfile(variant):
            assert is_variant_fgm(variant)
            assert load_fgm(variant).seed == 0        # genuinely seed 0, not a silent default
    assert is_variant_fgm(SAMPLE_FGM)

    print("selftest ok")


if __name__ == "__main__":
    selftest()
