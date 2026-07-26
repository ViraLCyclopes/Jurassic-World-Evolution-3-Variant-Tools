"""Read-only access to a species' PRISTINE variant FGM parameters.

Deliberately separate from reader_kit: Blender-side code must never be able to write an OVL, and
must never read the live install, which usually has an experiment reader installed over it.

Every value is a `list[float]`, never a bare float, because `u_globalKeyColour` and `u_furTint`
are 3-component. Returning `value[0]` silently truncates them -- the exact bug that made the v17
experiment look like an incomplete transfer. Use `scalar()` when you know a parameter is
single-component; it raises rather than truncating if you are wrong.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reader_kit as rk  # noqa: E402


def read_all_variants(species, sex="Female"):
    """All variant FGMs' parameters, in variant order (index N == UI swatch N)."""
    # sexless species (Indominus Rex) are "<Species>.ovl", not "<Species>_<Sex>.ovl"
    ovl = rk._ovl(os.path.join(rk.pristine(species, sex), rk.species_ovl_name(species, sex)))
    return [rk._params(loader) for _, loader in rk._variants(ovl)]


def read_variant(species, sex="Female", variant=0):
    """One variant's parameters. Values are always lists -- 3-component attrs stay 3-component."""
    return read_all_variants(species, sex)[variant]


def scalar(params, name):
    """A single-component parameter as a float. Raises if the attribute is a vector."""
    v = params[name]
    if len(v) != 1:
        raise ValueError(f"{name} has {len(v)} components; use params[{name!r}] directly")
    return float(v[0])


def selftest():
    p = read_variant("Lokiceratops", "Female", 0)
    assert abs(scalar(p, "u_globalKeyThreshold") - 1.71) < 1e-3, scalar(p, "u_globalKeyThreshold")
    assert abs(scalar(p, "u_globalKeyTolerance") - 0.24) < 1e-3, scalar(p, "u_globalKeyTolerance")
    assert abs(scalar(p, "u_globalColourBrightnessBase") - 1.38) < 1e-3
    # multi-component values must survive intact, not be truncated to value[0]
    assert p["u_globalKeyColour"] == [1.0, 1.0, 1.0], p["u_globalKeyColour"]
    assert len(p["u_furTint"]) == 3
    # scalar() must refuse a vector rather than quietly returning its first component
    try:
        scalar(p, "u_globalKeyColour")
    except ValueError:
        pass
    else:
        raise AssertionError("scalar() truncated a 3-component parameter")

    all12 = read_all_variants("Lokiceratops", "Female")
    assert len(all12) == 12, len(all12)

    # remapIndex is a species-level DEFAULT that individual variants may override -- it is not
    # constant across variants, which is what the plan originally assumed. Measured on
    # Lokiceratops Female 2026-07-24: ten variants share one set, variant 0 differs in slot 13,
    # and variant 5 carries -1 across slots 2..9.
    idx = [tuple(int(v[f"u_remapIndex{i}"][0]) for i in range(1, 17)) for v in all12]
    sets = set(idx)
    assert len(sets) == 3, f"expected 3 distinct remapIndex sets, got {len(sets)}"
    common = max(sets, key=idx.count)
    assert idx.count(common) == 10, idx.count(common)
    assert idx[0][12] != common[12], "variant 0 should override exactly one slot"

    # -1 is a legal remapIndex meaning "this layer takes no remap". The bake must treat it as a
    # skip, NOT clamp it to row 0 of the LUT, or variant 5's disabled layers get a real colour.
    assert set(idx[5][1:9]) == {-1}, idx[5]
    assert all(v >= 0 for row in idx for v in row[9:]), "trailing slots should never be -1"

    print(f"  PASS - variant_reader: 12 variants, {len(p)} attributes, "
          f"{len(sets)} remapIndex sets (-1 = layer takes no remap)")


if __name__ == "__main__":
    selftest()
