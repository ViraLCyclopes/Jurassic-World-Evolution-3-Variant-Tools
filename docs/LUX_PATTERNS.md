# Authoring a lux pattern — glow at night, invisible by day

Companion to `PATTERNS.md`. Everything here is measured off shipped files
(`parasaurolophus_pattern_ccblue_00`, `ccpink_00`, `indominusrex_patternset_lux`,
`indoraptor_pattern_lux_00`) and verified by baking the LUT.

Worked example: `Dinosaur Files/Lux Authoring/spinosaurusjwr_pattern_lux_00.fgm`, built by
`build_spino_lux.py`.

## TWO things are required, not one

A lux needs both, and they live in **different files**:

| | where | what it does |
|---|---|---|
| **1. opacity all `0.0`** | the pattern FGM | stops the colour row painting, so there is no albedo contribution |
| **2. `pPatterning_EmissiveAdaptiveBrighnessWeight` > 0** | the **master `<species>.fgm`** | suppresses the glow in daylight |

**Getting only #1 gives a pattern that glows in broad daylight.** Measured 2026-08-08: a yellow lux
built for SpinosaurusJWR with all-zero opacity still glowed at noon. An albedo-only capture showed
no yellow at all, proving the opacity half was working perfectly — the glow was pure emissive, and
emissive is added regardless of time of day unless something suppresses it.

The suppression is the adaptive weight, and it is **0.0 on almost every species**:

| master fgm | adaptive | maxBright | |
|---|---|---|---|
| parasaurolophus | **0.4** | **8.0** | ships ccblue / ccpink |
| indominusrex | **0.2** | **6.0** | ships lux |
| indoraptor | **0.2** | **6.0** | ships lux |
| *35 other base-game species* | 0.0 | 4.0 | ship no lux |

Every base-game species that ships a lux has a non-zero weight; every one that does not has zero.
That is a clean split, but it is **correlation across 3 positive cases, not a traced mechanism** —
the parameter has not been followed through the IR. Treat the direction as very likely, not proven.
(A modded cosmetic also carries 0.2/6.0, but it almost certainly copied Indominus, so it is not
independent evidence.)

So **adding a lux to a species that has never had one means editing its master FGM too.** That is
the step this guide originally missed.

`Lux Authoring/spinosaurusjwr.fgm` is the patched master for the worked example — a surgical copy
with only those two values changed, to Parasaurolophus's 0.4 / 8.0.

## Opacity: what it actually controls

The composite is

    result = albedo * (1 - opacity) + colour * opacity

and emissive is a **separate** term added on top. So:

| opacity | daylight | night |
|---|---|---|
| **0 everywhere** | colour never paints — animal looks untouched | emissive glows |
| **> 0 anywhere** | colour paints as ordinary albedo | emissive glows *and* the colour is visible by day |

Opacity governs the **albedo** contribution only. It is not a day/night switch — the emissive term
is added on top either way. `indominusrex_pattern_01_07` is a lux despite an ordinary name, and
`indoraptor_pattern_lux_00` is **not** a clean lux despite its name: its mean opacity is 0.703, so
it deliberately shows colour in daylight too. Apply that file to another species and you get
daytime colour; that is the file's authoring, not a species difference.

Verified: all-zero opacity keys bake to an all-zero 32-entry LUT, so a single `0.0` key would do.
Shipped files use six spread across the range, which is worth copying — it documents intent and
survives someone nudging one key.

## The trap: the background index must not glow

The pattern map is dominated by one value meaning "no pattern here", and the emissive LUT must be
**black at that index** or the entire animal lights up.

Every species has its own background, so this is the one number you must look up per species:

```python
import pattern_lut, numpy as np
from PIL import Image
a = np.array(Image.open("<species>_patternset_01.u_basepatternmap.png"))[..., 0]
bg, lo, hi = pattern_lut.map_stats(a)
print(bg, pattern_lut.index_of(bg))          # background byte -> LUT index
print(pattern_lut.index_of(lo), pattern_lut.index_of(hi))   # reachable range
```

For **SpinosaurusJWR**:

| | |
|---|---|
| background byte | 137 → **index 16.65**, and **87.4% of texels sit at index 17** |
| reachable indices | **15.44 – 27.84** |
| occupancy > 1% | 17 (87.4%), 18 (5.5%), 19 (2.4%), 20 (1.4%) |

So its glow belongs in **18–27**, with 16 and 17 held black.

**Do not copy key positions from another species.** `ccblue` glows at indices 1 and 10. On
Parasaurolophus that is fine; on SpinosaurusJWR those indices are unreachable and the keys would be
dead — a pattern that renders as nothing at all with no error to explain why.

Keys past the last one are **held**, not zeroed, so a lit key at 27 also lights 28–31. That is
harmless when the map cannot reach them, but it means "lit indices" alone is not a fault check —
compare against the reachable range.

## Recipe

0. **Check the master `<species>.fgm`.** If `pPatterning_EmissiveAdaptiveBrighnessWeight` is 0.0,
   set it to 0.2–0.4 and raise `pPatterning_EmissiveMaximumBrightness` to 6.0–8.0 to match. Skip
   this and the pattern will glow at noon however you author the keys.
1. **Measure** the species' background index and reachable range (snippet above).
2. **Opacity**: every used key `0.0`. Spread ~6 across the reachable range.
3. **Emissive**: black at the background index and the one below it, then ramp your colour upward
   through the reachable indices. Put the brightest keys on the sparse high indices — those are the
   distinct markings, so they read as bright accents rather than a wash.
4. **Colour**: never painted while opacity is 0, but set it to a dim version of your glow anyway.
   If anyone later raises opacity the pattern degrades to a plausible daylight skin instead of
   white. Shipped lux files all carry real colour keys.
5. **Verify by baking**, not by eye:
   - `bake(model)["opacity"].max() == 0`
   - `bake(model)["emissive"][bg_index].max() == 0`
   - no lit index at or below the background

## The Spino yellow lux, as built

```
emissive  16 (0.00, 0.00, 0.000)   guard below background
          17 (0.00, 0.00, 0.000)   THE background index -- black
          18 (0.25, 0.17, 0.010)
          19 (0.55, 0.38, 0.030)
          20 (0.85, 0.60, 0.060)
          23 (1.00, 0.75, 0.100)
          27 (1.00, 0.88, 0.200)   brightest, sparse markings
opacity   15, 17, 19, 22, 25, 28  -> all 0.0
colour    17,18,20,23,27          -> dim amber, graceful if opacity is raised
```

Baked result: opacity LUT max `0.000000`, emissive black at 16 and 17, lit and reachable at
18–27, brightest at 27.

## The two master-FGM attributes

Both live on `<species>.fgm`, not on the pattern, and the game's own typo is in the first name.

| attribute | what it does | lux species | default |
|---|---|---|---|
| `pPatterning_EmissiveAdaptiveBrighnessWeight` | **daylight suppression** — the thing that makes a lux behave like a lux | 0.2–0.4 | **0.0** |
| `pPatterning_EmissiveMaximumBrightness` | overall glow multiplier | 6.0–8.0 | 4.0 |

At the default 4.0 multiplier an emissive value near 1.0 lands roughly 4× overbright, so check these
before rebalancing every key.

## Shipping it

The FGM alone is not a usable cosmetic. A new pattern also needs a slot in the set: the patternset
FGM, and `SpeciesCosmeticSets.NumPatterns` plus `SpeciesCosmeticPatterns` rows in `c0dinosaurs.fdb`.
Replacing an existing pattern index is the cheaper route for testing.

Emissive previews in Blender only because `apply_pattern` now links LUT row 1 to the BSDF's
Emission Color; before that the row was baked and consumed by nothing, so every lux looked
identical to an ungraded pattern.

**Untested in game.** The bake is verified and the structure matches four shipped lux files, but
this specific file has not been rendered in JWE3.
