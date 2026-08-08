# The JWE3 dinosaur colour model, read off the shader

Source: `ir/0300_ps_DinosaurLayered_Layered_Opaque_GBuffer_0_Win64_SM60.txt`.
Solved 2026-07-24. Line numbers below refer to that file.

Every step here is read directly from the disassembly, not fitted. Where a step matches an
earlier in-game measurement, that is noted — those are the cross-checks.

---

## 0. Where the parameters live

The material's parameters are a block in a bindless structured buffer, indexed by a byte from
the material table (255 = "disabled"). Within the block:

Read as `uint4`s. Exact layout, decoded from the IR (register names in brackets):

| load | word | contents |
|---|---|---|
| `+0` | 0 `[%2682]` | lo f16 = **keyColour.r**, hi f16 = **keyTolerance** |
| | 1 `[%2683]` | lo f16 = **keyColour.g**, hi f16 = **keyThreshold** |
| | 2 `[%2684]` | lo f16 = **keyColour.b**; bit 16 = **keyType**; bit 17 = palette-gradient enable; bits 24–31 = **paletteStrength** (byte/255) |
| | 3 `[%2685]` | lo f16 = **instancePaletteScale**, hi f16 = **instancePaletteOffset** |
| `+16` | 0 `[%2688]` | hue-rotation matrix, **Base** (3 x signed 10-bit) |
| | 1 `[%2689]` | hue-rotation matrix, **Palette** (3 x signed 10-bit) |
| | 2 `[%2690]` | lo f16 = **brightnessBase**, hi f16 = **brightnessPalette** |
| | 3 `[%2691]` | lo f16 = **saturationBase**, hi f16 = **saturationPalette** |
| `+32` | 0 `[%2694]` | gradient **offset** (a) — 3 x signed 10-bit |
| | 1 `[%2695]` | gradient **amplitude** (b) |
| | 2 `[%2696]` | gradient **freq** (c) |
| | 3 `[%2697]` | gradient **phase** (d) |

**The block is 48 contiguous bytes.** The shader's `+16` / `+32` are element offsets into a
`Buffer<uint4>`, i.e. one and two `uint4`s along — *not* +256 / +512 bytes. Confirmed by finding a
real block in a capture: word 3 sits exactly three dwords before word 6.

**VERIFIED against a real capture** (`JWE3_2026.07.20_12.20_frame14499.rdc` @228062360,
Albertosaurus_Juvenile variant 4). Ten independent values agree with the FGM: brightness
(1.0, 0.49), saturation (1.0996, 2.037), palette scale 4.199 / offset 3.449, strength 0.502, and
both hue matrices. See `..\Variant Research\harvest_blocks.py`.

### The hue-rotation matrix is confirmed, and so is the rotP law

```
theta = pi * rot
p = cos + (1-cos)/3      q = (1-cos)/3 - sqrt(1/3)*sin      r = (1-cos)/3 + sqrt(1/3)*sin
packed = round(511 * (p, q, r))
```

For `rotP = 0.322` this predicts `(351.1, -170.1, 330.1)`; the capture holds `(351, -170, 330)`.
That is an exact, independent confirmation of the separately measured "hue += 180 deg * rotP" law.

**Free structural invariant:** `p + q + r = 511` for *any* angle, because
`cos + 3*(1-cos)/3 = 1`. Two packed triples both summing to 511 is a ~1-in-millions coincidence,
which is what makes scanning a multi-gigabyte capture cheap.

### Known anomaly

Bit 16 of word 2 decodes as 1 on the Albertosaurus block, but that variant's FGM has
`u_globalKeyType = 0`. Either the sense is inverted or bit 16 is something else (perhaps
`pEnableGlobalColouring`). Unresolved — do not trust `keyType` from a decoded block until a second
sample settles it.

Packing conventions used throughout:

- **signed 10-bit x3 in a uint** — `shl 22 / ashr 22`, `shl 12 / ashr 22`, `shl 2 / ashr 22`,
  then scaled by **1/511**.
- **f16 pairs in a uint** — low half and `lshr 16`.
- **byte** — scaled by 1/51 (range 0–5) or 1/255 (range 0–1).

## 1. Remap LUT (line ~1477)

```
u   = dot(baseRGB, Rec709) * 31/32 + 1/64      # luminance -> column
v   = remapIndex / 16 + 1/32                   # layer      -> row
rgb = SampleLevel(remapTexArray[slice], u, v, 0)
```

The `31/32 + 1/64` and `1/16 + 1/32` are exact texel centring for a **32 x 16** texture — the
size of `pRemapTexture` in `SwatchLibrary.ovl`, which is an array addressed by slice. The shipped
content is a greyscale ramp, i.e. identity / no recolour.

`remapIndex` is unpacked from a flags word: bit 14 enables, bits 8–11 are the index, bit 13 is a
second enable. Disabled yields −1 and the sample is skipped.

## 2. Base grade (line ~1227)

```
g = sqrt(dot(c, c * Rec709))                   # note: sqrt of a weighted square, not plain luma
c = g + (c - g) * saturation                   # u_baseColourSaturationN
c = (c - 0.5) * contrast + 0.5                 # u_baseColourContrastN
c = saturate(c)
```

Contrast arrives as a **byte scaled by 1/51**, so it is quantised to 0–5 in steps of ~0.0196,
with 255 meaning "disabled, use the default". Worth remembering when a measured law looks steppy.

## 3. Key-colour mask (line ~3374)

```
m     = saturate(1 - saturate(length(albedo - keyColour) * keyTolerance))
sign  = keyType ? -1 : +1
bias  = keyType ?  1 :  0
blend = saturate(keyThreshold * sign * m + bias)
```

**Cross-check:** this reproduces the measured "`u_globalKeyTolerance` is the master recolour
switch". A large tolerance drives the distance term past 1, so `m -> 1` over the whole animal and
everything is repainted.

## 4. Base and Palette hue grades (line ~3420)

Two independent grades, computed identically and then blended by `blend` from step 3:

```
c = albedo * brightness                        # f16
c = M * c                                      # circulant hue-rotation matrix
g = sqrt(dot(c, c * Rec709))
c = g + (c - g) * saturation                   # f16
```

`M` is a **circulant 3x3**, stored as three signed 10-bit values `(p,q,r)` in one uint:

```
row0 = (p, q, r)      row1 = (r, p, q)      row2 = (q, r, p)
```

which is exactly the standard hue-rotation-about-the-grey-axis matrix. **It is precomputed on the
CPU from `rotB` / `rotP`** — which is why this shader contains almost no trigonometry, and why
grepping for `Sin`/`Cos` never found the palette.

Grade A is Base (`rotB`, `satB`, `brightB`), grade B is Palette (`rotP`, `satP`, `brightP`).
Result: `lerp(A, B, blend)`.

## 5. The palette gradient — the answer (line ~3498)

```
t      = height * 100 * instancePaletteScale + instancePaletteOffset
arg    = 2*pi * ( (t / 51.1) * freq + phase / 511 )
colour = saturate( (offset + amplitude * cos(arg)) / 511 )
```

**Watch the scaling — it is not uniform.** `freq` is used **raw**; `phase` is divided by 511
*before* the 2*pi; `amplitude` and `offset` are raw and the *sum* is divided by 511. Exact
register chain: `%2864 = t/51.1`, `%2865 = %2864*freq`, `%2868 = %2865 + phase/511`,
`%2871 = %2868*2pi`, `%2877 = cos*amplitude`, `%2880 = %2877 + offset`, `%2881 = %2880/511`.

That is **Inigo Quilez's cosine-gradient palette**, `a + b * cos(2*pi*(c*t + d))`, evaluated per
RGB channel, parameterised by the **height map**.

`offset`, `amplitude`, `freq`, `phase` are four vec3s of signed 10-bit values packed three to a
uint, at material block `+32`. **The seed selects these twelve numbers**, baked CPU-side.

This is why the palette is shared across all 323 species, and why no palette texture exists
anywhere in the game.

It is then mixed toward mid-grey:

```
strength = globalColourWeight * (instancePaletteStrength / 255) * blend
out      = colour * strength + (1 - strength) * 0.5
```

**This settles the naming question.** `u_instancePaletteScale/Offset/Strength` are not a colour
grade — they map the height map into the gradient. The `Collection_Variant` material, which is
the same system with un-obfuscated names, calls them `pPaletteHeightScale`,
`pPaletteHeightOffset`, `pPaletteHeightStrength` and `pPaletteHeightComplexity`.

### Where `height` comes from (line ~2401)

Per layer, `pHeightTexture` red channel is sampled at that layer's UV (`sampleBias`, array slice
per layer), scaled by a per-layer f16 and offset by another (the offset is byte-scaled by 0.01,
i.e. a percentage). The 16 layers are then composited by **height-blending** — the taller layer
wins, with a soft edge whose width comes from the layer mask — and that composited height is what
feeds `t`. It is accumulated in the same unrolled 16-iteration loop as albedo, normal and weight,
so the palette is applied **once to the blended surface**, not per layer.

### `u_globalPaletteMaximumComplexity` is NOT in the shader

Searched the whole of container 300: there is no `Round`, `Floor` or `Trunc` anywhere in the
palette path (the three rounding ops in the file are elsewhere). Complexity never reaches the GPU
as its own value, so it must be folded into the twelve coefficients CPU-side. The name
("**Maximum** Complexity") suggests a cap on `freq`, but that is inference, not a reading.

**Consequence for any fit:** the unknown is keyed on `(seed, complexity)`, not seed alone.
The shipped variants use **720 distinct (seed, complexity) pairs** across 239 seeds — 46 seeds
appear at only one complexity, but 3 appear at eight. Complexity 0 is *not* "palette off" (its 490
variants still use 78 distinct scale values).

## 6. Layer weight (line ~3486)

```
out = lerp(baseColour, gradedColour, globalColourWeight)     # u_globalColourWeightN
```

**Cross-check:** at `u_globalColourWeight = 1.0` the base path is multiplied by `1 - 1 = 0`, so
`u_baseColourSaturation/Contrast` from step 2 cannot possibly matter. That is precisely the
measured result — 25 attributes changed by up to 10x moved the render 0.03 degrees. The IR and
the null agree exactly.

---

## Rosetta stone

`Collection_Variant` (string pool after container 132) is the same colour system with readable
parameter names:

| dinosaur | Collection |
|---|---|
| `u_globalPaletteSeed` | `pMaterialSeed` |
| `u_globalColourRotationOffsetBase` | `pBaseKeyHueRotation` |
| `u_globalColourRotationOffsetPalette` | `pPaletteKeyHueRotation` |
| `u_globalColourSaturation/BrightnessBase` | `pBaseKeySaturation` / `pBaseKeyBrightness` |
| `u_globalPaletteMaximumComplexity` | `pPaletteHeightComplexity` |
| `u_instancePaletteScale` | `pPaletteHeightScale` |
| `u_instancePaletteOffset` | `pPaletteHeightOffset` |
| `u_instancePaletteStrength` | `pPaletteHeightStrength` |
| `u_globalColourWeight1..16` | `pGlobalColouringWeight_00..15` |
| `u_globalKeyType` | `pKeyColourMaskType` |

## What is still missing

**seed -> the twelve 10-bit coefficients.** Baked CPU-side, so it is in the game executable, not
in any shader or asset. Three routes, cheapest first:

1. **Fit from the existing captures.** 108 seeds are already catalogued, and a rendered animal
   spans a wide range of height values, so a 12-parameter cosine fit per seed is well
   over-determined. No new captures, no game launch. **Recommended.**
2. RenderDoc, now with an exact fingerprint: four consecutive uints at material block `+32`
   decoding to signed 10-bit triples.
3. Locate the bake function in the executable.

Everything else in this document is directly implementable in Blender today.

### `freq` is quantised, and complexity caps it (11 harvested rows, 2026-07-24)

**`freq` is always an integer multiple of 51** (= 511/10, i.e. 0.1 in normalised units), chosen
independently per RGB channel. Expressed in those steps:

| complexity | observed steps | complexity+1 |
|---|---|---|
| 0 | (0,0,0) — amplitude also (0,0,0) | 1 |
| 1 | (1,2,2) (1,1,1) (2,2,2) (2,1,1) | 2 |
| 2 | (3,3,1) (1,3,3) (3,1,3) | 3 |
| 3 | (4,4,1) | 4 |

**No channel's step ever exceeds `complexity + 1`.** That is exactly what the name
`u_globalPaletteMaximumComplexity` / `pPaletteHeightComplexity` should mean — a cap on the
gradient frequency, i.e. on how many colour bands appear across the height range. Complexity 0
zeroes both frequency and amplitude, so the gradient is a flat mid-grey and the palette is
effectively off.

10 of 11 harvested rows obey this exactly. Two *different* seeds at the same complexity can have
different steps, so the seed picks the step per channel and complexity only bounds it.

**Sharper: the step is only ever 1 or `complexity + 1` — never anything between.** All 9 usable
rows obey this, including four independent complexity-2 rows whose steps are drawn only from
{1, 3} and never 2. If the step were uniform over `1..complexity+1` the chance of that is 0.001.
So each channel's frequency is **one bit**, not a ~3-bit choice: "slow" or "as fast as complexity
allows". Caveat that matters — complexity 1 makes the claim vacuous (`1..2` *is* `{1,2}`), so the
whole result rests on five rows, and it needs re-checking as soon as more complexity ≥ 2 rows
arrive. Re-run `analyse_coefficients.py` after every harvest; it prints the sample size and the
chance figure itself.

### Ranges of the other three (27 values each, 9 rows)

| vector | observed | of full scale | plausible true range |
|---|---|---|---|
| amplitude | 128..255 | 0.250..0.499 | **0.25 .. 0.50** |
| offset | 182..408 | 0.356..0.798 | ~0.35 .. 0.80 |
| phase | 0..511 | 0.000..1.000 | 0 .. 1 (a plain fraction of a turn) |

Amplitude landing on 0.250..0.499 with 27 samples is about as clean a read on `[0.25, 0.5]` as
that many samples can give. Offset is less certain; 0.35..0.80 is a guess at round designer
numbers, and the honest statement is "somewhere near there". All three look uniform (no
significant skew about their midpoints). None of this is a prediction rule yet — it constrains
the search, it does not replace the numbers.

**Use this as a validity filter.** The single non-conforming row (seed 30, complexity 5, steps
0.39/9.33/9.75) is almost certainly a misidentified block, not a real counter-example — any row
whose `freq` is not a multiple of 51 should be discarded.

**Caution — do not over-generalise from R=G.** The first three rows harvested all happened to have
freq R = G, and that briefly looked like "freq depends only on complexity". It does not; the wider
sample shows all three channels varying independently.

### Harvest status

`..\Variant Research\harvest_blocks.py` extracts blocks from any `.rdc` in `%TEMP%\RenderDoc`,
writing `gradient_coefficients.json`. The method is proven end to end, but the three captures from
2026-07-20 yielded **only one block** — a capture contains a block only for materials actually
being rendered, so one visible dinosaur means one block.

11 rows harvested so far, in `gradient_coefficients.json`.

**The fingerprint must NOT include `instancePaletteScale`/`Offset`.** They are *instance*
parameters — the game varies them per animal, so the GPU block never matches the FGM value.
Including them cut the yield from 11 blocks to 2. Match on brightness + saturation (four f16
values), then confirm with both hue matrices.

### What limits the yield (measured, `diagnose_harvest.py`, 2026-07-24)

The funnel on the 1.14 GB park capture:

| stage | count |
|---|---|
| structural (both hue triples sum to 511) | 7967 |
| word6 in the known brightness set | 25 |
| full (word6, word7) fingerprint | 11 |
| rejected by the hue confirm | **0** |
| confirmed | 11 |

Read that carefully, because it corrects an earlier guess in this file. 7967 structural hits is
what random data alone predicts over 285 M words (~3800 expected), so the cheap filter is finding
noise, not hidden blocks. Only ~25 real dinosaur material blocks were resident at all, and the hue
confirm rejected **nothing** — the fingerprint is not too strict, there is simply nothing more
there.

**So yield ≈ the number of distinct (species, variant) pairs actually being rendered.** The
Baryonyx run (variants 00, 01, 02, 05 at 56/64/184-byte spacing) is four *animals* whose parameter
blocks were uploaded together, not a species' whole 16-variant set. The earlier claim that a
capture holds far more than the dinosaurs on screen was wrong.

Consequence: covering all 239 seeds needs ~239 rendered variants — a heavily stocked park and
several captures, not one lucky one.

### Ruled out (2026-07-24)

- **No baked table in the executable.** `hunt_seed_table.py` mask-searched all four coefficient
  words for every harvested row across JWE3.exe (327 MB) and RenoirCore.dll. Two isolated hits,
  67 MB apart, against 2.7 expected from chance alone on a 30-bit compare. The coefficients are
  computed at load time.
- **Not a stock PRNG with an obvious decode.** `hunt_seed_prng.py` tried 11 generators (MSVC,
  glibc, minstd ×2, xorshift32, Java, drand48, splitmix64, pcg32, mt19937, xoshiro128\*\*) × 5
  seedings, in both per-seed and single-indexed-stream modes, testing whether a row's three phase
  values appear as `round(511*u)` in the first 64 draws. Best was 2/9 rows, i.e. noise. Either the
  decode differs, phase is drawn past index 64, or it is not a stock generator.

**Still needed:** more rows, especially at complexity 4–10 where we have almost nothing. Nine
unknowns remain per seed (offset, amplitude, phase); `freq` is now down to one bit per channel.

**But note this is not on the critical path for Blender.** Reproducing a *specific* variant needs
only that variant's block, which a capture with that animal on screen gives exactly. The seed →
coefficient map is what would let us predict variants we have never captured; it is a bonus, not a
prerequisite.

---

## 9. `%97` — the height multiplier, and why previews come out washed out (2026-08-01)

**This is the largest known error in the Blender reproduction**, found while chasing "Sonoran
Desert Pyroraptor is not orange enough". Re-read of container 300 confirms the section 5 formula is
correct as transcribed, with one correction (below) — the fault is a value we have never measured.

### The divisor is 51.1, not 51

`%2864 = fmul fast float %2824, 0x3F940A0500000000`, and that constant is **0.01956947 = 1/51.1**,
not 1/51 (0.01960784). A 0.2% difference, so it changes nothing visible, but the transcription above
is now exact. The `* 100` in `t` is real and confirmed at `%2822 = fmul fast float %2208, 1.0e+02`.

### `%97` multiplies every layer's height

```
%95 = bufferLoad(handle %12, index %60, offset 44)
%97 = bitcast %96 to float
```

`%12` is `createHandle(rangeId 4, index 21)` and `%60` is `loadInput(sigId 0)` —
`__USER_CLUSTER_BINDLESSOFFSET`. So `%97` is a **per-draw** float at byte 44 of a cluster record in
a different buffer from the material-parameter block (which uses handle `%2679`), and it multiplies
**both** height terms of every layer — the texture term (`%977`) and the offset term (`%979`).

### Why it dominates the result

The gradient completes one cosine cycle per `51.1 / (freq * 100 * paletteScale)` of composited
height. For Pyroraptor variant 00 (`paletteScale` 5.0, `freq` 51/204/204) that is **0.002** in red
and **0.0005** in green and blue. But the composited height only spans about **0.0004** at
`%97 = 1` (measured on Lokiceratops layer 1).

So at `%97 = 1` the gradient traverses well under one cycle across the whole animal and lays down a
single near-constant colour — for variant 00, a pale sandy tan around sRGB `(235, 202, 174)`. The
game shows a considerably more saturated orange, which is what *more of the cycle* looks like.

**The game is not sampling a different palette. It is sampling more of the same one.**

`blender_layer_nodes.HEIGHT_SCALE` stands in for `%97` and is currently **1.0, a guess**. Every
washed-out, "unifying pale wash" preview is explained by it.

### Finding it in a capture

Not readable at a fixed offset from the palette block — different resource. The cluster record is
64 bytes and the shader reads, at index `%60`:

| offset | type | use |
|---|---|---|
| +4  | int   | flag, tested `!= 0` |
| +8  | int   | flag, tested `== 0` |
| +16 | int   | |
| +20, +24, +28 | float | |
| +32, +36, +40 | float | scales object position |
| **+44** | **float** | **`%97`** — the height multiplier |
| +48, +52, +56, +60 | int | bindless indices |
| next record +0 | int | bindless texture index, `UMin(x, 24575)` |

The bindless indices are the usable structural signature: several consecutive uint32 all below
24576, next to two 0/1 flags and a float3 of plausible scale.

**The RenderDoc replay API is the right tool here**, reversing the conclusion recorded for the
palette blocks. That conclusion was correct for *those* — they are findable by byte scan because two
hue matrices summing to 511 is a strong, cheap filter. This buffer has no comparable signature, and
the replay API can simply read the SRV bound for a draw instead of guessing at bytes.

### Hunting `%97` in a capture — result, and a NEGATIVE result that matters

A relaxed byte scan of `JWE3_2026.07.31_01.35_frame74161.rdc` (five consecutive plausible bindless
indices, finite sane floats at +32/36/40/44) yields **253 candidate records**, dominated by
**`%97 = 121.5` (172 occurrences)** with a tail of near values — 121.501, 121.651, 121.78, 121.93,
121.991, 123.5 — which is what a per-draw value with small per-instance variation looks like.
Unconfirmed: the signature is weak and nothing yet ties a record to a specific draw.

**But raising `%97` does not make a variant more saturated — it makes it LESS.** Modelled on
Pyroraptor variant 00 (seed 26, `paletteScale` 5.0, height span 0.0004 at `%97 = 1`), the mean
colour after the mid-grey mix and the overlay is:

| `%97` | red cycles across the body | mean sRGB |
|---|---|---|
| 1.0   | 0.20  | (187, 156, 132) warm tan |
| 10.0  | 2.00  | (167, 159, 135) |
| 121.5 | 24.25 | (167, 159, 135) |
| 484.0 | 96.61 | (167, 159, 135) |

Once the gradient completes more than about one cycle the result converges to its own mean, and the
mean of `offset + amplitude*cos` is just `offset/511` — a desaturated grey. **A large `%97` washes
colour OUT.** So `HEIGHT_SCALE = 1.0` is not what makes previews pale, and raising it cannot be the
fix for "the game is more orange".

This also means the earlier note in `blender_layer_nodes.py` — "raising it makes the gradient cycle
per scale instead" — is true but not desirable on its own.

### Where the saturation actually comes from for a variant like Sonoran Desert

Variant 01_00 has `keyType = 1`, `keyThreshold = 1.65`, **`keyTolerance = 0.1`**. A tolerance that
small makes `blend = saturate(1 - m/0.1)` switch hard, so most of the animal sits at `blend = 0` and
takes the **Base** grade, not the palette one. That grade is `brightnessBase = 2.68`,
`saturationBase = 0.897`, rotation ~0 — a strong brightening of the raw diffuse.

**So for this variant the palette is a minor term and the base grade dominates.** Any investigation
of "not orange enough" should start at `brightnessBase` and the key mask, not at the gradient.
