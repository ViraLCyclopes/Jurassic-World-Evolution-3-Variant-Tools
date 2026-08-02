# Harvesting palette seeds

> These tools live in `VariantEditor/Harvesting/`. Run them from this folder. The data they read and
> write (`gradient_coefficients.json`, `seedsweep_manifest.json`, …) stays in the Variant Research
> folder two levels up — only the tools live here.

**Every seed anyone captures makes the tool better for everyone.** This is the one part of JWE3's
dinosaur colour model that cannot be computed — it has to be measured, one seed at a time, and the
table is shared.

- [Why this is needed](#why-this-is-needed)
- [Where the numbers come from](#where-the-numbers-actually-come-from)
- [I just want to add someone's seeds](#i-just-want-to-add-someones-seeds) ← start here
- [Harvesting your own with RenderDoc](#harvesting-your-own-with-renderdoc)
- [Sharing yours back](#sharing-yours-back)
- [Troubleshooting](#troubleshooting)

## Why this is needed

A variant's colour comes from two halves:

| half | where it lives | status |
|---|---|---|
| the **grade** — brightness, saturation, hue rotation, key colour, palette strength | in the `.fgm`, readable | ✅ solved |
| the **gradient** — `gradOffset`, `gradAmplitude`, `gradPhase` per channel | computed by the game from the seed, never stored | ❌ must be measured |

Check what's covered right now:

```
python coeff_store.py --status
```

```
palette coverage: 48/256 seeds harvested (51 rows, 0 of them yours)
missing: 208 seeds
```

For a seed that isn't in the table the tool still shows the **correct overall colour** (the grade is
known), it just can't show the **variation** — the preview falls back to a flat gradient and is
labelled `gradient: approximate`. In game the colour is always correct; only the preview is limited.

**These cannot be predicted.** The seed is hashed: ~10,000 combinations of nine standard hash/PRNG
families were tested against all 48 known seeds and the best result was 3/48, which is pure chance.
Neighbouring seeds differ as much as random pairs, so interpolation is out too. Measurement is the
only way.

## Where the numbers actually come from

The game computes the coefficients on the CPU and uploads them in the **GPU material buffer**. They
appear in no shader and no asset file — but a RenderDoc capture stores that buffer uncompressed, so
they can be read straight out of it.

Finding them in gigabytes of capture works because of a structural accident:

1. **The filter.** Two words of the block are circulant hue-rotation matrices, whose packed triples
   sum to **511 regardless of the rotation angle**. Two independent triples both summing to 511 is a
   one-in-millions coincidence, which is cheap to scan for over gigabytes.
2. **The identification.** Six `f16` values in the candidate block (brightness ×2, saturation ×2,
   palette scale, palette offset) are matched against the known values from every shipped variant's
   `.fgm`. That says *which variant* this block belongs to — and therefore its seed and complexity.
3. **The confirmation.** Both hue matrices are then predicted from the `.fgm`'s rotation values and
   must match what's in the block.

That's 10+ independent values agreeing, so a hit is not in doubt. `harvest_blocks.py` does all
three. Verified end to end on Albertosaurus Juvenile variant 4 (seed 29, complexity 3).

One thing you get for free: **`gradFreq` is not seed-dependent.** It's a harmonic of 51 chosen by
the *complexity* (seed 18 gives `[51,51,102]` at complexity 1 and `[51,51,153]` at 2). Only the
other three triples are seed-hashed.

## I just want to add someone's seeds

Someone sends you a `.json`. One command:

```
python coeff_store.py --merge their_seeds.json
```

```
merged their_seeds.json -> C:\Users\you\AppData\Local\JWE3VariantTools\gradient_coefficients.json
  37 added, 0 updated, 88 rows total
palette coverage: 85/256 seeds harvested
```

That's it. Notes:

- **It's safe to re-run.** Merging is idempotent, and rows without real coefficients are rejected
  rather than poisoning the table.
- **It lands in *your* folder**, never inside the install — so updating or reinstalling the tool
  never loses your seeds.
- **No restart needed.** If the editor is open it picks the new rows up on the next lookup; the
  `gradient: approximate` badge flips to `exact` as soon as a seed is covered.
- Your rows win over the bundled ones, so you can correct a bad row locally.

## Harvesting your own with RenderDoc

Roughly an hour, and it yields ~50 seeds per pass.

### 0. Install RenderDoc (once)

**Download:** <https://renderdoc.org/builds> — take the **Windows 64-bit installer** (the stable
release; you do not need a nightly). It is free and open source.

Install to the default location, which is what every command below assumes:

```
C:\Program Files\RenderDoc\
    qrenderdoc.exe        the UI
    renderdoccmd.exe      the command-line launcher used in the Steam launch option
```

If you install somewhere else, substitute your own path in the launch option.

Check it worked:

```
"C:\Program Files\RenderDoc\renderdoccmd.exe" version
```

You also need **Python 3** with `numpy` (the harvester reads gigabyte buffers):

```
pip install numpy
```

> ⚠️ **This edits your game's OVL files.** They are backed up automatically, and step 6 restores
> them — don't skip it. **Close the game** before any staging or restoring step, or the file copy
> fails halfway and leaves a mixed set.

### 1. Pick seeds and stage them — game closed

```
python gen_seedsweep_v2.py
```

This writes an unharvested seed into ~53 different species' `_Male` OVLs, so one capture catches
~53 seeds at once, and backs every file up first into `Backup_SeedSweepV2_<timestamp>\`.

It prints which seed went to which species. Keep that output.

### 2. Find out which dinosaurs now carry a seed

The sweep does **not** touch every species — it picks ~53 hosts and writes one seed into each. You
need to know exactly which, because a seed is only recoverable if that animal is on screen.

```
python spawn_list.py
```

```
  seed   0  ->  Minmi (Male)  [Land]
  seed   1  ->  Homalocephale (Male)  [Land]
  seed   2  ->  Archaeornithomimus (Male)  [Land]
  ...
  53 staged, 26 already harvested, 27 worth capturing
```

- `[have]` marks seeds already in your table — spawning those adds nothing.
- `python spawn_list.py --todo` shows **only** the ones still worth capturing.
- `python spawn_list.py --write` saves it as `SEEDSWEEP_SPAWN_LIST.txt` so you can read it on a
  second monitor while playing.

It reads the sweep manifest, so it stays correct after the staging output has scrolled away, and it
re-checks your coefficient table every time you run it.

**Only the listed sex is modified** (the sweep stages `_Male` OVLs by default). Spawning a female of
a host species shows you the normal colours, not a staged seed.

### 3. Launch the game and build the park

Spawn **one animal of each species from that list**. Each needs to be *visible on screen* at capture
time — the material buffer only contains what's being drawn. Any variant of the slot works.

### 4. Capture with RenderDoc

**JWE3 must be launched *by* RenderDoc** — injecting into an already-running game does not reliably
hook it. The way that works for this game is a **Steam launch option**, so Steam still starts the
game normally and RenderDoc wraps it.

#### Steam launch options (this is the one to use)

Steam ▸ right-click **Jurassic World Evolution 3** ▸ *Properties* ▸ **Launch Options**, and paste:

```
"C:\Program Files\RenderDoc\renderdoccmd.exe" capture --opt-hook-children %command%
```

- `%command%` is Steam's placeholder — it substitutes the real executable and its arguments, so you
  never have to name `JWE3.exe` or worry about which drive your library is on.
- `--opt-hook-children` is **required**: the process Steam starts spawns the actual game process, and
  without child hooking RenderDoc attaches to the launcher and captures nothing.

Then start the game from Steam as usual. The RenderDoc overlay in the top-left confirms it is hooked.

Clear the launch options again when you are done, or every future launch goes through RenderDoc.

#### Alternative: the RenderDoc UI

1. Open **qrenderdoc** (`C:\Program Files\RenderDoc\qrenderdoc.exe`).
2. **File ▸ Launch Application**.
3. *Executable Path* → your `JWE3.exe` (next to `crash_reporter.exe`, typically
   `C:\Program Files (x86)\Steam\steamapps\common\Jurassic World Evolution 3\JWE3.exe`).
4. Tick **Hook into Children**, then **Launch**.

If you have more than one copy of the game, use the one you are actually modding — the same install
your OVLs were staged into.

#### Then take the capture

1. Load your park and get the host dinosaurs on screen — the material buffer only holds what is
   actually being drawn, so anything off-camera yields nothing.
2. Press **F12** (or *Capture Frame Immediately* in the RenderDoc overlay).

> **Prefer the species viewer over a park frame.** A park frame carries terrain, vegetation, crowds
> and weather; the species viewer is close to an empty scene with one animal in it. The capture is
> smaller, it opens far more reliably (park captures have crashed RenderDoc outright — see
> Troubleshooting), and the dinosaur is the dominant draw instead of one of thousands of events.
>
> One park capture was found to contain **11 distinct variants of the same species**, so a single
> good capture can be worth much more than the "one block per species" rule of thumb suggests.
> Scan what you already have before spending more game time.

Captures land in `%TEMP%\RenderDoc` and are 1–5 GB each, named like
`JWE3_2026.07.20_12.20_frame14499.rdc`. That is exactly where the harvester looks — no configuration
needed.

> **If the overlay never appears**, RenderDoc hooked the launcher instead of the game — check that
> `--opt-hook-children` is present in the launch options (or *Hook into Children* is ticked in the
> UI).

### 5. Harvest

```
python harvest_blocks.py
```

Scans every `.rdc` in `%TEMP%\RenderDoc`. To scan just the newest one:

```
python harvest_blocks.py frame14499
```

(any substring of the capture filename). It prints each seed it recovers and merges them into
`gradient_coefficients.json` — merges, never rebuilds, so a targeted scan can't delete earlier work.

### 6. Move them into your own table

```
python ..\coeff_store.py --merge ..\..\gradient_coefficients.json
python ..\coeff_store.py --status
```

### 7. Restore your game files — game closed

```
python restore_seedsweep_all.py --apply
```

Run it without `--apply` first for a dry run. Use this rather than `gen_seedsweep_v2.py --restore`:
the sweep's own restore only walks the manifest, and the manifest misses any `_Female` OVLs staged
by `--females`, so it silently leaves those modified.

## Sharing yours back

```
python coeff_store.py --export my_seeds.json
```

Exports only the rows **you** harvested — usually a few KB. Send that file; the other side runs
`--merge` on it. Add `--all` to export the bundled rows too.

Each row is self-describing, so a merge never needs any other context:

```json
"29_3": {
  "seed": 29, "complexity": 3,
  "gradOffset": [396, 405, 212], "gradAmplitude": [130, 187, 230],
  "gradFreq": [204, 204, 51],    "gradPhase": [511, 66, 59],
  "from": ["Albertosaurus_Juvenile.ovl", "albertosaurus_juvenile_variant_01_04.fgm"],
  "capture": "JWE3_2026.07.20_12.20_frame14499.rdc", "offset": 228062360
}
```

Values are **signed 10-bit** (−512…511), so negative numbers are normal and correct.

## Testing it yourself

Don't take any of this on trust — every piece checks itself, and you can verify a harvest end to end
without asking anyone.

### 1. Is the toolchain sane?

Every module self-tests. Run them; each prints `selftest ok` (`fgm_probe.py` prints `probe ok`):

```
:: in VariantEditor\Harvesting\
python harvest_blocks.py --selftest
python gen_seedsweep_v2.py --selftest

:: in VariantEditor\
python variant_model.py
python preview_assets.py
python fgm_io.py
python fgm_probe.py
python coeff_store.py
python palette_preview.py
python preview_bridge.py
set QT_QPA_PLATFORM=offscreen && python editor_ui.py
set QT_QPA_PLATFORM=offscreen && python variant_editor.py --selftest
```

If one fails, **stop** — anything downstream of it is untrustworthy. `harvest_blocks.py --selftest`
is the important one before a harvest: it re-checks every row already in the table against the
detection filter, so a corrupted table shows up immediately.

### 2. Did my capture identify the right variant?

Every row records which OVL and `.fgm` it came from. Cross-check that its seed really is that
variant's seed — if the identification were wrong, this would disagree:

```python
import json, fgm_io, coeff_store
for key, row in coeff_store.rows().items():
    ovl, fgm = row.get("from", ["?", "?"])
    print(key, "seed", row["seed"], "<-", fgm)
```

Then open that `.fgm` in the editor: the **Seed** and **Complexity** fields must match the row's
`seed`/`complexity`. They come from completely different places — one from the file, one from a GPU
buffer — so agreement is real evidence.

### 3. Is a merged seed actually live?

```
python coeff_store.py --status
```

Open a variant using that seed in the editor. The badge must read **`gradient: exact`** (green), not
`approximate`. If the editor was already open, no restart is needed — the table reloads by itself.

### 4. Does the preview match the game?

**Do not compare a Blender render against an ordinary in-game screenshot.** That was the advice
here for a long time and it is not good enough: the screenshot is lit by sun, sky and shadows and
passed through a tonemap, while the Blender render is flat albedo. The two differ by an unknown
factor, and in practice that ambiguity was enough to hide a **3x** error for several sessions —
long enough for a wrong conclusion to be written into the code with a selftest defending it.

**Compare albedo against albedo instead.** The `JWE3_ReShadeAddon` project puts the game's own
GBuffer albedo — the surface colour with no lighting at all — on screen, so an ordinary screenshot
becomes a measurement:

1. Install the add-on (see its README; needs ReShade **with add-on support**).
2. In game, press **Home**, enable **JWE3_Albedo**, set *Region* to Fullscreen, *Sky* to
   "Black (raw target)", *Exposure* to **1.0**.
3. Screenshot the dinosaur wearing the variant you are checking.
4. Render the same `.fgm` in Blender to a **linear EXR**, not a PNG.

Two things will ruin the comparison if you skip them:

* **The GBuffer albedo is sRGB-encoded.** Decode it before comparing against a linear render.
  (Sanity check: read a concrete path or sand — decoded they land at ~0.18 and ~0.29 linear, which
  is right for those materials. Read as linear they come out brighter than white paper, which is
  how you know you have skipped the decode.)
* **Measure region for region.** Comparing a whole-model mean against one patch, or a body-covert
  sample against a wing-fan sample, will manufacture an error that is not there. This has happened.

Sky reads **exactly 0.0** in that view, so a non-zero pixel is geometry — a free mask for isolating
the animal.

For a harvested seed the two should agree closely. For an unharvested one the overall colour matches
but the fine variegation is missing — that's the flat-gradient fallback, and it's the difference the
badge is telling you about.

The editor's **Palette** strip is a shortcut for the same check: it's computed from the same maths
in pure Python, so if the strip and Blender disagree, one of them is wrong and worth reporting.

### 5. Did I break anything by editing an FGM?

Round-trip it. Open a variant, change nothing, **Save As** to a new file, then reload that file —
every value must come back identical. The editor only writes the ~30 attributes it owns and copies
the other ~110 through untouched, so an unchanged save must be a no-op.

## Troubleshooting

**`PERMISSION DENIED` while staging or restoring** — the game is still running. Close it and re-run;
whatever was already written is fine.

**The game crashes on load after staging** — the `.aux` file went inconsistent. Staging must run with
`update_aux=True` and with the game closed; restore from your backup and stage again.

**`no matching .rdc captures`** — captures aren't in `%TEMP%\RenderDoc`. Check RenderDoc's capture
folder setting, or copy the `.rdc` there.

**The harvest finds nothing in a capture** — the dinosaurs weren't being drawn in the captured frame.
Get them on screen and capture again.

**A seed still shows `approximate` after merging** — that seed genuinely isn't in the table.
`python coeff_store.py --status` reports what's covered.

**RenderDoc crashes when opening a capture** — the harvester does *not* need RenderDoc to open
anything (it byte-scans the `.rdc` directly), so this only blocks you if you wanted to look at the
frame yourself. Things that have actually mattered, in order:

* **Capture and GUI must be the same RenderDoc version.** The `.rdc` records the version that wrote
  it, near the start of the file.
* **Try the species viewer instead of a park frame** — far simpler, and it has opened when park
  captures would not.
* In *Settings ▸ Replay*, **pin the replay GPU** if the machine has more than one adapter, and drop
  the **optimisation level** to "No optimisation" — slower, more faithful, and it gets past some
  crashes.
* **Save the evidence before relaunching.** RenderDoc wipes `%TEMP%\RenderDoc\dumps\` and its log on
  the next start, so copy the newest `RenderDoc_*.log` and the `dumps` folder somewhere first.
  Without them there is nothing to diagnose from.

**Palette JSON generated before August 2026 has the wrong `keyType`** — `export_palette.block` used
to hardcode it to `True`, justified by a survey claiming `u_globalKeyType` was 0 everywhere. That
premise was false, and the GPU bit is the **complement** of the FGM value. Regenerate any
`PaletteJSON/*.json` produced before then; the harvested coefficients themselves are unaffected,
since they are read from the capture rather than computed. See `docs/SLIDERS.md`.
