"""PatternModel: a plain-data class for an editable JWE3 dinosaur pattern FGM.

A pattern is a 32-entry gradient LUT defined by sparse keys. Positions run 0..31; -1 means the key
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
