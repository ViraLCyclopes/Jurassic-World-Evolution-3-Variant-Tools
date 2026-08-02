# JWE3 Variant Tools — working guide

The README says what the tools *are*. This says how to actually run a job with them, and — more
usefully — how each one fails **silently**, because almost every mistake here renders something
plausible rather than throwing an error.

Companion docs: [`SLIDERS.md`](SLIDERS.md) for what each colour parameter does,
[`../Harvesting/README.md`](../Harvesting/README.md) for capture mechanics.

---

## 0. The one rule

> **Blender runs the INSTALLED add-on, not your source tree.**

Editing files under `VariantEditor/` changes nothing until you rebuild, reinstall and **restart**.
`importlib.reload` re-reads a module's *existing* path and ignores `sys.path`, so it will not save
you. If a fix "doesn't work", check this first:

```python
import blender_parts; print(blender_parts.__file__)
```

If that prints a path under `AppData\Roaming\Blender Foundation\...\addons\VariantEditor\`, you are
running the installed copy — which is correct, and means your edit is not in it yet.

---

## 1. One-time setup

```bat
python setup_gui.py
```

Writes `%LOCALAPPDATA%\JWE3VariantTools\jwe3_variant_tools.json`, which **every** tool reads
(add-on, desktop editor, harvesting scripts) so they always agree on which install is being modded.

| key | what it points at | notes |
|---|---|---|
| `game_dir` | `...\Jurassic World Evolution 3\Win64\ovldata` | auto-detected via Steam registry + `libraryfolders.vdf` |
| `cobra_tools` | your cobra-tools checkout | auto-detected from the installed Blender add-on |
| `swatch_dir` | `SwatchLibrary` folder | **multi**: several paths joined by `;` |
| `fur_library` | `DinosaurFur` folder | **multi**; also auto-found by walking up from the species folder |
| `textures_dir` | one extracted textures folder | set from the editor's Textures row |
| `captures_dir` | your RenderDoc capture folder | defaults to `%TEMP%\RenderDoc` |

Precedence is **env var → config file → auto-detect**, so you can override any key for one run
without editing the file.

> Run-time state (sweep manifests, OVL backups, your harvested coefficients) lives in that same
> config folder and **never inside the install** — an update to the install would otherwise delete
> the sweep manifest, which is the only record of which OVLs you modified and where the backups went.

---

## 2. Build and install the add-on

```bat
python build_addon.py --selftest     # validate first; refuses to package game assets
python build_addon.py                # -> VariantEditor.zip, beside the VariantEditor folder
```

Then in Blender: **Edit ▸ Preferences ▸ Add-ons ▸ ▾ ▸ Install from Disk…**, pick the zip, and
**restart Blender**.

Rebuild after *any* source change. The selftest is not optional cover — it is what stops game
assets (`SwatchLibrary/`, `Textures/`) being packaged or committed.

---

## 3. Putting a variant on a model in Blender

**Step 1 — import the model.** Use cobra-tools to import the species `.ms2`. This is not something
the Variant Tools do; they colour a model that is already in the scene.

**Step 2 — grade every part.** Sidebar (press <kbd>N</kbd>) ▸ **JWE3** tab ▸ **JWE3 Variants**:

| field | meaning |
|---|---|
| Species Dir | the extracted species folder, e.g. `…\Pyroraptor\Female` |
| Variant Index | cosmetic slot, `0` = variant 00 |
| Seed Override | optional; substitute a harvested seed (see below) |
| **Apply to All Parts** | runs `variant_parts.apply_variant_all` |

**Point Species Dir at the folder holding the `*_variantset_01.dinosaurmaterialvariants` manifest.**
A pristine extraction often does *not* have one, and without it parts cannot be discovered.

**Read the report.** Success looks like `4 graded, 0 skipped`. **A skipped part is never cosmetic** —
`fur_shell` and `fur_fin` sit over the body and occlude it almost completely, so one ungraded part
means you are looking at a raw base texture and every colour judgement you make is wrong. The
operator raises a WARNING rather than reporting success when anything is skipped; believe it.

### The single-variant shortcut

**File ▸ Import ▸ JWE3 Variant (.fgm)** builds and grades in one step, picking the mesh itself (or
using your selection if that object belongs to the same species). It also re-mirrors `fur_shell` and
`fur_fin` automatically — the message ends `[re-mirrored fur_fin, fur_shell]` when it does.

> Why that matters: rebuilding the body's layer stack orphans the shell/fin `JWE3_Mirror_*` groups,
> because those are *copies* of the body's per-layer nodes. An orphaned group has `node_tree = None`,
> which has no sockets, which severs the chain. The animal then renders from raw base diffuse and
> looks like a colour-model bug. If you ever rebuild the body by another route, run **Apply to All
> Parts** afterwards.

### Patterns

* **File ▸ Import ▸ JWE3 Pattern (.fgm)** — applies a pattern; re-applying replaces rather than
  stacks. Watch for `NO INDEX MAP FOUND` in the message: without the species' index map the whole
  mesh reads one LUT entry, i.e. a flat tint.
* **File ▸ Import ▸ JWE3 Patterns — Reload from disk** — re-reads every recorded pattern after you
  edit an FGM in cobra-tools. The LUT is baked at import, so editing the file alone does not move
  the preview.
* **File ▸ Export ▸ JWE3 Pattern → .fgm** — writes edited ramp stops back. Verified bit-identical
  and idempotent on real patterns.

**Do not judge a pattern by eye.** Most of a species' body maps to zero pattern opacity by design,
so a *correct* pattern changes very little at a glance. Diff two renders instead.

### Seeds that have no coefficients

An unharvested seed has no measured gradient and grades **flat** — which looks exactly like a wiring
fault. Use **Seed Override** to substitute a harvested seed at the same complexity and confirm the
palette is alive.

A substituted material is **not** what the game renders. Both seeds are recorded (`jwe3_seed` = what
was drawn, `jwe3_fgm_seed` = what the file says) so a substituted preview can never be mistaken for
a faithful one. Undo it before comparing to the game.

---

## 4. Judging colour honestly

This is where most time gets lost. Four rules, each learned by getting it wrong:

1. **View transform must be `Standard`**, not AgX or Filmic.
2. **Compare flat albedo, not a beauty render.** `blender_layer_nodes.preview_albedo(mat)` routes the
   final albedo to the surface as pure emission — no lights, no AO, no tone map. It is the only
   honest comparison against a game capture.
3. **Render to OpenEXR, not PNG.** PNG is sRGB-encoded; reading those values as linear is the single
   most repeated mistake in this project's history. If you must use PNG, convert back to linear
   before quoting a number.
4. **Use the `JWE3_OrthoSide` camera** and hide non-LOD0 meshes. LODs are coincident geometry and
   will fight; the airlift straps occlude.

For `fur_shell`/`fur_fin` there is no top-level Principled — it lives inside cobra's `MainShader`
group. Tap `MainShader.Base Colour` for a flat-albedo bypass, which sits pre-AO and so matches where
the body and feathers are tapped.

> **A saved `.blend` keeps the OLD maths.** Materials are data: fixing the code does not change a
> material that was already built. `apply_variant_all` re-applies the *grade* but never rebuilds the
> feathers *albedo chain*, so an overlay-term fix cannot reach an existing scene. Rebuild first:
> ```python
> blender_feather_nodes.build_feathers(obj, "<species>_feathers.fgm",
>                                      part_manifest.fur_library_dirs(species_dir),
>                                      mat_name="JWE3_Feathers_<Sex>")   # returns (mat, report)
> ```
> then **Apply to All Parts**. This cost a full measurement cycle once; it will again.

---

## 5. The desktop editor

```bat
python variant_editor.py
python variant_editor.py "…\pyroraptor_variant_01_00.fgm"
```

Needs Python 3.11 + PyQt5. It reads and writes **loose extracted `.fgm` files only** — never an OVL.
Getting the FGM out and back in stay your own cobra-tools steps.

Editing and saving work with or without Blender; the live preview is optional. Parameter meanings
are in [`SLIDERS.md`](SLIDERS.md).

---

## 6. Harvesting palette seeds

**Harvesting rewrites game OVLs.** Originals are backed up first and you can restore at any time; if
the game misbehaves afterwards, Steam ▸ Properties ▸ Installed Files ▸ Verify integrity restores it.

### The guided way

```bat
python Harvesting/harvest_gui.py
```

One window that walks the whole pass: prepare a sweep → spawn and capture → harvest → restore. It
shows your coverage, and whenever your game files are modified it shows a **red banner with Restore
one click away** — in every state, so you cannot lose track of it. It also warns if you try to close
with the game still modified.

Two refusals are built in, and neither can be overridden:

* **it will not install a sweep over an already-modified game** — doing so would back up the
  *modified* files as if they were the originals and permanently destroy the restore path;
* **it will not install while the game is running** — that fails halfway and leaves a partly
  installed sweep.

Any step is reachable directly from the **Go to** bar — the card only says what's recommended next.
A disabled button explains itself in its tooltip.

**Harvest one…** scans a single `.rdc` instead of the whole folder, which matters once you have
gigabytes of old captures and only want the one you just took. It deliberately does *not* advance
the "last harvested" marker, because a targeted scan doesn't establish that everything else has
been read — so the full Harvest stays on offer.

**On first run, captures already sitting in your folder are treated as already harvested.** Anyone
arriving from the command line has a full capture folder that has been scanned already; calling
those "new" would send you straight to Harvest instead of walking you from the start. The log says
how many were assumed done, and Harvest is always available if you want them re-scanned — merging
is idempotent, so a redundant scan costs only time.

Harvests are shareable: export yours and merge someone else's, which is much the fastest way to
finish the remaining seeds.

### Doing it by hand

The GUI runs these; they remain authoritative and work exactly as before.

```bat
python gen_seedsweep_v2.py --selftest   # validate the plan, touch no game file
python gen_seedsweep_v2.py              # build + install (backs up originals first)
python spawn_list.py                    # what to spawn in-game
                                        # ... capture with RenderDoc ...
python harvest_blocks.py                # scan captures -> coefficients
python audit_captures.py                # what a capture actually contained
python restore_seedsweep_all.py --apply # put the game back
```

`restore_seedsweep_all.py` reads the sweep manifest in your config folder. That manifest is the only
record of which OVLs were touched — do not delete it before restoring.

### You need seeds, not (seed, complexity) pairs

`gradFreq` is **solved analytically** and needs no harvesting:

```
gradFreq[ch] = 51                                         if mask[ch] == 0
             = min(511, round(511 * (complexity + 1)/10)) if mask[ch] == 1
```

`mask` is a 3-bit **seed-only** value, constant across complexity. Verified against every harvested
row: 0 of 438 values off-model, 0 mask conflicts across the 25 seeds held at multiple complexities.
(`511/10 = 51.1` is the same constant as the gradient's `T_DIVISOR`. Beware `51*(c+1)` — it agrees up
to complexity 3 then drifts one low: 255 vs **256**, 306 vs **307**, 459 vs **460**.)

`gradOffset` / `gradAmplitude` / `gradPhase` are seed-only too. So **one capture of a seed at any
complexity ≥ 1 gives you everything about that seed.** Sweep at complexity ≥ 1 — at complexity 0 both
branches equal 51 and the mask is unreadable.

Do **not** try to derive offset/amplitude/phase from the seed. ~10k hash/PRNG combinations were
tested; best result was 3/48, versus chance.

---

## 7. When something looks wrong

| Symptom | Almost always |
|---|---|
| Animal renders **red / raw texture** | `fur_shell`/`fur_fin` mirrors severed — run **Apply to All Parts** |
| Mesh renders **flat white** | two grade groups stacked; unsplice before re-applying |
| Mesh renders **flat 0.5 grey** | a grade node left unnamed, severing the albedo chain |
| Whole animal **flat purple** | a missing normal map wired as `(0,0,0)` — not a direction |
| Head **blotchy**, eye/teeth repainted | layer masks disconnected; check `dead_group_nodes(mat, recurse=True)` |
| Palette looks **flat / no gradient** | unharvested seed — try Seed Override |
| Pattern seems to **do nothing** | correct, mostly — diff renders instead of eyeballing |
| Fix **didn't take** | you edited source without rebuild + reinstall + **restart** |
| Everything looks **too dark/bright** | beauty render, not flat albedo; or PNG read as linear |

Two health checks worth knowing:

```python
blender_parts.verify_surface_chain(mat)          # raises with the exact broken nodes named
blender_parts.dead_group_nodes(mat, recurse=True)  # [] is healthy; recurse matters
```

The recursive form matters: a dead group *nested* inside a layer group leaves the material's own
tree looking perfectly healthy while the masks quietly do nothing.

---

## 8. Known rough edges

* `part_manifest.selftest` is pinned to **Psittacosaurus**, which may not be in your extraction; it
  raises `SystemExit` with a "set `JWE3_DINO_ROOTS`" message.
* Several selftests raise `SystemExit` as a friendly CLI message. That is fine from a terminal but
  **fatal inside Blender** — run them headless:
  ```bat
  blender --background --factory-startup --addons VariantEditor --python your_script.py
  ```
* `build_feathers` returns `(mat, report)`, not the material its docstring promises.
* Check `species_dirs` in your config if textures resolve oddly — a wrong species→folder mapping
  there resolves the wrong textures silently.

---

## 9. Status of the colour model

Verified against the game's own unlit GBuffer albedo (Pyroraptor v00):

| region | ours / game |
|---|---|
| feather coverts | **1.01 / 1.06 / 1.06** |
| neck skin (with pattern) | 1.01 / 0.95 / 0.70 |
| thigh skin | 0.89 / 0.63 / 0.63 |
| wing fan | 0.60 / 0.50 / 0.44 — **still short** |

The wing fan remains ~2x dark and hue-shifted; the pattern has been ruled out as the cause. Since
the coverts match exactly on the *same material and shader*, the difference is regional — which card
atlas region / UV1 the fan samples — not the colour maths.

**Treat in-game behaviour of anything you generate as unproven until you have seen it in the game.**
