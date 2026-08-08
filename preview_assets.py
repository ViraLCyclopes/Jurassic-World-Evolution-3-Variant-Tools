"""Where a species' preview textures and layer definition live.

Split out of `variant_editor.py` so BOTH sides can use it: the PyQt5 app (outside Blender) and the
Blender add-on's `File > Import > JWE3 Variant (.fgm)` operator (inside Blender). Nothing here may
import PyQt5 or bpy -- Blender has no PyQt5, and the app has no bpy.

Conventions discovered from the existing research folder, not invented:

    mask_dir     ../Textures/<Species>/              e.g. .../Textures/Baryonyx
    mask_prefix  <species lowercased>                e.g. "baryonyx", giving
                                                     baryonyx.playered_blendweights_[00]_A.png
    layers_json  ../LayerJSON/<Species>_<Sex>.json   from: python ../export_layers.py <Species>

Run:  python preview_assets.py   -> selftest ok
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.join(HERE, "vendor")           # the vendored research modules, inside the package
# Package-local, so the software owns everything it needs. `Textures/` is only a last-resort
# fallback -- masks normally come from the folder the model or the .fgm was imported from.
TEXTURES_DIR = os.path.join(HERE, "Textures")
LAYERJSON_DIR = os.path.join(HERE, "LayerJSON")
DINO_FILES_DIR = os.path.dirname(os.path.dirname(HERE))   # a sane start dir for file dialogs


def _layerjson_index():
    """[(species_keys, sex, path)] built by READING each LayerJSON, not by parsing filenames.

    WHY NOT FILENAMES. The old lookup matched `<species>_` as a filename prefix, which breaks on
    every multi-token species: the editor's box reads `Herrerasaurus_Male`, so the prefix search
    wants `herrerasaurus_male_*` and a perfectly good `Herrerasaurus_Male.json` is never found.
    It also silently matched the WRONG species -- `Baryonyx_Female.json` satisfies a lookup for
    "Bary". Both failures end the same way: no colour-weight map, so the preview grades teeth and
    tongues (see docs/LAYERJSON.md).

    Every file already carries `species` and `sex` inside it -- `export_layers` has always written
    them -- so this needs no regeneration. `aliases` is optional and lets one file answer to
    several names (hybrids, renamed mods).

    Cached on the directory's mtime: this runs on every texture refresh.
    """
    global _LJ_CACHE
    dirs = layerjson_dirs()
    if not dirs:
        return []
    # Cache on EVERY searched folder's mtime, not just one -- generating into the user folder must
    # invalidate the index even though the shipped folder has not changed.
    stamp = tuple((d, os.path.getmtime(d)) for d in dirs)
    cached = globals().get("_LJ_CACHE")
    if cached and cached[0] == stamp:
        return cached[1]

    out, claimed = [], set()
    for directory in dirs:            # user folders first; the shipped baseline last
        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(directory, name)
            keys, sex, species = set(), None, None
            try:
                with open(path, encoding="utf-8") as fh:
                    d = json.load(fh)
                if isinstance(d, dict):
                    if d.get("species"):
                        species = str(d["species"]).strip()
                        keys.add(species.lower())
                    for a in d.get("aliases") or ():
                        keys.add(str(a).strip().lower())
                    sex = (d.get("sex") or None)
            except (OSError, ValueError):
                pass                  # unreadable file: fall back to its NAME below, never raise
            if not keys:
                stem = os.path.splitext(name)[0]
                keys.add(stem.lower())
                species = species or stem
                if "_" in stem:       # legacy "<Species>_<Sex>.json" with no metadata inside
                    head, tail = stem.rsplit("_", 1)
                    keys.add(head.lower())
                    species = head
                    sex = sex or tail
            # A user file SHADOWS a shipped one for the same species+sex: same species regenerated
            # from your own extraction is the answer you want, not the one we happened to ship.
            token = (frozenset(keys), (sex or "").lower())
            if token in claimed:
                continue
            claimed.add(token)
            out.append((keys, (sex or "").lower(), path, species or ""))
    globals()["_LJ_CACHE"] = (stamp, out)
    return out


def layerjson_dirs():
    """Every folder searched for LayerJSONs, highest priority first.

    The configured per-user folder(s) come first and the folder shipped inside the package last, so
    a species you generate always wins over one we ship and neither has to be deleted.

    This is the fix for a whole class of silent wrong-species bugs: the add-on installed in Blender
    is a COPY of the package, so anything generated after the last `build_addon.py` was invisible
    to it. Nothing errored -- `species_from_object_name` simply matched the longest species it
    could see, and a `spinosaurusjwr` mesh resolved to "Spinosaurus".
    """
    dirs = []
    try:
        import jwe3_config
        dirs.extend(jwe3_config.get_dirs("layerjson_dir"))
    except Exception:
        pass                          # no config module (bare checkout): shipped folder only
    dirs.append(LAYERJSON_DIR)
    out, seen = [], set()
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        k = os.path.normcase(os.path.abspath(d))
        if k not in seen:
            seen.add(k)
            out.append(os.path.abspath(d))
    return out


def layers_json_for(species, sex=None):
    """The LayerJSON for a species, matched on the metadata INSIDE the files.

    Filenames are not significant -- name them whatever you like. Matching is exact and
    case-insensitive on `species` or any entry in `aliases`; a requested `sex` is preferred but a
    file for another sex is accepted rather than returning nothing, because layer and swatch
    assignment is normally shared across sexes and a Female stack still protects the mouth.
    """
    if not species:
        return None
    want = species.strip().lower()
    entries = _layerjson_index()
    hits = [e for e in entries if want in e[0]]
    if not hits:
        # Multi-token box names: `Herrerasaurus_Male` should still find a `Herrerasaurus` file.
        base = want.rsplit("_", 1)[0] if "_" in want else None
        if base:
            hits = [e for e in entries if base in e[0]]
    if not hits:
        return None
    if sex:
        for entry in hits:
            if entry[1] == sex.strip().lower():
                return entry[2]
    return hits[0][2]


MASK_MARKER = ".playered_blendweights_["


class AssetError(Exception):
    """Preview assets could not be resolved. The message says exactly which piece is missing."""


def detect_mask_prefix(folder):
    """The real mask prefix used by the files in `folder`, or None if it holds no masks.

    MUST be read off the files, never built from the species name: Baryonyx's masks are
    `baryonyx.playered_blendweights_[00]_R.png` but Psittacosaurus's are
    `psittacosaurus_female.playered_blendweights_[00]_R.png` -- the sex token is in one and not the
    other, so any name-derived guess is wrong for half the species.
    """
    if not folder or not os.path.isdir(folder):
        return None
    for name in sorted(os.listdir(folder)):
        low = name.lower()
        if low.endswith(".png") and MASK_MARKER in low:
            return name[:low.index(MASK_MARKER)]
    return None


def mask_dir_for(species):
    """Folder holding a species' masks, or None.

    Resolution order:

      1. the CONFIGURED texture folder (editor UI: Textures > Browse), stored by
         `jwe3_config.set_textures_dir`. One folder, repointed as you move between species. This is
         the supported mechanism -- texture sets are large and user-extracted, and belong wherever
         the user keeps them, not inside the install;
      2. LEGACY: `Textures/<Species>` inside the install. That folder ships EMPTY and exists only so
         an existing setup keeps working. Nothing should be copied into it any more; it can be
         dropped once no one is relying on it.

    The legacy scan matches case-insensitively, then falls back to an abbreviated folder name
    (`Loki` -> Lokiceratops, `Psittaco` -> Psittacosaurus), longest match first. Users name these
    folders by hand, so exact-match-only was too brittle.
    """
    if not species:
        return None
    try:
        import jwe3_config
        configured = jwe3_config.textures_dir()
    except Exception:
        configured = None          # config is optional; never let it break preview resolution
    if configured:
        return configured
    if not os.path.isdir(TEXTURES_DIR):
        return None
    dirs = [d for d in sorted(os.listdir(TEXTURES_DIR))
            if os.path.isdir(os.path.join(TEXTURES_DIR, d))]
    low = species.lower()
    for d in dirs:
        if d.lower() == low:
            return os.path.join(TEXTURES_DIR, d)
    best = None
    for d in dirs:
        if len(d) >= 4 and low.startswith(d.lower()) and (best is None or len(d) > len(best)):
            best = d
    return os.path.join(TEXTURES_DIR, best) if best else None


def preview_paths(species, sex=None):
    """(mask_dir, mask_prefix, layers_json) for a species, or None if it can't be previewed.

    The prefix is READ OFF THE FILES, never derived from the folder name. It used to be
    `basename(mask_dir).lower()`, which worked only while masks lived in `Textures/<Species>` --
    point it at a real extraction like `.../Baryonyx/Female` and the prefix became "female", so no
    mask matched and `build` wired nothing at all, silently. `detect_mask_prefix` existed for
    exactly this and was not being called.

    The folder name stays as a last resort, for a folder that holds a LayerJSON but no masks.
    """
    md, lj = mask_dir_for(species), layers_json_for(species, sex)
    if md is None or lj is None:
        return None
    return md, (detect_mask_prefix(md) or os.path.basename(md).lower()), lj


def generate_layers_json(species, sex="Female", folder=None):
    """Build the missing `LayerJSON/<Species>_<Sex>.json`. Returns the path, or raises AssetError.

    `folder` -- the folder you extracted the species into -- is the preferred source: it needs no
    game install, and it works for modded and custom species. It must contain the
    `.dinosaurmateriallayers` file alongside the per-layer `.fgm` files.

    Falling back to reading the game's own OVL is unreliable: the layout varies between species
    (Carnotaurus' species OVL carries no layer definition at all), which is why the folder is tried
    first whenever one is known.
    """
    import sys
    if PARENT not in sys.path:
        sys.path.insert(0, PARENT)
    import export_layers

    errors = []
    if folder and os.path.isdir(folder):
        try:
            path, _layers = export_layers.export_from_folder(folder, species, sex or "Female")
            return path
        except Exception as e:
            errors.append("from %s: %s: %s" % (folder, type(e).__name__, e))
    try:
        path, _layers = export_layers.export(species, sex or "Female")
        return path
    except Exception as e:
        errors.append("from the game install: %s: %s" % (type(e).__name__, e))

    raise AssetError(
        "could not build a LayerJSON for %s.\n  %s\n\nThe reliable fix: extract the species so that "
        "its *.dinosaurmateriallayers file sits in the SAME FOLDER as its .fgm files, and import "
        "the variant .fgm from there." % (species, "\n  ".join(errors)))


def assets_for(model_species, model_sex=None, fgm_path=None, fgm_species=None, allow_generate=True,
               model_dir=None):
    """(mask_dir, mask_prefix, layers_json) for previewing `fgm_path` on a `model_species` mesh.

    Mask folder, in order of preference:

      1. The folder the .fgm was imported from -- a species you extracted yourself sits in one
         folder (models.ms2 + its masks + its variant/layer FGMs), so that is where its textures
         are. Skipped when the .fgm belongs to a DIFFERENT species than the model, because then the
         masks must still come from the model (its UVs), not from the .fgm.
      2. `Textures/<Species>` for the model's species.

    Raises AssetError naming the missing piece rather than returning a bare None.
    """
    cross = bool(fgm_species and model_species
                 and fgm_species.lower() != model_species.lower())

    mask_dir = mask_prefix = None
    # 0. The folder the MODEL itself was imported from, when the caller could work it out. This is
    #    cobra-tools' own rule -- `create_material(reporter, in_dir, matname)` reads the .fgm and
    #    lists the .png files out of the single folder the mesh came from -- and it is right even
    #    for a cross-species preview, because it is the model's folder, not the .fgm's.
    if model_dir:
        prefix = detect_mask_prefix(model_dir)
        if prefix:
            mask_dir, mask_prefix = model_dir, prefix
    if mask_dir is None and fgm_path and not cross:
        folder = os.path.dirname(os.path.abspath(fgm_path))
        prefix = detect_mask_prefix(folder)
        if prefix:
            mask_dir, mask_prefix = folder, prefix
    if mask_dir is None:
        mask_dir = mask_dir_for(model_species)
        mask_prefix = detect_mask_prefix(mask_dir) if mask_dir else None

    if mask_dir is None or mask_prefix is None:
        where = ("the folder holding %s, nor Textures/%s" % (os.path.basename(fgm_path or "?"), model_species)
                 if fgm_path and not cross else "Textures/%s" % model_species)
        raise AssetError(
            "no blend-weight masks for %s -- looked in %s.\nExtract the species (its models.ms2, "
            "its *%sNN]_R.png masks and its FGMs all in one folder) and import the .fgm from there."
            % (model_species, where, MASK_MARKER))

    layers_json = layers_json_for(model_species, model_sex)
    if layers_json is None and allow_generate:
        # Prefer the folder the assets actually came from -- the model's own folder if we know it,
        # otherwise the .fgm's. Both normally hold the .dinosaurmateriallayers file.
        src = model_dir or (os.path.dirname(os.path.abspath(fgm_path)) if fgm_path else None)
        layers_json = generate_layers_json(model_species, model_sex, folder=src)
    if layers_json is None:
        raise AssetError("no LayerJSON for %s -- run:  python export_layers.py %s"
                         % (model_species, model_species))
    return mask_dir, mask_prefix, layers_json


def previewable_species():
    """Species that have a LayerJSON, sorted.

    A curated `Textures/<Species>` folder used to be required too, which is now wrong: masks are
    taken from the folder the model (or the .fgm) was imported from, and `Textures/` is only a
    last-resort fallback that the packaged software does not ship. Requiring it made every species
    unpreviewable in a clean install.

    Read from the LayerJSONs' own `species` field across EVERY searched folder, not from filenames.
    Splitting the filename on "_" was wrong twice over: it cannot see a user-generated species at
    all (wrong folder), and it mangles the ones it does see -- `SpinosaurusJWRExpanded_Female.json`
    became "SpinosaurusJWRExpanded" only by luck, while a legacy `Spinojwrexpanded_Female.json`
    yielded a species name that matches no mesh.

    This list is what `species_from_object_name` matches against, so a species missing here is not
    an error -- it is a silently wrong material built from some other animal's layer stack.
    """
    found = set()
    for entry in _layerjson_index():
        name = (entry[3] or "").strip()
        if name:
            found.add(name)
    return sorted(found)


def species_from_object_name(name, species_list=None):
    """Which known species an imported Blender mesh belongs to, e.g.
    `baryonyx_female_ob0_L0` -> "Baryonyx". None if it matches none of them.

    This is what lets a Spinosaurus variant be previewed on a Baryonyx: the MASKS and LayerJSON must
    follow the mesh you are looking at (its UVs and blend-weight textures), while the COLOUR comes
    from the .fgm. Longest match wins so "Spinosaurus" beats a hypothetical "Spino".
    """
    if not name:
        return None
    # Compare with separators stripped: the species is "IndominusRex" but cobra-tools names the
    # mesh "indominus_rex_female_ob0_L0", so a plain substring test never matches a multi-word
    # species.
    low = name.lower().replace("_", "").replace("-", "").replace(" ", "")
    best = None
    for sp in (species_list if species_list is not None else previewable_species()):
        if sp.lower().replace("_", "") in low and (best is None or len(sp) > len(best)):
            best = sp
    return best


def sex_from_object_name(name):
    """"Female"/"Male"/"Juvenile" from an imported mesh's name, or None."""
    low = (name or "").lower()
    for sex in ("female", "male", "juvenile"):
        if sex in low:
            return sex.capitalize()
    return None


def pick_target_object(names, species=None, sex=None):
    """Best guess at which imported mesh is the body, from a list of Blender object names.

    cobra-tools imports one object per LOD plus props, e.g. `baryonyx_female_ob0_L0` (body, LOD0),
    `..._L1`.. (lower LODs), `..._airliftstraps_ob0_L0` (a prop). The body at LOD0 is what you want,
    so score: species match, then sex match, then the `_ob0_l0` body-LOD0 tail, and prefer the
    shortest name (props add tokens). Returns None if nothing matches at all.
    """
    if not names:
        return None
    # separators stripped, so "IndominusRex" matches "indominus_rex_female_ob0_L0"
    sp = (species or "").lower().replace("_", "")
    sx = (sex or "").lower()

    def score(n):
        low = n.lower()
        flat = low.replace("_", "").replace("-", "")
        s = 0
        if sp and sp in flat:
            s += 8
        if sx and sx in low:
            s += 4
        if low.endswith("_ob0_l0"):
            s += 3
        elif "_l0" in low:
            s += 1
        return (s, -len(low))

    best = max(names, key=score)
    return best if score(best)[0] > 0 else None


def selftest():
    species = previewable_species()
    assert "Baryonyx" in species, species
    # a LayerJSON alone makes a species previewable -- the packaged software ships no Textures/
    assert os.path.isfile(layers_json_for("Baryonyx", "Female"))
    md = mask_dir_for("Baryonyx")
    if md:                      # only when a curated Textures/ fallback happens to be present
        md2, mp, lj = preview_paths("Baryonyx", "Female")
        assert os.path.isdir(md2) and os.path.isfile(lj)
        # DO NOT pin this to "baryonyx". `textures_dir` is one folder repointed as you work, not a
        # per-species map, so the fallback resolves to whatever you last pointed at -- the test then
        # failed with "spinosaurusjwr" purely because that is the folder in the config today. Third
        # time a test here has been pinned to the coverage of the day; assert the INVARIANT instead.
        assert mp and mp == detect_mask_prefix(md2), (mp, md2)
        # the prefix must match real files on disk, or a build silently wires up nothing
        assert any(f.startswith(mp + ".playered_blendweights_") for f in os.listdir(md2)), md2
    assert preview_paths("Nosuchsaurus") is None
    assert preview_paths(None) is None
    # a species with no LayerJSON for that sex still resolves via its only one
    assert layers_json_for("Baryonyx", "Male") == layers_json_for("Baryonyx", "Female")

    # target picking, against the real object names in the user's scene
    names = ["baryonyx_female_airliftstraps_ob0_L0", "baryonyx_female_ob0_L1",
             "baryonyx_female_ob0_L0", "spinosaurus_female_ob0_L0", "Cube"]
    assert pick_target_object(names, "Baryonyx", "Female") == "baryonyx_female_ob0_L0"
    assert pick_target_object(names, "Spinosaurus", "Female") == "spinosaurus_female_ob0_L0"
    assert pick_target_object(["Cube", "Lamp"], "Baryonyx", "Female") is None
    assert pick_target_object([]) is None

    # a mesh's own species/sex -- this is what keeps Baryonyx textures on the Baryonyx model even
    # when the .fgm being previewed is a Spinosaurus variant
    assert species_from_object_name("baryonyx_female_ob0_L0") == "Baryonyx"
    assert species_from_object_name("spinosaurus_female_ob0_L0") == "Spinosaurus"
    assert species_from_object_name("Cube") is None
    assert species_from_object_name(None) is None
    assert species_from_object_name("x_spino_y", ["Spino", "Spinosaurus"]) == "Spino"
    assert species_from_object_name("spinosaurus_x", ["Spino", "Spinosaurus"]) == "Spinosaurus"
    # multi-word species: cobra-tools names the mesh with underscores, the species has none
    assert species_from_object_name("indominus_rex_female_ob0_L0", ["IndominusRex"]) == "IndominusRex"
    assert species_from_object_name("indominusrex_female_ob0_L0", ["IndominusRex"]) == "IndominusRex"
    assert pick_target_object(["indominus_rex_female_ob0_L0", "Cube"], "IndominusRex", "Female") \
        == "indominus_rex_female_ob0_L0"
    assert sex_from_object_name("baryonyx_female_ob0_L0") == "Female"
    assert sex_from_object_name("baryonyx_ob0_L0") is None

    # Abbreviated texture folders must still resolve (users name these by hand). These exercise the
    # LEGACY `Textures/<Species>` scan, so the configured folder has to be out of the way -- it
    # answers for every species and would mask the thing under test. Isolate via a throwaway config
    # dir rather than clearing the real setting, which would edit the developer's own config.
    import tempfile
    _old_cfg = os.environ.get("JWE3_CONFIG_DIR")
    os.environ["JWE3_CONFIG_DIR"] = tempfile.mkdtemp()
    try:
        psi = mask_dir_for("Psittacosaurus")
        if psi:
            assert os.path.basename(psi) == "Psittaco", psi
        loki = mask_dir_for("Lokiceratops")
        if loki:
            assert os.path.basename(loki) in ("Loki", "Lokiceratops"), loki
    finally:
        if _old_cfg is None:
            os.environ.pop("JWE3_CONFIG_DIR", None)
        else:
            os.environ["JWE3_CONFIG_DIR"] = _old_cfg

    # The mask prefix MUST come off the FILES, not the species name -- Baryonyx uses "baryonyx"
    # while Psittacosaurus uses "psittacosaurus_female", so deriving it from the species is wrong.
    #
    # Assert that property directly instead of pinning the expected string: `md` is the configured
    # `textures_dir`, one folder repointed as you work, so a literal "baryonyx" only ever passed
    # while that happened to be the folder in the config.
    for folder in (md, psi):
        if not folder:
            continue
        prefix = detect_mask_prefix(folder)
        assert prefix, folder
        assert any(f.startswith(prefix + MASK_MARKER) for f in os.listdir(folder)), (prefix, folder)
        # and it is genuinely read off the files: the species name alone would not produce a
        # prefix carrying a sex token for the species that need one
        assert prefix == prefix.lower(), prefix
    assert detect_mask_prefix(LAYERJSON_DIR) is None      # a folder with no masks
    assert detect_mask_prefix(None) is None

    # resolution prefers the .fgm's own folder ...
    if psi:
        fgm_in_folder = os.path.join(psi, "psittacosaurus_female_variant_01_00.fgm")
        if os.path.isfile(fgm_in_folder):
            mdir, mpre, _lj = assets_for("Psittacosaurus", "Female", fgm_path=fgm_in_folder,
                                         fgm_species="Psittacosaurus")
            assert mdir == psi and mpre == "psittacosaurus_female", (mdir, mpre)
            # ... but NOT when the .fgm is another species: masks follow the model
            mdir2, mpre2, _ = assets_for("Baryonyx", "Female", fgm_path=fgm_in_folder,
                                         fgm_species="Psittacosaurus")
            assert mpre2 == "baryonyx", (mdir2, mpre2)

    # a species with nothing on disk raises a message naming what is missing
    try:
        assets_for("Nosuchsaurus", "Female", allow_generate=False)
    except AssetError as e:
        assert "Nosuchsaurus" in str(e)
    else:
        raise AssertionError("assets_for accepted a species with no assets")
    print("selftest ok")


if __name__ == "__main__":
    selftest()
