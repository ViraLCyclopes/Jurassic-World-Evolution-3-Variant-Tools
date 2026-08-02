# JWE3 Variant Editor

**Get it:** *Code ▸ Download ZIP* (or [Releases](../../releases) → `VariantEditor.zip`) and install
it in Blender like any add-on · extract it anywhere to also run the desktop editor and the
harvesting tools.

DISCLAIMER: THIS PROJECT WAS MADE WITH THE HELP OF CLAUDE CODE. THIS IS TO GIVE EVERYONE WHO USES THIS A HEADS UP. 

| I want to… | Do this |
|---|---|
| Put a variant on a model in Blender | Install `VariantEditor.zip`, then *File ▸ Import ▸ JWE3 Variant (.fgm)* |
| Tune a variant with live sliders | Extract the repo, `python variant_editor.py` |
| Point it at my game / Swatch Library | `python setup_gui.py` (usually auto-detected) |
| Capture new palette seeds | `python Harvesting/harvest_gui.py` — guided, with backups and one-click restore |
| Understand the whole workflow | **[docs/GUIDE.md](docs/GUIDE.md)** — setup, Blender, judging colour, harvesting, troubleshooting |

Requires **Blender 4.5 LTS** + **cobra-tools**; the desktop editor also needs **Python 3.11** and
**PyQt5** (`pip install PyQt5`).


A standalone PyQt5 tool for editing a JWE3 dinosaur variant `.fgm` — every colour parameter on a
slider, with a live Blender preview on the real imported dinosaur model.

**The tool never opens an OVL.** It reads and writes *loose extracted* `.fgm` files only. Getting
the `.fgm` out of an OVL, and putting the edited one back in, stay your own cobra-tools steps.

```
  extract  ──►   EDIT HERE   ──►  inject
 (cobra-tools)  variant_editor  (cobra-tools)
```

## Quick start

```
python variant_editor.py
python variant_editor.py "..\Base Game Dinos\Baryonyx\Female\baryonyx_variant_01_00.fgm"
```

Editing and saving work with or without Blender. The preview is optional.

## Two ways to use it

**A. Just look at a variant on your model** — *File ▸ Import ▸ JWE3 Variant (.fgm)* inside Blender.
Pick a variant `.fgm` and it builds the material onto your imported dinosaur and grades it, in one
step. No editor, no socket. It finds the right mesh itself (or uses your selection if that object
belongs to the same species), and switching variants replaces the previous one cleanly.

**B. Tune a variant with live sliders** — the standalone `variant_editor.py` app, below.

Both come from the same add-on install, and both build the *real* material.

## Harvesting palette seeds

`python Harvesting/harvest_gui.py` walks a whole capture pass: prepare a sweep → spawn and capture
→ harvest → restore. It shows your seed coverage, and whenever your game files are modified it
shows a red banner with **Restore** one click away, in every state. Any step is reachable directly
from the action bar; disabled buttons say why in their tooltip.

**It modifies game OVL files** when you install a sweep. Originals are backed up first, restore is
always available, and it refuses outright to install over an already-modified game (that would back
up the modified files as if they were the originals and destroy the restore path) or while the game
is running. Steam ▸ Properties ▸ Installed Files ▸ Verify integrity is the fallback.

Harvests are shareable — export yours, merge someone else's. Pooling captures is much the fastest
way to finish the remaining seeds.

**They stay in sync.** With the editor open, importing a variant in Blender pulls that variant's
settings into the editor window (and adopts the mesh it targeted), so you can import in Blender and
immediately start tuning. Untick **Follow Blender imports** to keep editing undisturbed.

> **Why not cobra-tools' own FGM import?** It creates a **stub** material — `use_nodes = False`,
> flat 0.8 grey. It does not build the JWE3 layer stack or the palette grade, so it cannot show you
> a variant's colour. That is what this tool reproduces. Our importer is a separate menu entry; it
> does not patch or replace anything cobra-tools does.

## Installing the Blender side (once)

You need **two** add-ons in Blender: cobra-tools (to import JWE3 models at all) and this tool.

### 1. Blender

**Blender 4.5 LTS** — <https://www.blender.org/download/lts/>. Anything 4.2+ should work; 4.5 LTS is
what this is developed and tested against.

### 2. cobra-tools — the JWE3 model importer

This is the community toolset that reads `.ovl`, `.ms2` and `.fgm`. Our add-on uses it to read
variant files, and you need it to get a dinosaur into Blender in the first place.

1. Download it from <https://github.com/OpenNaja/cobra-tools> (green **Code ▸ Download ZIP**, or a
   release zip).
2. Blender ▸ **Edit ▸ Preferences ▸ Add-ons** ▸ the **⌄** dropdown (top right) ▸ **Install from
   Disk…** ▸ pick the zip.
3. Tick it in the list to enable it.

You do **not** need to tell our tool where cobra-tools is — it finds the installed add-on itself.

### 3. JWE3 Variant Tools — this tool

Either zip works — same as how you install cobra-tools:

- **Code ▸ Download ZIP** from the repo (the whole master/main branch), or
- **`VariantEditor.zip`** from the [Releases page](../../releases) — smaller, code and data only.

Then in Blender:

1. **Edit ▸ Preferences ▸ Add-ons ▸ ⌄ ▸ Install from Disk…**
2. Pick the zip.
3. Tick **JWE3 Variant Tools** (category *Import-Export*) to enable it.

The repo zip unpacks as `Jurassic-World-Evolution-3-Variant-Tools-main`; that is fine, Blender
loads add-on folders by name and hyphens are not a problem (`cobra-tools-master` installs the same
way).

> ⚠️ **Do not install a single `.py` file.** The add-on is a *folder*: it needs its siblings, its
> `vendor/` modules and its `data/`. A one-file install fails on the first import.

The console prints:

```
JWE3 Variant Tools: listening on 127.0.0.1:8990
JWE3 Variant Tools: File > Import > JWE3 Variant (.fgm)
```

> **After installing, set the folder.** Installing *copies* the single `.py` into Blender's add-ons
> folder, where it can no longer see `preview_assets.py` beside it or the research modules further
> out — those can't be bundled, they *are* the live research folder. Expand the add-on entry and set
> **Variant Research folder** to `…\Dinosaur Files\Variant Research`. The panel then shows
> *Modules found: OK*, and the console warns at startup if it's wrong.
>
> Running the file from Blender's *Scripting* tab instead needs no configuration.

### 4. Check it works

*File ▸ Import* should list **JWE3 Variant (.fgm)**. Import a dinosaur `.ms2` with cobra-tools,
then import a variant `.fgm` onto it — see [Two ways to use it](#two-ways-to-use-it) above.

## The full workflow

**1. Extract the variant FGM** from the species OVL with your normal cobra-tools workflow, e.g.

```
python ..\..\..\..\..\cobra-tools-master\ovl_tool_cmd.py extract <Species_Female.ovl> -o <dir>
```

and find `<species>[_<sex>]_variant_<NN>_<NN>.fgm` in the output.

**2. Import the dinosaur into Blender** with cobra-tools' Blender plugin — the `.ms2` mesh (e.g.
`Spino Female\models.ms2`) plus its FGM. You need a real mesh object with correct UVs; the preview
material is built **onto that object**, not onto synthetic geometry. Note the object's name in the
Outliner (often `models`).

**3. Install the add-on — it is called `JWE3 Variant Tools`** (category *Import-Export*).

*Edit ▸ Preferences ▸ Add-ons ▸* the **v** dropdown top-right *▸ Install from Disk…*, pick
`blender_listener.py` from this folder, and tick **JWE3 Variant Tools**. The console prints
`JWE3 Variant Tools: listening on 127.0.0.1:8990`.

> **Installing copies the file.** It then no longer sits next to `preview_assets.py`, and the
> research modules (`blender_layer_nodes.py`, …) are further away still — those cannot be bundled,
> they *are* the live research folder. So after installing, expand the add-on and set
> **Variant Research folder** to `…\Dinosaur Files\Variant Research`. The panel shows
> *Modules found: OK* once it is right, and the console warns at startup if it isn't.
> Running the file from the Scripting tab needs no configuration.

*(For iteration instead of installing: Scripting tab ▸ open `blender_listener.py` ▸ Run Script ▸
call `register()` once in the Python console.)*

**4. Run the editor** — `python variant_editor.py`. The indicator top-right reads
**Blender: connected** in green. If it is red, enable the add-on and use *Preview ▸ Reconnect to
Blender*; no need to restart.

**5. Open your extracted `.fgm`** (*File ▸ Open*). The species is inferred from the filename and
selected in the dropdown.

**6. Type the imported mesh object's name** into **Blender object**, then press
**Build / assign material**. The layer material is built from that species' masks and assigned onto
your imported dino.

**7. Drag sliders.** Every edit re-grades the material in the viewport within about a second
(pushes are debounced ~100 ms, so a drag sends one update, not a hundred).

**8. Save** (*File ▸ Save* / *Save As*), then inject the `.fgm` back into the OVL yourself.

To start a variant from scratch, *File ▸ New from template* — pick any existing variant `.fgm` as
the template. It supplies the ~130 attributes the editor does not expose (shader, texture
references); your edited values are written on top when you *Save As*.

## Mixing species: another dinosaur's variant on your model

Applying a **Spinosaurus** variant to a **Baryonyx** model works and is a supported use:

> **Textures follow the model. Colour follows the `.fgm`.**

Select (or name) the Baryonyx mesh and import a Spino variant: you get Baryonyx masks, textures and
UVs, graded with the Spinosaurus variant's colour block. The status message spells it out —
`[Spinosaurus colours on the Baryonyx model]`. Using the `.fgm`'s own species for the masks would
paint Spino blend-weights onto Baryonyx UVs and produce garbage.

## Which .fgm files work

Only **variant** FGMs — `<species>[_<sex>]_variant_<NN>_<NN>.fgm`, shader `DinosaurLayered_Variant`,
144 attributes. A species folder also holds files that look similar but carry **no palette
parameters at all**:

| file | shader | attrs | usable? |
|---|---|---|---|
| `..._variant_01_00.fgm` | `DinosaurLayered_Variant` | 144 | **yes** |
| `..._layer_01.fgm` | `DinosaurLayered_Layer` | 13 | no |
| `..._pattern_01_00.fgm` | — | — | no |
| `<species>.fgm` | `DinosaurLayered_Layered_Opaque` | 29 | no |

Opening one of the others raises a clear error naming the shader. (It used to load as seed-0
defaults, which looked like "the importer stopped updating".)

**A variant can also be genuinely neutral.** `spinosaurus_female_variant_01_01.fgm` really does
carry seed 0, all brightness/saturation 1.0 and paletteScale 0.0 — it is *meant* to look like the
plain base texture. That is data, not a failure.

## Palette seeds — shared, and growing

The exact-vs-approximate split above is a **data** limit, not a code one, and it improves every time
anyone captures a seed. Check your coverage:

```
python coeff_store.py --status        ->  palette coverage: 48/256 seeds harvested
```

Someone sent you seeds? One command, and the open editor picks them up with no restart:

```
python coeff_store.py --merge their_seeds.json
```

Your seeds live in your own folder (`%LOCALAPPDATA%\JWE3VariantTools\`), so updating or reinstalling
the tool never loses them, and your rows override the bundled ones.

**→ [Harvesting/README.md](Harvesting/README.md)** — installing RenderDoc, how the coefficients are
recovered from a capture, a step-by-step harvest (including which dinosaurs to spawn), how to share
yours back, and how to test all of it yourself.

## Accuracy of the preview

| | preview |
|---|---|
| **Grade** (brightness, saturation, hue rotation, key colour, palette scale/offset/strength) | **exact** |
| **Gradient** for a seed present in `../gradient_coefficients.json` | **exact** |
| **Gradient** for any other seed | **approximate** — flat gradient, grade still exact |

The badge next to the Build button reads **gradient: exact** (green) or **gradient: approximate**
(amber) for the current seed and complexity, so you always know which you are looking at.

**In-game colour is always correct regardless of the badge** — the approximation is a limitation of
the *preview*, not of the file you save. The exact-seed set grows on its own as
`../gradient_coefficients.json` accumulates more harvested seeds; nothing here needs updating when
it does.

## What is editable

Seed, complexity, key colour (+ threshold, tolerance), brightness base/palette, saturation
base/palette, hue rotation base/palette, palette scale/offset/strength, and the 16 per-layer colour
weights (collapsible section at the bottom).

Per-layer **saturation** and **contrast** are *not* here: `pDiffuseSaturation`/`pDiffuseContrast`
live in the per-layer FGMs (`<species>_layer_NN.fgm`), a different file. The model carries the
fields for forward compatibility, but v1 does not read or write them.

Slider ranges are defaults taken from a survey of 40 shipped variant FGMs, not hard limits — a file
whose value sits outside a range widens that slider instead of being clamped, so opening and saving
a variant never alters a value you did not touch.

## What to extract for a species

Extract a species into **one folder** and import from there. The folder needs all of this:

| file(s) | what it is |
|---|---|
| `models.ms2` | the mesh you import into Blender |
| `<species>[_<sex>]_variant_NN_NN.fgm` | the variants — the colours you edit |
| `<species>[_<sex>]_layer_NN.fgm` | the 16 per-layer materials |
| **`*.dinosaurmateriallayers`** | **the layer definition — easy to miss, and nothing works without it** |
| `*.playered_blendweights_[NN]_*.png` | the layer masks |
| `*.pbase*texture*.png` | base diffuse / normal / AO |

> ⚠️ **`.dinosaurmateriallayers` must be in the folder alongside the `.fgm` files.** It is what says
> which swatch and settings each of the 16 layers uses. Without it the layer definition cannot be
> built and the import fails — the error surfaces as a `StopIteration`, or
> `could not build a LayerJSON for <Species>`, which does not obviously point at a missing file.

Everything else resolves automatically once those are together in one folder.

## Preview assets — where the textures come from

Same rule cobra-tools itself uses (`create_material(reporter, in_dir, matname)` reads the `.fgm` and
lists the `.png` files out of the one folder the model came from). Masks are looked for in this
order:

1. **The folder the mesh's own textures were imported from** — read off the images already on the
   object. This is the right answer even for a cross-species preview, since it follows the *model*.
2. **The folder the `.fgm` was imported from** — a species you extracted yourself has its
   `models.ms2`, masks and FGMs together in one folder. Skipped when the `.fgm` is a different
   species than the model.
3. `../Textures/<Species>/` as a fallback. Abbreviated folder names work (`Psittaco` →
   Psittacosaurus, `Loki` → Lokiceratops).

The **mask prefix is detected from the files**, never built from the species name — Baryonyx's masks
are `baryonyx.playered_blendweights_[00]_R.png` but Psittacosaurus's are
`psittacosaurus_female.playered_...`, so any name-derived guess is wrong half the time.

The layer definition is `../LayerJSON/<Species>_<Sex>.json`. If it's missing it is **generated
automatically** on first use (equivalent to `python ../export_layers.py <Species>`), so a species
you have merely extracted becomes previewable with no setup step.

Editing and saving work for **any** species — only the preview needs these.

## Keeping track in Blender

Each import stamps the material so you can tell what you're looking at:

- the grade node's label becomes `baryonyx_variant_01_05.fgm   seed 35/1`, plus
  `(NO COEFFS - base grade only)` when the seed isn't harvested;
- the material carries custom properties `jwe3_variant_fgm`, `jwe3_variant_path`, `jwe3_seed`,
  `jwe3_complexity`, `jwe3_gradient` — visible under Material Properties ▸ Custom Properties, and
  they survive saving the `.blend`.

## Files

| file | what it is |
|---|---|
| `variant_editor.py` | the app — run this |
| `editor_ui.py` | the PyQt5 window (sliders, colour picker, badges) |
| `variant_model.py` | the plain-data variant model |
| `fgm_io.py` | loose `.fgm` load/save via cobra-tools `FgmHeader` |
| `preview_bridge.py` | socket client + model → palette-block conversion |
| `blender_listener.py` | **the Blender add-on** — install this one in Blender (socket server + the File ▸ Import entry) |
| `preview_assets.py` | where a species' masks/LayerJSON live; shared by the app and the add-on (imports neither PyQt5 nor bpy) |
| `palette_preview.py` | predicts a variant's colours in pure Python — drives the live Palette strip, no Blender |
| `coeff_store.py` | the layered seed table (bundled + your own captures); `--status` / `--merge` / `--export` |
| `theme.py` | Qt theme ported from SpeciesGenerator's `app.css` |
| `Harvesting/` | the seed-harvesting toolchain and its guide — see [Harvesting/README.md](Harvesting/README.md) |
| `fgm_probe.py` | the reference probe proving the `.fgm` read/write calls |

## Tests

No pytest. Every module has a `selftest()`:

```
python variant_model.py
python fgm_io.py
python preview_bridge.py
python fgm_probe.py
set QT_QPA_PLATFORM=offscreen && python editor_ui.py
set QT_QPA_PLATFORM=offscreen && python variant_editor.py --selftest
```

Each prints `selftest ok` (`fgm_probe.py` prints `probe ok`). `blender_listener.py` has no
selftest — it needs a running Blender; `python blender_listener.py` only confirms it imports
cleanly outside Blender, and its docstring documents the manual test.

The socket path and the Blender side are **not** covered by any selftest. They are covered by the
manual smoke test in steps 2–7 above.
