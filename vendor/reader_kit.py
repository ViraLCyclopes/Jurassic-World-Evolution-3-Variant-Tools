"""reader_kit -- build, install, verify and restore variant-reader OVLs in one call.

Every experiment in this project is the same shape: take a species' 12 variant FGMs, pin every
attribute that would otherwise drift, vary one or two on purpose, install, capture, restore.
Doing that by hand is where the errors came from -- v3-v6 were all invalidated by uncontrolled
attributes, and several capture rounds were mislabelled.

So the rules are baked in here rather than re-typed each time:
  * ALWAYS build from a pristine copy pulled out of Content0\\Dinosaurs.zip, never from whatever
    happens to be installed (which is usually a previous reader).
  * PIN every attribute known to vary across a pristine variant set.
  * ASSERT at build time that nothing varies except what the plan intends -- refuse to install
    otherwise.
  * VERIFY the file actually on disk after installing, not the one in memory.
  * Print the capture order against the on-screen swatch names, since those are what the user
    reads back.

Typical use:

    import reader_kit as rk
    plan = [rk.V(seed=s, slots=[0]*16, weights=[1.0]*16) for s in SEEDS]
    rk.run(plan, species="Lokiceratops", tag="v11")

`rk.restore(species)` puts the stock OVL back.
"""
import sys, os, logging, shutil, tempfile, zipfile

import _paths  # noqa: E402

# Vendored: every one of these was an absolute path to one developer's machine (including a
# throwaway temp folder). cobra-tools and the game come from the shared config, which detects both;
# scratch and output stay inside the package or the OS temp folder.
REPO = _paths.cobra_tools()
sys.path.insert(0, REPO)
if not hasattr(logging, "success"):
    logging.success = lambda *a, **k: None
logging.disable(logging.WARNING)
from utils.config import Config                      # noqa: E402
from generated.formats.ovl import OvlFile            # noqa: E402

GAME = "Jurassic World Evolution 3"
ROOT = _paths.game_content() or ""
LAND = os.path.join(ROOT, "Dinosaurs", "Land")
ZIP = os.path.join(ROOT, "Dinosaurs.zip")
SCRATCH = os.path.join(tempfile.gettempdir(), "jwe3_variant_tools")
OUTPROJ = os.path.join(_paths.PKG, "Build")
os.makedirs(SCRATCH, exist_ok=True)

# on-screen SKIN COLOR place names, in variant order (a per-index localisation key, so the same
# list identifies the variant on ANY species)
SWATCHES = ["Sonoran Desert", "Death Valley", "Great Sandy Desert", "Champlain Valley",
            "Salar del Huasco", "Limpopo River", "Qilian Mountains", "Yukon River",
            "Svalbard", "Amazon Rainforest", "Mangrove Forest", "Gambia River Basin"]

# every attribute observed to vary across a pristine 12-variant set, pinned unless overridden
PINNED = {
    "u_globalKeyTolerance":                1.20,
    "u_globalKeyThreshold":                1.56,
    "u_globalKeyType":                     0,
    "u_instancePaletteOffset":             1.00,
    "u_instancePaletteScale":              1.00,
    "u_instancePaletteStrength":           1.00,
    "u_globalPaletteMaximumComplexity":    5,
    "u_globalColourRotationOffsetBase":    0.00,
    "u_globalColourRotationOffsetPalette": 0.90,
    "u_globalColourSaturationBase":        1.50,
    "u_globalColourSaturationPalette":     1.60,
    "u_globalColourBrightnessBase":        1.40,
    "u_globalColourBrightnessPalette":     1.00,
}


class V(dict):
    """One variant. seed + optional per-layer slots/weights + arbitrary attribute overrides."""

    def __init__(self, seed, slots=None, weights=None, label="", **overrides):
        super().__init__()
        self["seed"] = seed
        self["slots"] = list(slots) if slots is not None else [0] * 16
        self["weights"] = list(weights) if weights is not None else [1.0] * 16
        self["label"] = label
        self["overrides"] = overrides
        assert len(self["slots"]) == 16 and len(self["weights"]) == 16


def _ovl(path):
    o = OvlFile()
    cfg = Config(REPO); cfg.load(); o.cfg = cfg
    o.game = GAME; o.load_hash_table()
    o.load(path, {"game": GAME})
    return o


def _variants(o):
    out = [(n, l) for n, l in o.loaders.items()
           if n.endswith(".fgm") and getattr(l.header, "shader_name", "") == "DinosaurLayered_Variant"]
    out.sort(key=lambda t: t[0])
    return out


def _params(l):
    a = l.header.attributes.data
    v = l.header.value_foreach_attributes.data
    return {a[i].name: [float(x) for x in v[i].value] for i in range(len(a))}


def locate(species, sex="Female"):
    """(prefix inside the zip, name of the species .ovl) -- or raise with what was actually there.

    The old code assumed `Dinosaurs/Land/{species}/{sex}/`, which is wrong three ways:

      * the realm is not always Land (there are Air, Water and Shared);
      * the folder's capitalisation may not match a name parsed from a filename;
      * NOT EVERY SPECIES IS SPLIT BY SEX. Indominus Rex is a single hybrid and lives at
        `Dinosaurs/Land/IndominusRex/IndominusRex.ovl` with no Female/ subfolder at all, so
        importing one failed with "nothing found under Dinosaurs/Land/IndominusRex/Female/".
    """
    want = species.lower().replace("_", "")
    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()

    species_dirs = []
    for e in names:
        parts = e.split("/")
        if len(parts) >= 3 and parts[0] == "Dinosaurs" and parts[2].lower().replace("_", "") == want:
            d = "/".join(parts[:3]) + "/"
            if d not in species_dirs:
                species_dirs.append(d)
    if not species_dirs:
        realms = sorted({n.split("/")[1] for n in names if n.count("/") > 1})
        raise AssertionError("no species folder for %r in %s (realms present: %s)"
                             % (species, os.path.basename(ZIP), ", ".join(realms)))

    for base in species_dirs:
        real = base.split("/")[2]                       # the folder's true capitalisation
        sexed = "%s%s/" % (base, sex)
        if any(e.startswith(sexed) and not e.endswith("/") for e in names):
            return sexed, "%s_%s.ovl" % (real, sex)
        # sexless: take the files directly inside the species folder
        if any(e.startswith(base) and e.count("/") == 3 and not e.endswith("/") for e in names):
            return base, "%s.ovl" % real
    raise AssertionError("found %r at %s but no files for sex %r and none directly inside"
                         % (species, species_dirs[0], sex))


def pristine(species, sex="Female"):
    """Extract the stock files from Dinosaurs.zip once, cache them, return the folder."""
    dst = os.path.join(SCRATCH, f"pristine_{species}_{sex}")
    if os.path.isdir(dst) and os.listdir(dst):
        return dst
    pre, _ovl_name = locate(species, sex)
    os.makedirs(dst, exist_ok=True)
    n = 0
    with zipfile.ZipFile(ZIP) as z:
        for e in z.namelist():
            if e.startswith(pre) and not e.endswith("/"):
                with z.open(e) as src, open(os.path.join(dst, os.path.basename(e)), "wb") as out:
                    shutil.copyfileobj(src, out)
                n += 1
    assert n, f"nothing found under {pre} in {ZIP}"
    print(f"  extracted {n} pristine files -> {dst}")
    return dst


def species_ovl_name(species, sex="Female"):
    """The species' .ovl filename -- `Baryonyx_Female.ovl`, but `IndominusRex.ovl` when sexless."""
    return locate(species, sex)[1]


def natural(species, sex="Female", variant=0):
    """Read a species' stock per-layer slots/weights, for building 'natural' conditions."""
    d = _params(_variants(_ovl(os.path.join(pristine(species, sex),
                                            f"{species}_{sex}.ovl")))[variant][1])
    return ([int(d[f"u_remapIndex{i}"][0]) for i in range(1, 17)],
            [round(d[f"u_globalColourWeight{i}"][0], 3) for i in range(1, 17)])


def build(plan, species="Lokiceratops", sex="Female", tag="rX", pinned=None):
    """Build the reader from pristine. Returns the built .ovl path. Asserts control."""
    assert len(plan) == 12, f"need exactly 12 variants, got {len(plan)}"
    pin = dict(PINNED); pin.update(pinned or {})
    name = f"{species}_{sex}.ovl"
    src = pristine(species, sex)
    build_dir = os.path.join(SCRATCH, f"reader_{tag}_{species}")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)
    for f in os.listdir(src):
        shutil.copy2(os.path.join(src, f), build_dir)
    path = os.path.join(build_dir, name)

    o = _ovl(path)
    vls = _variants(o)
    assert len(vls) == 12, f"expected 12 variant FGMs, found {len(vls)}"
    for (_, l), spec in zip(vls, plan):
        m = dict(pin)
        m["u_globalPaletteSeed"] = spec["seed"]
        for i in range(16):
            m[f"u_remapIndex{i+1}"] = spec["slots"][i]
            m[f"u_globalColourWeight{i+1}"] = spec["weights"][i]
        m.update(spec["overrides"])
        a = l.header.attributes.data
        v = l.header.value_foreach_attributes.data
        for i in range(len(a)):
            if a[i].name in m:
                v[i].value[0] = m[a[i].name]
        l.write_memory_data()
    o.save(path, commands={"update_aux": True})

    # ---- control assertion, against the SAVED file ----
    dicts = [_params(l) for _, l in _variants(_ovl(path))]
    varying = [k for k in sorted(dicts[0])
               if len({tuple(round(x, 4) for x in d[k]) for d in dicts}) > 1]
    allowed = ({"u_globalPaletteSeed"}
               | {f"u_remapIndex{i}" for i in range(1, 17)}
               | {f"u_globalColourWeight{i}" for i in range(1, 17)}
               | {k for s in plan for k in s["overrides"]})
    bad = [k for k in varying if k not in allowed]
    if bad:
        raise AssertionError(f"UNCONTROLLED attributes vary across variants: {bad}")
    print(f"  built {name}: {len(varying)} attrs vary, all intended.")
    return path


def install(built, species="Lokiceratops", sex="Female", tag="rX"):
    """Copy the built .ovl over the live install and verify what landed on disk."""
    dst = os.path.join(LAND, species, sex, f"{species}_{sex}.ovl")
    assert os.path.isfile(dst), f"no live install at {dst}"
    os.makedirs(OUTPROJ, exist_ok=True)
    shutil.copy2(built, os.path.join(OUTPROJ, f"{species}_{sex}_{tag}.ovl"))
    shutil.copy2(built, dst)
    print(f"  installed -> {dst}")
    return dst


def _order_rows(path, plan):
    """Read the capture order back off the file on disk. Returns list of row tuples."""
    dicts = [_params(l) for _, l in _variants(_ovl(path))]
    rows = []
    for k, (d, spec) in enumerate(zip(dicts, plan)):
        s = int(d["u_globalPaletteSeed"][0])
        assert s == spec["seed"], f"variant {k}: seed {s} != planned {spec['seed']}"
        gs = sorted(set(int(d[f"u_remapIndex{i}"][0]) for i in range(1, 17)))
        gw = sorted(set(round(d[f"u_globalColourWeight{i}"][0], 2) for i in range(1, 17)))
        rows.append((k + 1, SWATCHES[k], spec["label"], s, gs, gw))
    return rows


def report(path, plan, species="Lokiceratops", sex="Female"):
    print(f"\n  CAPTURE ORDER ({species} {sex}) -- shoot in this order, shadows off, magenta bg")
    print(f"  {'#':>2} {'swatch':<20} {'label':<14} {'seed':>5} | {'slots':<24} | weights")
    for n, sw, lbl, s, gs, gw in _order_rows(path, plan):
        print(f"  {n:>2} {sw:<20} {lbl:<14} {s:>5} | {str(gs):<24} | {gw}")


# short folder names for the capture tree; falls back to the species name
SHORT = {"Lokiceratops": "Loki", "Spinosaurus": "Spino", "Dilophosaurus": "Dilo",
         "Acrocanthosaurus": "Acro", "Sinoceratops": "Sino"}
CAPTURE_ROOT = r"D:\JWE2 Stuff\Cobra Tool Versions\Main Mod Kit\JWE 3 Luas\Base Game\Dinosaur Files"


def capture_dir(path, plan, species="Lokiceratops", sex="Female", tag="rX", suffix=""):
    """Create 'Seed Swap Reader V10\\Loki\\' and drop the capture order in beside it.

    Writing the plan next to the images is deliberate: capture sets have been mislabelled
    several times, and a text file in the folder cannot drift from the images the way a
    remembered ordering can.
    """
    name = f"Seed Swap Reader {tag.upper()}" + (f" {suffix}" if suffix else "")
    folder = os.path.join(CAPTURE_ROOT, name, SHORT.get(species, species))
    os.makedirs(folder, exist_ok=True)
    rows = _order_rows(path, plan)
    with open(os.path.join(folder, "CAPTURE_ORDER.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"{name} -- {species} {sex}\n")
        fh.write("Shoot in this order. The swatch place-name is what identifies the variant\n")
        fh.write("on screen; chronological file order must match this list.\n")
        fh.write("Rig: shadows/AO off, magenta backdrop (pUnlitTint 4,0,4).\n\n")
        fh.write(f"{'#':>2}  {'swatch':<20} {'condition':<14} {'seed':>5}  {'slots':<24} weights\n")
        for n, sw, lbl, s, gs, gw in rows:
            fh.write(f"{n:>2}  {sw:<20} {lbl:<14} {s:>5}  {str(gs):<24} {gw}\n")
    print(f"  capture folder -> {folder}")
    return folder


def run(plan, species="Lokiceratops", sex="Female", tag="rX", pinned=None,
        do_install=True, suffix=""):
    print(f"\n=== reader {tag} :: {species}_{sex} ===")
    built = build(plan, species, sex, tag, pinned)
    live = install(built, species, sex, tag) if do_install else built
    report(live, plan, species, sex)
    folder = capture_dir(live, plan, species, sex, tag, suffix)
    print(f"\n  no restart needed -- reload the dinosaur in the hatchery.")
    print(f"  restore with: python -c \"import reader_kit as rk; rk.restore('{species}')\"")
    return live, folder


def restore(species="Lokiceratops", sex="Female"):
    """Put the stock OVL back."""
    src = os.path.join(pristine(species, sex), f"{species}_{sex}.ovl")
    dst = os.path.join(LAND, species, sex, f"{species}_{sex}.ovl")
    shutil.copy2(src, dst)
    d = _params(_variants(_ovl(dst))[0][1])
    print(f"restored {dst}")
    print(f"  variant 0 now: seed {int(d['u_globalPaletteSeed'][0])} "
          f"keyTol {d['u_globalKeyTolerance'][0]:.2f} "
          f"cplx {int(d['u_globalPaletteMaximumComplexity'][0])}  (stock values)")
