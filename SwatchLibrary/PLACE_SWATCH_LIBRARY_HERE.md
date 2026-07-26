# Put the Swatch Library here

This folder ships **empty on purpose**. The Swatch Library is ~45 MB of Jurassic World Evolution 3's
own textures — Frontier's assets, not ours to redistribute. You already own the game, so you extract
them yourself, once.

## What goes here

The PNG slices extracted from **`SwatchLibrary.ovl`**, i.e. files that look like:

```
swatch_anky_ankylo_backplates.pdiffusetexture_[00].png
swatch_anky_ankylo_backplates.pdiffusetexture_[01].png
...
```

Around 274 files. Drop them straight in this folder — no subfolders.

## How to get them

Find `SwatchLibrary.ovl` in your game install (under `Win64\ovldata`) and extract it with
**cobra-tools**, then copy the extracted `.png` files here:

```
python <cobra-tools>\ovl_tool_cmd.py extract "<game>\Win64\ovldata\...\SwatchLibrary.ovl" -o <a temp folder>
```

## Using a different folder instead

You don't have to use this one. Any of these wins over it:

1. the add-on preference **Swatch Library folder** (Edit ▸ Preferences ▸ Add-ons ▸ JWE3 Variant Tools)
2. the `JWE3_SWATCH_DIR` environment variable
3. `%LOCALAPPDATA%\JWE3VariantTools\SwatchLibrary`

## How to tell it worked

The add-on preferences panel reports the Swatch Library as found. Until then, layers that use swatch
textures fall back to flat colour — the tool still runs, the preview is just less accurate.
