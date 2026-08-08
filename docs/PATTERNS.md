# JWE3 Dinosaur Patterns, and the multi-part cosmetic model

Companion to `PALETTE.md`. That document covers the **colour variant** — the cosmetic axis driven by
`<species>_variant_NN_NN.fgm` and the cosine-gradient palette. This one covers the **pattern**, the
*second, independent* cosmetic axis, and the **mesh-part model** that both axes actually apply to.

Researched 2026-07-30. Everything below is marked **MEASURED** (read out of shipped files, the FDB,
or a live Blender session) or **HYPOTHESIS** (consistent with the data, not yet confirmed).
Nothing here has been validated against a running game — see *Open questions*.

---

## 1. Patterns are a separate cosmetic axis — MEASURED

`c0dinosaurs.fdb` carries two pattern tables:

| table | rows | columns |
|---|---|---|
| `SpeciesCosmeticPatterns` | 1605 | `SpeciesID, SetID, PatternIndex, EquivalentBaseSetPatternIndex, UiLabel, UiIcon, UiPatternTexture, UiColour, UnlockBundle` |
| `CosmeticPatternDefaults` | 8 | `SetDefault, PatternIndex, UiLabel, UiIcon, UiPatternTexture, UiColour, UnlockBundle` |

`CosmeticPatternDefaults` rows are `('Standard', N, '[Cosmetic_Pattern_N+1]', …, 'GeneMod_Cosmetic_Pattern_N+1')`
— so a pattern is unlocked by its own gene mod, independently of the colour variant. A dinosaur wears
a `(variant, pattern)` pair.

As with variants, the **friendly UI names are not in the FDB** — they live in a localisation file.
Map a screenshot to a pattern by **swatch position**, not by name. This is the same trap
`PALETTE.md` records for variants.

---

## 2. The three files that make up a pattern — MEASURED

| file | shader | what it holds |
|---|---|---|
| `<sp>_patternset_01.fgm` | `DinosaurLayered_PatternSet` | the index maps (textures) |
| `<sp>_pattern_01_NN.fgm` | `DinosaurLayered_Pattern` | 67 attributes: the keys |
| `<sp>_patternset_01.dinosaurmaterialpatterns` | — | the ordered slot list |

### 2.1 The pattern set — four index maps

Every `patternset` FGM checked declares exactly these four textures, in this order:

```
u_basePatchworkMap
u_basePatternMap
u_feathersBasePatchworkMap
u_feathersBasePatternMap
```

On a non-feathered species the two `feathers*` entries are **RGBA placeholders**
(`FgmDtype.RGBA`, two `bytecolor` rows) rather than texture references — Lokiceratops and Baryonyx
are both like this. On Pyroraptor `u_feathersBasePatternMap` is a **real texture**
(`pyroraptor_patternset_01.u_feathersbasepatternmap.png`). So the feathers path is first-class in
the format and simply unused by most species.

Measured on Lokiceratops, both maps 1024×1024 RGBA, R=G=B, alpha constant 255:

| map | distinct levels | range | as `v/255 × 31` |
|---|---|---|---|
| `u_basePatternMap` | 229 | 13–243 | 1.6 – 29.5 |
| `u_basePatchworkMap` | 59 | 191–249 | 23.2 – 30.3 |

The pattern map's normalised range lands almost exactly on the key-position span (§2.2). The
patchwork map's does not, and its 59 discrete levels in a narrow band look like region IDs on a
different scale. See *Open questions*.

### 2.2 The pattern — 32 keys, three channels

`DinosaurLayered_Pattern`, 67 attributes, identical schema in all 28 files surveyed:

| family | count | value type |
|---|---|---|
| `u_colourKey_NN_Position` / `_RGB` | 12 | `INT` / `FLOAT_3` |
| `u_emissiveKey_NN_Position` / `_RGB` | 12 | `INT` / `FLOAT_3` |
| `u_opacityKey_NN_Position` / `_Value` | 8 | `INT` / `FLOAT` |
| `u_patchworkFlags` | 1 | `INT` |
| `u_usePatchwork` | 1 | `BOOL` |
| `u_usePatternLUT` | 1 | `BOOL` |

**Positions span exactly 0–31, with `-1` meaning "unused".** Surveyed over all 28 pattern FGMs on
disk: colour keys min −1 max 31, emissive min −1 max 31 (288 of them are −1, i.e. emissive is mostly
off), opacity min −1 max 31.

**`u_patchworkFlags` is 31 in all 28 files** — `0b11111`, five bits, consistent with 32 slots.

`u_usePatchwork` is **0 in all 28**. `u_usePatternLUT` is 1 in 22 and **0 in 6**.

> **This is a 32-entry gradient LUT defined by sparse keys.** Colour, emissive and opacity are three
> independent key sets over the same 0–31 axis.

### 2.3 The slot list — INTERLEAVED BY PART, and this is the important one

`.dinosaurmaterialpatterns` is an XML header with `set_count`, `pattern_count`, and a `<patterns>`
pool of `<pattern>` entries. An entry with `has_ptr="0"` is a **null** — the Blank Pattern — and has
no FGM behind it. **There is no `_06.fgm` standing in for blank.**

The list is **not** a list of patterns. It is a flat list of *(logical pattern × mesh part)*, with
the parts interleaved:

| species | `pattern_count` | nulls | contents |
|---|---|---|---|
| Lokiceratops | 7 | 1 | `01_00 … 01_05`, + null |
| Baryonyx | 8 | 1 | `01_00 … 01_06`, + null |
| Indominus rex | 8 | 1 | `01_00 … 01_06`, + null |
| Psittacosaurus ♀ | 13 | 1 | `01_00, 01_00_Quills, 01_01, 01_01_Quills, …`, + null |
| **Pyroraptor** | **12** | **0** | `Pattern_01_00, FeathersPattern_01_00, Pattern_01_01, FeathersPattern_01_01, …` |
| Indominus `patternset_lux` | 1 | 0 | `Lux_00` — the film set |

`.dinosaurmaterialvariants` works the same way. Pyroraptor's is `variant_count="24"` =
12 logical variants × 2 parts, `Variant_01_00, FeathersVariant_01_00, Variant_01_01, …`.

**Consequences, all of which bite:**

- **Stride = number of parts.** Never assume 1.
- **The body↔feather (or body↔quills) pairing is explicit in the manifest.** Do not re-derive it from
  filenames — read it.
- **The blank pattern is not universal.** Pyroraptor has none; `patternset_lux` has none.
- **"7 patterns per species" is wrong as a rule.** It holds for Loki (6+blank) and coincidentally for
  Baryonyx/Indominus (7+blank), and fails for Psittacosaurus and Pyroraptor.

`pyroraptor_variantset_jw.dinosaurmaterialvariants` has `has_sets="1" variant_count="1"` and yields
no `<variant_name>` entries — a *different* structure from the base set, presumably a set reference.
**Its layout has not been read.** This is the film-variant mechanism that Pyroraptor, Parasaurolophus
and Indominus rex use.

---

## 3. How the keys reach the GPU — MEASURED, plus one hypothesis

The master species FGM (`<sp>.fgm`, `DinosaurLayered_Layered_Opaque`) declares:

```
pPatterning_PatchworkGradientMap        <- RGBA placeholder, NOT a texture reference
pPatterning_PatternGradientMap          <- RGBA placeholder, NOT a texture reference
pPatterning_EmissiveAdaptiveBrighnessWeight   (0.0 on Lokiceratops; note the game's own typo)
pPatterning_EmissiveMaximumBrightness         (4.0 on Lokiceratops)
```

Both gradient maps are placeholders in every species checked, and **no gradient-map file exists
anywhere on disk**. Container 300's only bindless resource is `T0`, a
`texture f32 2darray t0,space1 24576` heap.

> **HYPOTHESIS — the gradient map is baked CPU-side from the keys at load and bound bindlessly,**
> exactly as the twelve palette coefficients are baked from the seed (`PALETTE.md` §"The seed picks
> those twelve numbers, baked CPU-side"). The `+64 w0` field in the 80-byte per-layer GPU block is
> already identified in `jwe3-blender-reproduction` as "a patterning base index", which is the
> natural handle into that heap.

This is the *good* case: unlike seeds — which are unreachable because the bake is unknown
(`jwe3-seed-hash-dead-end`) — the pattern's key data is fully present in the FGM, so the bake is
reproducible from first principles. **Patterns are freely authorable in a way palettes are not.**

### Colour-space note — MEASURED

Stored key RGBs are `FLOAT_3`. Some are exactly byte-quantised (`0.6235294` = 159/255,
`0.4196079` = 107/255, `0.1490196` = 38/255) and some are not (`0.6061094` × 255 = 154.56).
So a round-trip through an 8-bit colour picker would silently rewrite untouched keys. Any editor
must edit the **raw floats** and gamma-correct only for display.

---

## 4. The mesh-part model — MEASURED live in Blender

Read off Pyroraptor Female's imported `models.ms2` (Blender 4.5.9, cobra-tools importer),
22 real mesh parts after excluding `*_joint_physics`:

| mesh part | material | shader | UV layers | LODs |
|---|---|---|---|---|
| `fur` | `pyroraptor_fur` | `DinosaurFur_Vanilla_BaseLayered` | UV0 | 0–5 |
| `feathers` | `pyroraptor_feathers` | `DinosaurFeathers_ClipDoubleSided` | UV0, UV1 | 0–5 |
| `fur_fin` | `pyroraptor_fur_fin` | `DinosaurFur_Vanilla_Fin` | UV0, UV1 | **0–1 only** |
| `fur_shell` | `pyroraptor_fur_shell` | `DinosaurFur_Vanilla_Shell` | UV0 | **0–1 only** |
| `airliftstraps` | `airliftstraps` | — | UV0, UV1 | 0–5 |

**There is no `DinosaurLayered` mesh part on Pyroraptor.** `pyroraptor.fgm` exists and is a normal
`DinosaurLayered_Layered_Opaque`, but **no mesh uses it**.

### 4.1 `DinosaurFur_Vanilla_BaseLayered` IS the body

Its texture list is **identical** to `pyroraptor.fgm`'s:

```
pBaseAOTexture  pBaseDiffuseTexture  pBaseNormalTexture  pBaseTransmissionTexture
pLayered_BlendWeights  pLayered_DiffuseTexture  pLayered_HeightTexture
pLayered_PackedTexture  pLayered_RemapTexture  pLayered_WarpOffset
pPatterning_PatchworkGradientMap  pPatterning_PatternGradientMap
```

It adds fur shading attributes on top — `gAnisotropicStrength`, `gDirectionScale`, `pFurDepthScale`,
`pAnisoNormalBend`, `gWetHair*`, `gLodFade`.

> **So `DinosaurFur_Vanilla_BaseLayered` = `DinosaurLayered_Layered_Opaque` + anisotropy.** The
> existing 16-layer node build in `blender_layer_nodes.py` should transfer to a furred body largely
> unchanged. A furred species is *not* a new colour system.

Pyroraptor also runs **8 layer FGMs, not 16** — consistent with the variable `pLayered_LayerCount`
already handled in `layer_chain.py` (Baryonyx's `[1]*6 + [0]*10` case).

### 4.2 Fin and shell are derived, not independent

Both carry **only** `pBase*` plus one extra (`pFinAlphaTexture` / `pShellMap`) — **no `pLayered_*`
textures at all**. In Blender they are wired to the *same image datablocks* as the body
(`pyroraptor.pbasediffusetexture.png`, `pyroraptor.pbasenormaltexture_*`,
`pyroraptor_fur.pbaseaotexture_*`). They are the classic shell-and-fin fur technique layered over the
body's colour, and they drop out below LOD1.

They still declare `pPatterning_*GradientMap`.

### 4.3 Feathers is the one genuinely separate path

```
pDinosaurFeathers_BaseDiffuseTexture     pFeathers_BaseColourTexture
pFeathers_NormalTexture                  pFeathers_RoughnessPackedTexture
pFeathers_Aniso_PackedTexture            pFeathers_AOHeightOpacityTransmission_PackedTexture
pFeathers_EmissiveTexture
pIridescenceTexture                      pIridescenceMaskTexture
pPatterning_FeathersPatchworkGradientMap pPatterning_FeathersPatternGradientMap
```

No layered textures. Its own patterning gradient maps under `Feathers*` names — matching the
`u_feathersBase*Map` index maps in the pattern set. Plus **iridescence**, which has no equivalent
anywhere in the body path.

### 4.4 Patterning is universal across parts

**All four** part shaders — `BaseLayered`, `Fin`, `Shell`, `Feathers` — declare
`pPatterning_*GradientMap`. Whatever the composite turns out to be, it is applied to every part.

### 4.5 The shared fur/feather library

`…\Personal Mods\JWE3\Images and Models\Dinosaurs\DinosaurFur\` holds
`base.fgm`, `baselayered.fgm`, `feathers.fgm`, `fin.fgm`, `hair.fgm`, plus per-species overrides
(`fins_orangutan.fgm`, `fins_therizinosaurus.fgm`) and the shared feather textures
(`feathers.pfeathers_basecolourtexture.png`, `…_normaltexture_RG.png`, `…_aniso_packedtexture_*`,
`…_roughnesspackedtexture_*`, `…_aoheightopacitytransmission_packedtexture_*`).

Structurally this is `SwatchLibrary.ovl` again: a shared library plus per-species overrides
(`pyroraptor_feathers.fgm` is a local override of `DinosaurFur/feathers.fgm`).

**Confirmed consequence:** cobra-tools imported `pyroraptor_feathers` with **exactly one image** —
the local `pdinosaurfeathers_basediffusetexture` — and **none** of the shared `pFeathers_*` textures,
because they are not beside the model. Any faithful build must resolve the shared library itself.

### 4.5.1 Resolution is by `<dependency_name>`, not by guesswork — MEASURED

Each texture slot in `pyroraptor_feathers.fgm` names its file explicitly:

| slot | `<dependency_name>` | in `DinosaurFur/`? |
|---|---|---|
| `pDinosaurFeathers_BaseDiffuseTexture` | `pyroraptor_feathers.pdinosaurfeathers_basediffusetexture.tex` | local, beside the model |
| `pFeathers_BaseColourTexture` | `feathers.pfeathers_basecolourtexture.tex` | yes |
| `pFeathers_NormalTexture` | `feathers.pfeathers_normaltexture.tex` | yes |
| `pFeathers_RoughnessPackedTexture` | `feathers.pfeathers_roughnesspackedtexture.tex` | yes |
| `pFeathers_AOHeightOpacityTransmission_PackedTexture` | `feathers.pfeathers_aoheightopacitytransmission_packedtexture.tex` | yes |
| `pFeathers_Aniso_PackedTexture` | `feathers.pfeathers_aniso_packedtexture.tex` | yes |
| `pFeathers_EmissiveTexture` | — | **inline RGBA placeholder** |
| `pIridescenceTexture` | — | **inline RGBA placeholder** |
| `pIridescenceMaskTexture` | — | **inline RGBA placeholder** |
| `pPatterning_Feathers{Patchwork,Pattern}GradientMap` | — | **inline RGBA placeholders** (runtime-baked, §3) |

So the shared-library lookup is a **dependency-name match in the library folder**, with the local
folder taking precedence. No name-derived guessing is needed or wanted.

> **Iridescence is OFF on Pyroraptor** — both iridescence slots and the emissive slot are
> placeholders. The most complex-looking part of `DinosaurFeathers` is not on the critical path for
> this species. Do not assume this generalises; check the slots per species.

### 4.5.2 The importer's colour spaces are inconsistent — MEASURED, and it is a trap

With all 12 shared textures loaded into `pyroraptor_feathers` (Blender 4.5.9, 2026-07-30), the split
channel PNGs came in with **mixed** colour spaces from sibling channels of the *same* packed texture:

| image | assigned | correct |
|---|---|---|
| `…aoheightopacitytransmission_packedtexture_R` / `_G` | **sRGB** | Non-Color |
| `…aoheightopacitytransmission_packedtexture_B` / `_A` | Non-Color | Non-Color |
| `…aniso_packedtexture_R` / `_G` | **sRGB** | Non-Color |
| `…roughnesspackedtexture_A` | **sRGB** | Non-Color |
| `…roughnesspackedtexture_R` / `_G` / `_B` | Non-Color | Non-Color |
| `feathers.pfeathers_basecolourtexture` | sRGB | sRGB — correct, it is albedo |
| `feathers.pfeathers_normaltexture_RG` | Non-Color | Non-Color |

Every one of those slots is data — AO, height, opacity, transmission, anisotropy, roughness — so the
sRGB assignments are wrong and will skew any build that trusts them. **Force Non-Color on every
non-albedo channel, and re-assert it on reuse**: image datablocks are shared, and
`jwe3-blender-reproduction` records a session lost to exactly this (a throwaway material set
`Non-Color` on the base diffuse and turned the whole animal brown, and the reuse path did not repair
it).

The shared textures are 512×512 against the local base diffuse's 1024×1024 — the same "small shared
texture, tiled up" resolution-independence the swatch system uses.

### 4.5.3 cobra-tools builds a COMPLETE material, and its channel mapping is usable — MEASURED

The importer wires each material into a shared `MainShader` node group — a generic PBR group with
inputs `Base Colour, Detail, Ambient Occlusion, Normal, Roughness, Smoothness, Specular, Metalness,
Opacity, Transmission, Emissive`, all feeding a Principled BSDF. `pyroraptor_feathers` has **10 of
them connected**, `pyroraptor_fur` has 5.

Its unpacking of the packed textures is worth taking, because it agrees with the textures' own names:

| packed texture | R | G | B | A |
|---|---|---|---|---|
| `pFeathers_RoughnessPackedTexture` | Metalness | Roughness | Specular | — |
| `pFeathers_AOHeightOpacityTransmission_PackedTexture` | AO | *(height — unconnected)* | Opacity | Transmission |
| `pBaseNormalTexture` (fur) | \_RG → Normal | | \_A → Roughness, \_B → Specular | |

`Base Colour` takes the species-local `pDinosaurFeathers_BaseDiffuseTexture`; `Detail` takes the
shared `pFeathers_BaseColourTexture`. Left unconnected: `aniso_R/_G` and the height channel —
Principled has no socket for either.

> **TRAP: `MainShader` is ONE node group shared by all four materials** — `pyroraptor_feathers`,
> `pyroraptor_fur`, `pyroraptor_fur_fin`, `pyroraptor_fur_shell`. Editing it in place changes all
> four at once. Any build must copy it to a single user or construct its own, never mutate it. Same
> shape as the shared-image-datablock bug in `jwe3-blender-reproduction`.

### 4.5.4 Apparatus note — `is` comparison on bpy nodes silently fails

Two claims in an earlier draft of this document — "the feathers material has no Principled BSDF" and
"nothing is linked into the group" — were **both wrong**, and both came from the same bug:

```python
for l in nt.links:
    if l.to_node is grp:      # WRONG - matches nothing
```

bpy returns a **fresh Python wrapper on every attribute access**, so `is` between separately-fetched
node references is false even for the same node. It does not raise; the loop just yields nothing,
which reads exactly like "there are no links". **Compare by `.name`.** This is the `verify-
measurement-apparatus` lesson again: the user spotted both errors by opening the group and looking.

### 4.6 Relevant shader containers

| container | name | IR lines |
|---|---|---|
| 202 | `ps_DinosaurFeathers_ClipDoubleSided_GBuffer_0_Win64_SM60` | 2411 |
| 238 | `ps_DinosaurFur_Vanilla_BaseLayered_GBuffer_0_Win64_SM60` | 4978 |
| 265 | `ps_DinosaurFur_Vanilla_Base_GBuffer_0_Win64_SM60` | 3361 |
| 300 | `ps_DinosaurLayered_Layered_Opaque_GBuffer_0_Win64_SM60` | 5641 |

Feathers is the *smallest* of these by a wide margin. There is also
`DinosaurFeathers_ClipSingleSided`, and `DinosaurFur_Vanilla_{Base, Fin, Shell}`.

---

## 4.6 The background level — MEASURED 2026-07-30, and it pins the index mapping

**Every pattern map has a dominant "background" value meaning *no pattern here*, and it is
per-species.** Measured as the mode over all 17 species with both a map and pattern FGMs on disk:

| background | index = `v/255 x 31` | species |
|---|---|---|
| **137** | 16.65 | Albertosaurus, Baryonyx, Dilophosaurus, Indominus, Indoraptor, Lokiceratops, Scorpios, Spinosaurus, SpinosaurusJWR, T. rex ♀/♂ |
| **138** | 16.78 | Dreadnoughtus, Psittacosaurus, Pyroraptor, Therizinosaurus, Titanosaurus, Ultimasaurus |
| **205** | 24.92 | **Dimetrodon** |
| **0** | 0.00 | **Herrerasaurus** (66% of pixels are exactly 0) |

Coverage is low: the mode is 48–100% of pixels (Pyroraptor 97%, Indominus 89%, Lokiceratops 86%),
so a pattern touches only a small fraction of the body.

**Min/max hides this completely.** Herrerasaurus reads 0–251 and Dimetrodon 191–246, which look like
ordinary ranges; the distribution is what shows one is 66% black and the other sits in a high band.
The user spotted both by eye before any of this was measured — [[verify-measurement-apparatus]]
again.

### The test that pins `v/255 x 31`

If the background means "no pattern", opacity must be ~0 there. Baking each shipped pattern's
opacity keys and sampling at the species' own background index:

* **11 of 17 species give exactly 0.00, across every one of their patterns** — Albertosaurus,
  Baryonyx, Herrerasaurus, Indominus, Indoraptor, Psittacosaurus, Pyroraptor, SpinosaurusJWR,
  T. rex ♀ and ♂, Ultimasaurus.
* Near-zero (0.05–0.07): Dimetrodon, Lokiceratops, Scorpios rex.
* **Every failure is a MODDED asset** — Therizinosaurus (0.26) and Scorpios are the Deathclaw
  replacements; Dreadnoughtus and Titanosaurus have byte-identical numbers, so the latter is a
  reskin of the former. Base-game species pass, mods do not.

**Why this is evidence for the index formula and not just for the mask:** for the 137/138 species
the zero lands at **16.65, the middle of the axis**. There is no reason for opacity to be zero
mid-range under any other mapping. A wrong formula would scatter those zeros.

**Weaker than it looks for Herrerasaurus specifically:** its background maps to index 0, the edge of
the range, where the clamp makes opacity equal the lowest key regardless. Consistent, but it is the
mid-axis species that carry the argument.

### The individual FGMs settle it — the background is legible WITHOUT the texture

Aggregate statistics were not the strongest evidence; the raw key lists are. Albertosaurus,
background 137 -> index **16.65**:

```
albertosaurus_female_pattern_01_00   opacity ... (16, 0.0), (17, 0.0), (20, 0.399), (31, 0.199)
albertosaurus_female_pattern_01_01   opacity ... (16, 0.0), (17, 0.0), (21, 0.479), (25, 0.5)
albertosaurus_female_pattern_01_02   opacity ... (15, 0.0), (16, 0.0), (17, 0.0), (22, 0.666)
```

**Every pattern pins a zero-opacity PLATEAU at 16 and 17, straddling 16.65.** Two adjacent keys, both
zero, bracketing the background index, in all seven patterns. Nothing but the index formula puts a
deliberate double zero there.

Herrerasaurus, background 0 -> index **0.0**, does the same thing at its own background and ramps
*up and away* from it:

```
herrerasaurus_pattern_01_00..05   opacity (1, 0.0), (3, 0.15), (8, 0.31), (15, 1.0), (17, 1.0), (31, 1.0)
```

All six share that identical opacity curve and differ only in colour keys.

> **So the background value is per-SPECIES** (one shared `u_basePatternMap`) **but every pattern FGM
> independently encodes its response to it**, and that response is readable: the background is
> wherever the opacity curve is pinned to zero. The texture and the FGMs are therefore
> cross-checkable without each other — a free consistency check for any authored pattern.

### PROVEN: the threshold is recoverable from the FGM alone

Baking each shipped pattern's opacity curve and taking the centre of its zero plateau, against the
index predicted from that species' own map background:

| species | bg | predicted | argmin(opacity), per pattern | |
|---|---|---|---|---|
| **Dimetrodon** | `cd` | 24.92 | **24 24 24 24 24 24** | ✅ |
| **Herrerasaurus** | `00` | 0.00 | **0 0 0 0 0 0** | ✅ |
| Lokiceratops | `89` | 16.65 | 16 ×8 | ✅ |
| Albertosaurus | `89` | 16.65 | 16 ×7 | ✅ |
| Indominus, T. rex ♀/♂, Indoraptor, Scorpios, Ultimasaurus | `89`/`8a` | 16.65/16.78 | 16 (occasionally 18) | ✅ |
| Psittacosaurus, Pyroraptor | `8a` | 16.78 | 17 ×8 | ✅ |
| Dreadnoughtus, Titanosaurus, Therizinosaurus, SpinosaurusJWR | — | — | scattered | ❌ modded — see below |

**13 of 17.** Dimetrodon's patterns bottom out at 24 and no other species' do; Herrerasaurus's bottom
at 0 and no other species' do. The threshold is therefore **encoded in each pattern FGM**, not only
in the texture — a pattern FGM alone is enough to recover which greyscale level its species treats
as "no pattern".

### A FLAT map has TWO outcomes — "flat" does not mean "blank"

An earlier version of this section wrote the four misses off as mods getting it wrong. **That was
wrong**, and so was the correction that replaced it. Both fixes came from the Deathclaw mod's author.

Filling the whole map with one value is a deliberate technique, and **which** value decides what
happens:

| flat map filled with… | outcome |
|---|---|
| the value that is **transparent** in the FGM's opacity curve | the species has **no pattern** |
| **any other** value | that slot's colour is painted **over the entire animal** |

So a flat map cannot be classified without also reading the opacity curve. Measured on the two
Deathclaw species, which are filled with *almost* the same grey and behave differently:

* **Scorpios rex** — 100% `0x89` (index 16.65), opacity there **0.04** → effectively blank.
* **Therizinosaurus** — 100% `0x8a` (index 16.78), opacity there **0.26** → the whole body takes a
  26% near-black tint.

**The fill is NOT what separates them.** `0x89` and `0x8a` are one byte apart, perceptually
identical, and both read as **L=57 in Photoshop**. Both species also author the *same* zero key at
position 16. What differs is the ramp immediately after it:

```
Scorpios rex      (16, 0.0), (20, 0.224), (22, 0.763)   ->  at 16.78: 0.043
Therizinosaurus   (16, 0.0), (19, 1.0)                  ->  at 16.78: 0.259
```

Therizinosaurus climbs to full opacity by position 19, so being 0.78 of a slot past the zero already
costs 0.26. Refilling its map with `0x89` gives 0.218 — no real improvement. The fix is on the FGM
side: a zero key at 17, or move the `19 -> 1.0` key further out.

> **Author the fill in HEX, never by Lab L.** Bytes `0x88`, `0x89` and `0x8a` all display as L=57
> in Photoshop, spanning LUT index 16.53–16.78. Matching a background by eye or by the L slider
> lands on any of the three, which is harmless under a gentle ramp and not harmless under a steep
> one.

### Duplicate key positions are real, and np.interp corrupts them silently

Therizinosaurus carries **three keys at position 11** (`0.0`, `0.24`, `1.0`); Scorpios rex carries
two at position 20. `np.interp` requires strictly increasing x and returns something arbitrary
otherwise — no error. That silently flattened the 11..16 region and made an early version of
`threshold_from_model` report Therizinosaurus' zero at index 14 instead of 11 and 16, which in turn
produced a confidently wrong "refill the map with `0x73`" recommendation.

`bake_channel` now dedupes by position, **last slot wins**. That matches the FGM's slot layout
(later slots overwriting earlier) but is an **assumption**, not measured — see open question 8.

`pattern_lut.resolve_threshold()` therefore reports **agree**, **mismatch** (warns that the pattern
will tint the whole animal), or **flat** — and for a flat map it returns `uniform_opacity` and
`uniform_colour` so the caller can tell blanking from a deliberate full-body wash. `Threshold.blank`
is `uniform_opacity <= BLANK_OPACITY` (0.08), not merely `lo == hi`.

Dreadnoughtus/Titanosaurus and SpinosaurusJWR are not flat, so they remain genuine mismatches.

### Why the background is mid-grey

`0x89` -> index 16.65, the **middle** of the 0..31 axis, so a pattern can travel in **both**
directions. The user's screenshot of `lokiceratops_patternset_01.u_basepatternmap.png` shows exactly
that: pale near-white blotches *and* near-black stripes on the same `898989` field. Herrerasaurus at
`00` can only travel up, and its opacity curve ramps up accordingly; Dimetrodon at `cd` (24.9)
travels mostly down.

Artists pick the background from the pure greyscale axis — `898989`, `8a8a8a`, `cdcdcd`, `000000` —
R=G=B in every map measured.

**The colour keys at the background index are DON'T-CARE**, because the opacity mask hides them.
That is why they vary wildly between FGMs of the same species: Lokiceratops pins pure black in all
six, while `baryonyx_pattern_01_04` leaves a vivid `222,102,4` orange sitting there. Do not read
meaning into that value, and do not "fix" it.

### Consequences

- **Keys outside a species' map range are DEAD.** Dimetrodon's map reaches indices 23.2–29.9, so any
  key it authors below ~23 can never be sampled. An editor should grey those slots out —
  `pattern_lut.reachable_range()` computes it.
- **The background index needs opacity ~0 or the pattern tints the whole animal.** That is a real
  authoring validation rule, and it is what the modded assets get wrong.
- A **flat** map samples one slot for the whole body. Filled with the transparent value it means
  "no pattern"; filled with anything else it washes the entire animal in that slot's colour. Both
  are deliberate techniques — see the section above. Never assume flat means blank.

## 4.7 The pattern composite, read out of the IR — MEASURED 2026-08-08

Traced in `ir/0300_ps_DinosaurLayered_Layered_Opaque_GBuffer_0_Win64_SM60.txt`, the pattern block
runs **L3628–L3961**. This closes open questions 2, 3 and 5, and most of 1.

### 4.7.1 Where the material parameters live

The material block is **nine consecutive 64-byte rows** of the structured buffer `T4` (`t21`), with
the base row supplied by the `__USER_CLUSTER_BINDLESSOFFSET` input (`%60`), so a parameter address is
`row*16 + byte/4` as a flat dword index. Row 8 carries the patterning control words:

| dword | role |
|---|---|
| `[8][20]` | patterning master enable (`%440`) — zero disables the whole block |
| `[8][24]` | bindless index of the 32×32 gradient LUT (`%442`) |
| `[8][28]` | index into the `T1` byte-address pattern-slot table (`%444`) |

**The slot order is NOT the FGM's texture order.** `pBaseDiffuseTexture` is texture `[1]` in the file
but the slots sampled four times each (once per `pLayered_BlendWeightBatchCount` batch) are d13/d14/d15,
which are layered maps. Do not map FGM texture index to dword slot by position.

### 4.7.2 The pattern index and its flags are PER-CREATURE, not per-material

    id     = PACKEDFACEINVARIANTS & 0x7FFFFFFF     ; per-primitive invariant
    rec    = T5(t22)[id] + 96                      ; -> entity record
    word   = T12(t113)[rec] + 4
    patternIndex  = word        & 255              ; %2965 — the LUT ROW
    patchworkFlags= (word >> 16) & 255             ; %2972

So the pattern a given animal wears, **and its patchwork flags**, arrive as instance data, not from
the FGM. `patternIndex == 255` means "no pattern" (`%3126 = icmp ult %3008, 255`).

### 4.7.3 Patchwork is a five-region ON/OFF GATE — SOLVED, and VERIFIED IN GAME 2026-08-08

    region = (uint)(u_basePatchworkMap.Sample().x * 4.99) & 31    ; %2999-%3001
    apply  = (patchworkFlags >> region) & 1                       ; %3002-%3005
    if (!apply) -> branch past the ENTIRE pattern block (L3712 -> label 3220)

The multiplier is the literal `0x4013F5C280000000` = **4.99**, so for a map value in `[0,1]` the
region ID is **0–4 — five regions, never more**; the `& 31` can never fire.

**This is why `u_patchworkFlags` is 31 in every shipped pattern FGM.** 31 = `0b11111` = all five
regions enabled. And the gate is itself guarded by `icmp ult flags, 31` (`%2997`): when flags ≥ 31
the shader takes `%3007 = 1` and applies the pattern unconditionally, never sampling the region at
all. So **the patchwork path is inert in all shipped content**, which is consistent with how few
species ship a map at all.

Of the three candidates previously listed — patch-selects-variant, mask, per-patch index offset —
it is **the mask**, and specifically a *region-gated on/off mask*: a texel whose region bit is clear
receives no pattern whatsoever, not a different one.

#### The in-game proof — an INVERSION test

Run on Atrociraptor Female (patchwork map R3 = 40.1%, R4 = 59.9%), editing the six
`atrociraptor_pattern_01_0N.fgm` inside `Atrociraptor_Female.ovl`.

The first attempt — flags 31 → 15 and nothing else — produced **no visible change**, and that null
was worthless: it could equally mean the flags don't come from the FGM, the game never loaded the
edited OVL, or the change was real but invisible. The last is the likely culprit: Atrociraptor's
pattern paints `[0.16,0.11,0.07]` dark brown and pure black at opacity 0.7 onto a dark brown animal.
**Design the positive control before reading a null.**

Second attempt added one: every used colour key set to a saturated hue (one per slot, so the colour
that appears names the pattern index) and every used opacity key forced to 1.0. Result: vivid
magenta — confirming slot 0, that the OVL loads, and that pattern FGM data reaches the shader.

But magenta arrived in *patches*, and patches alone prove nothing: the opacity LUT has no keys below
position 14, so the pattern map by itself could blotch the same way. The decisive run changed
**exactly one value, `u_patchworkFlags` 15 → 16** — the complement mask, `0b01111` → `0b10000` —
leaving colour and opacity byte-identical:

| surface | flags = 15 | flags = 16 |
|---|---|---|
| head, snout, jaw | base skin | **patterned** |
| legs, feet | base skin | **patterned** |
| belly, underside | base skin | **patterned** |
| central torso, neck, back | **patterned** | base skin |

A clean complement, with coverage growing from ~40% to ~60% — matching the predicted R3/R4 split.
Since the opacity keys were identical across the two builds, **nothing but the region mask can
invert coverage.** The gate is real, and `u_patchworkFlags` on the pattern FGM drives it.

#### `u_usePatchwork` IS the master enable — ISOLATED 2026-08-08

Third run: identical build, `u_usePatchwork` 1 → 0, flags left at 16. Result: **the animal rendered
100% uniformly patterned, no base skin anywhere.** The gate does not run at all.

So the patchwork gate needs BOTH:

    u_usePatchwork  == 1      <- CPU-side master enable, absent from the shader
    u_patchworkFlags <  31    <- shader-side guard, `icmp ult flags, 31`

**This corrects the earlier reading of why patchwork is inert in shipped content.** `flags == 31` is
not the primary reason — `u_usePatchwork == 0` is, and it alone disables the feature regardless of
what the flags say. The engine evidently only writes real flags into the instance byte when
`u_usePatchwork` is set.

#### FULL-INSTALL CENSUS 2026-08-08 — and it retires the "always 31" claim

Every dinosaur OVL in `Content0`, `ContentDeluxe`, `ContentPDLC1` and `ContentPDLCRebirth` scanned:
**2059 pattern FGMs, 0 load failures.**

| | |
|---|---|
| `u_usePatchwork` | **0 in all 2059.** No exceptions. This is the single reason patchwork is inert |
| `u_patchworkFlags` | 31 in 2029 — **but 30 files differ**: 0 (×25), 14 (×2), 20 (×2), 29 (×1) |
| `u_usePatternLUT` | 1 in 1600, **0 in 459** — 22% of patterns bypass the LUT (open question 4) |
| patternsets shipping a patchwork map | **100, across 63 distinct species** |

**"`patchworkFlags` is 31 in every shipped file" was WRONG** and is retracted. Three cases look like
genuine authoring rather than defaults, all with `usePatternLUT=1`:

    dryosaurus_pattern_01_05        flags 20 = 0b10100   zones 2 and 4
    giganotosaurus_pattern_01_05    flags 29 = 0b11101   zones 0,2,3,4
    patagotitan_female_pattern_01_01 / _03   flags 14 = 0b01110   zones 1,2,3

So somebody did author patchwork masks. They never fire, because `usePatchwork` is 0. The 25 files
at flags 0 are different — mostly juvenile patterns that also carry `usePatternLUT=0`, i.e. blanks.

Two earlier figures came from the partial 30-species texture dump and were badly off: "11 of 35
patternsets ship a map" (really 100 across 63 species) and "139 shipped pattern FGMs" (really 2059).
**Never quote counts off that dump** — see the sample-size caveat in §4.7.5.

The FDB carries the feature too: `c0dinosaurs.fdb` → `SpeciesCosmeticSets.PatchworkFlags`, declared
`STRING` with no default and **NULL in all 407 rows**. A bitfield stored as text implies a
designer-facing format (`"3,4"`), so the intended authoring route existed and was never filled in.

It also **retroactively removes the opacity-LUT confound** from the inversion result above: with the
gate disabled the coverage is total, which proves the baked opacity LUT really is 1.0 across the
whole range. The blotches seen at flags 15 and 16 were therefore 100% the patchwork gate and nothing
else. Screenshots and the exact builds are under `Backup_PatchworkTest\`.

#### The low regions work too — zones 0 and 2 VERIFIED 2026-08-08

Shipped maps only ever use regions 3 and 4, so the low regions were untested. Fourth run **rewrote
the patchwork texture itself**, preserving the exact spatial layout but relabelling the zones —
old region 3 → **region 0** (grey 26), old region 4 → **region 2** (grey 128) — then set
`u_usePatchwork = 1`, `u_patchworkFlags = 1` (`0b00001`, bit 0 only).

Result: **an exact match to the flags = 15 render** — magenta torso/neck/back, base-skin
head/legs/belly. Region **0 paints** and region **2 masks**, behaving identically to 3 and 4.

Combined with the earlier runs the gate is confirmed for regions 0, 2, 3 and 4. Region 1 is only
demonstrated as a mask bit, never as a painting one, but the shader does a generic `1 << region`
then `AND` with no region-specific branch, so **treat all five zones as usable.**

The region formula `region = trunc(v/255 × 4.99)` is confirmed by construction: greys 26 and 128
were chosen as region centres and landed in zones 0 and 2 exactly as predicted.

**Texture injection recipe** (`ovl_tool_cmd.py extract --name … / inject -f … --in-place
--update-aux`) round-trips a BC4_UNORM 1024² map cleanly — the re-extracted map read back
`{0: 40.1%, 2: 59.9%}`, matching the authored values. **Injection rewrites the `.aux`**, so the
`.aux` must be deployed alongside the `.ovl`; copying only the `.ovl` leaves the old texture live.

### 4.7.4 The LUT lookup — and it confirms `v × 31`

The gradient map is a **32×32 RGBA** texture: 32 columns = LUT positions, 32 rows = pattern slots.

    u  = patternMap.x * 0.96875 + 0.015625     ; = (v*31 + 0.5)/32
    v0 = patternIndex * 0.03125 + 0.0078125    ; = (row + 0.25)/32  -> RGBA: colour.rgb + opacity.a
    v1 = patternIndex * 0.03125 + 0.0234375    ; = (row + 0.75)/32  -> RGB : emissive.rgb

`u = (v·31 + 0.5)/32` is **exactly** the `v/255 × 31` mapping §2.2 inferred from the key spans — now
proven rather than suggestive. Colour+opacity and emissive are two sub-rows of the same LUT row,
which is why they are authored as one 32-entry axis.

Because `u` only lands on a texel centre when `v·31` is an integer, the **linear sampler blends
adjacent LUT entries** for in-between map values. What remains open from question 1 is whether the
32 entries themselves are baked as a ramp or as steps between the sparse keys — that is CPU-side
baking, not shader behaviour.

### 4.7.5 Which map is which — settled by corpus presence, NOT by value range

The two maps occupy dwords 32 and 33; d32 feeds the LUT column, d33 feeds the region gate. Assigning
names by value range **does not work and was tried**: both map types are dominated by the same
plateau levels (137 or 205, at 60–97% of texels), so §2.1's min/max ranges are tail artefacts.

The discriminator is presence across the corpus. In
`…\Personal Mods\JWE3\Images and Models\Dinosaurs`: **32 `u_basepatternmap` textures against 4
species with a `u_basepatchworkmap`** (IndominusRex lux, Lokiceratops, SpinosaurusJWR,
Therizinosaurus). The LUT column is sampled on every patterned draw, so it must be the map every
patternset ships. Hence **d32 = `u_basePatternMap`, d33 = `u_basePatchworkMap`**.

**Sample-size caveat.** That dump holds **26 species folders; the live game has 130** (Land 110,
Air 10, Water 9, Shared 1) — roughly 20% coverage. What is safe is the *within-sample asymmetry*:
of the 26 species dumped, **100% carry a pattern map and ~15% carry a patchwork map**, and that is
what the role assignment rests on. What is NOT established is the absolute count — "four species in
the game" is wrong; at 15% the real figure is plausibly ~20. §4.7.6's five-plateau authoring
guidance rests on n=4 and should be re-checked against a full sweep before it is trusted.

### 4.7.6 What this means for authoring

- `u_patchworkFlags` is a **5-bit mask over five zones**, not the 32 slots §2.2 guessed at. Values
  0–30 are meaningful; 31 disables masking entirely. **Confirmed in game** (§4.7.3) — editing it on
  the pattern FGM changes which surfaces carry the pattern.
- Plateau values map to regions by `region = trunc(v/255 × 4.99)`, so the region centres are
  `v ≈ (region + 0.5)/4.99 × 255` → roughly **26, 77, 128, 179, 230**. **Only regions 3 and 4 are
  proven**; that is what the in-game test exercised and what 8 of the 9 shipped maps use (plateaus
  at ~191–204 and ~205+). IndominusRex lux is the lone exception, using regions 1–3. Five-zone
  authoring is *plausible* — the shader clearly supports 0–4 — but **untested**, and no shipped map
  uses more than two zones. Prove a third zone before relying on one.
- Since the flags reach the shader as **instance** data, per-creature patchwork variation is
  mechanically possible even though every FGM on disk says 31. That is the interesting lead, and it
  is **not yet proven** that the engine ever writes anything but 31 into that byte.

## 5. Open questions

1. **Where is the pattern composited, and how?** Partly answered in §4.7 — the LUT fetch and its
   indexing are settled, and the sampler blends adjacent entries. Still unknown: whether the block
   lands before or after the palette grade, whether the key colour replaces or blends the albedo,
   and whether the 32 baked entries ramp or step between sparse keys.
2. ~~**How is the LUT indexed?**~~ **SOLVED §4.7.4** — `u = (v·31 + 0.5)/32`, exactly the mapping
   §2.2 inferred.
3. ~~**What is `u_patchworkFlags`?**~~ **SOLVED §4.7.3** — a 5-bit mask over five patchwork regions;
   31 means "all zones on", which short-circuits the gate entirely.
4. **What does `u_usePatternLUT = 0` mean?** True of 6 of 28 files. Alternate path, or a disabled
   pattern?
5. ~~**What is the `patchwork` path for?**~~ **SOLVED §4.7.3** — a per-region on/off gate on the whole
   pattern block. Inert in shipped data because every file sets flags to 31.
6. **`variantset_jw` structure** — `has_sets="1"`, unread. The film-cosmetic mechanism.
7. **Where does `u_furTint` go?** It is in every variant FGM, all 144 attributes of which are shared
   byte-for-byte between body and feather variants, and the Blender reproduction has never used it.
8. **How does the game resolve DUPLICATE key positions?** Real files carry them (Therizinosaurus has
   three keys at position 11, Scorpios rex two at position 20). `bake_channel` assumes the last slot
   wins. Untested against the shader — settle it from the IR, or by authoring a duplicate pair with
   two obviously different colours and looking at the result in game.

## 6. Method note — the correction that mattered

Two claims made early in this research were **wrong**, both from reading an incomplete extraction
rather than the game files:

- *"Baryonyx and Spinosaurus have no patterns."* False. That was the state of
  `Variant Research/Textures/`. Baryonyx has **7 patterns + blank** in
  `…\Personal Mods\JWE3\Images and Models\Dinosaurs\Land (Base)\Baryonyx\Female`.
- *"A species has ~7 patterns, the last being blank."* False as a rule — see §2.3.

Both would have been caught by checking a second species before generalising. `Variant Research/
Textures/` is a **partial dump**; the `Personal Mods` tree is the fuller extraction. Prefer it.
