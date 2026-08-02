"""Discover a species' mesh parts and de-interleave its cosmetic manifests.

`.dinosaurmaterialpatterns` and `.dinosaurmaterialvariants` are NOT lists of patterns or variants.
They are flat lists of (logical index x mesh part), with the parts interleaved:

    Pyroraptor  pattern_count=12
        Pattern_01_00, FeathersPattern_01_00, Pattern_01_01, FeathersPattern_01_01, ...

so the stride is the number of parts, and the body<->feather (or body<->quills) pairing is stated
explicitly in the file. Never re-derive it from filenames. An entry with has_ptr="0" is a null --
the Blank Pattern -- and has NO FGM behind it; there is no _06.fgm standing in for it. Some species
have no null at all (Pyroraptor).

The part token is NOT in a fixed position: it is an infix on Pyroraptor (FeathersPattern) and a
suffix on Psittacosaurus (_Quills). Parse around the invariant core instead.

Feathers and quills are ONE tier -- psittacosaurus_female_quills.fgm is the same
DinosaurFeathers_ClipDoubleSided shader as pyroraptor_feathers.fgm, and both carry their own
144-attribute variant paired 1:1 with the body. Only the part token differs.
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

#  <prefix>_ [<part>] (Pattern|Variant) _<set>_<index> [_<part>]
#
#  The set token is NOT always numeric: IndominusRex ships `IndominusRex_Pattern_Lux_00`.
#  The index is always numeric, which is what keeps the suffix unambiguous.
_CORE = re.compile(r"^(?P<prefix>.*?)(?P<infix>[A-Za-z]*)(?:Pattern|Variant)"
                   r"_(?P<set>[A-Za-z0-9]+)_(?P<index>\d+)(?:_(?P<suffix>[A-Za-z]+))?$")


def split_part(name):
    """`Pyroraptor_FeathersPattern_01_00` -> ('Pyroraptor_01_00', 'Feathers').

    Returns (core, part); part is '' for the body. The core is stable across parts, so two names
    with the same core are the same logical cosmetic on different meshes.
    """
    m = _CORE.match(name)
    if not m:
        raise ValueError(f"unparseable cosmetic name: {name!r}")
    prefix = m.group("prefix").rstrip("_")
    part = m.group("infix") or m.group("suffix") or ""
    core = f"{prefix}_{m.group('set')}_{m.group('index')}"
    return core, part


class Manifest:
    def __init__(self, count, parts, slots):
        self.count = count      # raw entry count, INCLUDING nulls
        self.parts = parts      # ordered, '' (the body) first
        self.slots = slots      # list of {part: fgm base name or None}

    def __repr__(self):
        return f"<Manifest count={self.count} parts={self.parts} slots={len(self.slots)}>"


def parse_manifest(path):
    root = ET.parse(path).getroot()
    pool = root.find("patterns")
    if pool is None:
        pool = root.find("variants")
    if pool is None:
        pool = next((c for c in root if len(c)), None)
    if pool is None:
        raise ValueError(f"no entry pool in {path}")

    entries = []                        # (core, part, original_name) or None for a null
    for e in pool:
        if e.get("has_ptr") == "0":
            entries.append(None)
            continue
        name_el = next((c for c in e if c.tag.endswith("_name")), None)
        if name_el is None or not (name_el.text or "").strip():
            entries.append(None)
            continue
        original = name_el.text.strip()
        core, part = split_part(original)
        entries.append((core, part, original))

    parts, order = [], {}
    for ent in entries:
        if ent and ent[1] not in order:
            order[ent[1]] = len(parts)
            parts.append(ent[1])
    if "" in parts:                     # body first, then the rest in first-seen order
        parts.sort(key=lambda p: (p != "", order[p]))
    if not parts:
        parts = [""]

    slots, by_core = [], {}
    for ent in entries:
        if ent is None:
            slots.append({p: None for p in parts})
            continue
        core, part, original = ent
        if core not in by_core:
            by_core[core] = {p: None for p in parts}
            slots.append(by_core[core])
        # store what the manifest ACTUALLY said -- never reconstruct it from (core, part),
        # which cannot round-trip an infix and a suffix through one rule
        by_core[core][part] = original

    return Manifest(len(entries), parts, slots)


def resolve_texture(dep_name, local_dir, library_dir):
    """A texture named by `<dependency_name>`: the model's own folder wins, the shared
    DinosaurFur/ library is the fallback.

    Matching is case-insensitive -- loader keys are lowercase while the pointers are mixed case,
    the same trap layer_chain.py hit. The extension is ignored because the FGM names a `.tex`
    while what is on disk after extraction is one or more `.png`.
    """
    want = os.path.basename(dep_name).lower()
    stem = os.path.splitext(want)[0]
    # `library_dir` may be one folder or several -- the user can configure more than one shared
    # library, and `fur_library_dirs` returns them in priority order.
    libs = [library_dir] if isinstance(library_dir, str) else list(library_dir or ())
    for d in [local_dir] + libs:
        if not d or not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            fl = f.lower()
            if fl == want or os.path.splitext(fl)[0] == stem:
                return os.path.join(d, f)
    return None


CHANNEL_SUFFIXES = ("R", "G", "B", "A", "RG", "RGB", "RGBA")


def fgm_slots(fgm_path):
    """{slot name: dependency_name or None} for every `<textureinfo>` in a .fgm.

    `None` marks an INLINE RGBA placeholder -- the slot exists and the shader reads it, but the
    material supplies a constant colour instead of a file. Those are common: `pyroraptor.fgm`
    declares ten slots and names files for only four.
    """
    root = ET.parse(fgm_path).getroot()
    out = {}
    for t in root.iter("textureinfo"):
        dep = t.find("dependency_name")
        text = (dep.text or "").strip() if dep is not None else ""
        out[t.get("name")] = text or None
    return out


def texture_files(dep_name, *dirs):
    """{channel: path} for the PNGs cobra-tools extracted from one named `.tex`.

    Matching follows cobra-tools' own importer (`modules_import/material.py:
    build_tex_nodes_dict.is_part_of_tex`): take the dependency's stem and accept any file whose
    name starts with `<stem>.` or `<stem>_`. The trailing token after `<stem>_` is the channel
    split, so `pyroraptor.pbasenormaltexture.tex` finds
    `pyroraptor.pbasenormaltexture_RG.png` as `{"RG": ...}` and a bare
    `<stem>.png` as `{"": ...}`.

    Requiring the separator is what stops `pdiffuse` from swallowing `pdiffusemelanistic` --
    a plain `startswith(stem)` matches both.

    `dirs` are searched in order and the first directory that yields anything wins, so a
    species-local override beats the shared library. This is deliberately name-driven and
    prefix-agnostic: one material can legitimately mix prefixes, and Pyroraptor's feathers do
    (`pyroraptor_feathers.pdinosaurfeathers_basediffusetexture` alongside
    `feathers.pfeathers_normaltexture`), so no rule built from the species name can resolve both.
    """
    if not dep_name:
        return {}
    stem = os.path.splitext(os.path.basename(dep_name))[0].lower()
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        found = {}
        for f in sorted(os.listdir(d)):
            s, e = os.path.splitext(f)
            if e.lower() != ".png":
                continue
            sl = s.lower()
            if sl == stem:
                found[""] = os.path.join(d, f)
            elif sl.startswith(stem + "_"):
                tail = s[len(stem) + 1:]
                if tail.upper() in CHANNEL_SUFFIXES:
                    found[tail.upper()] = os.path.join(d, f)
        if found:
            return found
    return {}


FUR_LIBRARY_DIR = "DinosaurFur"
PART_FGM_TOKEN = {"Feathers": "feathers", "Quills": "quills"}


def part_base_fgm(species_dir, part):
    """The part's own base `.fgm` in a species folder, or None.

    Prefers the species-prefixed file (`pyroraptor_feathers.fgm`) over the bare shared one
    (`feathers.fgm`), because only the prefixed one NAMES the species' textures --
    `feathers.fgm` leaves `pDinosaurFeathers_BaseDiffuseTexture` as an inline RGBA placeholder,
    so building from it silently loses the body-space colour map.
    """
    token = PART_FGM_TOKEN.get(part)
    if not token or not species_dir or not os.path.isdir(species_dir):
        return None
    bare = None
    for f in sorted(os.listdir(species_dir)):
        fl = f.lower()
        if not fl.endswith(".fgm"):
            continue
        if fl == f"{token}.fgm":
            bare = os.path.join(species_dir, f)
        elif fl.endswith(f"_{token}.fgm"):
            return os.path.join(species_dir, f)
    return bare


def fur_library_dirs(species_dir=None):
    """Every folder to search for shared feather-card textures, best first.

        1. `DinosaurFur` found by walking UP from the species folder -- where an extraction
           naturally puts it, so the common case needs no configuration at all;
        2. whatever the user configured as `fur_library` (add-on preferences / `setup_gui.py` /
           `JWE3_FUR_LIBRARY`), which may list SEVERAL folders separated by os.pathsep.

    The card textures (`feathers.pfeathers_*`) are shared by every feathered species and are usually
    ALSO copied into the species folder, so all of this is a fallback -- `resolve_texture` checks
    the local folder first regardless.
    """
    out = []
    d = os.path.abspath(species_dir or "")
    seen = set()
    while species_dir and d and d not in seen and os.path.isdir(d):
        seen.add(d)
        cand = os.path.join(d, FUR_LIBRARY_DIR)
        if os.path.isdir(cand):
            out.append(cand)
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    try:
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if pkg not in sys.path:
            sys.path.insert(0, pkg)
        import jwe3_config
        out.extend(jwe3_config.get_dirs("fur_library"))
    except Exception:
        pass
    uniq, got = [], set()
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in got:
            got.add(k)
            uniq.append(p)
    return uniq


def fur_library(species_dir):
    """The single best shared-library folder, or None. `fur_library_dirs` is the full list."""
    dirs = fur_library_dirs(species_dir)
    return dirs[0] if dirs else None


def _roots():
    raw = os.environ.get("JWE3_DINO_ROOTS", "")
    return [r for r in raw.split(os.pathsep) if r.strip() and os.path.isdir(r)]


def _find(basename):
    """First file with this basename under any configured root. Selftest fixture only --
    runtime callers pass explicit paths."""
    for root in _roots():
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.lower() == basename.lower():
                    return os.path.join(dirpath, f)
    return None


def selftest():
    # --- the name parser handles infix AND suffix part tokens ---
    assert split_part("Lokiceratops_Pattern_01_00") == ("Lokiceratops_01_00", "")
    assert split_part("Pyroraptor_FeathersPattern_01_00") == ("Pyroraptor_01_00", "Feathers")
    assert split_part("Psittacosaurus_Female_Pattern_01_00_Quills") == \
        ("Psittacosaurus_Female_01_00", "Quills")
    assert split_part("Pyroraptor_FeathersVariant_01_03") == ("Pyroraptor_01_03", "Feathers")
    # the set token is not always numeric
    assert split_part("IndominusRex_Pattern_Lux_00") == ("IndominusRex_Lux_00", "")

    # --- fgm_slots / texture_files: name-driven texture resolution, cobra-tools' rule ---
    #     A prefix-built filename is wrong in general. Pyroraptor's feathers material names files
    #     under TWO prefixes at once, so nothing derived from the species name resolves both.
    fgm = _find("pyroraptor.fgm")
    if fgm:
        d = os.path.dirname(fgm)
        slots = fgm_slots(fgm)
        assert slots.get("pBaseDiffuseTexture") == "pyroraptor.pbasediffusetexture.tex", slots
        # a declared-but-fileless slot must read as an inline placeholder, not as missing
        assert "pBaseTransmissionTexture" in slots and slots["pBaseTransmissionTexture"] is None
        assert texture_files(slots["pBaseDiffuseTexture"], d).get("") , "bare .png not found"
        # one .tex can extract to several channel files -- the normal map gives _RG (the two
        # channels the shader uses) alongside a separate _A and _B. Ask for the one you want.
        nrm = texture_files(slots["pBaseNormalTexture"], d)
        assert "RG" in nrm and set(nrm) <= set(CHANNEL_SUFFIXES) | {""}, nrm
        assert texture_files(None, d) == {}
        # the separator guard: `pdiffuse` must not swallow `pdiffusemelanistic`
        for ch, p in texture_files("x.pbasediffusetexture.tex", d).items():
            base = os.path.basename(p).lower()
            assert not base.startswith("x.pbasediffusetexturemelanistic"), base
    feath = _find("pyroraptor_feathers.fgm")
    if feath:
        d = os.path.dirname(feath)
        fs = fgm_slots(feath)
        # the two-prefix case, in one material
        assert fs["pDinosaurFeathers_BaseDiffuseTexture"].startswith("pyroraptor_feathers."), fs
        assert fs["pFeathers_NormalTexture"].startswith("feathers."), fs
        assert texture_files(fs["pDinosaurFeathers_BaseDiffuseTexture"], d)
        assert texture_files(fs["pFeathers_NormalTexture"], d)

    if not _roots():
        raise SystemExit(
            "selftest needs the extracted dinosaur trees. Set JWE3_DINO_ROOTS to one or more\n"
            "directories separated by %r, e.g.\n"
            r"  ...\Personal Mods\JWE3\Images and Models\Dinosaurs" "\n"
            r"  ...\Dinosaur Files\Variant Research\Textures" % os.pathsep)

    def man(basename):
        p = _find(basename)
        if not p:
            raise SystemExit(f"selftest could not find {basename} under JWE3_DINO_ROOTS")
        return parse_manifest(p)

    # Lokiceratops: 6 patterns + a null. The baseline, single-part.
    lo = man("lokiceratops_patternset_01.dinosaurmaterialpatterns")
    assert lo.count == 7 and lo.parts == [""], (lo.count, lo.parts)
    assert len(lo.slots) == 7 and lo.slots[6][""] is None, "null slot not represented"
    assert lo.slots[0][""] == "Lokiceratops_Pattern_01_00"

    # Psittacosaurus: 6 x 2 + a null. Breaks "stride is 1".
    ps = man("psittacosaurus_female_patternset_01.dinosaurmaterialpatterns")
    assert ps.parts == ["", "Quills"], ps.parts
    assert len(ps.slots) == 7, len(ps.slots)
    assert ps.slots[0]["Quills"] == "Psittacosaurus_Female_Pattern_01_00_Quills"
    assert ps.slots[6][""] is None and ps.slots[6]["Quills"] is None

    # Pyroraptor: 6 x 2 with NO null. Breaks "there is always a blank".
    py = man("pyroraptor_patternset_01.dinosaurmaterialpatterns")
    assert py.count == 12 and py.parts == ["", "Feathers"], (py.count, py.parts)
    assert len(py.slots) == 6, len(py.slots)
    assert all(s[""] is not None and s["Feathers"] is not None for s in py.slots), "null invented"
    assert py.slots[3]["Feathers"] == "Pyroraptor_FeathersPattern_01_03"

    # Indominus rex: 7 + a null. Breaks "six patterns".
    ind = man("indominusrex_patternset_01.dinosaurmaterialpatterns")
    assert len(ind.slots) == 8 and ind.slots[7][""] is None, len(ind.slots)

    # variants use the same shape -- Pyroraptor is 12 logical x 2 parts
    pv = man("pyroraptor_variantset_01.dinosaurmaterialvariants")
    assert pv.count == 24 and len(pv.slots) == 12 and pv.parts == ["", "Feathers"], \
        (pv.count, len(pv.slots), pv.parts)

    # --- texture resolution. Psittacosaurus' quills FGM names BOTH shared-library textures
    #     (feathers.*) and local overrides (psittacosaurus_female_quills.*), so it exercises
    #     both branches. Pyroraptor uses only the library branch and would pass even if
    #     local-first precedence were broken. ---
    quills = _find("psittacosaurus_female_quills.fgm")
    lib = _find("feathers.pfeathers_basecolourtexture.png")
    if quills and lib:
        loc, lib = os.path.dirname(quills), os.path.dirname(lib)
        got = resolve_texture("feathers.pfeathers_basecolourtexture.tex", loc, lib)
        assert got and os.path.dirname(got) == lib, f"library fallback failed: {got}"
        got = resolve_texture("PSITTACOSAURUS_FEMALE_QUILLS.PFEATHERS_NORMALTEXTURE.TEX", loc, lib)
        assert got and os.path.dirname(got) == loc, f"case-insensitive local-first failed: {got}"
        assert resolve_texture("no_such_texture.tex", loc, lib) is None
    else:
        print("  (skipped resolve_texture: quills FGM or DinosaurFur library not under the roots)")

    print("selftest ok")


if __name__ == "__main__":
    selftest()
