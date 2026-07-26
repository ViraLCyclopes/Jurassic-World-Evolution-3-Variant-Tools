# License

**GPL-3.0-or-later.**

Blender add-ons link against Blender's Python API and must be GPL-compatible, and this tool builds
on **cobra-tools** (GPL-3.0), so GPL-3.0 is the license this has to carry.

> When creating the GitHub repository, pick **GNU General Public License v3.0** in the *Add a
> license* dropdown — GitHub will drop the full license text in as `LICENSE`. This file is only the
> summary and the notes below.

## What is and is not covered

**Covered by this license** — everything in this repository: the Python code, the vendored node and
palette modules in `vendor/`, and the measured data in `data/`
(`gradient_coefficients.json`, `swatch_params.json`, `seedsweep_fingerprints.json`), which are this
project's own measurements.

**NOT in this repository, and not ours to license:**

- **Jurassic World Evolution 3 assets** — textures, models, `.ovl`/`.fgm` files. The
  `SwatchLibrary/` and `Textures/` folders ship empty for exactly this reason; you extract those
  from your own copy of the game. `build_addon.py` refuses to package them, and `.gitignore`
  refuses to commit them.
- **cobra-tools** — a separate GPL-3.0 project, installed separately. This tool detects and uses it;
  it does not bundle it.
- **Blender** — GPL-3.0, installed separately.

Jurassic World Evolution 3 is a trademark of Frontier Developments plc. This is an unofficial
fan-made modding tool with no affiliation with or endorsement by Frontier or Universal.
