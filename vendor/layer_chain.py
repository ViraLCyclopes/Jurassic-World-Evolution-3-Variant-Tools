"""Resolve a species' 16 skin layers to their swatch textures and per-layer parameters.

THE CHAIN, which took a while to find and is not guessable from any one file:

    <species>_<sex>_layers.dinosaurmateriallayers   16 Layer entries, each naming a swatch FGM
        -> swatch FGM in SwatchLibrary.ovl          gives an array_index per texture slot
            -> SwatchLibrary/<swatch>.pheighttexture_[NN].png   the actual slice

Per-layer settings live somewhere else again -- in `<species>_<sex>_layer_NN.fgm` -- and the
per-pixel layer masks in yet another place, the species' `playered_blendweights` texture (4
textures x RGBA = 16 channels, and the master FGM's `pLayered_BlendWeightBatchCount` = 4 says so).

Dead ends worth recording so nobody re-walks them: the layer FGMs carry NO textures at all
(`textures.data` is None, not empty), the master material's `pLayered_HeightTexture` entries are
all -1 because the swatch arrays are bound bindlessly, and the OVL's dependency list never
mentions a swatch.

Verified on Spinosaurus Female: 16/16 layers resolve to a height slice.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reader_kit as rk  # noqa: E402

import _paths  # noqa: E402  (vendored: data lives inside the package)
SWATCH_PARAMS = _paths.swatch_params()
SWATCH_DIR = _paths.swatch_dir()

LAYERS_EXT = ".dinosaurmateriallayers"


class LayersNotFound(Exception):
    """No layer definition could be found. The message says where we looked."""


def find_layers_file(folder, sex=None):
    """The `.dinosaurmateriallayers` in a folder, or None.

    Matched on EXTENSION ONLY -- the stem varies (`carnotaurus_layers...`,
    `cearadactylus_female_layers...`), so constructing the name from the species is unreliable.
    When a folder holds more than one, a `sex` hint picks the matching file.
    """
    import glob as _glob
    hits = sorted(_glob.glob(os.path.join(folder, "*" + LAYERS_EXT)))
    if not hits:
        return None
    if sex:
        for h in hits:
            if sex.lower() in os.path.basename(h).lower():
                return h
    return hits[0]


def _fgm_index(folder):
    """{lowercase filename: path} for every .fgm in the folder, for case-insensitive lookup."""
    out = {}
    for name in os.listdir(folder):
        if name.lower().endswith(".fgm"):
            out[name.lower()] = os.path.join(folder, name)
    return out


def _params_from_fgm_file(path):
    """{attribute name: [values]} from a loose extracted .fgm -- the folder equivalent of
    `reader_kit._params`, which needs an OVL loader."""
    from modules.formats.FGM import FgmContext
    from generated.formats.fgm.structs.FgmHeader import FgmHeader
    h = FgmHeader.from_xml_file(path, FgmContext(loader=None))
    names = [a.name for a in h.attributes.data]
    vals = h.value_foreach_attributes.data
    return {names[i]: [float(x) for x in vals[i].value] for i in range(len(names))}


def resolve_from_folder(folder, sex=None):
    """The layer chain, built from a folder YOU extracted. No game install, no Dinosaurs.zip.

    This is the path the tools use. Reading the game's own OVL turned out to be unworkable: OVL
    layout varies between species (Carnotaurus' species OVL carries no .dinosaurmateriallayers at
    all), and depending on the installed game to preview a variant is wrong anyway -- it breaks for
    modded and custom species, which are the whole point.

    The folder needs the `.dinosaurmateriallayers` file alongside the per-layer `.fgm` files.
    """
    from generated.formats.dinosaurmaterialvariants.structs.DinoLayersHeader import DinoLayersHeader
    from modules.formats.FGM import FgmContext

    path = find_layers_file(folder, sex)
    if path is None:
        raise LayersNotFound(
            "no *%s file in %s\nExtract the species so that file sits alongside its .fgm files "
            "-- it is the layer definition and nothing can be built without it." % (LAYERS_EXT, folder))

    header = DinoLayersHeader.from_xml_file(path, FgmContext(loader=None))
    fgms = _fgm_index(folder)
    slices = swatch_slices()
    swatch_w = swatch_colour_weights()

    out = []
    cursor = 0                      # POST-increment, exactly as in resolve() below
    for i, lay in enumerate(header.layers.data):
        swatch = str(lay.texture_fgm_name.data)
        inc = int(lay.increment_channel)
        tptr = getattr(lay, "transform_fgm_name", None)
        tname = str(tptr.data) if tptr is not None and tptr.data else None

        params = {}
        if tname:
            fgm_path = fgms.get((tname + ".fgm").lower())
            if fgm_path:
                params = _params_from_fgm_file(fgm_path)

        out.append({
            "index": i,
            "layer_no": i + 1,
            "swatch": swatch,
            "transform_fgm": tname,
            "used": swatch not in ("None", "", "0"),
            "increment_channel": inc,
            "slices": slices.get((swatch + ".fgm").lower(), {}),
            "swatch_colour_weight": swatch_w.get((swatch + ".fgm").lower(), 1.0),
            "blend_texture": cursor // 4 if 0 <= cursor < 16 else None,
            "blend_channel": "RGBA"[cursor % 4] if 0 <= cursor < 16 else None,
            "params": params,
        })
        cursor += inc

    # Per-layer transform FGMs carry UV tiling, contrast and colouring weight. Missing them is not
    # fatal -- the chain still resolves -- but the preview silently loses those settings, so say so
    # rather than letting it look like a rendering bug.
    want = [l for l in out if l["used"] and l["transform_fgm"]]
    got = [l for l in want if l["params"]]
    if want and not got:
        print("  WARNING: none of the %d layer .fgm files are in %s -- extract them alongside %s, "
              "or the preview loses per-layer tiling and contrast."
              % (len(want), folder, os.path.basename(path)))
    elif len(got) < len(want):
        print("  WARNING: %d of %d layer .fgm files missing from %s"
              % (len(want) - len(got), len(want), folder))
    return out

# The four shared array textures every swatch indexes into.
SLOTS = ("pDiffuseTexture", "pHeightTexture", "pPackedTexture", "pRemapTexture")


def swatch_slices():
    """{swatch fgm name (lowercase): {slot: array_index}} from the dumped SwatchLibrary."""
    if not os.path.isfile(SWATCH_PARAMS):
        raise FileNotFoundError(f"{SWATCH_PARAMS} missing -- run extract_swatches.py first")
    out = {}
    for name, p in json.load(open(SWATCH_PARAMS)).items():
        if "__error__" in p:
            continue
        out[name.lower()] = {slot: entries[0]["array_index"]
                             for slot, entries in p["textures"].items() if entries}
    return out


def swatch_colour_weights():
    """{swatch fgm name (lowercase): the SWATCH's own pGlobalColouringWeight}.

    This is NOT the same number as the transform FGM's `pGlobalColouringWeight`, and the shader
    multiplies the two (`%2120 * %2148`, the low and high f16 halves of layer-block word 18).

    The swatch value is the material's own say in whether it may ever be repainted by the palette,
    and it is a hard 0 for `Swatch_Bone`, `Swatch_Nail` and `Swatch_Mouth_Flesh` -- beaks, horns,
    claws and tongues keep their own colour on every dinosaur in the game. Using only the transform
    value repaints all of them; the user spotted it on Lokiceratops' beak and tongue.

    Confirmed against nine layer blocks read out of RenderDoc captures across two species: the low
    half of word 18 equals this value in all nine, including both 0 and 1 cases.
    """
    if not os.path.isfile(SWATCH_PARAMS):
        raise FileNotFoundError(f"{SWATCH_PARAMS} missing -- run extract_swatches.py first")
    out = {}
    for name, p in json.load(open(SWATCH_PARAMS)).items():
        if "__error__" in p:
            continue
        v = p.get("attrs", {}).get("pGlobalColouringWeight")
        if v is not None:
            out[name.lower()] = float(v[0] if isinstance(v, list) else v)
    return out


def resolve(species, sex="Female"):
    """The 16 layers as dicts: swatch name, array slices, and the layer FGM's parameters.

    `blend_texture` / `blend_channel` say which of the four `playered_blendweights` textures and
    which of its RGBA channels carries this layer's mask.

    **The channel is data-driven, not positional.** Each Layer entry carries `increment_channel`,
    and the channel cursor advances only when it is 1. Spinosaurus, Lokiceratops and Dilophosaurus
    are all 1s, which makes the mapping look like `N//4, "RGBA"[N%4]` -- but Baryonyx is
    `[1,1,1,1,1,1,0,0,0,0,0,0,0,0,0,0]`, because its unused layers (swatch name `None`) consume no
    channel. Assuming the positional form would silently mis-assign every mask past layer 5.
    """
    # not f"{species}_{sex}.ovl": a sexless species (Indominus Rex) is just "<Species>.ovl"
    ovl = rk._ovl(os.path.join(rk.pristine(species, sex), rk.species_ovl_name(species, sex)))
    try:
        ml = next(l for n, l in ovl.loaders.items() if n.endswith(".dinosaurmateriallayers"))
    except StopIteration:
        raise LayersNotFound(
            "%s contains no .dinosaurmateriallayers -- OVL layout varies between species.\n"
            "Use resolve_from_folder() against your own extracted folder instead; it needs no "
            "game install." % rk.species_ovl_name(species, sex))
    slices = swatch_slices()
    swatch_w = swatch_colour_weights()

    out = []
    cursor = 0                      # POST-increment, matching cobra-tools' own importer:
    for i, lay in enumerate(ml.header.layers.data):     # a layer consumes the current channel,
        swatch = str(lay.texture_fgm_name.data)         # then the flag decides whether to advance
        inc = int(lay.increment_channel)

        # Each Layer names its transform FGM explicitly -- do not pair by sort order. The name is
        # mixed case (`Spinosaurus_Female_Layer_01`) while loader keys are lowercase, so the
        # lookup must be case-insensitive.
        tptr = getattr(lay, "transform_fgm_name", None)
        tname = str(tptr.data) if tptr is not None and tptr.data else None
        loader = ovl.loaders.get(f"{tname}.fgm".lower()) if tname else None
        params = rk._params(loader) if loader is not None else {}

        out.append({
            # `index` is 0-based (array position). `layer_no` is the 1-based number used by the
            # FGM filenames -- Lokiceratops_Layer_03 is index 2. Always quote layer_no or the FGM
            # name when talking to a human; talking in 0-based indices has already caused one
            # avoidable disagreement.
            "index": i,
            "layer_no": i + 1,
            "swatch": swatch,
            "transform_fgm": tname,
            "used": swatch not in ("None", "", "0"),
            "increment_channel": inc,
            "slices": slices.get((swatch + ".fgm").lower(), {}),
            # the swatch's own colouring veto -- multiply with params["pGlobalColouringWeight"]
            "swatch_colour_weight": swatch_w.get((swatch + ".fgm").lower(), 1.0),
            "blend_texture": cursor // 4 if 0 <= cursor < 16 else None,
            "blend_channel": "RGBA"[cursor % 4] if 0 <= cursor < 16 else None,
            "params": params,
        })
        cursor += inc
    return out


# All 54 slices of each shared array carry ONE filename prefix -- the name of whichever swatch
# the array texture happens to be attributed to, which is alphabetically first. The swatch's own
# name has nothing to do with its file; only `array_index` selects the slice.
ARRAY_PREFIX = "swatch_anky_ankylo_backplates"


def slice_png(layer, slot="pHeightTexture", suffix=""):
    """Path to this layer's slice of a shared array texture, or None if not extracted.

    `suffix` covers the textures that split across files -- `pPackedTexture` extracts as
    `_RGB` and `_A`. Note the channel suffix comes AFTER the slice index, not before:
    `ppackedtexture_[34]_RGB.png`.
    """
    idx = layer["slices"].get(slot)
    if idx is None:
        return None
    p = os.path.join(SWATCH_DIR,
                     f"{ARRAY_PREFIX}.{slot.lower()}_[{idx:02d}]{suffix}.png")
    return p if os.path.isfile(p) else None


def height_png(layer):
    """Path to this layer's height slice, or None if it is not extracted."""
    return slice_png(layer, "pHeightTexture")


def summarise(species, sex="Female"):
    layers = resolve(species, sex)
    print(f"{species} {sex}\n")
    print(f"{'fgm#':>4}  {'swatch':<42} {'hgt':>4} {'mask':>6} {'tile':>9} "
          f"{'hScale':>8} {'hOff':>6} {'remap':>5} {'weight':>6}")
    for L in layers:
        p = L["params"]
        tile = p.get("pUVTile", [0, 0])
        print(f"{L['layer_no']:>4}  {L['swatch']:<42} "
              f"{L['slices'].get('pHeightTexture', -1):>4} "
              f"{L['blend_texture']}{L['blend_channel']:>5} "
              f"{tile[0]:>4.0f}x{tile[1]:<4.0f} {p.get('pHeightScale',[0])[0]:>8.4f} "
              f"{p.get('pHeightOffset',[0])[0]:>6.2f} {p.get('pRemapLutIndex',[0])[0]:>5.0f} "
              f"{p.get('pGlobalColouringWeight',[0])[0]:>6.2f}")
    got = sum(1 for L in layers if "pHeightTexture" in L["slices"])
    onpath = sum(1 for L in layers if height_png(L))
    print(f"\n{got}/{len(layers)} resolved to a height slice; "
          f"{onpath}/{len(layers)} have the PNG extracted")
    return layers


def selftest():
    layers = resolve("Spinosaurus", "Female")
    assert len(layers) == 16, len(layers)
    assert all(L["slices"].get("pHeightTexture") is not None for L in layers), \
        [L["swatch"] for L in layers if "pHeightTexture" not in L["slices"]]

    # regression on the measured Spinosaurus chain (2026-07-24)
    assert layers[0]["swatch"] == "Swatch_Thero_Galli_WrinklesGary", layers[0]["swatch"]
    assert layers[0]["slices"]["pHeightTexture"] == 34
    assert layers[2]["slices"]["pHeightTexture"] == 29
    # two swatches are used twice; the same swatch must resolve to the same slice both times
    assert layers[5]["slices"] == layers[10]["slices"], "Swatch_Thero_Croc_BumpScales"
    assert layers[14]["slices"] == layers[15]["slices"], "Swatch_Sauro_Brachio_SmallSkin"

    # Bone and Mouth_Flesh must carry zero colouring weight -- teeth and gums take no palette.
    # This is an independent cross-check of the shader's layer-weight lerp (PALETTE.md section 6).
    for i in (1, 9):
        w = layers[i]["params"]["pGlobalColouringWeight"][0]
        assert w == 0.0, (i, layers[i]["swatch"], w)
    assert layers[1]["swatch"] == "Swatch_Bone" and layers[9]["swatch"] == "Swatch_Mouth_Flesh"

    # ...but that is SPINOSAURUS being tidy, not a rule. The veto that actually holds everywhere
    # lives on the swatch, and the shader multiplies the two. Lokiceratops is the counter-example
    # that proves it matters: its transform FGM says 1.0 for Bone and Mouth_Flesh, so trusting the
    # transform value alone repaints the beak and the tongue -- both spotted in a render before
    # the capture confirmed it.
    loki = resolve("Lokiceratops", "Female")
    beak = loki[9]      # Swatch_Bone, transform weight 1.0
    tongue = loki[13]   # Swatch_Mouth_Flesh, transform weight 1.0
    assert beak["swatch"] == "Swatch_Bone" and tongue["swatch"] == "Swatch_Mouth_Flesh"
    assert beak["params"]["pGlobalColouringWeight"][0] == 1.0, "premise of this test changed"
    assert beak["swatch_colour_weight"] == 0.0, beak["swatch_colour_weight"]
    assert tongue["swatch_colour_weight"] == 0.0, tongue["swatch_colour_weight"]
    # and skin swatches must NOT be vetoed, or the whole animal stops taking the palette
    assert loki[0]["swatch_colour_weight"] == 1.0, loki[0]["swatch"]
    assert loki[15]["swatch_colour_weight"] == 1.0, loki[15]["swatch"]

    # -1 is a legal remap index at the layer level too, matching variant_reader's finding
    assert layers[6]["params"]["pRemapLutIndex"][0] == -1.0
    assert layers[12]["params"]["pRemapLutIndex"][0] == -1.0

    # Spinosaurus increments every layer, so its mask split does cover all 16 channels once
    assert all(L["increment_channel"] == 1 for L in layers)
    seen = {(L["blend_texture"], L["blend_channel"]) for L in layers}
    assert len(seen) == 16, sorted(seen)
    assert {t for t, _ in seen} == {0, 1, 2, 3}

    # ...but that must come from the flag, not from the position. Baryonyx is the counter-example
    # that proves it: unused layers carry increment_channel 0 and consume no mask channel.
    bary = resolve("Baryonyx", "Female")
    inc = [L["increment_channel"] for L in bary]
    assert inc == [1] * 6 + [0] * 10, inc
    assert bary[5]["blend_channel"] == "G" and bary[5]["blend_texture"] == 1
    # every layer after the last increment shares one channel
    for L in bary[6:]:
        assert (L["blend_texture"], L["blend_channel"]) == (1, "B"), (L["index"], L)
    assert not bary[15]["used"], "trailing Baryonyx layers should be unused"
    assert sum(1 for L in bary if L["used"]) == 6, [L["swatch"] for L in bary]

    # NOTE on pre- vs post-increment. cobra-tools consumes the current channel and advances
    # afterwards, which is what we follow. The alternative (advance first) is indistinguishable on
    # every species examined so far, because the only zero flags sit on UNUSED trailing layers --
    # both orderings assign identical channels to all six real Baryonyx layers. So this is not
    # load-bearing today, and if a species ever carries a zero flag on a *used* layer it must be
    # settled from the shader, not from either implementation.
    for L in bary[:6]:
        assert L["blend_texture"] == L["index"] // 4
        assert L["blend_channel"] == "RGBA"[L["index"] % 4]

    # each layer names its transform FGM explicitly rather than us pairing by sort order
    assert all(L["params"] for L in layers), \
        [L["index"] for L in layers if not L["params"]]
    assert layers[0]["transform_fgm"], layers[0]

    # a swatch uses the SAME array index in all four slots, so one index identifies a swatch
    for L in layers:
        vals = set(L["slices"].values())
        assert len(vals) == 1, (L["swatch"], L["slices"])

    # every layer's height and diffuse slice must be on disk, or extract_swatches.py has not run
    missing = [L["swatch"] for L in layers if not height_png(L)]
    assert not missing, f"height PNGs missing for {sorted(set(missing))}"
    assert slice_png(layers[0], "pDiffuseTexture"), "diffuse slice not found"
    assert slice_png(layers[0], "pPackedTexture", "_RGB"), "packed RGB slice not found"
    print("selftest ok - 16 layers, all resolved, all height slices on disk")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        selftest()
        print()
        summarise("Spinosaurus", "Female")
