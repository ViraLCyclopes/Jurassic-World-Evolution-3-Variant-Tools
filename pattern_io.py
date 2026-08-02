"""Load and save a JWE3 dinosaur pattern FGM (`DinosaurLayered_Pattern`, 67 attributes).

Loose extracted XML `.fgm` files only -- getting them out of an OVL and back in stays a
cobra-tools step, exactly as with `fgm_io.py` for variants.

Values are read and written as RAW floats. Do not requantise: some shipped values are exactly
byte-quantised (0.6235294 == 159/255) and some are not (0.6061094 * 255 == 154.56), so any 8-bit
round trip rewrites keys the user never touched.
"""
import logging
import os
import sys

logging.disable(logging.WARNING)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jwe3_config as cfg
from pattern_model import PatternModel, N_COLOUR_KEYS, N_EMISSIVE_KEYS, N_OPACITY_KEYS

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


def selftest():
    import shutil, tempfile
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
