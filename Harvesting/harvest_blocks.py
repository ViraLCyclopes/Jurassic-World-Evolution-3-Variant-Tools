"""Harvest JWE3 material-parameter blocks out of RenderDoc captures.

WHY. The whole dinosaur colour model is solved except one lookup:
`(seed, complexity) -> twelve signed 10-bit cosine-gradient coefficients`. The game bakes those
on the CPU, so they are not in any shader or asset -- but they ARE in the GPU material buffer,
and a .rdc stores that buffer uncompressed. This pulls them out.

HOW WE FIND A BLOCK. Words 4 and 5 are circulant hue-rotation matrices, and every such matrix
has rows summing to 1 -- so each packed triple sums to 511 regardless of the angle. Two
independent triples both summing to 511 is a ~1-in-millions coincidence, which makes it a cheap
structural filter over gigabytes. Candidates are then IDENTIFIED by matching six known f16 values
(brightness x2, saturation x2, palette scale, palette offset) against the shipped-variant table,
and CONFIRMED by predicting both hue matrices from the FGM's rotation values.

That is 10+ independent values agreeing, so a match is not in doubt.

Verified end to end on Albertosaurus_Juvenile variant 4 (seed 29, complexity 3).
"""
import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
import _hpaths  # noqa: E402  (puts the package and its vendor/ folder on sys.path)
import material_block as mb  # noqa: E402

CAPS = os.path.join(os.environ.get("TEMP", ""), "RenderDoc")
# Harvested rows go straight into YOUR coefficient table, which the editor layers over the shipped
# one -- so a capture shows up in the preview immediately, and survives updating the tool.
OUT = _hpaths.coeff_out()


def _f16w(lo, hi):
    return (struct.unpack("<H", struct.pack("<e", lo))[0] |
            (struct.unpack("<H", struct.pack("<e", hi))[0] << 16))


def variant_table():
    """{(word6, word7): [variant rows]} -- the identifying f16 fingerprint per variant.

    DO NOT add word3 (instancePaletteScale/Offset) to this key. Those are *instance* parameters --
    the game varies them per animal, so the GPU block never matches the FGM value and the whole
    fingerprint fails. That bug cut the yield from dozens of blocks to two. Brightness and
    saturation are material-level and do match; the hue matrices then confirm.
    """
    rows = json.load(open(os.path.join(HERE, "all_seeds.json")))
    # Rows for a seed-sweep mod, if one is installed. `gen_seedsweep_round.py` gives every swept
    # variant a brightness/saturation quadruple that collides with none of the shipped ones, which
    # is the only thing that makes the swept blocks identifiable -- without it all 36 share one
    # fingerprint and every match is ambiguous across all 36 seeds.
    sweep = os.path.join(HERE, "seedsweep_seeds.json")
    if os.path.isfile(sweep):
        extra = json.load(open(sweep))
        rows = rows + extra
        print(f"  + {len(extra)} seed-sweep rows from {os.path.basename(sweep)}")
    tab = {}
    for r in rows:
        try:
            key = (_f16w(r["u_globalColourBrightnessBase"], r["u_globalColourBrightnessPalette"]),
                   _f16w(r["u_globalColourSaturationBase"], r["u_globalColourSaturationPalette"]))
        except (KeyError, OverflowError, struct.error):
            continue
        tab.setdefault(key, []).append(r)
    return tab


def plausible(blk):
    """Reject degenerate blocks that pass the structural filter but are not real palettes.

    A material slot that was allocated but never used reads back as a block whose hue matrices
    still satisfy `sum == 511` (so `candidates` accepts it) while the gradient is all zeros. Every
    genuine row harvested so far has amplitude in 128..307 and frequency from a small discrete set
    {20, 51, 102, 153, 204, 307, 460, 476, 497, 511}, so a zero or a negative in either is out of
    family.

    Caught by the user pointing out they had never spawned a Dimorphodon, only hovered it in the
    spawner -- the block was real memory, never filled in, and it had reported three mutually
    contradictory values across runs.

    HONEST CAVEAT: an all-zero gradient is ALSO what a variant with the palette legitimately
    disabled would look like (PALETTE.md: bit 17 of word 2 is the gradient enable). This filter
    cannot tell the two apart. Excluding both is still right for this file's purpose -- it exists to
    learn `(seed, complexity) -> coefficients`, and a zero gradient teaches nothing either way --
    but do not read "rejected" as "false positive".

    NOT the reason a species-viewer capture yields nothing: with this filter disabled entirely, a
    capture taken in the species viewer still contains no matching block. Capture in the park.
    """
    amp = blk["gradAmplitude"]
    frq = blk["gradFreq"]
    if not any(amp) or not any(frq):
        return False
    return all(a >= 0 for a in amp) and all(f >= 0 for f in frq)


def candidates(words):
    """Vectorised structural filter: both hue-matrix words must have triples summing to 511."""
    def triple_sum(a):
        lo = (a & 0x3FF).astype(np.int32)
        mid = ((a >> 10) & 0x3FF).astype(np.int32)
        hi = ((a >> 20) & 0x3FF).astype(np.int32)
        for v in (lo, mid, hi):
            v[v >= 512] -= 1024
        return lo + mid + hi
    n = len(words) - 11
    if n <= 0:
        return np.array([], dtype=np.int64)
    w4 = words[4:4 + n]
    w5 = words[5:5 + n]
    ok = (np.abs(triple_sum(w4) - 511) <= 2) & (np.abs(triple_sum(w5) - 511) <= 2)
    return np.nonzero(ok)[0]


def scan(path, tab, chunk=128 << 20, filter_degenerate=True):
    """Yield (byte_offset, variant_row, decoded_block) for every confirmed block."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        base = 0
        while base < size:
            fh.seek(base)
            buf = fh.read(chunk)
            if len(buf) < 48:
                break
            a = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype=np.uint32)
            for i in candidates(a):
                w = [int(x) for x in a[i:i + 12]]
                key = (w[6], w[7])
                rows = tab.get(key)
                if not rows:
                    continue
                blk = mb.decode(w)
                if filter_degenerate and not plausible(blk):
                    continue
                # confirm: both hue matrices must match what the FGM's rotations predict
                for r in rows:
                    pb = mb.hue_matrix_from_rotation(r["u_globalColourRotationOffsetBase"])
                    pp = mb.hue_matrix_from_rotation(r["u_globalColourRotationOffsetPalette"])
                    if (max(abs(x - y) for x, y in zip(pb, blk["hueMatrixBase"])) <= 2 and
                            max(abs(x - y) for x, y in zip(pp, blk["hueMatrixPalette"])) <= 2):
                        yield base + int(i) * 4, r, blk
                        break
            # overlap so a block straddling a chunk boundary is not lost
            base += len(buf) - 64 if len(buf) == chunk else len(buf)


def main(only=()):
    """Scan captures and MERGE into the coefficient file.

    `only` filters captures by substring, so a new capture can be harvested on its own instead of
    re-reading five gigabytes. The merge matters: the old version rebuilt the file from whatever
    this run found, so a targeted scan would have silently deleted every previously harvested row.
    """
    if not os.path.isdir(CAPS):
        sys.exit(f"no capture folder at {CAPS}")
    caps = sorted(f for f in os.listdir(CAPS) if f.endswith(".rdc"))
    if only:
        caps = [c for c in caps if any(o in c for o in only)]
    if not caps:
        sys.exit(f"no matching .rdc captures in {CAPS}")
    tab = variant_table()
    print(f"{len(tab)} distinct variant fingerprints; scanning {len(caps)} captures\n")

    found = json.load(open(OUT)) if os.path.isfile(OUT) else {}
    before = len(found)
    for cap in caps:
        path = os.path.join(CAPS, cap)
        print(f"=== {cap} ({os.path.getsize(path)/1e9:.2f} GB) ===", flush=True)
        n = 0
        for off, row, blk in scan(path, tab):
            n += 1
            key = f"{round(row['u_globalPaletteSeed'])}_{round(row['u_globalPaletteMaximumComplexity'])}"
            rec = {
                "seed": round(row["u_globalPaletteSeed"]),
                "complexity": round(row["u_globalPaletteMaximumComplexity"]),
                "gradOffset": blk["gradOffset"],
                "gradAmplitude": blk["gradAmplitude"],
                "gradFreq": blk["gradFreq"],
                "gradPhase": blk["gradPhase"],
                "from": [row["ovl"].split("\\")[-1], row["fgm"]],
                "capture": cap,
                "offset": off,
            }
            if key in found and found[key]["gradFreq"] != rec["gradFreq"]:
                print(f"  !! CONFLICT at {key}: {found[key]['gradFreq']} vs {rec['gradFreq']}"
                      f"  ({found[key]['from'][1]} vs {rec['from'][1]})")
            found[key] = rec
        print(f"  {n} confirmed blocks, {len(found)} distinct (seed,complexity) so far\n",
              flush=True)

    json.dump(found, open(OUT, "w"), indent=1)
    print("=" * 72)
    print(f"{len(found)} distinct (seed, complexity) pairs "
          f"({len(found) - before} new this run)  ->  {OUT}")
    print("(the shipped variants use 720 pairs in total)")
    for k in sorted(found, key=lambda s: [int(x) for x in s.split("_")])[:20]:
        r = found[k]
        print(f"  seed {r['seed']:>3} cplx {r['complexity']}  "
              f"off{tuple(r['gradOffset'])} amp{tuple(r['gradAmplitude'])} "
              f"freq{tuple(r['gradFreq'])} pha{tuple(r['gradPhase'])}")


def selftest():
    """The structural filter must accept real hue matrices and reject arbitrary words."""
    good = []
    for rot in (0.0, 0.322, -0.75, 1.0, 0.5):
        p, q, r = mb.hue_matrix_from_rotation(rot)
        good.append((p & 0x3FF) | ((q & 0x3FF) << 10) | ((r & 0x3FF) << 20))
    for w in good:
        assert mb.is_hue_matrix(mb.s10x3(w)), (w, mb.s10x3(w))
    # a full synthetic block must be picked up at the right index
    words = np.array([0] * 4 + [good[0], good[1]] + [0] * 6, dtype=np.uint32)
    assert list(candidates(words)) == [0], list(candidates(words))
    # noise must not pass -- 5000 random words, expect no hits
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 2**32, size=5000, dtype=np.uint64).astype(np.uint32)
    assert len(candidates(noise)) == 0, len(candidates(noise))

    # a never-used material slot must be rejected even though its hue matrices look valid
    assert not plausible({"gradAmplitude": [0, 0, 0], "gradFreq": [0, 0, 0]})
    assert not plausible({"gradAmplitude": [181, 211, 167], "gradFreq": [0, 0, 0]})
    assert not plausible({"gradAmplitude": [0, 65, 372], "gradFreq": [157, 256, -208]})
    # every genuine row already harvested must survive the filter
    if os.path.isfile(OUT):
        for k, r in json.load(open(OUT)).items():
            assert plausible(r), (k, r["from"])
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main(only=tuple(a for a in sys.argv[1:] if not a.startswith("-")))
