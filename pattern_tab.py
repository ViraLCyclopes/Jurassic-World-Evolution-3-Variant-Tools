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

import patchwork
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
        self.pos.setToolTip(
            "WHERE this key sits along the pattern ramp, not how strong it is.\n\n"
            "The pattern is a %d-entry lookup table. The index map\n"
            "(u_basePatternMap) is a GREYSCALE texture: its value at each texel\n"
            "picks an entry, 0 at black through %d at white. This number is the\n"
            "entry this key occupies.\n\n"
            "Keys are SPARSE -- only the slots you set exist, and the table is\n"
            "interpolated between them. So two keys at 18 and 22 put a gradient\n"
            "across entries 18-22, and everything below the lowest and above the\n"
            "highest key is flat.\n\n"
            "'--' (-1) means the slot is UNUSED and contributes nothing. That is\n"
            "how the game marks a spare slot -- it is not position zero.\n\n"
            "Slots are a fixed set, so the order in this list means nothing; only\n"
            "the positions do. Two slots may share a position."
            % (LUT_SIZE, LUT_SIZE - 1))
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
    #: "From selected" clicked. The TAB does not own the Blender bridge -- the window does -- so it
    #: asks rather than reaching for it, and stays constructible with no bridge at all, which is
    #: what keeps the headless selftest possible.
    from_selected_requested = QtCore.pyqtSignal()
    #: "Apply to Blender" clicked. Applies onto the material ALREADY on the mesh -- it does not
    #: rebuild it. `blender_pattern_nodes.apply_pattern` unsplices any previous pattern and splices
    #: the new one into the existing chain, so a variant that is already built keeps its layer
    #: stack and grade untouched. Same bridge-free reasoning as `from_selected_requested`: the tab
    #: asks, the window (which owns the bridge) acts.
    apply_requested = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = PatternModel.template()
        self.path = None
        self.index_map = None            # raw greyscale bytes of u_basePatternMap, or None
        self.patchwork_map = None        # raw greyscale bytes of u_basePatchworkMap, or None
        self.patchwork_map_path = None   # ...and where it came from, for the Blender side
        self.index_map_path = None       # ...and where it came from, for the Blender side
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
                            ("From selected", self.from_selected_requested.emit),
                            ("Apply to Blender", self.apply_requested.emit),
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

        # The patchwork map splits the body into zones; patchworkFlags picks which of them the
        # pattern paints on. 100 patternsets across 63 species ship one; "(none)" is still normal.
        pw_row = QtWidgets.QHBoxLayout()
        self.patchwork_button = QtWidgets.QPushButton("Patchwork map...")
        self.patchwork_button.setToolTip(
            "The species' u_basePatchworkMap: which body zone each texel belongs to.\n\n"
            "100 patternsets across 63 species ship one, but many do not. Without\n"
            "one there is no zoning and the pattern paints everywhere, which is\n"
            "correct, not a fault.")
        self.patchwork_button.clicked.connect(self.patchwork_map_dialog)
        pw_row.addWidget(self.patchwork_button)
        self.patchwork_import_button = QtWidgets.QPushButton("Import painted...")
        self.patchwork_import_button.setToolTip(
            "Turn a painted zone map from any tool into a game-ready patchwork map.\n\n"
            "Export it WITHOUT colour management -- a map tagged sRGB is decoded on\n"
            "load and every value shifts zone.")
        self.patchwork_import_button.clicked.connect(self.import_patchwork_dialog)
        pw_row.addWidget(self.patchwork_import_button)
        self.patchwork_label = QtWidgets.QLabel("(none)")
        pw_row.addWidget(self.patchwork_label, 1)
        outer.addLayout(pw_row)

        self.strip = GradientStrip()
        outer.addWidget(self.strip)

        flags = QtWidgets.QHBoxLayout()
        self.use_lut = QtWidgets.QCheckBox("usePatternLUT")
        self.use_lut.setToolTip(
            "Use the key table above to colour the pattern.\n\n"
            "Off, the LUT is bypassed and the keys do nothing -- the usual reason\n"
            "an edited pattern shows no change at all.")
        self.use_patchwork = QtWidgets.QCheckBox("usePatchwork")
        self.use_patchwork.setToolTip(
            "MASTER ENABLE for patchwork -- verified in game 2026-08-08.\n\n"
            "On, the patchwork map splits the body into zones and patchworkFlags\n"
            "picks which zones the pattern paints on. Off, zoning is ignored\n"
            "entirely and the pattern paints everywhere.\n\n"
            "Every pattern the game ships sets this to 0, which is the real reason\n"
            "patchwork does nothing in retail.")
        self.patchwork_flags = QtWidgets.QSpinBox()
        self.patchwork_flags.setRange(0, 31)
        self.patchwork_flags.setToolTip(
            "Which body zones the pattern paints on: one bit per zone, 0-4.\n"
            "Bit set = that zone shows the pattern; clear = bare skin there.\n\n"
            "31 = 0b11111 = every zone on, which the shader treats as 'no zoning'\n"
            "(it tests flags < 31). Needs usePatchwork on to have any effect.\n\n"
            "Example: 15 = 0b01111 paints every zone except 4.")
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
        self.auto_find_patchwork_map(path)
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
        self.patchwork_map = None
        self.patchwork_map_path = None
        self.patchwork_label.setText("(none)")
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
        self.index_map_path = path        # Blender needs the PATH; the Qt overlay needs the array
        self.index_label.setText(os.path.basename(path))
        self.changed.emit()
        return True

    # -- patchwork map -----------------------------------------------------
    def patchwork_map_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open patchwork map (u_basePatchworkMap)", "",
            "Images (*.png *.tga *.jpg);;All files (*)")
        if path:
            self.set_patchwork_map(path)

    def set_patchwork_map(self, path):
        """Load the species' `u_basePatchworkMap` as RAW BYTES.

        Same rule as `set_index_map`, different symptom: this map holds ZONE IDS, and sRGB-decoding
        it slides texels across zone boundaries -- grey 205 (zone 4) decodes to about 0.66 and
        lands in zone 3, silently gating the wrong half of the animal.

        A shipped map is ALREADY quantised to zones, so it is read straight through here; the
        import path exists only for maps painted in arbitrary colours.
        """
        img = QtGui.QImage(path)
        if img.isNull():
            self.note.setText("patchwork map: could not read %s" % os.path.basename(path))
            return False
        img = img.convertToFormat(QtGui.QImage.Format_Grayscale8)
        w, h = img.width(), img.height()
        ptr = img.constBits()
        ptr.setsize(img.byteCount())
        arr = np.frombuffer(ptr, np.uint8).reshape(h, img.bytesPerLine())[:, :w].copy()
        self.patchwork_map = arr
        self.patchwork_map_path = path
        hist = patchwork.zone_histogram(arr)
        self.patchwork_label.setText("%s  (%s)" % (
            os.path.basename(path),
            ", ".join("zone %d %.0f%%" % (z, f * 100) for z, f in sorted(hist.items()))))
        self.changed.emit()
        return True

    def auto_find_patchwork_map(self, pattern_fgm_path):
        """Find `<patternset>.u_basepatchworkmap.png` beside a loaded pattern FGM.

        Pattern files are `<species>_pattern_<NN>_<NN>.fgm` and the map belongs to the SET,
        `<species>_patternset_<NN>.u_basepatchworkmap.png`, so the stem cannot simply be reused.
        100 patternsets across 63 species ship one; returning False is still a normal case.
        """
        d = os.path.dirname(pattern_fgm_path)
        if not os.path.isdir(d):
            return False
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".u_basepatchworkmap.png"):
                return self.set_patchwork_map(os.path.join(d, f))
        return False

    def import_patchwork_dialog(self):
        """Painted map in, quantised game-ready map out, then load it as the live preview map."""
        from PIL import Image
        import patchwork_import_dialog
        src, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open a painted zone map", "",
            "Images (*.png *.tga *.tif *.tiff *.jpg);;All files (*)")
        if not src:
            return
        dlg = patchwork_import_dialog.PatchworkImportDialog(self)
        if not dlg.load(src):
            self.note.setText("could not read %s" % os.path.basename(src))
            return
        if dlg.exec_() != QtWidgets.QDialog.Accepted or dlg.result_map is None:
            return
        out = self._patchwork_export_target()
        if not out:
            return
        Image.fromarray(dlg.result_map, mode="L").save(out)
        self.set_patchwork_map(out)

    def _patchwork_export_target(self):
        """Path the quantised map should be written to, or None if the user cancels.

        cobra-tools injects a texture via its `<patternset>.u_basepatchworkmap.tex` sidecar and
        picks the .png out of the same folder by name, so this name is a contract rather than a
        preference. Derive it from a `.tex` sitting beside the loaded map or pattern FGM; only
        fall back to a Save dialog when there is nothing to derive it from.
        """
        base = self.patchwork_map_path or self.path      # PatternTab.path = loaded pattern FGM
        if base and os.path.isdir(os.path.dirname(base)):
            d = os.path.dirname(base)
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(".u_basepatchworkmap.tex"):
                    return os.path.join(d, f[:-4] + ".png")
        out, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save quantised patchwork map", "", "PNG (*.png)")
        return out or None

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
        # Patchwork gate. Nearest-neighbour again: the map holds ZONE IDS, and interpolating
        # between two zones invents a third that neither texel belongs to.
        gate = None
        if self.patchwork_map is not None:
            pw = self.patchwork_map
            pyi = np.clip(np.arange(h) * pw.shape[0] // max(h, 1), 0, pw.shape[0] - 1)
            pxi = np.clip(np.arange(w) * pw.shape[1] // max(w, 1), 0, pw.shape[1] - 1)
            gate = patchwork.gate_mask(
                pw[pyi][:, pxi], self.model.patchworkFlags, self.model.usePatchwork)
        try:
            return pattern_lut.composite(linear_rgb, idx, self.model, gate=gate)
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

    # --- patchwork ------------------------------------------------------------
    import patchwork
    t = PatternTab()
    t.model = PatternModel.template()
    # composite_onto no-ops unless is_active(), which needs at least one opacity key with a
    # position >= 0. A blank template has none, so the whole test would silently pass by
    # returning the input unchanged. Give it a real key.
    t.model.opacityKeys = [(16, 1.0)] + [(UNUSED, 0.0)] * (N_OPACITY_KEYS - 1)
    t.model.usePatchwork = True
    t.model.patchworkFlags = 16                 # zone 4 only
    t.index_map = np.full((4, 4), 200, np.uint8)
    t.patchwork_map = np.array([[26, 230], [230, 26]], np.uint8)   # zones 0,4 / 4,0
    alb = np.full((4, 4, 3), 0.25)
    out = t.composite_onto(alb)
    # zone-0 quadrants keep base albedo; zone-4 quadrants are painted
    assert np.allclose(out[0, 0], alb[0, 0]), out[0, 0]
    assert not np.allclose(out[0, 3], alb[0, 3]), out[0, 3]
    # master enable off -> gate ignored entirely, everything paints
    t.model.usePatchwork = False
    out2 = t.composite_onto(alb)
    assert not np.allclose(out2[0, 0], alb[0, 0]), out2[0, 0]
    # no map -> no gating, and no crash
    t.model.usePatchwork = True
    t.patchwork_map = None
    assert t.composite_onto(alb) is not None

    print("selftest ok")


if __name__ == "__main__":
    selftest()
