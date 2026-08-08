"""Turn a hand-painted zone map into a game-ready patchwork map.

AUTHORING ONLY. Nothing here runs at preview time -- `patchwork.py` is the model of what the game
does, and keeping the two apart means work on this import cannot quietly change that model.

Source-agnostic on purpose: Blender, Substance Painter, Krita, Photoshop or a script, greyscale or
colour, 8- or 16-bit. Clustering is what makes that possible -- the author paints in whatever is
legible and says which zone each colour means, instead of having to hit greys 26/77/128/179/230.

EXPORT WITHOUT COLOUR MANAGEMENT. A map tagged sRGB is decoded on load and every value shifts zone.
"""
from dataclasses import dataclass

import numpy as np

import patchwork

#: A colour holding less than this share of texels is anti-aliasing, not a zone.
#: 0.5% cleanly separated the two real plateaus from ~50 edge levels across all nine shipped maps.
MIN_FRACTION = 0.005

#: Distinct colours considered before merging. Guards against a pathological source (photo, noise)
#: producing tens of thousands of candidates and hanging the UI.
MAX_CLUSTERS = 64

#: Candidates closer than this in RGB are one painted colour spread by lossy encoding.
#: Deliberately SMALL. It absorbs JPEG/BC4 dither around a painted colour and nothing more.
#:
#: It cannot separate colours that are closer together than their own noise, and it is not meant
#: to: Atrociraptor's shipped BC4 map holds two zones at greys ~193 and ~205 -- 12 apart, less than
#: the spread within each -- so no distance-based clustering could ever recover them. That map does
#: not need it. An existing game map is ALREADY quantised to zones, so it is read straight through
#: `patchwork.region_of` by `PatternTab.set_patchwork_map`. Import exists for maps painted in
#: arbitrary colours, where the author picks colours they can tell apart.
MERGE_DISTANCE = 12


@dataclass
class Cluster:
    rgb: tuple
    fraction: float
    zone: int = None            # None until the user (or assign_default_zones) picks one


def _rgb8(image):
    """Any image -> (H, W, 3) uint8. Drops alpha, expands greyscale, scales 16-bit down."""
    a = np.asarray(image)
    if a.dtype == np.uint16:
        a = (a >> 8).astype(np.uint8)
    elif a.dtype != np.uint8:
        a = np.clip(a, 0, 1) * 255.0 if a.max() <= 1.0 else np.clip(a, 0, 255)
        a = a.astype(np.uint8)
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
    return np.ascontiguousarray(a[..., :3])


def cluster(image, min_fraction=MIN_FRACTION):
    """Distinct painted colours holding real area, largest first."""
    a = _rgb8(image)
    flat = a.reshape(-1, 3)
    packed = (flat[:, 0].astype(np.uint32) << 16) | (flat[:, 1].astype(np.uint32) << 8) | flat[:, 2]
    vals, counts = np.unique(packed, return_counts=True)
    total = flat.shape[0] or 1
    order = np.argsort(-counts)
    out = []
    for i in order:
        frac = float(counts[i]) / total
        if frac < min_fraction or len(out) >= MAX_CLUSTERS:
            break
        v = int(vals[i])
        out.append(Cluster(((v >> 16) & 255, (v >> 8) & 255, v & 255), frac))
    if not out:            # every colour is tiny (noise); keep the single largest so import works
        i = int(np.argmax(counts))
        v = int(vals[i])
        out.append(Cluster(((v >> 16) & 255, (v >> 8) & 255, v & 255), float(counts[i]) / total))
    return _merge(out)


def _merge(clusters, distance=MERGE_DISTANCE, max_out=patchwork.N_ZONES):
    """Agglomerate candidates that are really one painted colour spread by compression.

    Repeatedly merges the closest pair of centroids, weighted by area, while they are nearer than
    `distance` OR there are still more than `max_out` of them. Without this a lossily-encoded map
    returns one "colour" per compression level -- 20 of them on the real Atrociraptor map, whose
    two plateaus sit at greys ~193 and ~205.

    `max_out` also makes the >5-colour case resolve itself instead of leaving rows unassigned.
    """
    cs = [Cluster(tuple(c.rgb), c.fraction) for c in clusters]
    while len(cs) > 1:
        best, bi, bj = None, -1, -1
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                d = sum((cs[i].rgb[k] - cs[j].rgb[k]) ** 2 for k in range(3)) ** 0.5
                if best is None or d < best:
                    best, bi, bj = d, i, j
        if best > distance and len(cs) <= max_out:
            break
        a, b = cs[bi], cs[bj]
        w = a.fraction + b.fraction or 1.0
        cs[bi] = Cluster(
            tuple(int(round((a.rgb[k] * a.fraction + b.rgb[k] * b.fraction) / w)) for k in range(3)),
            a.fraction + b.fraction)
        cs.pop(bj)
    cs.sort(key=lambda c: -c.fraction)
    return cs


def assign_default_zones(clusters):
    """Pre-assign the five largest clusters to zones 0..4; leave any remainder unassigned.

    Deliberately not an error: a 6-colour paint job is a normal mistake, and the table can fix it
    by merging colours onto shared zones. `quantise` is what refuses to run while any zone is None.
    """
    for i, c in enumerate(clusters):
        c.zone = i if i < patchwork.N_ZONES else None
    return clusters


def quantise(image, clusters):
    """Assign every texel to its nearest cluster, then write that cluster's zone centre.

    HARD EDGES, always. No anti-aliasing, no smoothing, nearest only -- the game does
    trunc(v * 4.99), so a soft ramp from zone 0 (26) to zone 4 (230) passes THROUGH zones 1, 2 and
    3 and paints phantom bands of unrelated zones along every painted edge. They are invisible here
    and obvious on the animal.
    """
    missing = [c for c in clusters if c.zone is None]
    if missing:
        raise ValueError("%d cluster(s) have no zone assigned" % len(missing))
    a = _rgb8(image)
    centres = np.array([c.rgb for c in clusters], np.int32)          # (K, 3)
    zones = np.array([c.zone for c in clusters], np.int32)           # (K,)
    flat = a.reshape(-1, 3).astype(np.int32)
    # nearest by squared euclidean RGB distance; chunked so a 2048^2 map cannot blow up memory
    out = np.empty(flat.shape[0], np.uint8)
    step = 1 << 18
    for s in range(0, flat.shape[0], step):
        chunk = flat[s:s + step]
        d = ((chunk[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        out[s:s + step] = np.array(patchwork.ZONE_CENTRES, np.uint8)[zones[d.argmin(axis=1)]]
    return out.reshape(a.shape[:2])


#: Zone colours for the confirm-table preview. Distinguishable at a glance, matching the mask
#: renders used to read the in-game verification runs.
PREVIEW_COLOURS = ((220, 60, 60), (220, 160, 40), (60, 200, 60), (60, 140, 220), (180, 80, 220))


def preview_rgb(grey_map):
    """Quantised greyscale map -> an RGB image with one flat colour per zone."""
    reg = patchwork.region_of(grey_map)
    lut = np.array(PREVIEW_COLOURS, np.uint8)
    return lut[np.clip(reg, 0, patchwork.N_ZONES - 1)]


def selftest():
    import numpy as np
    import patchwork

    # three flat colours, unequal areas
    img = np.zeros((10, 10, 3), np.uint8)
    img[:5] = (255, 0, 0)          # 50%  red
    img[5:8] = (0, 255, 0)         # 30%  green
    img[8:] = (0, 0, 255)          # 20%  blue
    cl = assign_default_zones(cluster(img))
    assert len(cl) == 3, cl
    assert [c.zone for c in cl] == [0, 1, 2], cl
    q = quantise(img, cl)
    assert q.dtype == np.uint8 and q.shape == (10, 10), (q.dtype, q.shape)
    # the quantised map must read back as the zones we assigned
    reg = patchwork.region_of(q)
    assert set(np.unique(reg).tolist()) == {0, 1, 2}, np.unique(reg)
    assert (patchwork.region_of(q[:5]) == 0).all()
    assert (patchwork.region_of(q[8:]) == 2).all()

    # an anti-aliased edge is ABSORBED, not promoted to its own cluster.
    # 100x100 = 10000 texels, so the 0.5% threshold is 50 texels; a 10-texel blend row is well
    # under it. (A 10x10 image cannot test this at all -- one texel is already 1%.)
    aa = np.zeros((100, 100, 3), np.uint8)
    aa[:50] = (255, 0, 0)
    aa[50:] = (0, 0, 255)
    aa[50, :10] = (128, 0, 128)    # 10 texels = 0.1%, pure AA
    cl2 = cluster(aa)              # default min_fraction 0.5%
    assert len(cl2) == 2, cl2      # the blend row must NOT become a third cluster
    assert {tuple(c.rgb) for c in cl2} == {(255, 0, 0), (0, 0, 255)}, cl2
    # and it must still quantise into one of the two real zones, not a third
    cl2 = assign_default_zones(cl2)
    assert len(np.unique(patchwork.region_of(quantise(aa, cl2)))) == 2

    # THE PHANTOM-BAND REGRESSION.
    # A smooth red->white gradient crosses every zone if quantisation is soft. It must collapse to
    # the TWO clusters that actually hold area, never five. In game a soft edge between zone 0 and
    # zone 4 renders as bands of zones 1/2/3 -- invisible in the editor, obvious on the animal.
    grad = np.zeros((1, 256, 3), np.uint8)
    grad[0, :, 0] = 255
    grad[0, :, 1] = np.arange(256)
    grad[0, :, 2] = np.arange(256)
    grad[0, :100] = (255, 0, 0)         # a real plateau
    grad[0, 156:] = (255, 255, 255)     # a second real plateau
    cl3 = assign_default_zones(cluster(grad))
    q3 = quantise(grad, cl3)
    assert len(np.unique(patchwork.region_of(q3))) == 2, np.unique(patchwork.region_of(q3))

    # uniform image -> exactly one cluster, still a valid map
    uni = np.full((4, 4, 3), 90, np.uint8)
    cl4 = assign_default_zones(cluster(uni))
    assert len(cl4) == 1 and cl4[0].zone == 0, cl4

    # more than five painted colours: merged down to five, all assignable. There are only five
    # zones, so they have to collapse somehow; nearest-colour is the sane way and the table can
    # still reassign afterwards.
    many = np.zeros((1, 70, 3), np.uint8)
    for i in range(7):
        many[0, i * 10:(i + 1) * 10] = (i * 30, 0, 0)
    cl5 = assign_default_zones(cluster(many))
    assert len(cl5) == patchwork.N_ZONES, cl5
    assert all(c.zone is not None for c in cl5), cl5
    assert len(np.unique(patchwork.region_of(quantise(many, cl5)))) == patchwork.N_ZONES

    # quantise still refuses an unassigned zone -- the dialog can clear one by hand
    cl5[0].zone = None
    try:
        quantise(many, cl5)
    except ValueError:
        pass
    else:
        raise AssertionError("quantise must refuse clusters with no zone")

    # COMPRESSION SPREAD. A painted map that went through JPEG/BC4: each colour becomes a spread
    # of neighbouring values. They must merge back to one cluster per painted colour, not one per
    # encoding level -- without merging this returns ~22 clusters.
    comp = np.zeros((100, 100, 3), np.uint8)
    rng = np.random.default_rng(0)
    comp[:40] = rng.integers(55, 66, (40, 100, 1)).repeat(3, axis=2)
    comp[40:] = rng.integers(195, 206, (60, 100, 1)).repeat(3, axis=2)
    cc = cluster(comp)
    assert len(cc) == 2, [(c.rgb, round(c.fraction, 3)) for c in cc]
    assert abs(cc[0].fraction - 0.6) < 0.02 and abs(cc[1].fraction - 0.4) < 0.02, cc
    # NOT merged when the painted colours are genuinely distinct but close-ish
    far = np.zeros((10, 10, 3), np.uint8)
    far[:5] = (100, 100, 100)
    far[5:] = (130, 130, 130)          # 52 apart in RGB, well over MERGE_DISTANCE
    assert len(cluster(far)) == 2, cluster(far)

    pv = preview_rgb(quantise(img, cl))
    assert pv.shape == (10, 10, 3) and pv.dtype == np.uint8

    print("selftest ok")


if __name__ == "__main__":
    selftest()
