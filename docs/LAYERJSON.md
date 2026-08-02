# LayerJSON — why the preview paints teeth and tongues, and how to fix it

## The symptom

In the editor's live diffuse preview, parts that should never be recoloured — **mouth flesh,
tongue, teeth, beak, claws, horns** — get graded along with everything else. In game they stay
their own colour no matter which variant you pick.

## Why it happens

The grade is gated per pixel by a value called **`colourWeight`**:

    out = lerp(albedo, gradedColour, colourWeight)

and that weight is a **product of two numbers**:

| term | comes from | what it does |
|---|---|---|
| swatch `pGlobalColouringWeight` | the shared SwatchLibrary | a **hard veto** — `Swatch_Bone`, `Swatch_Nail` and `Swatch_Mouth_Flesh` are **0** |
| variant `layerColourWeights[i]` | the variant FGM | the per-layer amount you actually author |

The editor builds that per-pixel map from a **LayerJSON** file for the species — one JSON per
species holding its 16 resolved layers, each carrying its `swatch_colour_weight`.

**If the LayerJSON is missing, the preview does not error.** It falls back to a flat
`colourWeight = 1.0`, which grades every texel — so the veto never applies and the mouth gets
painted. The preview still looks authoritative, which is what makes this worth knowing about.

Only a handful of LayerJSONs ship. Any species without one shows this.

## Fix: generate the file

    cd "...\Variant Research\VariantEditor\vendor"
    python export_layers.py Herrerasaurus

Takes several species at once: `python export_layers.py Herrerasaurus Albertosaurus Dimetrodon`.
Output lands in `LayerJSON/`, which is where the editor looks.

**This is a desktop step, not a Blender one.** Importing a species in Blender builds materials
inside Blender; it does not produce a LayerJSON.

### Hybrids, modded and sexless species

`export_layers.py <Species>` resolves the game's own OVL layout and assumes `Female`. For anything
custom — or a species with no `Female` / `Male` / `Juvenile` split, like IndominusRex — use
`export_from_folder` and point it at the extracted species folder instead:

```python
cd "...\Variant Research\VariantEditor\vendor"
python -c "import export_layers; print(export_layers.export_from_folder(r'D:\path\to\YourHybrid', 'YourHybrid')[0])"
```

**Leave `sex` at its default even if your species is sexless.** It only affects the filename and a
field inside the file. `preview_assets.layers_json_for()` tries `<Species>_<Sex>.json` first, then
falls back to **any** file beginning `<species>_`, so `YourHybrid_Female.json` is found regardless.

The folder must be the extracted species folder — the one with the layer FGMs and the
blend-weight masks, i.e. the same folder you point `apply_variant_all` at.

### Males and juveniles

The command line only ever writes **Female** — `export()` defaults to `sex="Female"` and the CLI
does not expose the argument. For the other sexes, call the function:

```python
python -c "import export_layers; print(export_layers.export('Herrerasaurus', 'Male')[0])"
python -c "import export_layers; print(export_layers.export('Herrerasaurus', 'Juvenile')[0])"
```

Modded species, same idea, pointed at that sex's folder:

```python
python -c "import export_layers; print(export_layers.export_from_folder(r'D:\path\to\Species\Male', 'Herrerasaurus', 'Male')[0])"
```

**You may not need them.** `layers_json_for()` falls back to *any* file starting `<species>_`, so
with only the Female file present a Male preview silently uses the Female layer stack. That is
usually harmless — layer and swatch assignment is typically shared across sexes, so mouth and claw
protection still works — but it is not guaranteed and nothing announces it.

**Match the name in the editor's species box, not the folder.** Males and juveniles are separate
species rows in this game (`Minmi` 334, `Minmi_Male` 1334, `Minmi_Juvenile` 2334). If the box reads
`Herrerasaurus_Male`, the prefix match is looking for `herrerasaurus_male_*`, so a plain
`Herrerasaurus_Male.json` will NOT be found. Align the filename to whatever the box shows.

## Check it worked

1. Open the new `LayerJSON/<Species>_Female.json`. It should have **16 layers**, every one carrying
   `swatch_colour_weight`, and at least one entry at **0.0** (normally `Swatch_Mouth_Flesh`).
   If the field is absent the preview silently reverts to grading everything.
2. Restart the editor, reselect the species, and the mouth flesh should stop being painted.

## Two ways it still comes up empty — both silent

* **Species name mismatch.** The fallback matches on the name in the editor's species box,
  lowercased. `Deathclaw_Female.json` matches a box reading `Deathclaw`; it does **not** match
  `Deathclaw Hybrid`.
* **Missing blend-weight masks.** Even with the JSON present, the map is accumulated from the
  species' `..._[NN]_C.png` masks. If those cannot be resolved the map is `None` and you are back
  to a flat 1.0 — *same symptom, different cause*. So if the mouth is still painted after
  regenerating, look at the masks, not the JSON.

## Known caveat: the eyes

The veto set is Bone / Nail / Mouth_Flesh, and the shipped LayerJSONs carry exactly **one**
zero-weight swatch each. Mouth flesh, teeth and claws are fully explained by the above. **Eyes may
still be recoloured after regenerating**, because no eye swatch appears in that list — if so, that
is a separate issue, not a missing LayerJSON, and worth reporting as such.
