"""The Pattern tab: edit a JWE3 dinosaur pattern FGM's 32-entry LUT, with a live preview.

WHY IT LOOKS LIKE THE DATA AND NOT LIKE A GRADIENT WIDGET
---------------------------------------------------------
A pattern is NOT a free-form ramp. It is a fixed set of slots -- 12 colour, 12 emissive, 8 opacity
-- each holding a position on the 0..31 axis, where -1 means "this slot is unused". A draggable
ramp would have to invent a stops abstraction and then simplify back into slots on every save,
which is exactly where `pattern_writeback` had to grow redundant-knot removal. Editing the slots
directly means what you see is what the FGM stores, and saving is not lossy.

THE FLOATS ARE RAW AND MUST STAY RAW
------------------------------------
Some shipped values are byte-quantised (0.6235294 == 159/255) and some are not
(0.6061094 * 255 == 154.56). A colour picker is 8-bit, so round-tripping every key through one
would silently rewrite keys nobody touched. This widget therefore tracks which slots were actually
EDITED and hands `edited_slots()` to the save path, which rewrites those and copies the rest
verbatim. `selftest` pins that: load, change one key, save, and every other key must be identical.
"""
import os

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

import pattern_io
import pattern_lut
from pattern_model import (LUT_SIZE, N_COLOUR_KEYS, N_EMISSIVE_KEYS, N_OPACITY_KEYS, UNUSED,
                           PatternModel)

#: channel name -> (slot count, is_rgb)
CHANNELS = (("colour", N_COLOUR_KEYS, True),
            ("emissive", N_EMISSIVE_KEYS, True),
            ("opacity", N_OPACITY_KEYS, False))


def _to_srgb8(v):
    """Linear float -> 0..255 for DISPLAY ONLY. Never feed this back into the model."""
    v = max(0.0, min(1.0, float(v)))
    s = v * 12.92 if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055
    return int(round(max(0.0, min(1.0, s)) * 255))


class GradientStrip(QtWidgets.QWidget):
    """The baked 32-entry LUT: colour on top, opacity as an alpha band over a checkerboard.

    Painted from `pattern_lut.bake`, i.e. from the same code the compositor samples, so the strip
    cannot disagree with the preview about what the pattern is.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lut = None
        self.setMinimumHeight(64)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_model(self, model):
        try:
            self.lut = pattern_lut.bake(model)
        except Exception:
            self.lut = None
        self.update()

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        if self.lut is None or w <= 0:
            p.fillRect(0, 0, w, h, QtGui.QColor(40, 44, 52))
            p.setPen(QtGui.QColor(140, 140, 140))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "no pattern loaded")
            return
        top = h // 2
        colour, opacity = self.lut["colour"], self.lut["opacity"]
        for x in range(w):
            i = min(LUT_SIZE - 1, int(x * LUT_SIZE / w))
            c = colour[i]
            p.fillRect(x, 0, 1, top,
                       QtGui.QColor(_to_srgb8(c[0]), _to_srgb8(c[1]), _to_srgb8(c[2])))
            # checkerboard behind, so 0 opacity is visibly "nothing" rather than black
            for y in range(top, h):
                base = 90 if ((x // 8) + (y // 8)) % 2 else 130
                a = float(np.clip(opacity[i][0], 0.0, 1.0))
                g = int(round(base * (1 - a) + 255 * a))
                p.fillRect(x, y, 1, 1, QtGui.QColor(g, g, g))
        p.setPen(QtGui.QColor(20, 20, 20))
        p.drawRect(0, 0, w - 1, h - 1)
        p.drawLine(0, top, w, top)


class _SlotRow(QtWidgets.QWidget):
    """One slot: a position spinner (-1 = unused) and either a colour swatch or a float."""

    changed = QtCore.pyqtSignal()

    def __init__(self, channel, index, is_rgb, parent=None):
        super().__init__(parent)
        self.channel, self.index, self.is_rgb = channel, index, is_rgb
        self._value = [0.0, 0.0, 0.0] if is_rgb else 0.0
        self._loading = False

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QtWidgets.QLabel("%02d" % index))

        self.pos = QtWidgets.QSpinBox()
        self.pos.setRange(UNUSED, LUT_SIZE - 1)
        self.pos.setSpecialValueText("--")          # UNUSED reads as "--", not as "-1"
        self.pos.setFixedWidth(56)
        self.pos.valueChanged.connect(self._touched)
        lay.addWidget(self.pos)

        if is_rgb:
            self.swatch = QtWidgets.QPushButton("")
            self.swatch.setFixedWidth(64)
            self.swatch.clicked.connect(self._pick)
            lay.addWidget(self.swatch)
            self.text = QtWidgets.QLineEdit()
            self.text.setPlaceholderText("r g b (linear)")
            self.text.editingFinished.connect(self._from_text)
            lay.addWidget(self.text, 1)
        else:
            self.spin = QtWidgets.QDoubleSpinBox()
            self.spin.setRange(0.0, 1.0)
            self.spin.setDecimals(6)
            self.spin.setSingleStep(0.01)
            self.spin.valueChanged.connect(self._touched)
            lay.addWidget(self.spin, 1)

    # -- state ------------------------------------------------------------
    def load(self, position, value):
        self._loading = True                 # loading must NOT mark the slot edited
        self.pos.setValue(int(position))
        if self.is_rgb:
            self._value = [float(c) for c in value]
            self._refresh_rgb()
        else:
            self._value = float(value)
            self.spin.setValue(self._value)
        self._loading = False

    def value(self):
        return (int(self.pos.value()),
                list(self._value) if self.is_rgb else float(self.spin.value()))

    def _refresh_rgb(self):
        r, g, b = (_to_srgb8(c) for c in self._value)
        self.swatch.setStyleSheet(
            "background-color: rgb(%d,%d,%d); border: 1px solid #222;" % (r, g, b))
        self.text.setText(" ".join("%.6g" % c for c in self._value))

    # -- edits ------------------------------------------------------------
    def _touched(self, *_a):
        if not self._loading:
            self.changed.emit()

    def _from_text(self):
        parts = self.text.text().replace(",", " ").split()
        try:
            vals = [float(x) for x in parts]
        except ValueError:
            self._refresh_rgb()
            return
        if len(vals) != 3:
            self._refresh_rgb()
            return
        self._value = vals               # RAW, straight from the field: no 8-bit round trip
        self._refresh_rgb()
        self._touched()

    def _pick(self):
        r, g, b = (_to_srgb8(c) for c in self._value)
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(r, g, b), self, "Key colour")
        if not c.isValid():
            return
        # sRGB picker -> linear storage. Only THIS slot is requantised, which is why edited-slot
        # tracking matters: every untouched slot keeps its original raw float.
        def lin(u):
            u = u / 255.0
            return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4
        self._value = [lin(c.red()), lin(c.green()), lin(c.blue())]
        self._refresh_rgb()
        self._touched()


class PatternTab(QtWidgets.QWidget):
    """Load / edit / save a pattern FGM. Emits `changed` whenever the model changes."""

    changed = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = PatternModel.template()
        self.path = None
        self.index_map = None            # raw greyscale bytes of u_basePatternMap, or None
        self._original = None            # the model as loaded, for verbatim copy-back
        self._edited = set()             # (channel, slot) actually touched by the user
        self._loading = False
        self._rows = {}
        self._build()
        self._push()

    # -- construction -----------------------------------------------------
    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)

        bar = QtWidgets.QHBoxLayout()
        for label, slot in (("Open .fgm...", self.open_dialog),
                            ("Save", self.save), ("Save As...", self.save_as),
                            ("Clear", self.clear)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        self.path_label = QtWidgets.QLabel("(no pattern loaded)")
        bar.addWidget(self.path_label)
        outer.addLayout(bar)

        # The index map is what turns one LUT entry into a pattern. Without it the whole mesh reads
        # a single entry -- a flat tint that looks like a broken pattern rather than a missing map,
        # so the label says so explicitly.
        imap = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("Index map...")
        b.clicked.connect(self.index_map_dialog)
        imap.addWidget(b)
        self.index_label = QtWidgets.QLabel("(none - overlay disabled)")
        imap.addWidget(self.index_label)
        imap.addStretch(1)
        outer.addLayout(imap)

        self.strip = GradientStrip()
        outer.addWidget(self.strip)

        flags = QtWidgets.QHBoxLayout()
        self.use_lut = QtWidgets.QCheckBox("usePatternLUT")
        self.use_patchwork = QtWidgets.QCheckBox("usePatchwork")
        self.patchwork_flags = QtWidgets.QSpinBox()
        self.patchwork_flags.setRange(0, 255)
        for w in (self.use_lut, self.use_patchwork):
            w.toggled.connect(self._on_edit)
            flags.addWidget(w)
        flags.addWidget(QtWidgets.QLabel("patchworkFlags"))
        self.patchwork_flags.valueChanged.connect(self._on_edit)
        flags.addWidget(self.patchwork_flags)
        flags.addStretch(1)
        outer.addLayout(flags)

        tabs = QtWidgets.QTabWidget()
        for name, count, is_rgb in CHANNELS:
            page = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(page)
            for i in range(count):
                row = _SlotRow(name, i, is_rgb)
                row.changed.connect(lambda n=name, i=i: self._on_slot_edit(n, i))
                self._rows[(name, i)] = row
                v.addWidget(row)
            v.addStretch(1)
            tabs.addTab(page, "%s (%d)" % (name, count))
        outer.addWidget(tabs, 1)

        self.note = QtWidgets.QLabel("")
        outer.addWidget(self.note)

    # -- model <-> widgets -------------------------------------------------
    def _keys(self, channel, model=None):
        m = model or self.model
        return {"colour": m.colourKeys, "emissive": m.emissiveKeys, "opacity": m.opacityKeys}[channel]

    def _push(self):
        """Model -> widgets. Never marks anything edited."""
        self._loading = True
        for name, count, _rgb in CHANNELS:
            keys = self._keys(name)
            for i in range(count):
                pos, val = keys[i] if i < len(keys) else (UNUSED, 0.0)
                self._rows[(name, i)].load(pos, val)
        self.use_lut.setChecked(bool(self.model.usePatternLUT))
        self.use_patchwork.setChecked(bool(self.model.usePatchwork))
        self.patchwork_flags.setValue(int(self.model.patchworkFlags))
        self._loading = False
        self.strip.set_model(self.model)

    def _pull(self):
        """Widgets -> model."""
        for name, count, _rgb in CHANNELS:
            keys = self._keys(name)
            for i in range(count):
                keys[i] = self._rows[(name, i)].value()
        self.model.usePatternLUT = self.use_lut.isChecked()
        self.model.usePatchwork = self.use_patchwork.isChecked()
        self.model.patchworkFlags = int(self.patchwork_flags.value())

    def _on_slot_edit(self, channel, index):
        if self._loading:
            return
        self._edited.add((channel, index))
        self._on_edit()

    def _on_edit(self, *_a):
        if self._loading:
            return
        self._pull()
        self.strip.set_model(self.model)
        self.note.setText("%d slot(s) edited" % len(self._edited) if self._edited else "")
        self.changed.emit()

    def edited_slots(self):
        return set(self._edited)

    # -- file --------------------------------------------------------------
    def open_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open pattern FGM", "", "FGM (*.fgm);;All files (*)")
        if path:
            self.load(path)

    def load(self, path):
        try:
            model = pattern_io.load_pattern_fgm(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Open pattern",
                                          "%s: %s" % (type(e).__name__, e))
            return False
        self.model = model
        self._original = PatternModel.from_dict(model.to_dict())   # deep copy, for verbatim keys
        self._edited.clear()
        self.path = path
        self.path_label.setText(os.path.basename(path))
        self._push()
        self.changed.emit()
        return True

    def save(self):
        if not self.path:
            return self.save_as()
        return self._save_to(self.path)

    def save_as(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save pattern FGM", self.path or "", "FGM (*.fgm);;All files (*)")
        if not path:
            return False
        return self._save_to(path)

    def _save_to(self, path):
        self._pull()
        out = self.restored_model()
        try:
            pattern_io.save_pattern_fgm(out, path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save pattern", "%s: %s" % (type(e).__name__, e))
            return False
        self.path = path
        self.path_label.setText(os.path.basename(path))
        self.note.setText("saved %s" % os.path.basename(path))
        return True

    def restored_model(self):
        """The edited model, with every UNTOUCHED slot restored to its original raw float.

        The widgets round-trip a colour through an 8-bit swatch and a "%.6g" text field, so a slot
        that was merely DISPLAYED can come back subtly different from what the FGM held. Only slots
        the user actually edited are allowed to change.
        """
        if self._original is None:
            return self.model
        out = PatternModel.from_dict(self.model.to_dict())
        for name, count, _rgb in CHANNELS:
            cur, orig = self._keys(name, out), self._keys(name, self._original)
            for i in range(count):
                if (name, i) not in self._edited and i < len(orig):
                    cur[i] = orig[i]
        return out

    def clear(self):
        self.model = PatternModel.template()
        self._original = None
        self._edited.clear()
        self.path = None
        self.path_label.setText("(no pattern loaded)")
        self._push()
        self.changed.emit()

    # -- index map ---------------------------------------------------------
    def index_map_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open pattern index map (u_basePatternMap)", "",
            "Images (*.png *.tga *.jpg);;All files (*)")
        if path:
            self.set_index_map(path)

    def set_index_map(self, path):
        """Load the species' `u_basePatternMap` as RAW BYTES.

        Read as bytes, NOT through texture_preview.load_texture: that decodes sRGB to linear, and
        this is DATA -- an index, not a colour. Gamma-decoding it would slide every texel to a
        different LUT entry, which renders as a plausible-looking but wrong pattern.
        """
        img = QtGui.QImage(path)
        if img.isNull():
            self.note.setText("index map: could not read %s" % os.path.basename(path))
            return False
        img = img.convertToFormat(QtGui.QImage.Format_Grayscale8)
        w, h = img.width(), img.height()
        ptr = img.constBits()
        ptr.setsize(img.byteCount())
        # rows are padded to bytesPerLine; slicing to w drops the padding
        arr = np.frombuffer(ptr, np.uint8).reshape(h, img.bytesPerLine())[:, :w].copy()
        self.index_map = arr
        self.index_label.setText(os.path.basename(path))
        self.changed.emit()
        return True

    # -- preview -----------------------------------------------------------
    def is_active(self):
        """True when there is something worth compositing."""
        return bool(self.model and any(p >= 0 for p, _ in self.model.opacityKeys))

    def composite_onto(self, linear_rgb):
        """Overlay the pattern on a linear albedo array. Returns it unchanged if not applicable."""
        if linear_rgb is None or not self.is_active() or self.index_map is None:
            return linear_rgb
        h, w = linear_rgb.shape[:2]
        src = self.index_map
        # NEAREST neighbour, deliberately. The map holds INDICES; interpolating between two texels
        # invents a LUT entry that lies between two unrelated keys, which smears pattern edges into
        # colours the pattern does not contain.
        yi = np.clip(np.arange(h) * src.shape[0] // max(h, 1), 0, src.shape[0] - 1)
        xi = np.clip(np.arange(w) * src.shape[1] // max(w, 1), 0, src.shape[1] - 1)
        idx = src[yi][:, xi]
        try:
            return pattern_lut.composite(linear_rgb, idx, self.model)
        except Exception as e:
            self.note.setText("overlay unavailable: %s: %s" % (type(e).__name__, e))
            return linear_rgb


def selftest():
    """Headless: the edited-slot contract, and that a blank pattern is a no-op."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])   # noqa: F841

    tab = PatternTab()
    m = PatternModel.template()
    # a value that is NOT byte-quantised: 0.6061094 * 255 == 154.56
    m.colourKeys[0] = (3, [0.6061094, 0.25, 0.125])
    m.colourKeys[1] = (9, [0.5, 0.5, 0.5])
    m.opacityKeys[0] = (0, 0.0)
    m.opacityKeys[1] = (31, 1.0)
    tab.model = m
    tab._original = PatternModel.from_dict(m.to_dict())
    tab._edited.clear()
    tab._push()

    # Merely displaying must not change anything, even though the swatch is 8-bit.
    tab._pull()
    assert tab.restored_model().to_dict() == m.to_dict(), "display round-trip mutated untouched keys"

    # Editing ONE slot must change that slot and nothing else.
    tab._rows[("colour", 1)]._value = [0.1, 0.2, 0.3]
    tab._on_slot_edit("colour", 1)
    out = tab.restored_model()
    assert out.colourKeys[1][1] == [0.1, 0.2, 0.3], out.colourKeys[1]
    assert out.colourKeys[0][1] == [0.6061094, 0.25, 0.125], \
        "an untouched raw float was rewritten: %r" % (out.colourKeys[0],)
    assert out.opacityKeys[1] == m.opacityKeys[1], out.opacityKeys[1]

    # is_active is what gates the overlay; a template has no opacity keys and must be inert.
    assert PatternTab().is_active() is False
    assert tab.is_active() is True

    # The strip bakes through the same code the compositor samples.
    tab.strip.set_model(m)
    assert tab.strip.lut is not None and tab.strip.lut["colour"].shape == (LUT_SIZE, 3)
    print("selftest ok")


if __name__ == "__main__":
    selftest()
