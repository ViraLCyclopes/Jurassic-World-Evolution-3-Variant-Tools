"""Build a CONTIGUOUS-RUN seed sweep across many host species, for cracking the seed generator.

WHY THIS IS DIFFERENT FROM v1. The first sweep put twelve seeds on the twelve variants of one
species. That was wrong twice over: the game keeps only ~1 material block per species resident, so
one capture yielded one block; and coverage of arbitrary shipped seeds is the wrong goal anyway.

The goal now is IDENTIFICATION, not coverage. `gradOffset`/`gradAmplitude`/`gradPhase` are a
function of the seed alone (an 8-bit input) that looks like packed PRNG output, and a generator is
exposed by feeding it CONSECUTIVE inputs -- the step between seed N and seed N+1 is what reveals its
structure, and scattered samples hide it. So this stages a run of consecutive seeds, one per host
species, and relies on the measured fact that harvest yield tracks SPECIES DIVERSITY: one animal of
each host on screen gives ~one block per host.

DESIGN.
  * One seed per host OVL, written to ALL TWELVE of its variants. Whichever variant the game
    surfaces then carries the seed we wanted -- no need to control variant selection.
  * Every variant is flattened to one template so the seed is the only thing that differs; the
    template's complexity is fixed, so gradFreq is constant across the whole sweep and the three
    seed-only coefficients are isolated.
  * Each host gets a UNIQUE brightness/saturation fingerprint, checked against `all_seeds.json` so
    it collides with none of the shipped variants. This is what lets `harvest_blocks` tell the
    blocks apart -- without it every block is ambiguous across every seed (the v1 trap).

Small Male/Juvenile OVLs (~0.15 MB) are valid hosts: verified to contain all twelve variant FGMs,
and they pack in a fraction of the time a 2 MB Female takes.

    python gen_seedsweep_v2.py            # build + install (backs up originals first)
    python gen_seedsweep_v2.py --selftest # validate the plan, touch no game file
"""
import json
import logging
import os
import shutil
import struct
import sys
import time

REPO = r"d:\JWE2 Stuff\Cobra Tool Versions\Main Mod Kit\cobra-tools-master"
GAME = "Jurassic World Evolution 3"
HERE = os.path.dirname(os.path.abspath(__file__))
import _hpaths  # noqa: E402  (package paths; puts the package + vendor/ on sys.path)
RESEARCH = _hpaths.PKG                                   # for the vendored module imports below
KIT = os.environ.get("JWE3_BACKUP_DIR") or _hpaths.backup_root()


def find_dinodir():
    """The game's `...\\ovldata\\Content0\\Dinosaurs`, from the shared config.

    Resolution (env -> config file -> Steam's own library list on every drive) lives in
    `jwe3_config`, so this script, the editor and the Blender add-on all agree on which install is
    being modded. Set it once with `python setup_gui.py`; hard-coding a `C:\\Program Files (x86)\\`
    path only ever worked on one machine.
    """
    sys.path.insert(0, os.path.dirname(HERE))          # VariantEditor/
    import jwe3_config
    ovldata = jwe3_config.get("game_dir")
    if not ovldata:
        return None
    dino = os.path.join(ovldata, "Content0", "Dinosaurs")
    return dino if os.path.isdir(dino) else None


DINODIR = find_dinodir() or ""

MANIFEST = _hpaths.manifest()
SEED_TABLE = _hpaths.seed_table()
FINGERPRINTS = _hpaths.FINGERPRINTS          # ships with the package (read-only input table)

TEMPLATE_SPECIES = "Spinosaurus"
TEMPLATE_REALM = "Land"
TEMPLATE_VARIANT = "variant_01_07.fgm"

SEED_LO, SEED_HI = 0, 63          # the contiguous run to complete
FINGERPRINT_FIELDS = ("u_globalColourBrightnessBase", "u_globalColourBrightnessPalette",
                      "u_globalColourSaturationBase", "u_globalColourSaturationPalette")


def _cobra():
    sys.path.insert(0, REPO)
    if not hasattr(logging, "success"):
        logging.success = lambda *a, **k: None
    logging.disable(logging.WARNING)
    from utils.config import Config
    from generated.formats.ovl import OvlFile
    return Config, OvlFile


def species_ovl(species, sex, realm):
    return os.path.join(DINODIR, realm, species, sex, f"{species}_{sex}.ovl")


def open_ovl(path, Config, OvlFile):
    ovl = OvlFile()
    cfg = Config(REPO)
    cfg.load()
    ovl.cfg = cfg
    ovl.game = GAME
    ovl.load_hash_table()
    ovl.load(path, {"game": GAME})
    return ovl


def variant_loaders(ovl):
    out = [(n, l) for n, l in ovl.loaders.items()
           if n.endswith(".fgm") and getattr(l.header, "shader_name", "") == "DinosaurLayered_Variant"]
    out.sort(key=lambda t: t[0])
    return out


def param_dict(loader):
    a = loader.header.attributes.data
    v = loader.header.value_foreach_attributes.data
    return {a[i].name: [x for x in v[i].value] for i in range(len(a))}


def apply(loader, seed, template, fingerprint):
    a = loader.header.attributes.data
    v = loader.header.value_foreach_attributes.data
    fp = dict(zip(FINGERPRINT_FIELDS, fingerprint))
    for i in range(len(a)):
        name = a[i].name
        if name == "u_globalPaletteSeed":
            v[i].value[0] = seed
        elif name in fp:
            v[i].value[0] = fp[name]
        elif name in template:
            src = template[name]
            for j in range(len(v[i].value)):
                v[i].value[j] = src[j]
    loader.write_memory_data()


def enumerate_hosts():
    """[(species, sex, realm, size)] for every dinosaur OVL, smallest first."""
    hosts = []
    for realm in ("Land", "Air", "Marine"):
        base = os.path.join(DINODIR, realm)
        if not os.path.isdir(base):
            continue
        for sp in sorted(os.listdir(base)):
            for sex in ("Female", "Male", "Juvenile"):
                p = species_ovl(sp, sex, realm)
                if os.path.isfile(p):
                    hosts.append((sp, sex, realm, os.path.getsize(p)))
    hosts.sort(key=lambda h: h[3])
    return hosts


def plan():
    """(seeds, hosts, fingerprints) or raises. Never touches a game file."""
    sys.path.insert(0, RESEARCH)
    import export_palette as ep
    held = set(ep.by_seed())
    seeds = sorted(set(range(SEED_LO, SEED_HI + 1)) - held)
    hosts = enumerate_hosts()
    fps = json.load(open(FINGERPRINTS))
    if len(hosts) < len(seeds):
        raise RuntimeError(f"only {len(hosts)} host OVLs for {len(seeds)} seeds")
    if len(fps) < len(seeds):
        raise RuntimeError(f"only {len(fps)} fingerprints for {len(seeds)} seeds")
    # the template species itself must not be a host, or we would overwrite our own template source
    hosts = [h for h in hosts if h[0] != TEMPLATE_SPECIES]
    return seeds, hosts[:len(seeds)], fps[:len(seeds)]


def main():
    seeds, hosts, fps = plan()
    Config, OvlFile = _cobra()

    tovl = open_ovl(species_ovl(TEMPLATE_SPECIES, "Female", TEMPLATE_REALM), Config, OvlFile)
    tl = [l for n, l in variant_loaders(tovl) if n.endswith(TEMPLATE_VARIANT)]
    if not tl:
        sys.exit("template variant not found")
    template = param_dict(tl[0])
    print(f"template: {TEMPLATE_SPECIES} {TEMPLATE_VARIANT}, {len(template)} params")
    print(f"staging {len(seeds)} consecutive seeds {seeds[0]}..{seeds[-1]} onto {len(hosts)} hosts\n")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(KIT, "Backup_SeedSweepV2_" + stamp)
    os.makedirs(backup, exist_ok=True)

    manifest = {"template": [TEMPLATE_SPECIES, TEMPLATE_VARIANT], "backup": backup, "hosts": []}
    seed_rows = []
    for seed, (sp, sex, realm, _sz), fp in zip(seeds, hosts, fps):
        live = species_ovl(sp, sex, realm)
        bak = os.path.join(backup, f"{sp}_{sex}.ovl")
        shutil.copy2(live, bak)                       # back up BEFORE modifying

        ovl = open_ovl(live, Config, OvlFile)
        vls = variant_loaders(ovl)
        # Most species have 12 variant FGMs; a few (Velociraptor, Coelophysis, ...) have 24 across
        # two cosmetic sets. Apply the seed to ALL of them -- whichever one the game surfaces then
        # carries it. Only bail if a host has fewer than 12, which would mean a malformed OVL.
        if len(vls) < 12:
            print(f"  SKIP {sp} {sex}: only {len(vls)} variants")
            continue
        for _n, l in vls:
            apply(l, seed, template, fp)
        ovl.save(live, commands={"update_aux": True})

        manifest["hosts"].append({"species": sp, "sex": sex, "realm": realm,
                                  "seed": seed, "fingerprint": [float(x) for x in fp],
                                  "ovl": live, "backup": bak})
        seed_rows.append({
            "ovl": f"{sp}_{sex}.ovl", "fgm": f"{sp.lower()}_variant_01_00.fgm",
            "u_globalColourBrightnessBase": float(fp[0]),
            "u_globalColourBrightnessPalette": float(fp[1]),
            "u_globalColourSaturationBase": float(fp[2]),
            "u_globalColourSaturationPalette": float(fp[3]),
            "u_globalColourRotationOffsetBase": float(template["u_globalColourRotationOffsetBase"][0]),
            "u_globalColourRotationOffsetPalette": float(template["u_globalColourRotationOffsetPalette"][0]),
            "u_globalPaletteSeed": int(seed),
            "u_globalPaletteMaximumComplexity": int(round(template["u_globalPaletteMaximumComplexity"][0])),
            "u_instancePaletteOffset": float(template["u_instancePaletteOffset"][0]),
            "u_instancePaletteScale": float(template["u_instancePaletteScale"][0]),
            "u_instancePaletteStrength": float(template["u_instancePaletteStrength"][0]),
            "u_globalKeyType": 0,
        })
        print(f"  seed {seed:>3} -> {sp} {sex}  ({realm})")

    # verify every seed read back from the live file
    print("\nverifying...")
    ok = True
    for h in manifest["hosts"]:
        ovl = open_ovl(h["ovl"], Config, OvlFile)
        got = {int(round(param_dict(l)["u_globalPaletteSeed"][0])) for _n, l in variant_loaders(ovl)}
        if got != {h["seed"]}:
            print(f"  MISMATCH {h['species']} {h['sex']}: wrote {h['seed']}, read {sorted(got)}")
            ok = False
    print("  all seeds verified" if ok else "  VERIFICATION FAILED")

    json.dump(manifest, open(MANIFEST, "w"), indent=1)
    json.dump(seed_rows, open(SEED_TABLE, "w"), indent=1)
    print(f"\nmanifest   -> {MANIFEST}")
    print(f"seed table -> {SEED_TABLE}  ({len(seed_rows)} rows)")
    print(f"backup     -> {backup}")
    if not ok:
        sys.exit("do not capture against this build")
    print(f"\n{len(seed_rows)} seeds staged. Spawn ONE animal of each host species, then capture.")
    print("Restore afterwards with: python gen_seedsweep_v2.py --restore")


def restore():
    man = json.load(open(MANIFEST))
    n = 0
    for h in man["hosts"]:
        if os.path.isfile(h["backup"]):
            shutil.copy2(h["backup"], h["ovl"])
            n += 1
    print(f"restored {n} host OVLs from {man['backup']}")


def stage_females(only_missing=True):
    """Stage the _Female OVL of each sweep species, with update_aux=True. GAME MUST BE CLOSED.

    For species whose MALE renders the female material FGM (the male's variant_01 prefab references
    the female's shared FGM -- Dilophosaurus is one), editing the _Male OVL has no visible effect.
    Staging the _Female OVL fixes it, and spawning the base (female) species then renders the seed.

    MUST use update_aux=True. `update_aux=False` avoids the running game's .aux lock and round-trips
    fine through cobra-tools, but the GAME CRASHES on load -- the aux ends up inconsistent. So this
    only works with the game closed. Verified the hard way 2026-07-25.
    """
    import export_palette as ep
    Config, OvlFile = _cobra()
    # confirm the game is closed by trying to open a Female aux for writing
    man = json.load(open(MANIFEST))
    males = [h for h in man["hosts"] if h["sex"] == "Male"]
    held = set(ep.by_seed())
    todo = [h for h in males if (not only_missing or h["seed"] not in held)]
    print(f"staging {len(todo)} Female OVLs with update_aux=True (game MUST be closed)\n")

    tovl = open_ovl(species_ovl(TEMPLATE_SPECIES, "Female", TEMPLATE_REALM), Config, OvlFile)
    tmpl = param_dict([l for n, l in variant_loaders(tovl) if n.endswith(TEMPLATE_VARIANT)][0])
    have = {(h["species"], "Female") for h in man["hosts"] if h["sex"] == "Female"}
    staged, skipped = 0, []
    for h in todo:
        sp, realm, seed, fp = h["species"], h["realm"], h["seed"], h["fingerprint"]
        if (sp, "Female") in have:
            continue
        fem = species_ovl(sp, "Female", realm)
        if not os.path.isfile(fem):
            skipped.append(sp + "(noF)")
            continue
        bak = os.path.join(man["backup"], f"{sp}_Female.ovl")
        if not os.path.isfile(bak):
            shutil.copy2(fem, bak)
        try:
            ovl = open_ovl(fem, Config, OvlFile)
            vls = variant_loaders(ovl)
            if len(vls) < 12:
                skipped.append(f"{sp}({len(vls)}v)")   # variants live in the male OVL; male is right
                continue
            for _n, l in vls:
                apply(l, seed, tmpl, fp)
            ovl.save(fem, commands={"update_aux": True})
            man["hosts"].append({"species": sp, "sex": "Female", "realm": realm, "seed": seed,
                                 "fingerprint": fp, "ovl": fem, "backup": bak})
            staged += 1
            print(f"  seed {seed:>3} -> {sp} Female")
        except PermissionError:
            sys.exit(f"\nPERMISSION DENIED on {sp} -- the game is still running. Close it and retry.")
        except Exception as e:
            skipped.append(f"{sp}({type(e).__name__})")
    json.dump(man, open(MANIFEST, "w"), indent=1)
    print(f"\nstaged {staged} Female OVLs; skipped (male OVL is the right target for these): {skipped or 'none'}")


def selftest():
    seeds, hosts, fps = plan()
    assert seeds == sorted(seeds), "seeds must be sorted"
    # the run must be genuinely contiguous once merged with what we hold
    sys.path.insert(0, RESEARCH)
    import export_palette as ep
    after = sorted(set(ep.by_seed()) | set(seeds))
    run = cur = 1
    for a, b in zip(after, after[1:]):
        cur = cur + 1 if b == a + 1 else 1
        run = max(run, cur)
    assert run >= (SEED_HI - SEED_LO + 1), f"expected a run of {SEED_HI-SEED_LO+1}, got {run}"
    assert len(hosts) == len(seeds) == len(fps), (len(hosts), len(seeds), len(fps))
    assert len({(h[0], h[1]) for h in hosts}) == len(hosts), "duplicate host"
    assert all(h[0] != TEMPLATE_SPECIES for h in hosts), "template species used as host"
    # fingerprints must be internally distinct
    def key(fp):
        f16 = lambda x: struct.unpack("<H", struct.pack("<e", x))[0]
        return (f16(fp[0]) | (f16(fp[1]) << 16), f16(fp[2]) | (f16(fp[3]) << 16))
    assert len({key(f) for f in fps}) == len(fps), "duplicate fingerprint"
    print(f"selftest ok - {len(seeds)} seeds {seeds[0]}..{seeds[-1]}, "
          f"{len(hosts)} hosts, run after harvest {run}, "
          f"{len(ep.by_seed()) + len(seeds)}/256 held")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--restore" in sys.argv:
        restore()
    elif "--females" in sys.argv:
        stage_females()
    else:
        main()
