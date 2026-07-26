# Optional: per-species texture folders

**You usually do not need this folder.** Masks are normally found automatically, in this order:

1. the folder your **model** was imported from (read off the images already on the mesh),
2. the folder the **`.fgm`** you are importing sits in.

Extract a species into one folder — `models.ms2`, its `.fgm` files and its `.png` textures together
— import from there, and everything resolves with no setup.

## When you *do* want this folder

As a fallback for the standalone editor, mainly for **cross-species** previews: putting a
Spinosaurus variant's colours on a Baryonyx model needs Baryonyx masks, and outside Blender the
editor cannot inspect your imported mesh to find them.

Drop a folder per species here:

```
Textures/
  Baryonyx/
    baryonyx.playered_blendweights_[00]_R.png
    baryonyx.pbasediffusetexture.png
    ...
  Psittaco/                     <- abbreviated names work (Psittaco -> Psittacosaurus)
    psittacosaurus_female.playered_blendweights_[00]_R.png
    ...
```

Notes:

- **The file prefix is read from the files**, never guessed from the species name — Baryonyx uses
  `baryonyx.` while Psittacosaurus uses `psittacosaurus_female.`, and both are fine.
- Folder names may be abbreviated (`Loki` → Lokiceratops, `Psittaco` → Psittacosaurus).
- These are game textures, so they are never shipped with this tool — extract them yourself with
  cobra-tools.

Inside Blender none of this is needed: the add-on reads the texture folder straight off the model
you imported.
