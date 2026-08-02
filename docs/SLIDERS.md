# What every control in the Variant Editor does

Read off the shader disassembly (`0238_ps_DinosaurFur_Vanilla_BaseLayered_GBuffer`), not inferred
from the attribute names. Several names are actively misleading — `keyTolerance` in particular does
not do what it sounds like.

Every value here corresponds to a `u_global*` attribute in the variant `.fgm`.

---

## The model, in one block

Everything below is a term in this:

```
mask     = saturate(1 - saturate(distance(rawDiffuse, keyColour) / keyThreshold))
keyBlend = saturate(1 - mask / keyTolerance)          # "1 -" present only when keyType is SET

baseGrade    = saturation(hueMatrixBase    x (brightnessBase    x albedo), saturationBase)
paletteGrade = saturation(hueMatrixPalette x (brightnessPalette x albedo), saturationPalette)
              + cosine gradient, scaled by colourWeight x paletteStrength x keyBlend

graded   = lerp(baseGrade, paletteGrade, keyBlend)
out      = lerp(albedo, graded, colourWeight)
```

Three things follow that are worth internalising before touching anything:

1. **Every texel is graded twice**, by two completely independent sets of
   brightness / saturation / hue. `keyBlend` picks which result it keeps.
2. **The key mask is measured against the RAW base diffuse texture**, not the composited albedo
   that the grade is applied to. Layers and patterns do not move the mask.
3. **The gradient only exists on the palette side.** On texels that take the base grade, palette
   strength and the seed do nothing at all.

---

## Seed and Complexity

| | |
|---|---|
| **Seed** | `u_globalPaletteSeed` |
| **Complexity** | `u_globalPaletteMaximumComplexity` |

The seed is **not a colour**. It is an index into a table baked inside the game executable that
maps **(seed, complexity)** — both together, never the seed alone — to twelve signed 10-bit
coefficients describing a cosine gradient. The same seed at a different complexity is a different
palette.

Because that bake happens in the executable, the coefficients cannot be read off disk. They are
recovered by harvesting them from a GPU capture. **A seed that has not been harvested grades
FLAT**, and a flat grade looks exactly like "this setting does nothing" — check the gradient badge
in the editor before concluding a control is broken.

Higher complexity generally means a busier gradient with more cycles across the body.

---

## The key mask: Key colour, Key threshold, Key tolerance, keyType

This group decides, per texel, **which of the two grades applies**. It is the single most
consequential thing in the file and the easiest to misread.

### Key colour — `u_globalKeyColour`
The reference the distance is measured from, compared against the **raw base diffuse**. It is
white on every Pyroraptor variant, which makes the mask effectively *"how dark is this texel"*.

### Key threshold — `u_globalKeyThreshold`
Divides that distance. Bigger threshold = more of the animal counts as "near" the key colour.

### Key tolerance — `u_globalKeyTolerance`
**Misleadingly named.** It divides the resulting **mask**, not the colour distance — the value is
uploaded to the GPU as `1/tolerance` and multiplied.

The practical effect is that **small values make a HARD split**. At `keyTolerance = 0.10`, a texel
has to sit more than 90% of the way to the threshold before it blends at all; everything else
snaps to one side. Large values give a soft ramp between the two grades.

### keyType — `u_globalKeyType`
Which side pale texels land on. Not a slider (it is a flag on the model), but it changes the render
more than any slider does.

> **The GPU bit is the COMPLEMENT of the FGM value.** `u_globalKeyType = 1` means the bit is
> CLEAR. This was measured in August 2026 against the game's own GBuffer albedo, on three variants
> — v00 and v02 (FGM 1, needing CLEAR) and v09 (FGM 0, needing SET). Because v02 and v09 carry
> opposite values *and* need opposite bits, no constant and no direct mapping can satisfy both.
>
> Getting this backwards is a ~3x brightness error on bare skin, and it looks like a brightness
> bug rather than a mask bug, which is why it went unnoticed for a long time.

---

## The two grades

Each side has its own brightness, saturation and hue rotation. They are independent — a variant can
be near-greyscale on one side and vivid on the other, and several shipped ones are.

### Brightness base / palette
`u_globalColourBrightnessBase` / `...Palette`. A straight multiply on the albedo, applied *before*
saturation and the hue matrix.

Anything above ~2 will push bright texels past 1.0 and clip them toward white. Palette-side values
are often well below 1 (0.5–0.8), because the gradient supplies the colour and the multiplier only
sets the level.

### Saturation base / palette
`u_globalColourSaturationBase` / `...Palette`. Interpolates the graded colour toward its own grey.

`1.0` unchanged · `0.0` fully greyscale · `>1.0` oversaturated.

The grey is an **RMS luma** — `sqrt(dot(c, c * Rec709))` — not the usual linear dot product. Values
like `0.131` (Pyroraptor v02) are a deliberate near-total desaturation, and on such variants the
hue comes from elsewhere entirely.

### Hue rotation base / palette
`u_globalColourRotationOffsetBase` / `...Palette`. Expanded into a circulant 3x3 matrix and uploaded
as ten-bit integers over 511.

Shipped variants keep this **tiny** — Pyroraptor's are `-0.007` and `0.072`, i.e. near-identity.
It is a nudge. It is not the way to recolour an animal, and treating it as one is a dead end.

---

## The gradient: Palette scale, Palette offset, Palette strength

The palette is a cosine gradient driven by the model's composited **height**:

```
t = height x 100 x paletteScale + paletteOffset
colour_i = (gradOffset_i + gradAmplitude_i x cos(2 pi (t/51.1 x gradFreq_i + gradPhase_i/511))) / 511
```

### Palette scale — `u_instancePaletteScale`
Sets how many cycles of the palette run across the body. Larger = tighter banding.

**More is not more colourful.** Past roughly one cycle the gradient converges toward its own mean,
which is a flat grey.

### Palette offset — `u_instancePaletteOffset`
Slides the gradient along the body — changes which part of the palette lands on the back versus the
belly, without changing the palette.

### Palette strength — `u_instancePaletteStrength`
How strongly the gradient contributes on the palette side. It is **gated**: effective strength is
`colourWeight x paletteStrength x keyBlend`, so on texels that take the base grade this does
nothing regardless of its value.

---

## Layer colour weights

Sixteen values, `u_globalColourWeight1..16`, mirroring each layer FGM's `pGlobalColouringWeight`.

A per-layer veto on grading. They accumulate as
`weight = lerp(previous, layerWeight, smoothstep(layerBlendMask))`, so a texel covered by any layer
whose weight is 1.0 ends up fully graded, and a texel no layer covers stays ungraded.

In practice they are nearly all `1.0` with a deliberate `0.0` somewhere — Pyroraptor zeroes slot 8,
`Swatch_Mouth_Flesh`, which is what keeps the mouth interior unpainted.

---

## Things that are NOT sliders but change the render more than they do

- **keyType** — see above. Worth more than any slider.
- **An unharvested seed** — grades flat, and reads as "nothing happened".
- **Whether the part is graded at all** — `fur_shell` and `fur_fin` sit over the body and occlude
  it almost completely. An ungraded shell shows the raw base texture, and every colour judgement
  made against it will be wrong.
