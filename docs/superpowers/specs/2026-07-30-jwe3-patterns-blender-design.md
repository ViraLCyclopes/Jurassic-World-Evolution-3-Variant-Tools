# JWE3 Patterns in Blender — design

**Date:** 2026-07-30
**Status:** design, awaiting approval
**Research backing:** `…\Dinosaur Files\Shader Research\PATTERNS.md` (written alongside this spec).
Read it first — every factual claim here is sourced there and marked measured or hypothesis.

## Goal

Reproduce JWE3's **pattern** cosmetic in Blender as an overlay on the existing variant material, and
build the reader/model/IO layer that a pattern editor will sit on.

## Why now

Patterns are the second of the two cosmetic axes a JWE3 dinosaur wears, and the Blender reproduction
covers only the first. Every reference render to date shows Blank Pattern. Unlike palette seeds —
which are unreachable because their CPU-side bake is unknown (`jwe3-seed-hash-dead-end`) — a
pattern's key data sits in plain sight in the FGM, so patterns are **freely authorable**. That makes
this the higher-value of the two axes for a modder.

## Scope

**In:**

- Read pattern FGMs, pattern sets and the slot manifest.
- Bake the 32-entry LUT from keys.
- Discover a species' **mesh parts** and the body↔feather/quills pairing.
- Build and splice a `JWE3_Pattern` node group onto the layered body part.
- Editable model + FGM save, ready for the editor UI in the follow-up spec.

**Out — deliberately:**

- **Rendering the feathers, fin and shell parts.** Their seams are built now; their node builds are a
  later spec. Reason: `DinosaurFeathers` is a second shader read, and blocking on it would delay
  everything visible.
- **The editor UI itself** (sliders, colour pickers, Blender N-panel). Follow-up spec. This one ends
  at a working `pattern_model` + `pattern_io` + a rendering node group.
- **Colour-accuracy validation.** The chosen validation species have no harvested seed coefficients
  (§Validation), so their base colour is approximate. Structure is validated; colour is not.
- Writing patterns back into an OVL. Loose extracted files only, as with the variant editor.

## Design

### The unit is a cosmetic set across parts, not a material

Pyroraptor forces this. It has **no `DinosaurLayered` mesh part at all** — its body is a
`DinosaurFur_Vanilla_BaseLayered`, and it also carries `feathers`, `fur_fin` and `fur_shell` parts.
Any "one species, one material" assumption breaks on it immediately.

Three tiers, distinguished by **texture set**, never by species name or shader name alone:

| tier | shaders | has `pLayered_*`? | own variant/pattern FGMs? |
|---|---|---|---|
| **layered body** | `DinosaurLayered_Layered_Opaque`, `DinosaurFur_Vanilla_BaseLayered` | yes | `variant` / `pattern` |
| **feathers** | `DinosaurFeathers_Clip{Single,Double}Sided` | no — own `pFeathers_*` set | `feathersvariant` / `featherspattern` |
| **derived fur** | `DinosaurFur_Vanilla_{Fin,Shell}` | no — reuses the body's `pBase*` | none; inherits the body's |

`DinosaurFur_Vanilla_BaseLayered`'s texture list is **identical** to
`DinosaurLayered_Layered_Opaque`'s, differing only in added anisotropy attributes. So the existing
`blender_layer_nodes.py` build covers a furred body too; a furred species is not a new colour system.

### Modules

Research modules live in `Variant Research/`, vendored into `VariantEditor/vendor/` with absolute
paths stripped, following the existing convention. One `selftest()` per module, no pytest. The
Blender side never imports cobra-tools — it consumes JSON.

| module | responsibility | knows about parts? |
|---|---|---|
| `pattern_reader.py` | read a pattern FGM's 67 attributes, the pattern set's 4 index maps, and the `.dinosaurmaterialpatterns` slot list | no |
| `pattern_lut.py` | **the bake** — sparse keys → 32-entry LUT for colour, emissive, opacity | no |
| `part_manifest.py` | **new.** discover mesh parts by texture set, de-interleave the manifest, pair body↔feather/quills, and *locate* the shared `DinosaurFur/` library and per-species overrides | **yes — the only one** |
| `export_pattern.py` | JSON bridge, keyed by part; mirrors `export_palette.py` | passthrough |
| `blender_pattern_nodes.py` | build + splice the `JWE3_Pattern` group onto a part | yes |
| `pattern_model.py` | plain-data editable model; mirrors `variant_model.py` | no |
| `pattern_io.py` | loose `.fgm` load/save via cobra-tools `FgmHeader`; mirrors `fgm_io.py` | no |

`pattern_lut`, `pattern_model` and `pattern_io` stay part-agnostic because the schemas are
**byte-identical** between body and feather: `feathersvariant` is `DinosaurLayered_Variant` with the
same 144 attributes (set difference empty in both directions), and `featherspattern` is
`DinosaurLayered_Pattern` with the same 67. Confining part-awareness to `part_manifest` is what makes
the later feathers spec a single new node-build file.

### De-interleaving the manifest

`.dinosaurmaterialpatterns` is a flat list of *(logical pattern × part)*, parts interleaved, with
`has_ptr="0"` marking a null (Blank Pattern). `.dinosaurmaterialvariants` is the same shape.

```
Pyroraptor  pattern_count=12  nulls=0
   Pattern_01_00, FeathersPattern_01_00, Pattern_01_01, FeathersPattern_01_01, …
```

`part_manifest` derives the stride from the **distinct part tokens actually present in the names**,
not from a constant, and returns `slots[logical_index][part] -> fgm name | None`.

The part token is **not always in the same position**, and this is the detail most likely to be got
wrong:

| species | names | part token |
|---|---|---|
| Pyroraptor | `Pyroraptor_Pattern_01_00`, `Pyroraptor_FeathersPattern_01_00` | **infix** — `Feathers` before `Pattern` |
| Psittacosaurus | `..._Pattern_01_00`, `..._Pattern_01_00_Quills` | **suffix** — `_Quills` after the index |

So the parser cannot key on a prefix. It matches the invariant `_Pattern_<set>_<index>` core and
treats whatever surrounds it as the part token, with the empty token meaning the body.

Hard requirements, each from a species that breaks the naive rule:

- Stride is **not** 1 (Psittacosaurus, Pyroraptor).
- The pairing is **read from the manifest**, never re-derived from filenames.
- A null entry may be **absent** (Pyroraptor, `patternset_lux`).
- **There is no `_06.fgm` standing in for blank** — the blank is a null with no file.

### The LUT bake

Keys are sparse over positions 0–31; `-1` means unused. Three independent key sets — 12 colour, 12
emissive, 8 opacity — over the same axis. `pattern_lut.bake()` returns a `(32, 3)` float array per
channel.

Interpolation between keys is **not yet known** (linear ramp vs. step at each key). `bake()` takes an
explicit `interp=` argument with both implemented, so settling it from the IR is a one-line change
rather than a rewrite. The default is linear, flagged in the docstring as unconfirmed.

### The Blender side

The LUT reaches Blender as a **generated 32×3 image datablock** — row 0 colour, row 1 emissive,
row 2 opacity — sampled at `u = (index + 0.5)/32`. This mirrors what the game does:
`pPatterning_PatternGradientMap` is a baked texture, bound bindlessly, with no file on disk.

A ColorRamp node was considered — it caps at exactly 32 stops and would be natively editable — and
rejected: it cannot carry colour, emissive and opacity in one node.

`JWE3_Pattern` is a **self-contained node group spliced after the palette grade**, taking graded
albedo in and returning patterned albedo out. Swapping a pattern replaces one group; removing it
unsplices and relinks. This mirrors how the grade node already works, and matches the game's own
model, where every part shader — body, fin, shell and feathers alike — declares
`pPatterning_*GradientMap`.

**Splice discipline is a hard requirement.** `jwe3-palette-apply-to-stacks` records that a second
`apply_to` call inserts a *second* grade node and renders the mesh white. `apply_pattern()` must
unsplice before splicing, and its selftest must assert that applying twice leaves **one** group.

`apply_pattern(part, ...)` takes a part from the start, so no material-per-object assumption is baked
in anywhere.

### Two traps carried over from the existing work

- **V is flipped for every LUT lookup.** Extracted PNGs keep DirectX row order; Blender's V runs from
  the bottom. Row `i` of a 3-row LUT is `1 - (i + 0.5)/3`.
- **Cycles, not EEVEE.** EEVEE Next renders the 16-layer material as flat magenta and fails silently.

### Colour space

The picker and model hold the **raw stored floats**; gamma correction is display-only. Stored key
RGBs are mixed — some exactly byte-quantised (`0.6235294` = 159/255), some not (`0.6061094` × 255 =
154.56) — so a round-trip through an 8-bit picker would silently rewrite untouched keys.

### Shader read

One genuine unknown: **where and how the pattern composites**. Method is this project's proven one —
read container 300 at the bindless `T0` samples, tracing from the `+64 w0` patterning base index
already identified in `jwe3-blender-reproduction`, then check against the game.

If the IR read stalls, the fallback is to ship the LUT bake and the node group with the composite
behind a documented assumption, since everything upstream of the composite is measured fact.

## Validation

Project convention: a `selftest()` per module, no pytest.

**Unit:**

- `pattern_lut.selftest()` pins the bake against hand-computed entries from Lokiceratops `01_00`,
  including a `-1` key that must be skipped and an all-`-1` emissive set.
- `pattern_io` round-trip: load → save an untouched pattern FGM, compare attributes. This is what
  guards the colour-space decision; it must fail if any float is requantised.
- `blender_pattern_nodes` double-apply test: applying twice yields one group, not two.

**Index-shape regression guards** — four species that each break a different naive rule:

| species | shape | breaks |
|---|---|---|
| Lokiceratops | 6 + null | — (the baseline) |
| Psittacosaurus ♀ | 6 × 2 + null | stride ≠ 1 |
| Indominus rex | 7 + null | "6 patterns" |
| Pyroraptor | 6 × 2, **no null** | "there is always a blank" |

**Visual:** render Lokiceratops under each of its 6 patterns and compare against in-game screenshots.
Per `jwe3-blender-reproduction`: judge colour on flat albedo (`preview_albedo`) with view transform
Standard, re-render a known-good frame first to rule out leaked state, and never set
`colorspace_settings` on a shared image datablock.

**Known validation gap:** none of Loki, Pyroraptor or Psittacosaurus has harvested seed coefficients,
so their base colour is approximate and this set validates **structure only**. Baryonyx — 7 patterns
+ blank, 4 harvested seeds, an already user-validated render — is the species that could validate
colour, and can be added later without design change.

**Blocking dependency:** every existing reference screenshot uses Blank Pattern. New in-game captures
of a non-blank pattern are required before the visual step can run. Nothing before it is blocked.

## Risks

| risk | mitigation |
|---|---|
| The composite can't be read out of the IR | Ship the measured parts behind a documented assumption; the LUT and node group stand regardless |
| Patchwork path is unexercised — `u_usePatchwork` is 0 in all 28 shipped files | Implement the pattern path only; leave patchwork unimplemented and say so, rather than guessing |
| `u_patchworkFlags` is 31 in every file, so it carries no information | Do not infer it from data. Same trap as `u_globalKeyType` in `PALETTE.md` — settle from IR or capture |
| `Variant Research/Textures/` is a partial dump and misled this research twice | Source species files from the `Personal Mods` tree; `part_manifest` reports what it could not resolve rather than silently proceeding |

## Follow-up specs

1. **Pattern editor UI** — 12 colour + 12 emissive keys with native colour pickers, 8 opacity keys,
   the flags, a pattern picker and a live LUT strip; in both the Blender N-panel and the PyQt5 app,
   sharing `pattern_model`.
2. **Feathers/fur rendering** — `DinosaurFeathers_ClipDoubleSided` (container 202, 2411 IR lines, the
   smallest of the part shaders), plus fin/shell, plus resolving the shared `DinosaurFur/` library.
