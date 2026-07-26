"""
VariantModel: a plain-data class representing an editable JWE3 dinosaur variant FGM.

Fields correspond to the variant FGM attributes, with sensible defaults provided by
template(). Serialization via to_dict/from_dict (JSON-safe) and to_json/from_json.
Lists are copied on serialization to prevent shared mutations.
"""

from dataclasses import dataclass, field, asdict
import json


@dataclass
class VariantModel:
    """
    Represents the editable parameters of a JWE3 dinosaur variant FGM.

    Fields:
    - seed (int): Palette seed (u_globalPaletteSeed)
    - complexity (int): Palette complexity (u_globalPaletteMaximumComplexity)
    - keyColour (list[float]): Key colour [r, g, b] (u_globalKeyColour)
    - keyThreshold (float): Key threshold (u_globalKeyThreshold)
    - keyTolerance (float): Key tolerance (u_globalKeyTolerance)
    - brightnessBase (float): Brightness base (u_globalColourBrightnessBase)
    - brightnessPalette (float): Brightness palette (u_globalColourBrightnessPalette)
    - saturationBase (float): Saturation base (u_globalColourSaturationBase)
    - saturationPalette (float): Saturation palette (u_globalColourSaturationPalette)
    - hueRotationBase (float): Hue rotation base (u_globalColourRotationOffsetBase)
    - hueRotationPalette (float): Hue rotation palette (u_globalColourRotationOffsetPalette)
    - paletteScale (float): Palette scale (u_instancePaletteScale)
    - paletteOffset (float): Palette offset (u_instancePaletteOffset)
    - paletteStrength (float): Palette strength (u_instancePaletteStrength)
    - layerColourWeights (list[float]): Per-layer colour weights (16 values, u_globalColourWeight1..16)
    - layerSaturation (list[float]): Per-layer saturation (16 values, deferred to layer FGMs)
    - layerContrast (list[float]): Per-layer contrast (16 values, deferred to layer FGMs)
    """

    seed: int = 0
    complexity: int = 1
    keyColour: list = field(default_factory=lambda: [1.0, 1.0, 1.0])
    keyThreshold: float = 1.56
    keyTolerance: float = 0.28
    brightnessBase: float = 1.0
    brightnessPalette: float = 1.0
    saturationBase: float = 1.0
    saturationPalette: float = 1.0
    hueRotationBase: float = 0.0
    hueRotationPalette: float = 0.0
    paletteScale: float = 1.0
    paletteOffset: float = 0.0
    paletteStrength: float = 0.265
    layerColourWeights: list = field(default_factory=lambda: [1.0] * 16)
    layerSaturation: list = field(default_factory=lambda: [1.0] * 16)
    layerContrast: list = field(default_factory=lambda: [1.0] * 16)

    @classmethod
    def template(cls) -> 'VariantModel':
        """Return a VariantModel with sensible defaults."""
        return cls(
            seed=0,
            complexity=1,
            keyColour=[1.0, 1.0, 1.0],
            keyThreshold=1.56,
            keyTolerance=0.28,
            brightnessBase=1.0,
            brightnessPalette=1.0,
            saturationBase=1.0,
            saturationPalette=1.0,
            hueRotationBase=0.0,
            hueRotationPalette=0.0,
            paletteScale=1.0,
            paletteOffset=0.0,
            paletteStrength=0.265,
            layerColourWeights=[1.0] * 16,
            layerSaturation=[1.0] * 16,
            layerContrast=[1.0] * 16,
        )

    def to_dict(self) -> dict:
        """
        Convert to a JSON-safe dict, copying lists to prevent shared mutations.
        """
        d = asdict(self)
        # Ensure lists are copies, not shared references
        d['keyColour'] = list(self.keyColour)
        d['layerColourWeights'] = list(self.layerColourWeights)
        d['layerSaturation'] = list(self.layerSaturation)
        d['layerContrast'] = list(self.layerContrast)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'VariantModel':
        """Reconstruct from a dict, copying lists to prevent shared mutations."""
        return cls(
            seed=d['seed'],
            complexity=d['complexity'],
            keyColour=list(d['keyColour']),
            keyThreshold=d['keyThreshold'],
            keyTolerance=d['keyTolerance'],
            brightnessBase=d['brightnessBase'],
            brightnessPalette=d['brightnessPalette'],
            saturationBase=d['saturationBase'],
            saturationPalette=d['saturationPalette'],
            hueRotationBase=d['hueRotationBase'],
            hueRotationPalette=d['hueRotationPalette'],
            paletteScale=d['paletteScale'],
            paletteOffset=d['paletteOffset'],
            paletteStrength=d['paletteStrength'],
            layerColourWeights=list(d['layerColourWeights']),
            layerSaturation=list(d['layerSaturation']),
            layerContrast=list(d['layerContrast']),
        )

    def to_json(self, path: str) -> None:
        """Write model to a JSON file."""
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def from_json(cls, path: str) -> 'VariantModel':
        """Load model from a JSON file."""
        with open(path, 'r') as f:
            d = json.load(f)
        return cls.from_dict(d)


def selftest():
    m = VariantModel.template()
    assert m.seed == 0 and m.complexity == 1
    assert len(m.layerColourWeights) == 16 and all(w == 1.0 for w in m.layerColourWeights)
    d = m.to_dict(); m2 = VariantModel.from_dict(d)
    assert m2.to_dict() == d, "dict round-trip drifted"
    import tempfile, os
    p = os.path.join(tempfile.gettempdir(), "vm_test.json")
    m.to_json(p); m3 = VariantModel.from_json(p)
    assert m3.to_dict() == d, "json round-trip drifted"
    m.seed = 42; assert m.to_dict()["seed"] == 42
    print("selftest ok")


if __name__ == "__main__":
    selftest()
