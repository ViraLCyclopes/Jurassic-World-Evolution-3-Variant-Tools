"""Audit what a set of RenderDoc captures actually contains, versus what harvesting extracts.

WHY. `harvest_blocks.py` finds a block by fingerprinting two f16 words -- brightness and
saturation -- against the shipped-variant table, then confirms with the hue matrices. That works,
but it is silent about its own failures: a capture that yields nothing looks exactly like a capture
with nothing in it. Of 28 captures on this machine only 11 ever contributed a row, and that is not
obviously a property of the captures.

So this asks the question harvesting cannot: of the palette blocks that are demonstrably PRESENT
(structurally valid and non-degenerate), how many do we identify, and why do we lose the rest?

Every block is sorted into one of four buckets:

    IDENTIFIED   fingerprint matched and the hue matrices confirmed  -- harvesting gets these
    HUE_FAIL     fingerprint matched, hue matrices did NOT confirm   -- a fingerprint collision
    NO_FINGER    no variant has this brightness/saturation pair      -- the interesting one
    DEGENERATE   zero gradient (unused slot, or palette switched off)

NO_FINGER is the bucket that decides the next move. Those blocks carry real gradient coefficients
we cannot attribute to a seed. If the bucket is empty, the byte-scanner is already extracting
everything the captures hold and the only way to more seeds is more game time. If it is full, the
coefficients are sitting right there and only the *identification* is missing -- which is what the
RenderDoc replay API would supply, by tying a block to the draw call and mesh that used it.

Before reaching for that, this tries a cheaper second opinion: the two hue matrices are computed
from the FGM's rotation parameters, so they identify a variant on their own, independently of
brightness and saturation. If the unattributed blocks match a known variant's rotations, the
fingerprint is simply the wrong key and no replay API is needed.

Read-only. This never writes the coefficient table -- `harvest_blocks.py` owns that.
"""
import collections
import json
import os
import sys

import numpy as np

import _hpaths  # noqa: E402  (puts the package and its vendor/ folder on sys.path)
import harvest_blocks as hb  # noqa: E402
import material_block as mb  # noqa: E402

CAPS = hb.CAPS

#: Bucket names, in report order.
IDENTIFIED, HUE_FAIL, NO_FINGER, DEGENERATE = "IDENTIFIED", "HUE_FAIL", "NO_FINGER", "DEGENERATE"
BUCKETS = (IDENTIFIED, HUE_FAIL, NO_FINGER, DEGENERATE)


def rotation_table(rows=None):
    """[(packed_base, packed_palette, row)] -- the hue-matrix fingerprint of every known variant.

    An identifier fully independent of brightness/saturation: both matrices are determined by the
    FGM's two rotation parameters, which `hue_matrix_from_rotation` predicts exactly. Used to give
    a second opinion on blocks the f16 fingerprint cannot place.
    """
    if rows is None:
        rows = json.load(open(_hpaths.all_seeds()))
    out = []
    for r in rows:
        try:
            pb = mb.hue_matrix_from_rotation(r["u_globalColourRotationOffsetBase"])
            pp = mb.hue_matrix_from_rotation(r["u_globalColourRotationOffsetPalette"])
        except (KeyError, TypeError):
            continue
        out.append((pb, pp, r))
    return out


def match_rotation(blk, rot_tab, tol=2):
    """Rows whose predicted hue matrices agree with this block's, within `tol` per component."""
    hb_, hp = blk["hueMatrixBase"], blk["hueMatrixPalette"]
    hits = []
    for pb, pp, r in rot_tab:
        if (max(abs(x - y) for x, y in zip(pb, hb_)) <= tol and
                max(abs(x - y) for x, y in zip(pp, hp)) <= tol):
            hits.append(r)
    return hits


def classify(words, tab, blk=None):
    """Sort one candidate block into a bucket. Returns (bucket, rows_or_None, block)."""
    blk = blk if blk is not None else mb.decode(words)
    if not hb.plausible(blk):
        return DEGENERATE, None, blk
    rows = tab.get((words[6], words[7]))
    if not rows:
        return NO_FINGER, None, blk
    # share the harvester's confirm step, so the audit cannot report a yield the harvest will not
    # actually produce
    hits = hb.confirm(blk, rows)
    return (IDENTIFIED, hits, blk) if hits else (HUE_FAIL, rows, blk)


def _grad_key(blk):
    """The four gradient triples -- what a block is actually worth to us."""
    return (tuple(blk["gradOffset"]), tuple(blk["gradAmplitude"]),
            tuple(blk["gradFreq"]), tuple(blk["gradPhase"]))


def audit_capture(path, tab, chunk=128 << 20):
    """Scan one .rdc and return {bucket: {distinct_block_words: sample}} plus raw hit counts.

    Blocks are deduplicated by their twelve words: the same material is uploaded once per frame per
    draw, so a raw count says more about how many times a dinosaur was drawn than about how many
    distinct palettes the capture holds.
    """
    seen = {b: {} for b in BUCKETS}
    raw = collections.Counter()
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        base = 0
        while base < size:
            fh.seek(base)
            buf = fh.read(chunk)
            if len(buf) < 48:
                break
            a = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype=np.uint32)
            for i in hb.candidates(a):
                w = tuple(int(x) for x in a[i:i + 12])
                bucket, rows, blk = classify(w, tab)
                raw[bucket] += 1
                if w not in seen[bucket]:
                    seen[bucket][w] = {"offset": base + int(i) * 4, "rows": rows, "blk": blk}
            base += len(buf) - 64 if len(buf) == chunk else len(buf)
    return seen, raw


def report(results, rot_tab, out=sys.stdout):
    """Print the per-capture table and the verdict. `results` is [(capture_name, seen, raw)]."""
    p = lambda *a: print(*a, file=out, flush=True)  # noqa: E731

    p("\n" + "=" * 78)
    p("PER CAPTURE (distinct blocks; raw hit count in brackets)")
    p("=" * 78)
    p(f"{'capture':<44}" + "".join(f"{b[:9]:>9}" for b in BUCKETS))
    totals = collections.Counter()
    for name, seen, raw in results:
        cells = ""
        for b in BUCKETS:
            n = len(seen[b])
            totals[b] += n
            cells += f"{n:>5}[{min(raw[b], 999):>3}]" if n else f"{'.':>9}"
        p(f"{name:<44}{cells}")

    # Deduplicate ACROSS captures too: the same animal re-captured is the same block.
    merged = {b: {} for b in BUCKETS}
    for _, seen, _ in results:
        for b in BUCKETS:
            merged[b].update(seen[b])

    p("\n" + "=" * 78)
    p("ACROSS ALL CAPTURES (distinct blocks)")
    p("=" * 78)
    for b in BUCKETS:
        p(f"  {b:<12} {len(merged[b]):>6}")

    ident = merged[IDENTIFIED]
    pairs = {(r["rows"][0]["u_globalPaletteSeed"], r["rows"][0]["u_globalPaletteMaximumComplexity"])
             for r in ident.values()}
    p(f"\n  identified -> {len(pairs)} distinct (seed, complexity) pairs, "
      f"{len({s for s, _ in pairs})} distinct seeds")

    unknown = merged[NO_FINGER]
    if not unknown:
        p("\nVERDICT: nothing unattributed. The byte-scanner is extracting everything these")
        p("captures hold -- more seeds requires more captures, not better tooling.")
        return merged

    grads = {_grad_key(r["blk"]) for r in unknown.values()}
    p(f"\n  unattributed -> {len(unknown)} distinct blocks, {len(grads)} distinct gradients")

    # Second opinion: can the hue matrices place them, where the f16 fingerprint could not?
    placed, seeds_recovered, unplaced = 0, set(), []
    for w, rec in unknown.items():
        hits = match_rotation(rec["blk"], rot_tab)
        if hits:
            placed += 1
            seeds_recovered.update((h["u_globalPaletteSeed"],
                                    h["u_globalPaletteMaximumComplexity"]) for h in hits)
        else:
            unplaced.append((w, rec))
    p(f"  of those, hue matrices match a known variant: {placed}/{len(unknown)}")
    if placed:
        new = {s for s, _ in seeds_recovered} - {s for s, _ in pairs}
        p(f"    -> {len(seeds_recovered)} candidate (seed, complexity) pairs, "
          f"{len(new)} seeds not already harvested")
        if new:
            p(f"    -> new seeds: {sorted(new)}")
        p("\n    CAVEAT: a hue match is weaker evidence than the full fingerprint. Rotation is one")
        p("    parameter, so variants sharing a rotation share a matrix; check how many rows each")
        p("    block matched before trusting an attribution.")
    p(f"  neither fingerprint nor hue matches: {len(unplaced)}")
    if unplaced:
        p("    (a mod, a non-dinosaur material, or a variant missing from all_seeds.json)")
        for w, rec in list(unplaced)[:5]:
            b = rec["blk"]
            p(f"      bright{(b['brightnessBase'], b['brightnessPalette'])} "
              f"sat{(b['saturationBase'], b['saturationPalette'])} "
              f"freq{tuple(b['gradFreq'])}")
    return merged


def main(only=()):
    if not os.path.isdir(CAPS):
        sys.exit(f"no capture folder at {CAPS}")
    caps = sorted(f for f in os.listdir(CAPS) if f.endswith(".rdc"))
    if only:
        caps = [c for c in caps if any(o in c for o in only)]
    if not caps:
        sys.exit(f"no matching .rdc captures in {CAPS}")

    tab = hb.variant_table()
    rows = json.load(open(_hpaths.all_seeds()))
    rot_tab = rotation_table(rows)
    print(f"{len(rows)} variant rows, {len(tab)} distinct f16 fingerprints; "
          f"auditing {len(caps)} captures", flush=True)

    results = []
    for i, cap in enumerate(caps, 1):
        path = os.path.join(CAPS, cap)
        print(f"[{i}/{len(caps)}] {cap} ({os.path.getsize(path)/1e9:.2f} GB)", flush=True)
        seen, raw = audit_capture(path, tab)
        print("      " + "  ".join(f"{b}={len(seen[b])}" for b in BUCKETS), flush=True)
        results.append((cap, seen, raw))

    report(results, rot_tab)


def selftest():
    """Each bucket must be reachable, and a planted block must land in the right one."""
    rot, rotp = 0.322, -0.75
    pb = mb.hue_matrix_from_rotation(rot)
    pp = mb.hue_matrix_from_rotation(rotp)
    pack = mb._pack_s10x3
    f16 = mb._pack_f16pair

    def block(w4, w5, amp=(181, 211, 167), freq=(204, 204, 51), w6=None, w7=None):
        return (0, 0, 1 << 17, 0, w4, w5,
                f16(1.0, 1.5) if w6 is None else w6,
                f16(2.0, 2.5) if w7 is None else w7,
                pack(396, 405, 212), pack(*amp), pack(*freq), pack(511, 66, 59))

    row = {"u_globalColourRotationOffsetBase": rot, "u_globalColourRotationOffsetPalette": rotp,
           "u_globalPaletteSeed": 29, "u_globalPaletteMaximumComplexity": 3,
           "u_globalColourBrightnessBase": 1.0, "u_globalColourBrightnessPalette": 1.5,
           "u_globalColourSaturationBase": 2.0, "u_globalColourSaturationPalette": 2.5,
           "ovl": "T.ovl", "fgm": "t.fgm"}
    tab = {(f16(1.0, 1.5), f16(2.0, 2.5)): [row]}
    rot_tab = rotation_table([row])

    good = block(pack(*pb), pack(*pp))
    assert classify(good, tab)[0] == IDENTIFIED, classify(good, tab)[0]

    # Same fingerprint, different rotation -> the hue confirm must reject it.
    other = mb.hue_matrix_from_rotation(0.1)
    assert classify(block(pack(*other), pack(*pp)), tab)[0] == HUE_FAIL

    # Unknown brightness/saturation -> unattributed, but the hue matrices still place it. This is
    # the whole point of the second opinion, so it must actually work.
    orphan = block(pack(*pb), pack(*pp), w6=f16(0.125, 0.375))
    assert classify(orphan, tab)[0] == NO_FINGER
    hits = match_rotation(mb.decode(orphan), rot_tab)
    assert len(hits) == 1 and hits[0]["u_globalPaletteSeed"] == 29, hits

    # A never-filled slot is degenerate even with valid hue matrices, and must NOT be counted as
    # unattributed -- otherwise every capture reports thousands of phantom findings.
    assert classify(block(pack(*pb), pack(*pp), amp=(0, 0, 0), freq=(0, 0, 0)), tab)[0] == DEGENERATE

    # A block must be found at the right offset by the same structural filter harvesting uses,
    # so the audit and the harvester cannot disagree about what is present.
    words = np.array((0,) * 4 + good[4:] + (0,) * 4, dtype=np.uint32)
    assert list(hb.candidates(words)) == [0], list(hb.candidates(words))

    # Deduplication key: two blocks differing only in gradient must be distinct gradients.
    a, b = mb.decode(good), mb.decode(block(pack(*pb), pack(*pp), freq=(20, 20, 20)))
    assert _grad_key(a) != _grad_key(b)

    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main(only=tuple(a for a in sys.argv[1:] if not a.startswith("-")))
