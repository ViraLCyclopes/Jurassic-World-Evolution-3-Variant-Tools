# What the pattern FGM fields mean

Reference for the Pattern tab. Everything here is read off real pattern FGMs and the code that
loads them; anything not established is marked **UNKNOWN** rather than guessed.

## The shape of a pattern

A pattern is a **32-entry lookup table** (`LUT_SIZE = 32`) plus a greyscale **index map**.

    u_basePatternMap (greyscale texture)  ->  entry 0..31  ->  colour / emissive / opacity

The index map is an *index*, not a colour: its value at each texel selects which LUT entry that
texel takes, black = 0 through white = 31. It is read as raw bytes and resampled **nearest** —
gamma-decoding or interpolating it would slide texels onto unrelated entries.

## The slots

| channel | slots | value |
|---|---|---|
| colour | 12 | linear RGB |
| emissive | 12 | linear RGB |
| opacity | 8 | float 0..1 |

Each slot is a **(position, value)** pair.

**Position** is the number beside each slot — *where* the key sits on the 0..31 axis, **not** how
strong it is. `-1` (shown as `--`) means the slot is **unused** and contributes nothing; it is not
position zero.

Keys are **sparse**: only the slots you set exist, and the table is interpolated between them.
Two colour keys at 18 and 22 produce a gradient across entries 18–22; below the lowest and above
the highest key the table is flat. Slot *order* carries no meaning — only positions do — and two
slots may share a position.

Worked example, `spinosaurusjwr_pattern_01_05.fgm`:

    colour  slot 04 -> pos 15   slot 05 -> pos 16   slot 06 -> pos 18
            slot 07 -> pos 22   slot 08 -> pos 27
    opacity slot 01 -> pos 16   slot 02 -> pos 17   slot 05 -> pos 22
            slot 06 -> pos 23   slot 07 -> pos 31   slot 08 -> pos 18

Note the colour and opacity keys sit at *different* positions — the two channels are independent
ramps over the same index axis, so a texel can be fully opaque while taking a colour interpolated
between two distant keys.

**Raw floats are preserved.** Some shipped keys are not byte-quantised (`0.6061094 × 255 = 154.56`)
and the colour picker is 8-bit, so merely *viewing* a pattern would rewrite every key. The tab
tracks which slots you actually edited and copies the rest back verbatim.

## The flags

| field | meaning |
|---|---|
| `usePatternLUT` | Use the key table. Off, the LUT is bypassed and the keys do nothing — the usual reason an edited pattern shows no change. |
| `usePatchwork` | **Master enable** for the patchwork zone gate. Off, zoning is ignored and the pattern paints everywhere. |
| `patchworkFlags` | Five-bit mask, one bit per body zone. Bit set = that zone shows the pattern. `31` = all zones on = no zoning. |

### Patchwork — the zone gate

**Traced in container 300 and verified in game 2026-08-08.** A greyscale `u_basePatchworkMap`
splits the body into up to five zones; `patchworkFlags` selects which zones the pattern paints on.
Zones switched off show base skin.

```
region = trunc(mapValue/255 * 4.99)        // 0..4
apply  = (patchworkFlags >> region) & 1
if (!apply) -> the whole pattern block is skipped for that texel
```

Both conditions must hold or there is no gating at all:

| | |
|---|---|
| `usePatchwork == 1` | CPU-side master enable, absent from the shader. **0 in all 2059 shipped pattern FGMs** — scanned across every content pack. This alone is why patchwork does nothing in retail |
| `patchworkFlags < 31` | shader guard `icmp ult flags, 31`, so values above 31 are indistinguishable from 31. 31 in 2029 of 2059 — but **not all**: 25 files carry 0, and Dryosaurus (20), Giganotosaurus (29) and Patagotitan (14) carry real hand-picked zone masks that never fire because `usePatchwork` is off |

Zone byte ranges, and the value to author for each:

| zone | map bytes | centre to author |
|---|---|---|
| 0 | 0–51 | 26 |
| 1 | 52–102 | 77 |
| 2 | 103–153 | 128 |
| 3 | 154–204 | 179 |
| 4 | 205–255 | 230 |

Shipped maps use only zones 3 and 4 (IndominusRex lux uses 1–3); zones 0 and 2 were verified usable
in game. **100 patternsets across 63 species ship a map** (full-install scan, 2026-08-08), so it is
common — but plenty of species have none, and "no patchwork map" is a normal case.

`patchwork.py` is the one definition of the gate — `gate_mask` for the Qt preview and
`gate_ramp_stops` for the Blender ramp, pinned to each other by its selftest. Full trace:
`Shader Research/PATTERNS.md` §4.7.

**Authoring**: paint zones in any tool, in any colours you can tell apart, then *Import painted…*
in the pattern tab. It clusters the painted colours, lets you assign each to a zone, and writes a
hard-edged quantised map. **Export without colour management** — a map tagged sRGB is decoded on
load and every value shifts zone. Quantisation is deliberately hard-edged: a soft gradient between
distant zones passes through the zones between them and paints phantom bands in game.

## How the pattern combines with the albedo

    result = albedo * (1 - opacity) + colour * opacity

`pattern_lut.composite()` is the definition of this, and the numpy twin of the Blender Mix node.
**This rule is still marked HYPOTHESIS in PATTERNS.md — it has never been traced in the IR.** If it
is ever corrected, change both together.

The pattern is applied **after** the variant grade. They are independent cosmetic axes: a material
may carry a pattern with no variant, or the reverse.

## Known gaps

- **The composite rule is unverified** (above). The patchwork gate around it, by contrast, is
  verified in game — see the zone-gate section.
- **Zone 1 is only proven as a mask bit**, never as a painting one: no shipped map has texels in it,
  so the in-game runs could not exercise it. The shader does a generic `1 << region` with no
  per-region branch, so it should behave like the rest.
- **No index map means a flat tint.** Without `u_basePatternMap` every texel reads the same LUT
  entry, which is correct behaviour for "no index map" but looks like a bug.
