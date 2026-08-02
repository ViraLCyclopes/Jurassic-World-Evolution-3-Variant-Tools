"""Bake a JWE3 pattern's sparse keys into the 32-entry gradient LUT.

The game bakes this CPU-side into `pPatterning_PatternGradientMap`, which is why that slot is an
inline RGBA placeholder in every shipped FGM and no gradient-map file exists on disk. This is the
reproduction of that bake.

INTERPOLATION IS NOT YET CONFIRMED. `interp="linear"` is an assumption; `"step"` is implemented so
that settling it from the shader IR is a one-line change. See PATTERNS.md open question 1.
"""
import numpy as np

from pattern_model import LUT_SIZE, UNUSED

# Opacity at or below this counts as "nothing renders here". Chosen from the shipped roster: the
# base-game species that author a true zero come out at exactly 0.00, while Dimetrodon and
# Lokiceratops land at 0.05-0.07 through key interpolation and are still meant to be transparent.
BLANK_OPACITY = 0.08


def _sample(lut, x):
    """LUT row at a fractional index, clamped and linearly blended between neighbours."""
    lo = int(np.floor(x))
    hi = min(LUT_SIZE - 1, int(np.ceil(x)))
    t = x - lo
    return lut[lo] * (1.0 - t) + lut[hi] * t


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
    # Stable sort by position, then keep the LAST key at each position.
    #
    # DUPLICATE POSITIONS OCCUR IN REAL DATA and must be handled explicitly: Therizinosaurus
    # carries three keys at position 11 (0.0, 0.24, 1.0) and Scorpios rex two at position 20.
    # np.interp requires increasing x and returns something arbitrary otherwise, which silently
    # corrupted the curve and made threshold_from_model report the wrong slot.
    #
    # "Last wins" matches how the attributes are laid out -- later slots overwrite earlier ones --
    # but it is an ASSUMPTION, not measured. See PATTERNS.md open question 8.
    used.sort(key=lambda kv: kv[0])
    dedup = {}
    for p, v in used:
        dedup[p] = v
    used = sorted(dedup.items())
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


def index_of(byte_value):
    """Greyscale byte from u_basePatternMap -> position on the 0..31 key axis.

    Supported by the background test in PATTERNS.md 4.6: 11 of 17 species author exactly zero
    opacity at the index this formula gives for their own background value, and for most of them
    that index is 16.65 -- mid-axis, where nothing would put a zero by accident.
    """
    return float(byte_value) / 255.0 * (LUT_SIZE - 1)


def map_stats(image_or_array):
    """(background_byte, lo_byte, hi_byte) for a pattern map.

    The background is the MODE, not the mean or the midpoint: every shipped map is dominated by one
    value meaning "no pattern here" (137 or 138 for most species, 205 for Dimetrodon, 0 for
    Herrerasaurus), covering 48-100% of the texture.
    """
    a = np.asarray(image_or_array)
    if a.ndim == 3:
        a = a[..., 0]
    a = a.ravel()
    vals, counts = np.unique(a, return_counts=True)
    return int(vals[counts.argmax()]), int(a.min()), int(a.max())


def reachable_range(image_or_array):
    """(lo_index, hi_index) of the key axis a species' map can actually sample.

    Keys outside this are DEAD -- Dimetrodon's map reaches 23.2..29.9, so a key it authors at
    position 5 can never be reached. An editor should grey those slots out.
    """
    _, lo, hi = map_stats(image_or_array)
    return index_of(lo), index_of(hi)


def threshold_from_model(model, interp="linear"):
    """Recover a species' "no pattern" threshold from a pattern FGM alone, as (index, byte).

    The opacity curve bottoms out at the index of that species' own map background, so the zero
    plateau gives the threshold back without the texture. Measured on 13 of 17 species; the four
    misses are all modded assets. See PATTERNS.md 4.6.

        Dimetrodon    -> (24, 197)   map background is 0xcd = 205 -> index 24.9
        Herrerasaurus -> ( 0,   0)   map background is 0x00
        most species  -> (16, 131)   map background is 0x89 = 137 -> index 16.65

    The byte is the centre of the plateau converted back, so it lands NEAR the true background
    rather than exactly on it -- keys sit on integers while the background rarely does.
    """
    lut = bake_channel(model.opacityKeys, 1, interp)[:, 0]
    zeros = np.flatnonzero(lut <= lut.min() + 1e-9)
    # plain Python types at the boundary: a numpy float64 is not `==` fragile, but a numpy bool
    # derived from one is not `is True`, which silently breaks callers that test identity
    idx = float((zeros.min() + zeros.max()) / 2.0)
    return idx, int(round(idx / (LUT_SIZE - 1) * 255))


class Threshold:
    """A species' "no pattern" level, and how confident we are in it.

    `byte` / `index` are the answer. `source` is "map", "fgm" or "both". `agree` is None when only
    one source was available OR the map is flat. `warning` is a human-readable problem or None.

    `flat` is True when the map is a single value, so the whole body samples ONE slot. What that
    means then depends on the opacity there, which is what `uniform_opacity` reports:

        flat and uniform_opacity ~ 0   -> blank: the species has no pattern (deliberate)
        flat and uniform_opacity > 0   -> the ENTIRE animal is uniformly tinted by uniform_colour
    """

    def __init__(self, byte, index, source, agree=None, warning=None,
                 flat=False, uniform_opacity=None, uniform_colour=None):
        self.byte, self.index, self.source = byte, index, source
        self.agree, self.warning, self.flat = agree, warning, flat
        self.uniform_opacity, self.uniform_colour = uniform_opacity, uniform_colour

    @property
    def blank(self):
        """A flat map whose one slot is transparent -- genuinely no pattern."""
        return bool(self.flat and (self.uniform_opacity is None
                                   or self.uniform_opacity <= BLANK_OPACITY))

    def __repr__(self):
        w = f" WARNING: {self.warning}" if self.warning else ""
        if self.flat:
            kind = "BLANK" if self.blank else f"UNIFORM a={self.uniform_opacity:.2f}"
            return f"<Threshold 0x{self.byte:02x} idx={self.index:.2f} FLAT/{kind}{w}>"
        return f"<Threshold 0x{self.byte:02x} idx={self.index:.2f} from {self.source}{w}>"


def resolve_threshold(pattern_map=None, model=None, interp="linear", tolerance=2.0):
    """Work out a species' threshold from whatever is available, and flag disagreement.

    NOTHING here is species-specific -- a dinosaur with a background nobody has seen resolves the
    same way. 0x89, 0xcd and 0x00 are simply what the shipped roster happens to use.

    The MAP wins when both are present: it is what the shader actually samples, whereas the FGM
    reading is inferred from an authoring convention and is quantised to integer key positions.
    The FGM is the fallback when no map is to hand.

    Disagreement beyond `tolerance` LUT slots is reported, not silently resolved. That is the
    modded-asset failure mode -- every species that failed the survey in PATTERNS.md 4.6 was a mod
    -- and it means the pattern will tint the whole animal instead of only its markings.
    """
    m_idx = f_idx = None
    flat = False
    if pattern_map is not None:
        bg, lo, hi = map_stats(pattern_map)
        m_idx = index_of(bg)
        flat = (lo == hi)
    if model is not None:
        f_idx, _ = threshold_from_model(model, interp=interp)

    if m_idx is None and f_idx is None:
        raise ValueError("resolve_threshold needs a pattern_map, a model, or both")

    if m_idx is None:
        return Threshold(int(round(f_idx / (LUT_SIZE - 1) * 255)), f_idx, "fgm")

    # A FLAT map means the whole body samples ONE slot. That is a legitimate authoring choice and
    # it has TWO different outcomes, so never assume "flat == no pattern":
    #
    #   * filled with the threshold colour  -> transparent there -> the species has no pattern.
    #     The Deathclaw mod does exactly this, and its leftover opacity curve must not be reported
    #     as a mismatch.
    #   * filled with any OTHER value       -> that slot's colour is painted over the ENTIRE animal.
    #
    # Which one it is depends on the opacity at that slot, so report the outcome rather than guess.
    if flat:
        a = c = None
        if model is not None:
            a = background_opacity(model, bg, interp=interp)
            c = _sample(bake_channel(model.colourKeys, 3, interp), m_idx).tolist()
        warn = None
        if a is not None and a > BLANK_OPACITY:
            warn = (f"the pattern map is a single flat value (0x{bg:02x}) but opacity there is "
                    f"{a:.2f}, not 0 -- the whole animal will be uniformly tinted rgb"
                    f"{tuple(round(x, 3) for x in c)}, not left unpatterned.")
        return Threshold(int(round(m_idx / (LUT_SIZE - 1) * 255)), m_idx, "map",
                         agree=None, warning=warn, flat=True,
                         uniform_opacity=a, uniform_colour=c)
    if f_idx is None:
        return Threshold(int(round(m_idx / (LUT_SIZE - 1) * 255)), m_idx, "map")

    agree = bool(abs(m_idx - f_idx) <= tolerance)
    warning = None
    if not agree:
        warning = (
            f"pattern map background is index {m_idx:.2f} but the FGM's opacity bottoms out at "
            f"{f_idx:.2f} ({abs(m_idx - f_idx):.1f} slots apart). The pattern will not be "
            f"transparent over the unpatterned area, so it will tint the whole animal.")
    # the map is ground truth; the FGM only corroborates it
    return Threshold(int(round(m_idx / (LUT_SIZE - 1) * 255)), m_idx, "both", agree, warning)


def background_opacity(model, background_byte, interp="linear"):
    """Baked opacity at the species' background index.

    Should be ~0: the background means "no pattern here". Every base-game species measured gives
    exactly 0.00; the ones that fail are modded assets whose patterns tint the whole animal.
    Use this as an authoring warning.
    """
    lut = bake_channel(model.opacityKeys, 1, interp)
    return float(_sample(lut, index_of(background_byte))[0])


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

    # --- DUPLICATE POSITIONS occur in real data and must resolve deterministically.
    #     Therizinosaurus really does carry three keys at position 11 (0.0, 0.24, 1.0) and
    #     Scorpios rex two at position 20. np.interp requires increasing x and returns something
    #     arbitrary otherwise, which corrupted the curve silently. Last key at a position wins. ---
    theri = [(10, 0.691), (11, 0.0), (11, 0.24), (11, 1.0), (16, 0.0), (19, 1.0),
             (-1, 0.0), (-1, 0.0)]
    t = bake_channel(theri, 1)
    assert np.isclose(t[11, 0], 1.0), f"duplicate keys did not resolve last-wins: {t[11, 0]}"
    assert np.isclose(t[16, 0], 0.0) and np.isclose(t[19, 0], 1.0)
    # position 11 must be reachable at all -- an unsorted np.interp used to flatten this region
    assert t[13, 0] > t[16, 0], "the 11..16 descent was lost"
    # "Last wins" means SLOT order in the FGM, so it is deliberately order-dependent: the same
    # three values in a different slot order resolve to a different key. Pinning both directions
    # here so the rule is unambiguous if it ever has to be revisited against the shader.
    reordered = [(11, 0.24), (19, 1.0), (11, 1.0), (10, 0.691), (16, 0.0), (11, 0.0)]
    assert np.isclose(bake_channel(reordered, 1)[11, 0], 0.0), "slot order stopped deciding"
    # ...and non-duplicate keys are unaffected by their slot order
    assert np.allclose(bake_channel(reordered, 1)[19], bake_channel(theri, 1)[19])

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

    # --- background / reachability helpers (PATTERNS.md 4.6) ---
    # the two real background bytes, and the indices they must produce
    assert np.isclose(index_of(137), 16.6549019), index_of(137)
    assert np.isclose(index_of(205), 24.9215686), index_of(205)
    assert index_of(0) == 0.0 and index_of(255) == 31.0

    # map_stats takes the MODE, not the mean or the midpoint. Build a map that would give a
    # different answer under either of those, so the test cannot pass by accident.
    fake = np.concatenate([np.full(900, 137, np.uint8), np.arange(0, 250, 5, dtype=np.uint8)])
    assert map_stats(fake) == (137, 0, 245), map_stats(fake)
    assert map_stats(fake)[0] != int(round(fake.mean())), "mode coincided with mean; weak fixture"
    assert np.allclose(reachable_range(fake), (0.0, 245 / 255 * 31))

    # Albertosaurus pins a zero plateau at 16 AND 17, straddling its background index of 16.65.
    # That double zero is the strongest evidence for the index formula -- keep it pinned.
    alberto = [(-1, 0.0), (12, 0.45), (13, 0.2), (16, 0.0), (17, 0.0), (20, 0.399),
               (31, 0.199), (-1, 0.0)]
    m2 = PatternModel.template()
    m2.opacityKeys = alberto
    assert background_opacity(m2, 137) == 0.0, background_opacity(m2, 137)
    # ...and it is NOT zero away from the background, or the assertion above proves nothing
    assert background_opacity(m2, 255) > 0.1, background_opacity(m2, 255)

    # the threshold is recoverable from the FGM alone. Albertosaurus' zero plateau is 16..17,
    # centre 16.5, which lands on its real 0x89 background.
    idx, byte = threshold_from_model(m2)
    assert idx == 16.5 and abs(byte - 137) <= 3, (idx, byte)

    # real Dimetrodon opacity keys bottom out at 24, recovering its 0xcd background -- and this
    # must NOT come out as 16, or the helper is just returning the common case
    dime = [(1, 0.5), (10, 0.35), (20, 0.1), (24, 0.0), (25, 0.0), (28, 0.4), (31, 0.6), (-1, 0.0)]
    m3 = PatternModel.template()
    m3.opacityKeys = dime
    idx, byte = threshold_from_model(m3)
    assert idx == 24.5 and abs(byte - 205) <= 5, (idx, byte)

    # --- resolve_threshold works for a background NOBODY on the shipped roster uses, which is
    #     the whole point: nothing is keyed to 0x89 / 0xcd / 0x00. ---
    exotic_map = np.concatenate([np.full(500, 64, np.uint8), np.arange(0, 200, 7, dtype=np.uint8)])
    exotic = PatternModel.template()
    exotic.opacityKeys = [(0, 0.9), (6, 0.4), (7, 0.0), (8, 0.0), (14, 0.5), (31, 1.0),
                          (-1, 0.0), (-1, 0.0)]
    t = resolve_threshold(exotic_map, exotic)
    assert t.byte == 64 and t.source == "both", (t.byte, t.source)
    assert t.agree is True and t.warning is None, t.warning     # 64 -> 7.78, FGM says 7.5

    # each source alone still answers
    assert resolve_threshold(pattern_map=exotic_map).source == "map"
    assert resolve_threshold(model=exotic).source == "fgm"
    try:
        resolve_threshold()
    except ValueError:
        pass
    else:
        raise AssertionError("resolve_threshold accepted no inputs")

    # --- DISAGREEMENT must be reported, not smoothed over. This is the modded-asset case. ---
    bad = resolve_threshold(exotic_map, m3)          # map says 7.78, Dimetrodon curve says 24.5
    assert bad.agree is False and bad.warning, bad
    assert "tint the whole animal" in bad.warning
    assert bad.byte == 64, "the map must win when the two disagree"

    # --- A FLAT map means the species was deliberately blanked to have no pattern (the Deathclaw
    #     mod does this). It must NOT be reported as a mismatch even though the leftover opacity
    #     curve disagrees wildly -- that would flag the modder's own correct work as an error. ---
    #     Blanked correctly: flat map AT the FGM's own transparent slot.
    blanked = np.full(4096, 137, np.uint8)
    t = resolve_threshold(blanked, m2)               # m2 (Albertosaurus) is transparent at 16.65
    assert t.flat is True and t.blank is True, (t.flat, t.blank)
    assert t.warning is None, f"correctly blanked map wrongly warned: {t.warning}"
    assert t.byte == 137, t.byte

    # --- A FLAT map at a value that is NOT transparent colours the WHOLE animal. That is a
    #     different outcome from blanking and must not be conflated with it. ---
    solid = np.full(4096, 250, np.uint8)             # index 30.4; m2 is opaque up there
    t = resolve_threshold(solid, m2)
    assert t.flat is True and t.blank is False, "flat-but-opaque misreported as blank"
    assert t.uniform_opacity > BLANK_OPACITY and t.uniform_colour is not None
    assert "uniformly tinted" in (t.warning or ""), t.warning

    # with no model we cannot know which of the two it is, so claim neither
    t = resolve_threshold(solid)
    assert t.flat is True and t.uniform_opacity is None and t.warning is None

    # a NON-flat map with a real disagreement still warns, so the checks above are not vacuous
    assert resolve_threshold(np.append(blanked, np.uint8(200)), m3).warning is not None
    print("selftest ok")


if __name__ == "__main__":
    selftest()
