"""Invert `blender_pattern_nodes.ramp_stops`: ColorRamp stops -> a PatternModel you can save.

This is what makes Blender an AUTHORING tool for patterns rather than a viewer. Edit the stops in
the node editor, write back, and `pattern_io.save_pattern_fgm` puts the result in the FGM.

WHY IT IS NOT A PLAIN READ-BACK. The ramp carries colour and opacity on ONE set of stops, placed on
the UNION of the two key sets, because a ColorRamp has a single position axis. So a stop sitting at
an opacity-only position holds an *interpolated* colour that was never an authored colour key, and
writing every stop into both families would both invent keys and overflow the budgets -- colour has
12 slots, opacity only 8, and the union can already reach 12.

The way out is the same fact that made the merge lossless going forwards, run backwards: **adding a
knot to a piecewise-linear curve does not change the curve, so removing a redundant one does not
either.** Inverting the merge is therefore knot removal, not a guess. A stop whose value already
sits on the straight line between its neighbours carries no information and is dropped.

WHAT IS PRESERVED, and why it matters. `pattern_model` stores RAW floats because some shipped values
are exactly byte-quantised (0.6235294 == 159/255) and some are not, so any requantisation silently
rewrites keys the user never touched. Blender's ramp elements are float32, so a value that made the
round trip through one comes back very slightly different. `_keep_original` therefore restores the
original double whenever the ramp value matches it to float32 precision -- an untouched key must
write back bit-identical, which the selftest enforces.

Emissive keys are carried across untouched: they are not in the ramp at all (every measured pattern
has exactly one emissive key, so a ramp for it would be a constant with extra steps).

Run:  python pattern_writeback.py     -> selftest ok      (no Blender needed)
"""
import struct

from pattern_model import (PatternModel, LUT_SIZE, UNUSED,
                           N_COLOUR_KEYS, N_OPACITY_KEYS)

#: A knot whose value is within this of the line between its neighbours carries no information.
#: Well below one 8-bit step (1/255 = 0.0039), so nothing visible is ever discarded as redundant.
EPS = 1e-5


def index_of_position(pos01):
    """Ramp position 0..1 -> integer LUT index 0..31.

    `ramp_stops` maps `pos / (LUT_SIZE - 1)`, so this is exactly its inverse. Rounding rather than
    truncating matters: float32 storage in the ramp element turns 16/31 into 0.5161290168, and
    truncating that lands on 15.
    """
    return max(0, min(LUT_SIZE - 1, int(round(float(pos01) * (LUT_SIZE - 1)))))


def _as_vec(v):
    return [float(x) for x in v] if isinstance(v, (list, tuple)) else [float(v)]


def _line_error(p0, v0, p1, v1, p, v):
    """How far `v` at `p` sits off the straight line from (p0,v0) to (p1,v1). Max over components."""
    t = 0.0 if p1 == p0 else (p - p0) / float(p1 - p0)
    return max(abs(v[i] - (v0[i] + (v1[i] - v0[i]) * t)) for i in range(len(v)))


def simplify(points, n_slots, eps=EPS):
    """Drop knots that a piecewise-linear curve does not need. Returns (points, worst_error).

    Endpoints are never removed -- they set the values held outside the key range, so dropping one
    changes the curve everywhere beyond it.

    Redundant knots (error <= eps) always go. Beyond that, knots are removed cheapest-first ONLY to
    get within `n_slots`, and `worst_error` reports the largest deviation introduced so the caller
    can tell a lossless simplification from a lossy one.
    """
    pts = [(int(p), _as_vec(v)) for p, v in points]
    worst = 0.0
    # A LEADING or TRAILING knot equal to its neighbour is redundant, even though it is an endpoint:
    # outside the key range the nearest key is held, so dropping it leaves exactly the same curve.
    # Without this the union merge inflates the key count -- Pyroraptor 01_02 has an opacity key at
    # index 3 and its first colour key at 4, which put a duplicate colour key at 3 and turned an
    # 8-key pattern into a 9-key one on every save.
    while len(pts) > 2 and max(abs(a - b) for a, b in zip(pts[0][1], pts[1][1])) <= eps:
        pts.pop(0)
    while len(pts) > 2 and max(abs(a - b) for a, b in zip(pts[-1][1], pts[-2][1])) <= eps:
        pts.pop()
    while len(pts) > 2:
        cand = []
        for i in range(1, len(pts) - 1):
            cand.append((_line_error(pts[i - 1][0], pts[i - 1][1],
                                     pts[i + 1][0], pts[i + 1][1],
                                     pts[i][0], pts[i][1]), i))
        err, i = min(cand)
        if err <= eps or len(pts) > n_slots:
            pts.pop(i)
            worst = max(worst, err)
        else:
            break
    # two endpoints can still exceed a budget of one; nothing sensible to drop, so report it
    return pts, worst


def _dedup(points):
    """Collapse stops that round to the same LUT index. Last wins, matching `pattern_lut.bake`."""
    out = {}
    for p, v in points:
        out[p] = v
    return sorted(out.items())


def _f32(x):
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def _keep_original(index, value, original_keys):
    """Restore the original raw double if `value` is that key having survived a float32 round trip.

    Without this, merely opening a pattern in Blender and writing it back would rewrite every key to
    its float32 image -- exactly the silent requantisation `pattern_model` exists to prevent.
    """
    for p, v in original_keys or []:
        if int(p) != index:
            continue
        ov = _as_vec(v)
        if len(ov) == len(value) and all(_f32(a) == _f32(b) for a, b in zip(ov, value)):
            return ov
    return value


def _pad(keys, n_slots, blank):
    """Sparse keys -> the FGM's fixed slot array, unused slots marked -1."""
    rows = [(int(p), list(v) if len(v) > 1 else v[0]) for p, v in keys]
    rows += [(UNUSED, list(blank) if isinstance(blank, (list, tuple)) else blank)] * (n_slots - len(rows))
    return rows[:n_slots]


def model_from_stops(stops, original=None, eps=EPS):
    """ColorRamp stops -> (PatternModel, report).

    `stops` is `[(pos01, r, g, b, opacity)]` -- what `blender_pattern_nodes.ramp_stops` produces and
    what `read_ramp` reads back out of a live node group.

    `original` is the model the ramp was built from. It supplies the emissive keys and the flags
    (neither is in the ramp) and the raw floats for untouched keys. Passing it is strongly
    recommended; without it emissive keys are dropped, which would blank them in the FGM.

    `report` is `{"colour_error": float, "opacity_error": float, "colour_keys": int,
    "opacity_keys": int}`. A non-zero error means a budget forced a lossy simplification -- surface
    it rather than swallowing it.
    """
    orig = original.to_dict() if isinstance(original, PatternModel) else (original or {})

    colour_pts, opacity_pts = [], []
    for s in stops:
        pos01, r, g, b, a = (list(s) + [0.0] * 5)[:5]
        idx = index_of_position(pos01)
        colour_pts.append((idx, [float(r), float(g), float(b)]))
        opacity_pts.append((idx, [float(a)]))

    ck, c_err = simplify(_dedup(colour_pts), N_COLOUR_KEYS, eps)
    ok, o_err = simplify(_dedup(opacity_pts), N_OPACITY_KEYS, eps)

    ck = [(p, _keep_original(p, v, orig.get("colourKeys"))) for p, v in ck]
    ok = [(p, _keep_original(p, v, orig.get("opacityKeys"))) for p, v in ok]

    model = PatternModel(
        colourKeys=_pad(ck, N_COLOUR_KEYS, [0.0, 0.0, 0.0]),
        emissiveKeys=[(int(p), list(v)) for p, v in orig["emissiveKeys"]]
                     if orig.get("emissiveKeys") else PatternModel().emissiveKeys,
        opacityKeys=_pad(ok, N_OPACITY_KEYS, 0.0),
        usePatchwork=bool(orig.get("usePatchwork", False)),
        usePatternLUT=bool(orig.get("usePatternLUT", True)),
        patchworkFlags=int(orig.get("patchworkFlags", 31)),
    )
    return model, {"colour_error": c_err, "opacity_error": o_err,
                   "colour_keys": len(ck), "opacity_keys": len(ok)}


def _ramp_stops(model):
    """A local copy of `blender_pattern_nodes.ramp_stops`, which cannot be imported outside Blender.

    Kept in the selftest only, and asserted against the real one whenever Blender IS available, so
    the two cannot drift apart unnoticed.
    """
    ck = sorted((p, v) for p, v in model.get("colourKeys", []) if p >= 0)
    ok = sorted((p, v) for p, v in model.get("opacityKeys", []) if p >= 0)
    if not ck and not ok:
        return []

    def at(keys, pos, default):
        if not keys:
            return default
        if pos <= keys[0][0]:
            return keys[0][1]
        if pos >= keys[-1][0]:
            return keys[-1][1]
        for (p0, v0), (p1, v1) in zip(keys, keys[1:]):
            if p0 <= pos <= p1:
                t = 0.0 if p1 == p0 else (pos - p0) / (p1 - p0)
                if isinstance(v0, (list, tuple)):
                    return [v0[i] + (v1[i] - v0[i]) * t for i in range(len(v0))]
                return v0 + (v1 - v0) * t
        return default

    out = []
    for pos in sorted({p for p, _ in ck} | {p for p, _ in ok}):
        col = at(ck, pos, [0.0, 0.0, 0.0])
        opa = at(ok, pos, 0.0)
        out.append((pos / float(LUT_SIZE - 1), col[0], col[1], col[2], opa))
    return out


def selftest():
    import numpy as np
    import pattern_lut

    # position round trip, including the float32 case that breaks truncation
    for i in range(LUT_SIZE):
        assert index_of_position(i / float(LUT_SIZE - 1)) == i, i
    assert index_of_position(_f32(16 / 31.0)) == 16, "float32 position must round, not truncate"
    assert index_of_position(-0.5) == 0 and index_of_position(2.0) == LUT_SIZE - 1

    # a collinear knot carries nothing and must go; a real corner must stay
    pts = [(0, [0.0]), (5, [0.5]), (10, [1.0])]
    keep, err = simplify(pts, 12)
    assert [p for p, _ in keep] == [0, 10] and err <= EPS, (keep, err)
    corner = [(0, [0.0]), (5, [1.0]), (10, [0.0])]
    keep, err = simplify(corner, 12)
    assert [p for p, _ in keep] == [0, 5, 10], keep

    # a LEADING knot equal to its neighbour is redundant (the first key is held below its position),
    # and so is a trailing one. This is what stops the union merge inflating the key count on save.
    lead = [(3, [0.5]), (4, [0.5]), (12, [1.0])]
    keep, err = simplify(lead, 12)
    assert [p for p, _ in keep] == [4, 12] and err <= EPS, (keep, err)
    trail = [(0, [0.0]), (8, [1.0]), (9, [1.0])]
    assert [p for p, _ in simplify(trail, 12)[0]] == [0, 8]
    # but a leading knot with a DIFFERENT value is a real key and must survive
    assert [p for p, _ in simplify([(3, [0.2]), (4, [0.5]), (12, [1.0])], 12)[0]] == [3, 4, 12]

    # over budget: knots go cheapest-first and the loss is REPORTED, not swallowed
    many = [(i, [0.0 if i % 2 else 1.0]) for i in range(12)]
    keep, err = simplify(many, 4)
    assert len(keep) == 4 and err > 0.1, (len(keep), err)

    # THE INVARIANT THAT MATTERS: model -> stops -> model must bake to the SAME LUT.
    # Key-by-key equality is too strong (the union merge legitimately moves knots around); what has
    # to survive is the curve that actually renders.
    model = PatternModel(
        colourKeys=[(6, [1.0, 0.0, 0.0]), (11, [0.0, 1.0, 0.0]), (14, [0.0, 0.0, 1.0]),
                    (17, [1.0, 1.0, 0.0]), (31, [0.25, 0.5, 0.75])]
                   + [(UNUSED, [0.0, 0.0, 0.0])] * 7,
        opacityKeys=[(1, 0.0), (14, 1.0), (16, 0.5), (18, 1.0), (25, 0.0)]
                    + [(UNUSED, 0.0)] * 3,
        emissiveKeys=[(3, [0.1, 0.2, 0.3])] + [(UNUSED, [0.0, 0.0, 0.0])] * 11,
    )
    stops = _ramp_stops(model.to_dict())
    back, rep = model_from_stops(stops, model)
    assert rep["colour_error"] <= EPS and rep["opacity_error"] <= EPS, rep
    a, b = pattern_lut.bake(model), pattern_lut.bake(back)
    for row in ("colour", "opacity"):
        assert np.allclose(a[row], b[row], atol=2e-6), (row, np.abs(a[row] - b[row]).max())

    # untouched keys must come back BIT-IDENTICAL -- the raw-float guarantee
    quantised = 0.6235294  # == 159/255
    awkward = 0.6061094    # * 255 == 154.56, deliberately not byte-quantised
    m2 = PatternModel(
        colourKeys=[(0, [quantised, awkward, 0.5]), (31, [0.0, 0.0, 0.0])]
                   + [(UNUSED, [0.0, 0.0, 0.0])] * 10,
        opacityKeys=[(0, awkward), (31, 1.0)] + [(UNUSED, 0.0)] * 6,
    )
    r2, _ = model_from_stops(_ramp_stops(m2.to_dict()), m2)
    assert r2.colourKeys[0][1][0] == quantised, r2.colourKeys[0][1][0]
    assert r2.colourKeys[0][1][1] == awkward, "a raw float was requantised by the round trip"
    assert r2.opacityKeys[0][1] == awkward, r2.opacityKeys[0][1]

    # emissive is not in the ramp at all, so it must be carried across untouched
    assert back.emissiveKeys[0] == (3, [0.1, 0.2, 0.3]), back.emissiveKeys[0]
    assert back.patchworkFlags == 31 and back.usePatternLUT is True

    # an EDIT must actually land, and must not disturb its neighbours
    edited = [list(s) for s in stops]
    edited[0][1] = 0.125                       # change red at the first stop
    e, _ = model_from_stops(edited, model)
    got = next(v for p, v in e.colourKeys if p == index_of_position(edited[0][0]))
    assert abs(got[0] - 0.125) < 1e-6, got

    # slot arrays must be exactly the sizes the FGM has, padded with -1
    assert len(back.colourKeys) == N_COLOUR_KEYS and len(back.opacityKeys) == N_OPACITY_KEYS
    assert all(p == UNUSED for p, _ in back.colourKeys[rep["colour_keys"]:])
    # ...and the result must survive PatternModel's own round trip, or it cannot be saved
    assert PatternModel.from_dict(back.to_dict()).to_dict() == back.to_dict()

    # IDEMPOTENCE. Saving twice must not keep growing the key set -- if round two differs from round
    # one, every save mutates the file and the pattern drifts with each edit session.
    once, _ = model_from_stops(_ramp_stops(model.to_dict()), model)
    twice, _ = model_from_stops(_ramp_stops(once.to_dict()), once)
    assert twice.to_dict() == once.to_dict(), "write-back is not idempotent"

    # no keys at all is a valid pattern, not a crash
    empty, rep0 = model_from_stops([], PatternModel())
    assert all(p == UNUSED for p, _ in empty.colourKeys) and rep0["colour_keys"] == 0

    # without an original, emissive must fall back to blanks rather than raise
    noorig, _ = model_from_stops(stops)
    assert all(p == UNUSED for p, _ in noorig.emissiveKeys)

    print("selftest ok")


if __name__ == "__main__":
    selftest()
