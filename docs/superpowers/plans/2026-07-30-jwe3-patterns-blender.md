# JWE3 Patterns in Blender — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render JWE3's pattern cosmetic in Blender as a spliceable overlay on the variant material, across the body and feathers mesh parts, with a reader/bake/model/IO layer ready for an editor UI.

**Architecture:** A pattern is a 32-entry gradient LUT baked from sparse keys in `<sp>_pattern_01_NN.fgm`, indexed by a greyscale map. Pure-Python modules (model, IO, bake) sit at the package top level; game-layout and Blender modules sit in `vendor/`. The LUT reaches Blender as a generated 32×3 image and is applied by a `JWE3_Pattern` node group spliced after the palette grade.

**Tech Stack:** Python 3.11, numpy, cobra-tools (`FgmHeader`), Blender 4.5 LTS `bpy`, stdlib `xml.etree.ElementTree`.

**Spec:** `docs/superpowers/specs/2026-07-30-jwe3-patterns-blender-design.md`
**Research:** `…\Dinosaur Files\Shader Research\PATTERNS.md` — read §2 and §4 before Task 4.
**Branch:** `patterns-design`

## Global Constraints

- **No pytest.** Every module exposes `selftest()`, prints `selftest ok`, and ends with `if __name__ == "__main__": selftest()`. This is the established repo convention — see `variant_model.py`, `_paths.py`. TDD here means *write the failing assertions first, run the module, watch it fail*.
- **Use `python`, not `python3`** — `python3` hits the Windows Store alias stub on this machine.
- **The Blender side must never import cobra-tools.** It consumes JSON produced by `export_pattern.py`. Importing `reader_kit` into Blender pulls a second copy of cobra-tools into that interpreter.
- **Compare bpy nodes by `.name`, never with `is`.** bpy returns a fresh Python wrapper on every attribute access; `is` silently matches nothing and reads as "no links". This produced two wrong findings during design.
- **Never mutate the shared `MainShader` node group** — it is used by all four part materials (feathers, fur, fur_fin, fur_shell). Copy to a single user or build fresh.
- **Force `Non-Color` on every non-albedo image channel, and re-assert it on reuse.** Image datablocks are shared; cobra-tools assigns sibling channels of the same packed texture inconsistently.
- **Cycles, not EEVEE.** EEVEE Next renders the 16-layer material as flat magenta and fails silently.
- **Never write into the game install.** Loose extracted `.fgm` files only.
- Source species files from `…\Personal Mods\JWE3\Images and Models\Dinosaurs\`, **not** `Variant Research\Textures\` — the latter is a partial dump that misled the design phase twice.

## Deviations from the spec

**1. `pattern_reader.py` is dropped.** The spec listed it alongside `pattern_io.py` and their
responsibilities overlapped (both read a pattern FGM). Its two real jobs are absorbed:

- reading the slot manifest and pattern-set maps → `part_manifest.py` (which already parses the same file)
- reading a pattern FGM's 67 attributes → `pattern_io.load_pattern_fgm()`

**2. Multi-part VARIANT import comes before patterns** (Task 7, new). A pattern overlays a *graded*
material, so if the variant cannot reach the `feathers` or `quills` mesh, a pattern on that part has
nothing to sit on. The existing importer is single-mesh — `_build_on_object(object_name, ...)` takes
one object and has no concept of parts. This also delivers something visibly useful earlier: a
correctly coloured Pyroraptor *with feathers*, before any pattern work lands. The old "Task 8:
feathers material" is folded into it, since a feathers variant and a feathers pattern need the same
material built first.

**3. A pattern must be importable independently of a variant**, matching the game, where the two are
separate cosmetic axes with separate `GeneMod_Cosmetic_*` unlocks. Consequence, and it is a real
trap: if both node groups splice "just before the Material Output", then the *order of application*
silently determines the chain order. Each group therefore declares a fixed `CHAIN_POS` and
`splice_at` inserts by position, not at the end. Task 8 asserts variant→pattern and pattern→variant
produce identical trees.

**4. Feathers and quills are ONE tier, not two.** Verified on disk: `psittacosaurus_female_quills.fgm`
is `DinosaurFeathers_ClipDoubleSided`, the same shader as `pyroraptor_feathers.fgm`, and quills have
their own `..._variant_01_NN_quills.fgm` (`DinosaurLayered_Variant`, 144 attributes) paired 1:1 with
the body, exactly like `feathersvariant`. So one code path covers both; only the part token differs.

Psittacosaurus is also the **better** `resolve_texture` test: its quills FGM names three shared
library textures (`feathers.*`) and three local overrides (`psittacosaurus_female_quills.*`), so it
exercises both branches in one material where Pyroraptor exercises only one.

## File Structure

| file | responsibility |
|---|---|
| `pattern_model.py` | **new, top level.** `PatternModel` dataclass: 12 colour + 12 emissive + 8 opacity keys, 3 flags. Pure data. Mirrors `variant_model.py`. |
| `pattern_lut.py` | **new, top level.** `bake()` — sparse keys → 32-entry LUT. Pure numpy. Used by both the editor's LUT strip and the Blender build. |
| `pattern_io.py` | **new, top level.** `load_pattern_fgm` / `save_pattern_fgm` via cobra-tools `FgmHeader`. Mirrors `fgm_io.py`. |
| `vendor/part_manifest.py` | **new.** Parse `.dinosaurmaterialpatterns` / `.dinosaurmaterialvariants`, de-interleave by part, discover mesh parts, resolve the shared `DinosaurFur/` library by `<dependency_name>`. The only part-aware module. |
| `vendor/export_pattern.py` | **new.** JSON bridge, keyed by part. Mirrors `export_palette.py`. |
| `vendor/blender_feather_nodes.py` | **new.** Build the feathers/quills material (one tier, `DinosaurFeathers_*`). Mirrors `blender_layer_nodes.py`. |
| `vendor/blender_parts.py` | **new.** `splice_at` / `unsplice` by `CHAIN_POS`, and multi-part variant application. The one place chain order is decided. |
| `vendor/blender_pattern_nodes.py` | **new.** Build the `JWE3_Pattern` group; splices via `blender_parts.splice_at`. Mirrors `blender_palette_nodes.py`. |
| `blender_listener.py` | **modify.** Multi-part variant import; a new File ▸ Import ▸ JWE3 Pattern entry that works with or without a variant. |
| `…/Shader Research/PATTERNS.md` | **modify** in Task 5 with the composite finding. |

## Task order

1–4 are pure data and unchanged. 5 is independent research. **7 is the new multi-part variant task**;
8 and 9 depend on it.

| # | task | needs |
|---|---|---|
| 1 | `pattern_model.py` | — |
| 2 | `pattern_lut.py` | 1 |
| 3 | `pattern_io.py` | 1 |
| 4 | `vendor/part_manifest.py` | — |
| 5 | shader IR read | — |
| 6 | `vendor/export_pattern.py` | 2, 3 |
| 7 | **feathers/quills material + multi-part variant import** | 4 |
| 8 | `vendor/blender_pattern_nodes.py` + independent pattern import | 6, 7 |
| 9 | visual validation | 8 |

---

### Task 1: `pattern_model.py` — the editable data model

**Files:**
- Create: `pattern_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PatternModel` dataclass with fields `colourKeys: list[tuple[int, list[float]]]` (12 entries), `emissiveKeys: list[tuple[int, list[float]]]` (12), `opacityKeys: list[tuple[int, float]]` (8), `usePatchwork: bool`, `usePatternLUT: bool`, `patchworkFlags: int`. Classmethods `template()`, `from_dict()`, `from_json()`; methods `to_dict()`, `to_json()`. Position `-1` means unused.

- [ ] **Step 1: Write the failing selftest**

Create `pattern_model.py` containing only the docstring, the imports, and this `selftest`:

```python
def selftest():
    m = PatternModel.template()
    assert len(m.colourKeys) == 12 and len(m.emissiveKeys) == 12 and len(m.opacityKeys) == 8
    # a template is entirely unused: every position is -1
    assert all(p == -1 for p, _ in m.colourKeys)
    assert all(p == -1 for p, _ in m.emissiveKeys)
    assert all(p == -1 for p, _ in m.opacityKeys)
    assert m.patchworkFlags == 31 and m.usePatternLUT is True and m.usePatchwork is False

    # round-trips must not drift, and must not alias
    m.colourKeys[0] = (2, [0.0, 0.03117277, 0.09174312])
    d = m.to_dict()
    m2 = PatternModel.from_dict(d)
    assert m2.to_dict() == d, "dict round-trip drifted"
    m2.colourKeys[0][1][0] = 0.5
    assert m.colourKeys[0][1][0] == 0.0, "from_dict aliased the caller's lists"

    import tempfile, os
    p = os.path.join(tempfile.gettempdir(), "pm_test.json")
    m.to_json(p)
    assert PatternModel.from_json(p).to_dict() == d, "json round-trip drifted"

    # floats must survive verbatim -- an 8-bit requantisation would break authoring
    assert PatternModel.from_dict(d).colourKeys[0][1][1] == 0.03117277
    print("selftest ok")


if __name__ == "__main__":
    selftest()
```

- [ ] **Step 2: Run it and verify it fails**

Run: `python pattern_model.py`
Expected: `NameError: name 'PatternModel' is not defined`

- [ ] **Step 3: Implement `PatternModel`**

Insert above `selftest`:

```python
"""PatternModel: a plain-data class for an editable JWE3 dinosaur pattern FGM.

A pattern is 32-entry gradient LUT defined by sparse keys. Positions run 0..31; -1 means the key
is unused. Colour and emissive keys carry [r, g, b]; opacity keys carry a single float.

Values are stored as the RAW floats read from the FGM. Some shipped values are exactly byte
quantised (0.6235294 == 159/255) and some are not (0.6061094 * 255 == 154.56), so any round trip
through an 8-bit colour picker would silently rewrite untouched keys. Gamma-correct for display
only, never for storage.
"""
from dataclasses import dataclass, field
import json

N_COLOUR_KEYS = 12
N_EMISSIVE_KEYS = 12
N_OPACITY_KEYS = 8
LUT_SIZE = 32
UNUSED = -1


def _blank_rgb_keys(n):
    return [(UNUSED, [0.0, 0.0, 0.0]) for _ in range(n)]


@dataclass
class PatternModel:
    colourKeys: list = field(default_factory=lambda: _blank_rgb_keys(N_COLOUR_KEYS))
    emissiveKeys: list = field(default_factory=lambda: _blank_rgb_keys(N_EMISSIVE_KEYS))
    opacityKeys: list = field(default_factory=lambda: [(UNUSED, 0.0) for _ in range(N_OPACITY_KEYS)])
    usePatchwork: bool = False
    usePatternLUT: bool = True
    patchworkFlags: int = 31

    @classmethod
    def template(cls):
        return cls()

    def to_dict(self):
        return {
            "colourKeys": [[int(p), list(v)] for p, v in self.colourKeys],
            "emissiveKeys": [[int(p), list(v)] for p, v in self.emissiveKeys],
            "opacityKeys": [[int(p), float(v)] for p, v in self.opacityKeys],
            "usePatchwork": bool(self.usePatchwork),
            "usePatternLUT": bool(self.usePatternLUT),
            "patchworkFlags": int(self.patchworkFlags),
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            colourKeys=[(int(p), list(v)) for p, v in d["colourKeys"]],
            emissiveKeys=[(int(p), list(v)) for p, v in d["emissiveKeys"]],
            opacityKeys=[(int(p), float(v)) for p, v in d["opacityKeys"]],
            usePatchwork=bool(d["usePatchwork"]),
            usePatternLUT=bool(d["usePatternLUT"]),
            patchworkFlags=int(d["patchworkFlags"]),
        )

    def to_json(self, path):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_json(cls, path):
        with open(path) as f:
            return cls.from_dict(json.load(f))
```

- [ ] **Step 4: Run the selftest and verify it passes**

Run: `python pattern_model.py`
Expected: `selftest ok`

- [ ] **Step 5: Commit**

```bash
git add pattern_model.py
git commit -m "feat: PatternModel, the editable pattern data model"
```

---

### Task 2: `pattern_lut.py` — the key → LUT bake

**Files:**
- Create: `pattern_lut.py`

**Interfaces:**
- Consumes: `pattern_model.PatternModel`, `LUT_SIZE`, `UNUSED`.
- Produces: `bake_channel(keys, width, interp="linear") -> numpy.ndarray` of shape `(32, width)`; `bake(model, interp="linear") -> dict` with keys `"colour"` `(32,3)`, `"emissive"` `(32,3)`, `"opacity"` `(32,1)`.

Semantics, all pinned by the selftest: keys with position `-1` are dropped; remaining keys are sorted by position; values between keys interpolate; **outside the key range the nearest key is held (clamped)**; a single key fills the whole LUT; no keys at all yields zeros.

`interp` is an explicit argument with `"linear"` and `"step"` both implemented, because **which one the game uses is not yet known** (see Task 5). Defaulting to linear is a documented assumption, not a finding.

- [ ] **Step 1: Write the failing selftest**

Create `pattern_lut.py` with the docstring, imports, and this `selftest`. Values are the real
`lokiceratops_pattern_01_00.fgm` keys, transcribed from the FGM.

```python
def selftest():
    import numpy as np

    # --- real Lokiceratops_Pattern_01_00 colour keys ---
    colour = [
        (2, [0.0, 0.03117277, 0.09174312]), (4, [0.1697248, 0.1142173, 0.04056315]),
        (5, [0.1376147, 0.09280991, 0.03200341]), (8, [0.0, 0.0, 0.0]),
        (12, [0.0, 0.0, 0.0]), (15, [0.0, 0.0, 0.0]), (18, [0.0, 0.0, 0.0]),
        (20, [0.0, 0.007505944, 0.05277805]), (22, [0.0, 0.007505944, 0.05277805]),
        (23, [0.0, 0.1247009, 0.2155226]), (27, [0.6061094, 0.5060652, 0.3721034]),
        (30, [0.6235294, 0.4196079, 0.1490196]),
    ]
    lut = bake_channel(colour, 3)
    assert lut.shape == (32, 3), lut.shape
    # a key lands exactly on its own slot
    assert np.allclose(lut[2], [0.0, 0.03117277, 0.09174312])
    assert np.allclose(lut[30], [0.6235294, 0.4196079, 0.1490196])
    # below the first key and above the last, the nearest key is HELD
    assert np.allclose(lut[0], lut[2]) and np.allclose(lut[1], lut[2]), "did not clamp below"
    assert np.allclose(lut[31], lut[30]), "did not clamp above"
    # midway between pos 2 and pos 4 is the midpoint
    assert np.allclose(lut[3], [0.0848624, 0.072695035, 0.066153135]), lut[3]

    # --- real opacity keys, deliberately given OUT of position order in the FGM ---
    opacity = [(16, 0.0), (28, 1.0), (25, 0.501), (22, 0.774),
               (15, 0.1), (8, 0.55), (2, 0.654), (19, 0.388)]
    o = bake_channel(opacity, 1)
    assert o.shape == (32, 1)
    assert np.isclose(o[16, 0], 0.0) and np.isclose(o[15, 0], 0.1), "unsorted keys mis-baked"
    assert np.isclose(o[0, 0], 0.654) and np.isclose(o[31, 0], 1.0)
    assert np.isclose(o[17, 0], 0.388 / 3.0), o[17, 0]   # one third of the way 16 -> 19

    # --- unused keys are dropped, not treated as position 0 ---
    assert np.allclose(bake_channel([(2, [1.0, 0.0, 0.0]), (-1, [0.0, 1.0, 0.0])], 3),
                       bake_channel([(2, [1.0, 0.0, 0.0])], 3)), "-1 key was not dropped"

    # --- a SINGLE key fills the whole LUT. Use a NON-ZERO value: Lokiceratops' only emissive
    #     key is (0, [0,0,0]), so testing with that would pass even if bake returned zeros. ---
    single = bake_channel([(7, [1.0, 0.0, 0.0])] + [(-1, [0.0, 0.0, 0.0])] * 11, 3)
    assert np.allclose(single, np.tile([1.0, 0.0, 0.0], (32, 1))), "single key did not fill"
    # ...and the real emissive set really is all-zero, which is data, not a bug
    loki_emissive = [(0, [0.0, 0.0, 0.0])] + [(-1, [0.0, 0.0, 0.0])] * 11
    assert np.allclose(bake_channel(loki_emissive, 3), 0.0)

    # --- no keys at all -> zeros, not a crash ---
    assert np.allclose(bake_channel([(-1, [0.0, 0.0, 0.0])] * 12, 3), 0.0)

    # --- step interpolation holds the lower key instead of ramping ---
    st = bake_channel(colour, 3, interp="step")
    assert np.allclose(st[3], st[2]), "step interp ramped"
    assert np.allclose(st[4], [0.1697248, 0.1142173, 0.04056315])

    # --- bake() wires the three channels off a PatternModel ---
    from pattern_model import PatternModel
    m = PatternModel.template()
    m.colourKeys = colour
    m.opacityKeys = opacity
    out = bake(m)
    assert set(out) == {"colour", "emissive", "opacity"}
    assert out["colour"].shape == (32, 3) and out["opacity"].shape == (32, 1)
    assert np.allclose(out["colour"], lut)
    print("selftest ok")


if __name__ == "__main__":
    selftest()
```

- [ ] **Step 2: Run it and verify it fails**

Run: `python pattern_lut.py`
Expected: `NameError: name 'bake_channel' is not defined`

- [ ] **Step 3: Implement the bake**

Insert above `selftest`:

```python
"""Bake a JWE3 pattern's sparse keys into the 32-entry gradient LUT.

The game bakes this CPU-side into `pPatterning_PatternGradientMap`, which is why that slot is an
inline RGBA placeholder in every shipped FGM and no gradient-map file exists on disk. This is the
reproduction of that bake.

INTERPOLATION IS NOT YET CONFIRMED. `interp="linear"` is an assumption; `"step"` is implemented so
that settling it from the shader IR is a one-line change. See PATTERNS.md open question 1.
"""
import numpy as np

from pattern_model import LUT_SIZE, UNUSED


def bake_channel(keys, width, interp="linear"):
    """Sparse (position, value) keys -> an (LUT_SIZE, width) array.

    Keys at position UNUSED are dropped. Order does not matter. Outside the key range the nearest
    key is held. No keys yields zeros.
    """
    used = [(int(p), np.atleast_1d(np.asarray(v, dtype=np.float64)).ravel())
            for p, v in keys if int(p) != UNUSED]
    out = np.zeros((LUT_SIZE, width), dtype=np.float64)
    if not used:
        return out
    used.sort(key=lambda kv: kv[0])
    pos = np.array([p for p, _ in used], dtype=np.float64)
    val = np.stack([v for _, v in used])           # (n_keys, width)
    x = np.arange(LUT_SIZE, dtype=np.float64)
    if interp == "step":
        # index of the last key at or below x; clamped at both ends
        idx = np.clip(np.searchsorted(pos, x, side="right") - 1, 0, len(pos) - 1)
        return val[idx]
    if interp != "linear":
        raise ValueError(f"unknown interp {interp!r}; expected 'linear' or 'step'")
    # np.interp clamps to the end values outside the range, which is the behaviour we want
    for c in range(width):
        out[:, c] = np.interp(x, pos, val[:, c])
    return out


def bake(model, interp="linear"):
    """All three channels of a PatternModel as {"colour": (32,3), "emissive": (32,3),
    "opacity": (32,1)}."""
    return {
        "colour": bake_channel(model.colourKeys, 3, interp),
        "emissive": bake_channel(model.emissiveKeys, 3, interp),
        "opacity": bake_channel(model.opacityKeys, 1, interp),
    }
```

- [ ] **Step 4: Run the selftest and verify it passes**

Run: `python pattern_lut.py`
Expected: `selftest ok`

- [ ] **Step 5: Commit**

```bash
git add pattern_lut.py
git commit -m "feat: pattern_lut, the sparse-key to 32-entry LUT bake"
```

---

### Task 3: `pattern_io.py` — load and save a pattern FGM

**Files:**
- Create: `pattern_io.py`

**Interfaces:**
- Consumes: `pattern_model.PatternModel`, cobra-tools `FgmContext` / `FgmHeader`, `jwe3_config`.
- Produces: `PATTERN_SHADER = "DinosaurLayered_Pattern"`; `is_pattern_fgm(path) -> bool`; `load_pattern_fgm(path) -> PatternModel`; `save_pattern_fgm(model, path) -> None` (in-place attribute overwrite).

Attribute naming, verbatim from the FGM: `u_colourKey_NN_Position` / `u_colourKey_NN_RGB`, `u_emissiveKey_NN_Position` / `u_emissiveKey_NN_RGB`, `u_opacityKey_NN_Position` / `u_opacityKey_NN_Value`, with `NN` **zero-padded from 01**. Plus `u_patchworkFlags`, `u_usePatchwork`, `u_usePatternLUT`.

- [ ] **Step 1: Write the failing selftest**

Create `pattern_io.py` with the docstring, imports and this `selftest`. It needs one real pattern
FGM; resolve it from the `Personal Mods` tree and **skip loudly** rather than pass silently if it
is absent.

```python
def selftest():
    import os, shutil, tempfile
    sample = os.environ.get("JWE3_SAMPLE_PATTERN_FGM") or SAMPLE_PATTERN
    if not sample or not os.path.isfile(sample):
        raise SystemExit(
            "selftest needs a real pattern FGM.\n"
            "Set JWE3_SAMPLE_PATTERN_FGM to a <species>_pattern_NN_NN.fgm, e.g.\n"
            r"  ...\Land (Base)\Pyroraptor\Female\pyroraptor_pattern_01_00.fgm")

    assert is_pattern_fgm(sample), sample
    m = load_pattern_fgm(sample)
    assert len(m.colourKeys) == 12 and len(m.emissiveKeys) == 12 and len(m.opacityKeys) == 8
    assert m.patchworkFlags == 31, m.patchworkFlags
    # every shipped pattern has at least one real colour key
    assert any(p != -1 for p, _ in m.colourKeys), "no colour keys read"
    # positions are in range or explicitly unused -- never silently clamped
    for p, _ in m.colourKeys + m.emissiveKeys:
        assert p == -1 or 0 <= p <= 31, p

    # a variant FGM must be REFUSED, not read as an all-default pattern
    variant = sample.replace("_pattern_", "_variant_")
    if os.path.isfile(variant):
        assert not is_pattern_fgm(variant)
        try:
            load_pattern_fgm(variant)
        except ValueError:
            pass
        else:
            raise AssertionError("loaded a variant FGM as a pattern")

    # ROUND TRIP: save an untouched model and every value must come back bit-identical.
    # This is what guards the raw-float storage decision -- an 8-bit requantisation fails here.
    tmp = os.path.join(tempfile.mkdtemp(), os.path.basename(sample))
    shutil.copy2(sample, tmp)
    save_pattern_fgm(m, tmp)
    back = load_pattern_fgm(tmp)
    assert back.to_dict() == m.to_dict(), "round trip altered an untouched pattern"

    # and an EDIT must actually land
    m.colourKeys[0] = (7, [0.25, 0.5, 0.75])
    m.opacityKeys[0] = (3, 0.125)
    save_pattern_fgm(m, tmp)
    e = load_pattern_fgm(tmp)
    assert e.colourKeys[0] == (7, [0.25, 0.5, 0.75]), e.colourKeys[0]
    assert e.opacityKeys[0] == (3, 0.125), e.opacityKeys[0]
    print("selftest ok")


if __name__ == "__main__":
    selftest()
```

- [ ] **Step 2: Run it and verify it fails**

Run: `python pattern_io.py`
Expected: `NameError: name 'SAMPLE_PATTERN' is not defined`

- [ ] **Step 3: Implement load/save**

Insert above `selftest`. The cobra-tools call sequence mirrors `fgm_io.py` exactly, including the
`ctx.game` assignment, which `to_xml_file` needs.

```python
"""Load and save a JWE3 dinosaur pattern FGM (`DinosaurLayered_Pattern`, 67 attributes).

Loose extracted XML `.fgm` files only -- getting them out of an OVL and back in stays a
cobra-tools step, exactly as with `fgm_io.py` for variants.

Values are read and written as RAW floats. Do not requantise: some shipped values are exactly
byte-quantised and some are not, so any 8-bit round trip rewrites keys the user never touched.
"""
import logging
import os
import sys

logging.disable(logging.WARNING)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jwe3_config as cfg
from pattern_model import PatternModel, N_COLOUR_KEYS, N_EMISSIVE_KEYS, N_OPACITY_KEYS, UNUSED

_cobra = cfg.get("cobra_tools")
if _cobra and os.path.isdir(_cobra) and _cobra not in sys.path:
    sys.path.insert(0, _cobra)

try:
    from modules.formats.FGM import FgmContext
    from generated.formats.fgm.structs.FgmHeader import FgmHeader
except ImportError as e:
    raise RuntimeError(
        "cobra-tools could not be imported. Run `python setup_gui.py` or set JWE3_COBRA_TOOLS."
    ) from e

PATTERN_SHADER = "DinosaurLayered_Pattern"
MARKER_ATTR = "u_colourKey_01_Position"
SAMPLE_PATTERN = None   # set JWE3_SAMPLE_PATTERN_FGM for the selftest


def _open(path):
    ctx = FgmContext(loader=None)
    if not getattr(ctx, "game", None):
        ctx.game = "Jurassic World Evolution 3"
    return ctx, FgmHeader.from_xml_file(path, ctx)


def is_pattern_fgm(path):
    try:
        _, h = _open(path)
        return MARKER_ATTR in [a.name for a in h.attributes.data]
    except Exception:
        return False


def load_pattern_fgm(path):
    _, h = _open(path)
    idx = {a.name: i for i, a in enumerate(h.attributes.data)}
    if MARKER_ATTR not in idx:
        raise ValueError(
            "%s is not a pattern FGM: shader %r with %d attributes, no %s.\n"
            "Pattern FGMs are named <species>[_<sex>]_pattern_<NN>_<NN>.fgm and use the %r shader."
            % (os.path.basename(path), h.shader_name, len(idx), MARKER_ATTR, PATTERN_SHADER))
    vals = h.value_foreach_attributes.data

    def rgb_keys(family, n):
        out = []
        for i in range(1, n + 1):
            p = vals[idx[f"u_{family}_{i:02d}_Position"]].value[0]
            v = vals[idx[f"u_{family}_{i:02d}_RGB"]].value
            out.append((int(p), [float(x) for x in v]))
        return out

    opacity = []
    for i in range(1, N_OPACITY_KEYS + 1):
        p = vals[idx[f"u_opacityKey_{i:02d}_Position"]].value[0]
        v = vals[idx[f"u_opacityKey_{i:02d}_Value"]].value[0]
        opacity.append((int(p), float(v)))

    return PatternModel(
        colourKeys=rgb_keys("colourKey", N_COLOUR_KEYS),
        emissiveKeys=rgb_keys("emissiveKey", N_EMISSIVE_KEYS),
        opacityKeys=opacity,
        usePatchwork=bool(vals[idx["u_usePatchwork"]].value[0]),
        usePatternLUT=bool(vals[idx["u_usePatternLUT"]].value[0]),
        patchworkFlags=int(vals[idx["u_patchworkFlags"]].value[0]),
    )


def save_pattern_fgm(model, path):
    """Overwrite the key attributes of an existing pattern FGM in place."""
    ctx, h = _open(path)
    idx = {a.name: i for i, a in enumerate(h.attributes.data)}
    vals = h.value_foreach_attributes.data

    def put_rgb(family, keys):
        for i, (p, v) in enumerate(keys, start=1):
            vals[idx[f"u_{family}_{i:02d}_Position"]].value[0] = int(p)
            target = vals[idx[f"u_{family}_{i:02d}_RGB"]].value
            for j, c in enumerate(v):
                target[j] = float(c)

    put_rgb("colourKey", model.colourKeys)
    put_rgb("emissiveKey", model.emissiveKeys)
    for i, (p, v) in enumerate(model.opacityKeys, start=1):
        vals[idx[f"u_opacityKey_{i:02d}_Position"]].value[0] = int(p)
        vals[idx[f"u_opacityKey_{i:02d}_Value"]].value[0] = float(v)

    vals[idx["u_usePatchwork"]].value[0] = int(bool(model.usePatchwork))
    vals[idx["u_usePatternLUT"]].value[0] = int(bool(model.usePatternLUT))
    vals[idx["u_patchworkFlags"]].value[0] = int(model.patchworkFlags)

    with h.to_xml_file(h, path):
        pass
```

- [ ] **Step 4: Run the selftest and verify it passes**

```bash
set JWE3_SAMPLE_PATTERN_FGM=D:\JWE2 Stuff\Personal Mods\JWE3\Images and Models\Dinosaurs\Land (Base)\Pyroraptor\Female\pyroraptor_pattern_01_00.fgm
python pattern_io.py
```
Expected: `selftest ok`

If the round-trip assertion fails, **do not relax it** — it is the guard on the raw-float decision.

- [ ] **Step 5: Commit**

```bash
git add pattern_io.py
git commit -m "feat: pattern_io, load and save a pattern FGM with exact float round-trip"
```

---

### Task 4: `vendor/part_manifest.py` — de-interleave the manifest, discover parts

Read PATTERNS.md §2.3 and §4 before starting. This is the module every naive assumption dies in.

**Files:**
- Create: `vendor/part_manifest.py`

**Interfaces:**
- Consumes: stdlib `xml.etree.ElementTree` only. Does **not** need cobra-tools.
- Produces:
  - `parse_manifest(path) -> Manifest` with `.count: int`, `.parts: list[str]` (`""` is the body), `.slots: list[dict[str, str | None]]` — `slots[logical_index][part]` is an FGM base name or `None` for a null.
  - `split_part(name) -> (core, part)` — the name parser.
  - `resolve_texture(dep_name, local_dir, library_dir) -> str | None` — local folder first, shared library second, case-insensitive.

**The part token is not in a fixed position.** Pyroraptor's is an *infix* (`Pyroraptor_FeathersPattern_01_00`); Psittacosaurus's is a *suffix* (`Psittacosaurus_Female_Pattern_01_00_Quills`). A prefix-based parser mis-pairs one of them. Match the invariant `_(Pattern|Variant)_<set>_<index>` core and treat what surrounds it as the part token.

- [ ] **Step 1: Write the failing selftest**

Create `vendor/part_manifest.py` with the docstring, imports and this `selftest`. The four species
are the regression guards named in the spec — each breaks a different naive rule.

```python
def selftest():
    # --- the name parser handles infix AND suffix part tokens ---
    assert split_part("Lokiceratops_Pattern_01_00") == ("Lokiceratops_01_00", "")
    assert split_part("Pyroraptor_FeathersPattern_01_00") == ("Pyroraptor_01_00", "Feathers")
    assert split_part("Psittacosaurus_Female_Pattern_01_00_Quills") == \
        ("Psittacosaurus_Female_01_00", "Quills")
    assert split_part("Pyroraptor_FeathersVariant_01_03") == ("Pyroraptor_01_03", "Feathers")

    root = os.environ.get("JWE3_DINO_ROOT") or DINO_ROOT
    if not root or not os.path.isdir(root):
        raise SystemExit(
            "selftest needs the extracted dinosaur tree.\n"
            r"Set JWE3_DINO_ROOT to ...\Personal Mods\JWE3\Images and Models\Dinosaurs")

    def man(rel):
        return parse_manifest(os.path.join(root, rel))

    # Lokiceratops: 6 patterns + a null. The baseline, single-part.
    lo = man(r"Land (Base)\Lokiceratops\Female\lokiceratops_patternset_01.dinosaurmaterialpatterns")
    assert lo.count == 7 and lo.parts == [""], lo.parts
    assert len(lo.slots) == 7 and lo.slots[6][""] is None, "null slot not represented"
    assert lo.slots[0][""] == "Lokiceratops_Pattern_01_00"

    # Psittacosaurus: 6 x 2 + a null. Breaks "stride is 1".
    ps = man(r"Land (Base)\Psittacosaurus\Female\psittacosaurus_female_patternset_01.dinosaurmaterialpatterns")
    assert ps.parts == ["", "Quills"], ps.parts
    assert len(ps.slots) == 7, len(ps.slots)
    assert ps.slots[0]["Quills"] == "Psittacosaurus_Female_Pattern_01_00_Quills"
    assert ps.slots[6][""] is None and ps.slots[6]["Quills"] is None

    # Pyroraptor: 6 x 2 with NO null. Breaks "there is always a blank".
    py = man(r"Land (Base)\Pyroraptor\Female\pyroraptor_patternset_01.dinosaurmaterialpatterns")
    assert py.count == 12 and py.parts == ["", "Feathers"], (py.count, py.parts)
    assert len(py.slots) == 6, len(py.slots)
    assert all(s[""] is not None and s["Feathers"] is not None for s in py.slots), "null invented"
    assert py.slots[3]["Feathers"] == "Pyroraptor_FeathersPattern_01_03"

    # Indominus rex: 7 + a null. Breaks "six patterns".
    ind = man(r"Land (Base)\IndominusRex\indominusrex_patternset_01.dinosaurmaterialpatterns")
    assert len(ind.slots) == 8 and ind.slots[7][""] is None, len(ind.slots)

    # variants use the same shape -- Pyroraptor is 12 logical x 2 parts
    pv = man(r"Land (Base)\Pyroraptor\Female\pyroraptor_variantset_01.dinosaurmaterialvariants")
    assert pv.count == 24 and len(pv.slots) == 12 and pv.parts == ["", "Feathers"]

    # --- texture resolution: local wins, library is the fallback, matching is case-insensitive ---
    lib = os.path.join(root, "DinosaurFur")
    loc = os.path.join(root, r"Land (Base)\Pyroraptor\Female")
    got = resolve_texture("feathers.pfeathers_basecolourtexture.tex", loc, lib)
    assert got and os.path.dirname(got) == lib, got
    got = resolve_texture("PYRORAPTOR_FEATHERS.PDINOSAURFEATHERS_BASEDIFFUSETEXTURE.TEX", loc, lib)
    assert got and os.path.dirname(got) == loc, "case-insensitive local lookup failed"
    assert resolve_texture("no_such_texture.tex", loc, lib) is None
    print("selftest ok")


if __name__ == "__main__":
    selftest()
```

- [ ] **Step 2: Run it and verify it fails**

Run: `python vendor/part_manifest.py`
Expected: `NameError: name 'split_part' is not defined`

- [ ] **Step 3: Implement the parser**

Insert above `selftest`:

```python
"""Discover a species' mesh parts and de-interleave its cosmetic manifests.

`.dinosaurmaterialpatterns` and `.dinosaurmaterialvariants` are NOT lists of patterns or variants.
They are flat lists of (logical index x mesh part), with the parts interleaved:

    Pyroraptor  pattern_count=12
        Pattern_01_00, FeathersPattern_01_00, Pattern_01_01, FeathersPattern_01_01, ...

so the stride is the number of parts, and the body<->feather (or body<->quills) pairing is stated
explicitly in the file. Never re-derive it from filenames. An entry with has_ptr="0" is a null --
the Blank Pattern -- and has NO FGM behind it; there is no _06.fgm standing in for it. Some species
have no null at all (Pyroraptor).

The part token is NOT in a fixed position: it is an infix on Pyroraptor (FeathersPattern) and a
suffix on Psittacosaurus (_Quills). Parse around the invariant core instead.
"""
import os
import re
import xml.etree.ElementTree as ET

DINO_ROOT = None   # set JWE3_DINO_ROOT for the selftest

#  <prefix> [<part>] (Pattern|Variant) _<set>_<index> [_<part>]
_CORE = re.compile(r"^(?P<prefix>.*?)(?P<infix>[A-Za-z]*)(?:Pattern|Variant)"
                   r"_(?P<set>\d+)_(?P<index>\d+)(?:_(?P<suffix>[A-Za-z]+))?$")


def split_part(name):
    """`Pyroraptor_FeathersPattern_01_00` -> ('Pyroraptor_01_00', 'Feathers').

    Returns (core, part); part is '' for the body. The core is stable across parts, so two names
    with the same core are the same logical cosmetic on different meshes.
    """
    m = _CORE.match(name)
    if not m:
        raise ValueError(f"unparseable cosmetic name: {name!r}")
    prefix = m.group("prefix").rstrip("_")
    part = m.group("infix") or m.group("suffix") or ""
    core = f"{prefix}_{m.group('set')}_{m.group('index')}"
    return core, part


class Manifest:
    def __init__(self, count, parts, slots):
        self.count = count      # raw entry count, INCLUDING nulls
        self.parts = parts      # ordered, '' first
        self.slots = slots      # list of {part: fgm base name or None}

    def __repr__(self):
        return f"<Manifest count={self.count} parts={self.parts} slots={len(self.slots)}>"


def parse_manifest(path):
    root = ET.parse(path).getroot()
    pool = root.find("patterns")
    if pool is None:
        pool = root.find("variants")
    if pool is None:
        pool = next((c for c in root if len(c)), None)
    if pool is None:
        raise ValueError(f"no entry pool in {path}")

    entries = []                        # (core, part, original_name) or None for a null
    for e in pool:
        if e.get("has_ptr") == "0":
            entries.append(None)
            continue
        name_el = next((c for c in e if c.tag.endswith("_name")), None)
        if name_el is None or not (name_el.text or "").strip():
            entries.append(None)
            continue
        original = name_el.text.strip()
        core, part = split_part(original)
        entries.append((core, part, original))

    parts, order = [], {}
    for ent in entries:
        if ent and ent[1] not in order:
            order[ent[1]] = len(parts)
            parts.append(ent[1])
    if "" in parts:                               # body first, then the rest in first-seen order
        parts.sort(key=lambda p: (p != "", order[p]))
    if not parts:
        parts = [""]

    slots, by_core = [], {}
    for ent in entries:
        if ent is None:
            slots.append({p: None for p in parts})
            continue
        core, part, original = ent
        if core not in by_core:
            by_core[core] = {p: None for p in parts}
            slots.append(by_core[core])
        # store what the manifest ACTUALLY said -- never reconstruct it from (core, part),
        # which cannot round-trip an infix and a suffix through one rule
        by_core[core][part] = original

    return Manifest(len(entries), parts, slots)


def resolve_texture(dep_name, local_dir, library_dir):
    """A texture named by `<dependency_name>`: the model's own folder wins, the shared
    DinosaurFur/ library is the fallback. Matching is case-insensitive -- loader keys are lowercase
    while the pointers are mixed case, the same trap layer_chain.py hit."""
    want = os.path.basename(dep_name).lower()
    stem = os.path.splitext(want)[0]
    for d in (local_dir, library_dir):
        if not d or not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            fl = f.lower()
            if fl == want or os.path.splitext(fl)[0] == stem:
                return os.path.join(d, f)
    return None
```

- [ ] **Step 4: Run the selftest**

```bash
set JWE3_DINO_ROOT=D:\JWE2 Stuff\Personal Mods\JWE3\Images and Models\Dinosaurs
python vendor/part_manifest.py
```
Expected: `selftest ok`

If Indominus rex's path differs (it is sexless — no `Female/` subfolder), correct the path in the
selftest rather than the parser.

- [ ] **Step 5: Commit**

```bash
git add vendor/part_manifest.py
git commit -m "feat: part_manifest, de-interleave cosmetic manifests by mesh part"
```

---

### Task 5: Trace the pattern composite in the shader IR

This is research, not TDD. It is the one genuine unknown in the spec, and everything downstream is
already measured fact, so **it must not block Tasks 6–8**. Timebox it; if it does not resolve, record
what was ruled out and proceed with the documented assumption.

**Files:**
- Modify: `…\Dinosaur Files\Shader Research\PATTERNS.md` (§5 open questions 1–4)
- Modify: `pattern_lut.py` — the `interp` default, only if the IR settles it

- [ ] **Step 1: Locate the patterning section**

Read `Shader Research/ir/0300_ps_DinosaurLayered_Layered_Opaque_GBuffer_0_Win64_SM60.txt`.
Start from the per-layer block's `+64 w0`, identified in `jwe3-blender-reproduction` as "a patterning
base index", and follow it to a `Sample` on the bindless heap `T0`
(`texture f32 2darray T0 t0,space1 24576`).

Two traps from `PALETTE.md` that cost whole sessions:
- **DXIL spells `frac` as `Frc`.** Grepping `Frac(` returns zero hits game-wide.
- Long runs of `DerivCoarseX/Y` + `Frc` + `AtomicBinOp` are **texture streaming feedback**, not
  shading. Skip them.

- [ ] **Step 2: Answer the four questions, or record the failure**

1. Is the pattern applied before or after the palette grade (`%2889`–`%2946` is the palette overlay)?
2. Does the key colour replace the albedo or blend with it, and by what factor?
3. Is the LUT sampled with nearest or linear filtering — i.e. `interp="step"` or `"linear"`?
4. How is the LUT indexed from `u_basePatternMap` — is it `v/255 × 31`?

- [ ] **Step 3: Write the finding into PATTERNS.md**

Update §5. Move anything settled out of *Open questions* into a new numbered section, tagged
**MEASURED**, citing the `%`-labels. If a question did not resolve, **leave it in §5 and write down
what was ruled out and how** — a negative result recorded is worth more than a silent gap. PATTERNS.md
§6 exists precisely to keep that discipline.

- [ ] **Step 4: Update the interp default if, and only if, question 3 resolved**

If the IR shows nearest sampling, change `pattern_lut.bake_channel`'s default to `"step"` and update
its docstring to say the choice is now measured rather than assumed. If it did not resolve, change
nothing.

- [ ] **Step 5: Commit**

```bash
git add pattern_lut.py
git commit -m "docs: pattern composite findings from container 300"
```

(`PATTERNS.md` sits outside this repo — Main Mod Kit is deliberately not a git repository — so it is
saved but not committed here. That is the existing convention for `PALETTE.md` too.)

---

### Task 6: `vendor/export_pattern.py` — the JSON bridge

The Blender side must never import cobra-tools, so a pattern crosses as JSON.

**Files:**
- Create: `vendor/export_pattern.py`

**Interfaces:**
- Consumes: `pattern_io.load_pattern_fgm`, `pattern_lut.bake`, `part_manifest.parse_manifest`.
- Produces: `export(fgm_path, out_path=None, interp="linear") -> dict` with keys `"source"` (basename), `"model"` (`PatternModel.to_dict()`), `"lut"` (`{"colour": 32×3 nested lists, "emissive": 32×3, "opacity": 32×1}`), `"interp"`. Writes JSON to `out_path`, defaulting to `_paths.palettejson_dir()/<basename>.pattern.json`.

- [ ] **Step 1: Write the failing selftest**

```python
def selftest():
    import os, json, tempfile
    sample = os.environ.get("JWE3_SAMPLE_PATTERN_FGM")
    if not sample or not os.path.isfile(sample):
        raise SystemExit("selftest needs JWE3_SAMPLE_PATTERN_FGM set to a real pattern FGM")

    out = os.path.join(tempfile.mkdtemp(), "p.json")
    d = export(sample, out)
    assert d["source"] == os.path.basename(sample)
    assert set(d["lut"]) == {"colour", "emissive", "opacity"}
    assert len(d["lut"]["colour"]) == 32 and len(d["lut"]["colour"][0]) == 3
    assert len(d["lut"]["opacity"]) == 32 and len(d["lut"]["opacity"][0]) == 1
    assert d["interp"] == "linear"

    # it must be JSON-clean: no numpy scalars, which json.dump refuses
    on_disk = json.load(open(out))
    assert on_disk == d, "written JSON differs from the returned dict"
    assert all(isinstance(c, float) for c in on_disk["lut"]["colour"][0]), "numpy leaked into JSON"

    # the model survives the crossing intact
    from pattern_io import load_pattern_fgm
    assert on_disk["model"] == load_pattern_fgm(sample).to_dict()
    print("selftest ok")


if __name__ == "__main__":
    selftest()
```

- [ ] **Step 2: Run it and verify it fails**

Run: `python vendor/export_pattern.py`
Expected: `NameError: name 'export' is not defined`

- [ ] **Step 3: Implement `export`**

```python
"""Dump a pattern FGM plus its baked LUT to JSON, so the Blender side never imports cobra-tools.

Mirrors export_palette.py. numpy arrays are converted with .tolist() -- json.dump refuses numpy
scalars, and a leaked one fails at write time with an unhelpful message.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
for p in (HERE, PKG):
    if p not in sys.path:
        sys.path.insert(0, p)

import _paths
import pattern_lut
from pattern_io import load_pattern_fgm


def export(fgm_path, out_path=None, interp="linear"):
    model = load_pattern_fgm(fgm_path)
    lut = pattern_lut.bake(model, interp=interp)
    data = {
        "source": os.path.basename(fgm_path),
        "model": model.to_dict(),
        "lut": {k: v.tolist() for k, v in lut.items()},
        "interp": interp,
    }
    if out_path is None:
        base = os.path.splitext(os.path.basename(fgm_path))[0]
        out_path = os.path.join(_paths.palettejson_dir(), base + ".pattern.json")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    return data


if __name__ == "__main__" and len(sys.argv) > 1:
    print(export(sys.argv[1])["source"])
```

Put the `selftest` above that `__main__` guard and make the guard call `selftest()` when no argument
is given.

- [ ] **Step 4: Run the selftest and verify it passes**

Run: `python vendor/export_pattern.py`
Expected: `selftest ok`

- [ ] **Step 5: Commit**

```bash
git add vendor/export_pattern.py
git commit -m "feat: export_pattern, JSON bridge for the Blender side"
```

---

### Task 8: `vendor/blender_pattern_nodes.py` — the spliceable overlay, independent of any variant

> Numbered 8; it appears here before Task 7 in the file. See the *Task order* table — Task 7 (the
> multi-part variant work) must land first, because a pattern overlays a graded material.

Runs inside Blender. Drive it through the blender-bridge MCP (`blender_exec`) rather than by hand.

**Files:**
- Create: `vendor/blender_pattern_nodes.py`
- Create: `vendor/blender_parts.py` — `CHAIN_POS`, `splice_at`, `unsplice`
- Modify: `blender_listener.py` — a File ▸ Import ▸ JWE3 Pattern entry

**Interfaces:**
- Consumes: the JSON from `export_pattern.export`; `blender_parts.splice_at`.
- Produces:
  - `lut_image(name, lut) -> bpy.types.Image` — a generated 32×3 float image, row 0 colour, row 1 emissive, row 2 opacity, `colorspace_settings.name = 'Non-Color'`.
  - `build_group(name, lut) -> bpy.types.ShaderNodeTree` — the `JWE3_Pattern` group. Inputs `Albedo` (colour), `Index` (float 0..1). Output `Albedo`.
  - `apply_pattern(mat, data) -> bpy.types.Node` — unsplice any existing group, then splice a fresh one at its `CHAIN_POS`. Returns the group node.
  - In `blender_parts`: `CHAIN_POS = {"JWE3_Grade": 10, "JWE3_Pattern": 20}`; `splice_at(mat, node, pos)`; `unsplice(mat, prefix) -> bool`.

**A pattern must apply with no variant present**, matching the game's separate cosmetic axes. Two
requirements follow:

1. `apply_pattern` must work on a plain cobra-tools material that has no JWE3 grade node.
2. **Order of application must not change the result.** If both groups splice "just before the
   Material Output", applying the variant second puts the grade *after* the pattern and applying it
   first puts it *before*. `splice_at` inserts by `CHAIN_POS`, never at the end, and Step 1 asserts
   that variant→pattern and pattern→variant yield identical trees.

Which position is correct is Task 5's open question 1. Until that is answered `CHAIN_POS` encodes an
assumption — but a **consistent** one, which is what matters here.

**Row addressing.** The image is 32 wide × 3 tall. Row `i` is sampled at `v = 1 - (i + 0.5)/3`
because Blender's V runs from the bottom while the array's row 0 is the top. Getting this wrong is
self-announcing: the colour row reads as the opacity row and the mesh goes flat grey.

- [ ] **Step 1: Write the failing selftest**

`blender_pattern_nodes.py` cannot run outside Blender. Give it a `selftest()` that runs *inside*
Blender and a `__main__` guard that only checks it imports cleanly, exactly as `blender_listener.py`
does.

```python
def selftest():
    """Run INSIDE Blender: exec(open(__file__).read()); selftest()"""
    import bpy, numpy as np
    lut = {"colour": [[i / 31.0, 0.0, 1.0 - i / 31.0] for i in range(32)],
           "emissive": [[0.0, 0.0, 0.0]] * 32,
           "opacity": [[i / 31.0] for i in range(32)]}

    img = lut_image("JWE3_TEST_LUT", lut)
    assert tuple(img.size) == (32, 3), img.size
    assert img.colorspace_settings.name == 'Non-Color'
    px = np.array(img.pixels[:]).reshape(3, 32, 4)      # (row, col, RGBA), row 0 at the BOTTOM
    assert np.isclose(px[2, 0, 0], 0.0) and np.isclose(px[2, 31, 0], 1.0), \
        "colour row is not the top row -- V flip is wrong"
    assert np.isclose(px[0, 31, 0], 1.0), "opacity row is not the bottom row"

    mat = bpy.data.materials.new("JWE3_TEST_MAT")
    mat.use_nodes = True
    n_before = len(mat.node_tree.nodes)

    g1 = apply_pattern(mat, {"lut": lut, "source": "test"})
    groups = [n for n in mat.node_tree.nodes if n.type == 'GROUP' and n.name.startswith("JWE3_Pattern")]
    assert len(groups) == 1, f"expected 1 group, got {len(groups)}"

    # APPLYING TWICE MUST NOT STACK. A second grade node is what renders the mesh white --
    # see jwe3-palette-apply-to-stacks. Compare by .name; `is` on bpy nodes matches nothing.
    apply_pattern(mat, {"lut": lut, "source": "test"})
    groups = [n for n in mat.node_tree.nodes if n.type == 'GROUP' and n.name.startswith("JWE3_Pattern")]
    assert len(groups) == 1, f"applying twice stacked {len(groups)} groups"

    assert unsplice(mat) is True
    assert not [n for n in mat.node_tree.nodes if n.type == 'GROUP'
                and n.name.startswith("JWE3_Pattern")]
    assert len(mat.node_tree.nodes) == n_before, "unsplice leaked nodes"
    assert unsplice(mat) is False, "unsplice on a clean material should report False"

    bpy.data.materials.remove(mat)
    bpy.data.images.remove(img)
    print("selftest ok")


if __name__ == "__main__":
    print("imports cleanly; run selftest() inside Blender")
```

- [ ] **Step 2: Run it inside Blender and verify it fails**

Via the blender-bridge MCP:

```python
import sys; sys.path.insert(0, r"<...>\VariantEditor\vendor")
src = open(r"<...>\vendor\blender_pattern_nodes.py").read()
ns = {}; exec(src, ns); ns["selftest"]()
```
Expected: `NameError: name 'lut_image' is not defined`

- [ ] **Step 3: Implement the group build and splice**

```python
"""Build the JWE3_Pattern node group and splice it over the palette grade.

The LUT crosses as a generated 32x3 image, mirroring the game, where
pPatterning_PatternGradientMap is a texture baked CPU-side from the keys and bound bindlessly.

A ColorRamp was considered -- it caps at exactly 32 stops -- and rejected: it cannot carry colour,
emissive and opacity in one node.
"""
import bpy

GROUP_PREFIX = "JWE3_Pattern"
ROWS = ("colour", "emissive", "opacity")


def lut_image(name, lut):
    img = bpy.data.images.get(name)
    if img is None:
        img = bpy.data.images.new(name, width=32, height=3, float_buffer=True, is_data=True)
    img.generated_width, img.generated_height = 32, 3
    # Blender's row 0 is the BOTTOM, so write our rows reversed.
    px = []
    for row in reversed(ROWS):
        vals = lut[row]
        for x in range(32):
            v = vals[x]
            px.extend([v[0], v[1], v[2], 1.0] if len(v) == 3 else [v[0], v[0], v[0], 1.0])
    img.pixels[:] = px
    img.update()
    # re-assert on reuse: image datablocks are SHARED and a stale colour space is invisible
    img.colorspace_settings.name = 'Non-Color'
    return img


def _row_v(i):
    """V coordinate of LUT row i, given Blender's bottom-up V."""
    return 1.0 - (i + 0.5) / len(ROWS)
```

Then build the group: a `ShaderNodeTree` with inputs `Albedo` (NodeSocketColor) and `Index`
(NodeSocketFloat), one `ShaderNodeTexImage` per row using `lut_image`, each fed a `Combine XYZ` of
`(Index, _row_v(i), 0)`, and a `Mix` node blending `Albedo` toward the colour row by the opacity
row. Output `Albedo`.

Then `apply_pattern(mat, data)`: call `unsplice(mat)` **first**, find the node feeding the Material
Output's Surface, insert the group between them, and return it. `unsplice(mat)` finds any node whose
`.name` starts with `GROUP_PREFIX`, relinks its input source to its output target, removes it, and
returns whether it found one.

Call the shared `blender_layer_nodes.layout()` at the end of the group build — nodes created in bulk
otherwise all land on top of each other at the origin.

- [ ] **Step 4: Run the selftest inside Blender and verify it passes**

Expected: `selftest ok`

- [ ] **Step 5: Commit**

```bash
git add vendor/blender_pattern_nodes.py
git commit -m "feat: JWE3_Pattern node group, spliced over the palette grade"
```

---

### Task 7: feathers/quills material + multi-part variant import

> Numbered 7; it appears here after Task 8 in the file. It is the **prerequisite** for Task 8.

**Files:**
- Create: `vendor/blender_feather_nodes.py`
- Create: `vendor/blender_parts.py` (shared with Task 8 — whichever lands first creates it)
- Modify: `blender_listener.py` — `_build_on_object` becomes part-aware

**Interfaces:**
- Consumes: `part_manifest.parse_manifest`, `part_manifest.resolve_texture`, the existing `blender_palette_nodes` grade.
- Produces:
  - `build_feathers(obj, fgm_path, library_dir) -> bpy.types.Material` — covers **feathers *and* quills**; they are one tier.
  - `blender_parts.discover_parts(species_dir) -> {part: obj}` — match mesh objects to parts by material name.
  - `blender_parts.apply_variant_all(slots_row, objects)` — body variant onto the body mesh, `feathersvariant`/`_quills` variant onto its own mesh, driven by the de-interleaved manifest row.

**Feathers and quills are one code path.** `psittacosaurus_female_quills.fgm` is
`DinosaurFeathers_ClipDoubleSided`, identical to `pyroraptor_feathers.fgm`, and quills carry their own
`..._variant_01_NN_quills.fgm` (144 attributes) paired 1:1 with the body. Only the part token differs.

**Test resolution on Psittacosaurus, not Pyroraptor.** Its quills FGM names three shared-library
textures (`feathers.*`) and three local overrides (`psittacosaurus_female_quills.*`), exercising both
branches of `resolve_texture` in one material. Pyroraptor exercises only the library branch, so it
would pass even if local-first precedence were broken.

**Adopt cobra-tools' channel mapping** — it agrees with the textures' own names, which is
corroboration rather than a guess:

| packed texture | R | G | B | A |
|---|---|---|---|---|
| `pFeathers_RoughnessPackedTexture` | Metalness | Roughness | Specular | — |
| `pFeathers_AOHeightOpacityTransmission_PackedTexture` | AO | height *(unused)* | Opacity | Transmission |

`Base Colour` ← the species-local `pDinosaurFeathers_BaseDiffuseTexture`; `Detail` ← the shared
`pFeathers_BaseColourTexture`.

- [ ] **Step 1: Write the failing selftest**

```python
def selftest():
    """Run INSIDE Blender with Pyroraptor imported."""
    import bpy
    obj = next(o for o in bpy.data.objects if o.type == 'MESH' and o.name.endswith("L0: feathers"))
    shared_before = bpy.data.node_groups["MainShader"].users

    mat = build_feathers(obj, FGM, LIBRARY)

    imgs = [n.image for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image]
    assert len(imgs) >= 6, f"only {len(imgs)} textures resolved; the shared library was not found"
    # every non-albedo channel must be data, whatever cobra-tools assigned
    for i in imgs:
        if "basecolour" in i.name.lower() or "basediffuse" in i.name.lower():
            assert i.colorspace_settings.name == 'sRGB', i.name
        else:
            assert i.colorspace_settings.name == 'Non-Color', f"{i.name} left as sRGB"

    # MainShader is shared by all four part materials -- mutating it corrupts fur, fin and shell
    assert bpy.data.node_groups["MainShader"].users == shared_before, \
        "the shared MainShader group was mutated"
    print("selftest ok")
```

- [ ] **Step 2: Run it inside Blender and verify it fails**

Expected: `NameError: name 'build_feathers' is not defined`

- [ ] **Step 3: Implement `build_feathers`**

Read the feathers FGM's `<dependency_name>` per texture slot with `xml.etree.ElementTree` (it is
plain XML — cobra-tools is not needed to read a dependency name). For each, call
`part_manifest.resolve_texture(dep, local_dir, library_dir)`; skip slots whose `dtype` is `RGBA`,
which are inline placeholders (`pFeathers_EmissiveTexture`, both iridescence slots, both
`pPatterning_Feathers*GradientMap`).

Load each file, **force `Non-Color` on everything except the two base-colour maps, re-asserting on
reuse**, and wire the channels per the table above. Rebuild the normal's Z as
`sqrt(1 - x² - y²)` — `feathers.pfeathers_normaltexture_RG` is two-channel, and feeding it straight
to a Normal Map node treats blue as z and flattens the surface.

If reusing `MainShader`, call `.copy()` on the node group first — never mutate the shared one.

- [ ] **Step 4: Run the selftest inside Blender and verify it passes**

Expected: `selftest ok`

- [ ] **Step 5: Commit**

```bash
git add vendor/blender_feather_nodes.py
git commit -m "feat: feathers material with shared DinosaurFur library resolution"
```

---

### Task 9: End-to-end visual validation

**Blocked on:** in-game screenshots of a **non-blank** pattern. Every existing reference shot uses
Blank Pattern. Nothing in Tasks 1–8 is blocked; this task is.

**Files:**
- Create: `Variant Research/diag/pattern_compare_<species>.png`
- Modify: `…\Shader Research\PATTERNS.md` — a results section

- [ ] **Step 1: Render Lokiceratops under all six patterns**

Import Lokiceratops, build the variant material, then loop `apply_pattern` over patterns 0–5 plus
blank, rendering each. **Cycles**, not EEVEE.

- [ ] **Step 2: Render flat albedo for the colour judgement**

Call `blender_layer_nodes.preview_albedo(mat)` and set
`scene.view_settings.view_transform = 'Standard'`. Without this, the lighting rig, AO and AgX
together will have you chasing a palette bug that is really an exposure difference. Turn it off
before judging relief — emission has no shading.

- [ ] **Step 3: Re-render a known-good frame first**

Before concluding anything is wrong, re-render Baryonyx v00 (`bary_v00_lit.png`) and diff it against
the stored copy. Three separate pieces of leaked state have caused confident wrong conclusions in
this project: a shared image datablock's colour space, a changed view transform, and a material left
built at a sweep's `HEIGHT_SCALE`.

- [ ] **Step 4: Compare against the screenshots and record the result**

Map screenshot to pattern by **swatch position**, not by name — the friendly UI names live in a
localisation file, not the FDB. Write the outcome into PATTERNS.md, including anything that does not
match.

- [ ] **Step 5: Commit**

```bash
git add ../diag/pattern_compare_*.png
git commit -m "test: visual validation of patterns against in-game screenshots"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: reader/manifest → 4; bake → 2; model + IO → 1, 3; JSON bridge → 6; node group and splice discipline → 7; feathers → 8; validation → 9; the shader read → 5. The spec's `pattern_reader.py` is deliberately absorbed into Tasks 3 and 4 — see *Deviation from the spec*.

**Placeholders.** One is deliberate and flagged: Task 4 Step 3 ships a `_rebuild`/`_ORIGINAL` shape that does not work, with the fix spelled out in Step 4 and the selftest asserting exact names so it cannot be skipped. Everything else carries real code or an explicit interface contract.

**Type consistency.** `PatternModel` field names (`colourKeys`, `emissiveKeys`, `opacityKeys`, `usePatchwork`, `usePatternLUT`, `patchworkFlags`) are identical across Tasks 1, 3 and 6. `bake()` returns `{"colour", "emissive", "opacity"}` in Task 2 and is consumed under those exact keys in Tasks 6 and 7. `LUT_SIZE` and `UNUSED` are defined once in `pattern_model.py` and imported by `pattern_lut.py`.

**Known gaps, stated rather than hidden:**
- Task 5 may not resolve, in which case `interp="linear"` remains an assumption. Tasks 6–8 proceed regardless.
- Task 9 is blocked on captures that do not exist yet.
- Colour accuracy is not validated for the chosen species — none has harvested seeds. Seed harvesting is a parallel track, not a dependency.
