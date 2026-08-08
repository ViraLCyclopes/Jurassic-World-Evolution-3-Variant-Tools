"""The JWE3 patchwork gate: which body zones a pattern is allowed to paint on.

THE ONE DEFINITION OF THE GATE. `vendor/blender_pattern_nodes` implements the same rule as a
constant-interpolation ColorRamp; this is the numpy twin, and that module's selftest pins the two
to the same arithmetic. Same arrangement `pattern_lut.composite` already has with `build_group`.
If the rule changes, change it in both.

Traced in container 300 (Shader Research/PATTERNS.md 4.7.3) and VERIFIED IN GAME 2026-08-08:

    region = (uint)(patchworkMap.x * 4.99) & 31      // 0..4, the & 31 can never fire
    apply  = (patchworkFlags >> region) & 1
    if (!apply) -> the whole pattern block is skipped for that texel

Both of these must hold or there is no gating at all:
    u_usePatchwork  == 1   CPU-side master enable, absent from the shader. Proven in game:
                           with it at 0 the animal renders fully patterned whatever the flags say.
    u_patchworkFlags <  31 shader guard `icmp ult flags, 31`; 31 = 0b11111 = every zone on.

Every shipped pattern FGM sets usePatchwork=0 and flags=31, so patchwork is inert in retail.
"""
import numpy as np

#: The DXIL literal 0x4013F5C280000000. NOT 4.99 -- the rounded value moves zone boundaries.
C = 4.989999771118164

#: Byte to write for each zone: round((r + 0.5) * 255 / C), i.e. the middle of the zone's band.
ZONE_CENTRES = (26, 77, 128, 179, 230)

#: Inclusive byte range of each zone, for UI readouts and tests.
ZONE_RANGES = ((0, 51), (52, 102), (103, 153), (154, 204), (205, 255))

N_ZONES = 5


def _as_unit(v):
    """Accept bytes (0..255) or floats (0..1); return float array in 0..1.

    Tested by RANGE, not dtype: once anything has cast to float, a byte map and a unit map are
    indistinguishable by dtype alone, and guessing wrong shifts every texel to a different zone.
    """
    a = np.asarray(v, dtype=np.float64)
    if a.ndim == 3:
        a = a[..., 0]
    if a.size and a.max() > 1.0:
        a = a / 255.0
    return np.clip(a, 0.0, 1.0)


def region_of(v):
    """Map value -> zone id 0..4. `& 31` mirrors the shader and can never actually fire."""
    return (_as_unit(v) * C).astype(np.int64) & 31


def gate_mask(pw_map, flags, use_patchwork):
    """True where the pattern paints. Shape follows `pw_map`.

    Returns all-True for both disabled cases so callers can multiply unconditionally.
    """
    shape = np.asarray(pw_map).shape[:2]
    flags = int(flags)
    if not use_patchwork or flags >= 31:
        return np.ones(shape, dtype=bool)
    return ((flags >> region_of(pw_map)) & 1).astype(bool)


def gate_ramp_stops(flags):
    """The same gate as `gate_mask`, shaped for a CONSTANT-interpolation ColorRamp.

    `flags` is fixed when a Blender node group is built, so the gate needs no bit arithmetic in
    nodes: one stop per zone at that zone's lower edge, carrying 1.0 if its bit is set else 0.0.
    `vendor/blender_pattern_nodes` builds the ramp from this; it lives HERE, next to `gate_mask`,
    so the two forms of one rule cannot drift and so the pin below can run without Blender.
    """
    flags = int(flags)
    on = (lambda z: True) if flags >= 31 else (lambda z: bool((flags >> z) & 1))
    return [(z / C, 1.0 if on(z) else 0.0) for z in range(N_ZONES)]


def eval_constant_ramp(stops, x):
    """Evaluate a constant-interpolation ramp: the value of the last stop at or below x."""
    v = stops[0][1]
    for pos, val in stops:
        if x >= pos:
            v = val
    return v


def zone_histogram(pw_map):
    """{zone: fraction of texels}, for the editor readout and import preview."""
    reg = region_of(pw_map)
    total = reg.size or 1
    vals, counts = np.unique(reg, return_counts=True)
    return {int(z): float(c) / total for z, c in zip(vals, counts)}


def selftest_golden():
    """Pin the gate to MEASURED in-game behaviour, not to our own arithmetic.

    Set JWE3_SAMPLE_PATCHWORK_MAP to Atrociraptor's u_basepatchworkmap.png. On 2026-08-08 that map
    was measured at zone 3 = 40.1%, zone 4 = 59.9%, and the game rendered flags 15 and 16 as exact
    complements on the animal -- head/legs/belly against torso/neck/back. Skipped when the variable
    is unset, like the other data-backed selftests in this package.
    """
    import os
    p = os.environ.get("JWE3_SAMPLE_PATCHWORK_MAP")
    if not p or not os.path.isfile(p):
        return False
    from PIL import Image
    a = np.array(Image.open(p))
    if a.ndim == 3:
        a = a[..., 0]
    hist = zone_histogram(a)
    assert abs(hist.get(3, 0) - 0.401) < 0.005, hist
    assert abs(hist.get(4, 0) - 0.599) < 0.005, hist
    g15 = gate_mask(a, 15, True)
    g16 = gate_mask(a, 16, True)
    assert (g15 ^ g16).all(), "flags 15 and 16 must be exact complements"
    assert abs(g15.mean() - 0.401) < 0.005, g15.mean()
    assert abs(g16.mean() - 0.599) < 0.005, g16.mean()
    return True


def selftest():
    import numpy as np

    # region_of over a full byte sweep -> exactly zones 0..4, at the documented boundaries
    sweep = np.arange(256, dtype=np.uint8)
    reg = region_of(sweep)
    assert set(np.unique(reg).tolist()) == {0, 1, 2, 3, 4}, np.unique(reg)
    for zone, (lo, hi) in enumerate(ZONE_RANGES):
        assert (reg[lo:hi + 1] == zone).all(), (zone, lo, hi)
    # every centre must land in its own zone -- the import writes these values
    for zone, centre in enumerate(ZONE_CENTRES):
        assert int(region_of(np.uint8([centre]))[0]) == zone, (zone, centre)

    m = np.array([[26, 77], [128, 230]], np.uint8)      # zones 0,1,2,4

    # master enable off -> no gating at all (VERIFIED in game: animal renders fully patterned)
    assert gate_mask(m, 0, False).all()
    assert gate_mask(m, 15, False).all()
    # flags >= 31 -> shader's `icmp ult flags, 31` short-circuits, no gating
    assert gate_mask(m, 31, True).all()
    assert gate_mask(m, 255, True).all()

    # flags 15 (0b01111) and 16 (0b10000) must be exact complements
    a = gate_mask(m, 15, True)
    b = gate_mask(m, 16, True)
    assert (a ^ b).all(), (a, b)
    assert a.tolist() == [[True, True], [True, False]]   # zone 4 is the only one bit-4 selects

    # a zone with its bit clear is gated off
    assert gate_mask(m, 0b00001, True).tolist() == [[True, False], [False, False]]

    h = zone_histogram(m)
    assert abs(h[0] - 0.25) < 1e-9 and abs(h[4] - 0.25) < 1e-9, h

    # --- DRIFT PIN: the ramp form must equal the mask form, exactly ------------
    # `vendor/blender_pattern_nodes` renders the gate as a constant-interpolation ColorRamp built
    # from gate_ramp_stops. If that ever disagrees with gate_mask, the desktop and Blender previews
    # silently show different animals. Pinned over every byte value, for every interesting flag.
    for flags in (0, 1, 5, 15, 16, 23, 30):
        stops = gate_ramp_stops(flags)
        assert len(stops) == N_ZONES, (flags, stops)
        assert stops[0][0] == 0.0, stops
        want = gate_mask(sweep, flags, True)
        got = np.array([eval_constant_ramp(stops, float(v) / 255.0) for v in sweep], bool)
        assert (got == want).all(), (flags, np.where(got != want)[0][:8])
    # >= 31 disables masking, so every stop passes
    assert all(v == 1.0 for _, v in gate_ramp_stops(31))

    if selftest_golden():
        print("golden ok (real Atrociraptor map)")

    print("selftest ok")


if __name__ == "__main__":
    selftest()
