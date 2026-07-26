"""Task 6: the PyQt5 editor window.

A `VariantEditorWindow` bound to one `VariantModel`. Every control writes straight into the model
and schedules a debounced (~100 ms) `PreviewBridge.push(model)`; with `bridge=None` nothing is
pushed, which is what makes this file testable headless (`QT_QPA_PLATFORM=offscreen`) with no
Blender and no socket.

This module owns NO file I/O and NO socket calls -- `variant_editor.py` (Task 7) connects the File
actions to `fgm_io` and the Build button to `PreviewBridge.build_material`. The window only exposes
them (`act_open`, `act_new`, `act_save`, `act_save_as`, `build_button`, `species_combo`,
`object_name_edit`).

TWO DATA-FIDELITY RULES, both there to stop the editor silently altering a variant you only opened:

  * Slider ranges are defaults, not limits. SPEC's min/max come from a survey of 40 real shipped
    variant FGMs (seed 6-171, paletteScale 0.04-8.35, paletteOffset 0.46-7.34, brightnessPalette
    0.4-5.0), with headroom. If a loaded file still falls outside one, the row WIDENS to fit rather
    than clamping -- open+save must never change a value the user did not touch.
  * `keyColour` is stored as the model's exact floats. The colour button is a display of them; the
    model is only written when the user actually picks a new colour. (QColor is 8-bit, so binding
    the model to it would quantise 0.274 -> 0.2745 just by opening a file.)

Run:  python editor_ui.py   -> selftest ok
"""
import os
import sys

from PyQt5 import QtCore, QtGui, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import theme  # noqa: E402
from variant_model import VariantModel  # noqa: E402

PUSH_DEBOUNCE_MS = 100
POLL_INTERVAL_MS = 1000        # how often to ask Blender whether File > Import loaded a variant
N_LAYERS = 16

# (field, label, min, max) -- int vs float is taken from the template value's type, so the spec
# stays a plain 4-tuple. Ranges: observed range across 40 shipped variant FGMs, plus headroom.
SPEC = [
    ("seed",               "Seed",                0,    255),
    ("complexity",         "Complexity",          0,     16),
    ("keyThreshold",       "Key threshold",     0.0,    4.0),
    ("keyTolerance",       "Key tolerance",     0.0,    1.0),
    ("brightnessBase",     "Brightness base",   0.0,    5.0),
    ("brightnessPalette",  "Brightness palette", 0.0,   6.0),
    ("saturationBase",     "Saturation base",   0.0,    4.0),
    ("saturationPalette",  "Saturation palette", 0.0,   4.0),
    ("hueRotationBase",    "Hue rotation base", -1.0,   1.0),
    ("hueRotationPalette", "Hue rotation palette", -1.0, 1.0),
    ("paletteScale",       "Palette scale",     0.0,   10.0),
    ("paletteOffset",      "Palette offset",   -2.0,   10.0),
    ("paletteStrength",    "Palette strength",  0.0,    1.0),
]


class _Row(QtWidgets.QWidget):
    """One slider + spinbox bound to one model field (or one layer-weight element).

    `field` is the model attribute name, or `layerColourWeights[i]` for a layer weight. Emits
    `valueChanged(field, value)` only on real user interaction -- `set_value` is silent by default
    so refreshing the widgets from a loaded model cannot look like an edit.
    """

    valueChanged = QtCore.pyqtSignal(str, object)

    STEPS = 1000  # slider resolution for float fields

    def __init__(self, field, label, lo, hi, is_int, parent=None):
        super().__init__(parent)
        self.field, self.lo, self.hi, self.is_int = field, lo, hi, is_int
        self._guard = False  # blocks the slider<->spin echo

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        if is_int:
            self.slider.setRange(int(lo), int(hi))
            self.spin = QtWidgets.QSpinBox()
            self.spin.setRange(int(lo), int(hi))
        else:
            self.slider.setRange(0, self.STEPS)
            self.spin = QtWidgets.QDoubleSpinBox()
            self.spin.setRange(lo, hi)
            self.spin.setDecimals(3)
            self.spin.setSingleStep((hi - lo) / 100.0)
        self.spin.setKeyboardTracking(False)
        self.spin.setFixedWidth(90)
        self.slider.setMinimumWidth(70)   # so a narrow window scrolls vertically, not horizontally
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin, 0)

        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)

    # -- range -----------------------------------------------------------
    def _ensure_range(self, v):
        """Widen to fit `v` rather than clamping it (see the module docstring's fidelity rules)."""
        lo, hi = min(self.lo, v), max(self.hi, v)
        if lo == self.lo and hi == self.hi:
            return
        self.lo, self.hi = lo, hi
        if self.is_int:
            self.slider.setRange(int(lo), int(hi))
            self.spin.setRange(int(lo), int(hi))
        else:
            self.spin.setRange(lo, hi)

    # -- conversion ------------------------------------------------------
    def _to_slider(self, v):
        if self.is_int:
            return int(round(v))
        if self.hi == self.lo:
            return 0
        return int(round((v - self.lo) / (self.hi - self.lo) * self.STEPS))

    def _from_slider_value(self, s):
        if self.is_int:
            return int(s)
        return self.lo + (self.hi - self.lo) * (float(s) / self.STEPS)

    # -- public ----------------------------------------------------------
    def value(self):
        return int(self.spin.value()) if self.is_int else float(self.spin.value())

    def set_value(self, v, emit=False):
        v = int(round(v)) if self.is_int else float(v)
        self._ensure_range(v)
        self._guard = True
        self.spin.setValue(v)
        self.slider.setValue(self._to_slider(v))
        self._guard = False
        if emit:
            self.valueChanged.emit(self.field, self.value())

    # -- internal --------------------------------------------------------
    def _from_slider(self, s):
        if self._guard:
            return
        self._guard = True
        self.spin.setValue(self._from_slider_value(s))
        self._guard = False
        self.valueChanged.emit(self.field, self.value())

    def _from_spin(self, v):
        if self._guard:
            return
        self._guard = True
        self.slider.setValue(self._to_slider(v))
        self._guard = False
        self.valueChanged.emit(self.field, self.value())


class _PaletteStrip(QtWidgets.QWidget):
    """Live colour ramp for the current settings, computed locally -- no Blender needed.

    Left edge is height 0, right edge height 1, which is roughly belly-to-back on the model. When
    the seed has no harvested coefficients the ramp is a single flat colour and says so, rather than
    quietly showing something that looks like a real palette.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colours = []
        self._note = ""
        self.setMinimumHeight(46)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_ramp(self, colours, note=""):
        self._colours, self._note = list(colours), note
        self.update()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        rect = self.rect()
        if not self._colours:
            p.fillRect(rect, QtGui.QColor(theme.COLOURS["bg_input"]))
            return
        n = len(self._colours)
        w = rect.width() / float(n)
        for i, (r, g, b) in enumerate(self._colours):
            # +1 width so neighbouring bands never leave a hairline gap when w is fractional
            p.fillRect(QtCore.QRectF(i * w, 0, w + 1.0, rect.height()), QtGui.QColor(r, g, b))
        p.setPen(QtGui.QColor(theme.COLOURS["border"]))
        p.drawRect(rect.adjusted(0, 0, -1, -1))
        if self._note:
            p.setPen(QtGui.QColor(theme.COLOURS["text_main"]))
            p.drawText(rect.adjusted(6, 0, -6, 0),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, self._note)


class VariantEditorWindow(QtWidgets.QMainWindow):
    """The editor window. `bridge` is a `PreviewBridge` or None (None -> never pushes)."""

    #: emitted (fgm_path, object_name) when Blender's File > Import loads a variant and
    #: "Follow Blender imports" is ticked. variant_editor connects it to its open handler.
    blenderImport = QtCore.pyqtSignal(str, str)

    def __init__(self, bridge=None, model=None, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.model = model if model is not None else VariantModel.template()
        self.current_path = None
        self._loading = False   # suppresses model writes while widgets are refreshed
        self._rows = {}

        self.setWindowTitle("JWE3 Variant Editor")
        theme.apply(self)          # SpeciesGenerator palette; cascades to every child widget
        self._build_menu()
        self._build_body()

        self._push_timer = QtCore.QTimer(self)
        self._push_timer.setSingleShot(True)
        self._push_timer.setInterval(PUSH_DEBOUNCE_MS)
        self._push_timer.timeout.connect(self._push_now)

        # Follow Blender's File > Import. Primed with the CURRENT serial so opening the app does not
        # immediately yank in whatever was imported before it started.
        self._import_serial = None
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_blender)
        if bridge is not None:
            state = self._safe_last_import()
            self._import_serial = (state or {}).get("serial")
            self._poll_timer.start()

        self.refresh_widgets()
        self.set_blender_connected(bool(bridge))

    # -- construction ----------------------------------------------------
    def _build_menu(self):
        """File actions are created here but connected by Task 7's main()."""
        menu = self.menuBar().addMenu("&File")
        self.act_open = menu.addAction("&Open .fgm...")
        self.act_open.setShortcut(QtGui.QKeySequence.Open)
        self.act_new = menu.addAction("&New from template...")
        menu.addSeparator()
        self.act_save = menu.addAction("&Save")
        self.act_save.setShortcut(QtGui.QKeySequence.Save)
        self.act_save_as = menu.addAction("Save &As...")
        menu.addSeparator()
        self.act_quit = menu.addAction("&Quit")
        self.act_quit.triggered.connect(self.close)

    def _build_body(self):
        """The body scrolls: 13 grade rows plus 16 expanded layer rows are taller than most
        screens, so the content goes in a QScrollArea and the window itself stays free to shrink."""
        content = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(content)
        outer.addWidget(self._build_preview_box())
        outer.addWidget(self._build_colour_box())
        outer.addWidget(self._build_grade_box())
        outer.addWidget(self._build_layers_box())
        outer.addStretch(1)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)          # content follows the window's width
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.scroll.setWidget(content)
        self.setCentralWidget(self.scroll)

        # A scroll area's own minimum would otherwise grow to fit its content, which is exactly
        # what stops the window shrinking. Set our own floor and a comfortable opening size.
        self.setMinimumSize(420, 300)
        self.resize(700, 820)

        self.statusBar().showMessage("ready")

    def _build_preview_box(self):
        box = QtWidgets.QGroupBox("Preview target")
        form = QtWidgets.QFormLayout(box)

        self.species_combo = QtWidgets.QComboBox()
        self.species_combo.setEditable(True)
        form.addRow("Species", self.species_combo)

        self.object_name_edit = QtWidgets.QLineEdit()
        self.object_name_edit.setPlaceholderText("name of the imported .ms2 mesh object in Blender")
        form.addRow("Blender object", self.object_name_edit)

        self.follow_check = QtWidgets.QCheckBox("Follow Blender imports")
        self.follow_check.setChecked(True)
        self.follow_check.setToolTip("When you import a variant .fgm in Blender, load its settings "
                                     "into this window. Untick to keep editing undisturbed.")
        form.addRow("", self.follow_check)

        row = QtWidgets.QHBoxLayout()
        self.build_button = QtWidgets.QPushButton("Build / assign material")
        self.blender_label = QtWidgets.QLabel()
        self.badge_label = QtWidgets.QLabel()
        row.addWidget(self.build_button)
        row.addStretch(1)
        row.addWidget(self.badge_label)
        row.addWidget(self.blender_label)
        holder = QtWidgets.QWidget()
        holder.setLayout(row)
        form.addRow(holder)
        return box

    def _build_colour_box(self):
        box = QtWidgets.QGroupBox("Palette  (one full cycle, on neutral grey)")
        lay = QtWidgets.QVBoxLayout(box)
        self.palette_strip = _PaletteStrip()
        self.palette_strip.setToolTip(
            "Every colour this variant can produce, computed here from the same maths the\n"
            "shader uses -- no Blender needed.\n\n"
            "Swept over one cycle of the palette, NOT over the model: the palette is indexed\n"
            "by the height map and repeats hundreds of times across a dinosaur, so this is the\n"
            "set of colours, not their layout. It assumes a neutral albedo, so it shows what\n"
            "the palette does rather than your dinosaur's finished skin -- Blender stays the\n"
            "authority for that.")
        lay.addWidget(self.palette_strip)
        return box

    def _build_grade_box(self):
        box = QtWidgets.QGroupBox("Grade")
        form = QtWidgets.QFormLayout(box)
        template = VariantModel.template()
        for field, label, lo, hi in SPEC:
            is_int = isinstance(getattr(template, field), int)
            row = _Row(field, label, lo, hi, is_int)
            row.valueChanged.connect(self._on_row_changed)
            self._rows[field] = row
            form.addRow(label, row)

        self.colour_button = QtWidgets.QPushButton()
        self.colour_button.setObjectName("colourSwatch")   # theme.swatch_style targets this
        self.colour_button.setFixedWidth(90)
        self.colour_button.clicked.connect(self._pick_colour)
        form.addRow("Key colour", self.colour_button)
        return box

    def _build_layers_box(self):
        """Collapsible section holding the 16 layerColourWeights sliders."""
        box = QtWidgets.QGroupBox("Layer colour weights")
        box.setCheckable(True)
        box.setChecked(False)
        outer = QtWidgets.QVBoxLayout(box)

        self._layers_body = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self._layers_body)
        form.setContentsMargins(0, 0, 0, 0)
        for i in range(N_LAYERS):
            field = "layerColourWeights[%d]" % i
            row = _Row(field, "Layer %d" % (i + 1), 0.0, 1.0, False)
            row.valueChanged.connect(self._on_row_changed)
            self._rows[field] = row
            form.addRow("Layer %d" % (i + 1), row)
        outer.addWidget(self._layers_body)

        self._layers_body.setVisible(False)
        box.toggled.connect(self._layers_body.setVisible)
        return box

    # -- model <-> widgets ------------------------------------------------
    def refresh_widgets(self):
        """Push every model value into its widget without writing back to the model."""
        self._loading = True
        try:
            for field, _label, _lo, _hi in SPEC:
                self._rows[field].set_value(getattr(self.model, field))
            for i in range(N_LAYERS):
                self._rows["layerColourWeights[%d]" % i].set_value(self.model.layerColourWeights[i])
            self._refresh_colour_button()
        finally:
            self._loading = False
        self._refresh_badge()
        self._refresh_ramp()

    def load_model(self, model, path=None):
        """Replace the edited model (used by File > Open / New) and refresh the whole window."""
        self.model = model
        self.current_path = path
        self.setWindowTitle("JWE3 Variant Editor" + (" - %s" % os.path.basename(path) if path else ""))
        self.refresh_widgets()
        self._schedule_push()

    def set_field(self, name, value):
        """Set one field on both the model and its widget.

        Accepts a SPEC field name, `keyColour` (a 3-list), `layerColourWeights` (a 16-list), or a
        single weight as `layerColourWeights[i]`.
        """
        if name == "keyColour":
            self.model.keyColour = [float(c) for c in value]
            self._refresh_colour_button()
        elif name == "layerColourWeights":
            self.model.layerColourWeights = [float(v) for v in value]
            self._loading = True
            try:
                for i in range(N_LAYERS):
                    self._rows["layerColourWeights[%d]" % i].set_value(self.model.layerColourWeights[i])
            finally:
                self._loading = False
        elif name in self._rows:
            row = self._rows[name]
            self._loading = True
            try:
                row.set_value(value)
            finally:
                self._loading = False
            self._write_field(name, row.value())
        else:
            raise KeyError("unknown field %r" % name)
        self._refresh_badge()
        self._refresh_ramp()
        self._schedule_push()

    def _write_field(self, field, value):
        """Write one widget value into the model (handles the layerColourWeights[i] form)."""
        if field.startswith("layerColourWeights["):
            i = int(field[field.index("[") + 1:-1])
            self.model.layerColourWeights[i] = float(value)
        else:
            setattr(self.model, field, value)

    def _on_row_changed(self, field, value):
        if self._loading:
            return
        self._write_field(field, value)
        if field in ("seed", "complexity"):
            self._refresh_badge()
        self._refresh_ramp()          # every field feeds the colour, so always
        self._schedule_push()

    # -- key colour --------------------------------------------------------
    def _model_qcolour(self):
        r, g, b = (max(0.0, min(1.0, float(c))) for c in self.model.keyColour[:3])
        return QtGui.QColor.fromRgbF(r, g, b)

    def _refresh_colour_button(self):
        r, g, b = (max(0.0, min(1.0, float(c))) for c in self.model.keyColour[:3])
        self.colour_button.setStyleSheet(theme.swatch_style(r, g, b))
        self.colour_button.setText("%.2f %.2f %.2f" % tuple(self.model.keyColour[:3]))

    def _pick_colour(self):
        c = QtWidgets.QColorDialog.getColor(self._model_qcolour(), self, "Key colour")
        if not c.isValid():
            return
        # Only a real pick writes the model -- otherwise 8-bit QColor would quantise it.
        self.model.keyColour = [c.redF(), c.greenF(), c.blueF()]
        self._refresh_colour_button()
        self._refresh_ramp()
        self._schedule_push()

    # -- badges ------------------------------------------------------------
    def _refresh_badge(self):
        """Gradient exact/approximate badge -- exact only for harvested seeds."""
        exact = self._gradient_exact(self.model.seed, self.model.complexity)
        if exact is None:
            self.badge_label.setText("gradient: unknown")
            self.badge_label.setStyleSheet("color: %s;" % theme.COLOURS["text_muted"])
            self.badge_label.setToolTip("could not load the harvested coefficient table")
        elif exact:
            self.badge_label.setText("gradient: exact")
            self.badge_label.setStyleSheet("color: %s;" % theme.COLOURS["success"])
            self.badge_label.setToolTip("this seed's coefficients are harvested; the preview matches the game")
        else:
            self.badge_label.setText("gradient: approximate")
            self.badge_label.setStyleSheet("color: %s;" % theme.COLOURS["warn"])
            self.badge_label.setToolTip("seed not harvested: the preview grade is exact but the "
                                        "gradient is flat. In-game colour is still correct.")

    def _refresh_ramp(self):
        """Recompute the live colour ramp. Pure maths, no Blender, cheap enough to run per edit."""
        if not hasattr(self, "palette_strip"):
            return
        try:
            from palette_preview import is_flat, ramp
            from preview_bridge import model_to_block
            block = model_to_block(self.model)
            colours = ramp(block, steps=96)
            note = ("no coefficients for seed %d — flat (in game the gradient is still there)"
                    % self.model.seed) if is_flat(block) else ""
        except Exception as e:
            self.palette_strip.set_ramp([], "colour preview unavailable: %s" % e)
            return
        self.palette_strip.set_ramp(colours, note)

    @staticmethod
    def _gradient_exact(seed, complexity):
        """True/False, or None if the coefficient table can't be loaded (bridge import is lazy so
        this module stays importable without export_palette present)."""
        try:
            from preview_bridge import PreviewBridge
            return PreviewBridge.gradient_exact(seed, complexity)
        except Exception:
            return None

    def set_blender_connected(self, connected):
        self.blender_label.setText("Blender: connected" if connected else "Blender: not connected")
        self.blender_label.setStyleSheet(
            "color: %s;" % theme.COLOURS["success" if connected else "error"])
        self.build_button.setEnabled(bool(connected))

    # -- preview push -------------------------------------------------------
    def _safe_last_import(self):
        """bridge.last_import() that never raises and tolerates a bridge without the method."""
        try:
            return self.bridge.last_import() if self.bridge is not None else None
        except Exception:
            return None

    def _poll_blender(self):
        """Notice a File > Import in Blender and republish it as `blenderImport`."""
        if self.bridge is None or not self.follow_check.isChecked():
            return
        state = self._safe_last_import()
        if not state or not state.get("path"):
            return
        if state.get("serial") == self._import_serial:
            return
        self._import_serial = state.get("serial")
        self.blenderImport.emit(state["path"], state.get("object") or "")

    def _schedule_push(self):
        if self.bridge is None:
            return
        self._push_timer.start()

    def _push_now(self):
        if self.bridge is None:
            return
        try:
            ok = self.bridge.push(self.model)
        except Exception as e:            # a dead listener must not kill the editor
            self.statusBar().showMessage("preview push failed: %s" % e)
            self.set_blender_connected(False)
            return
        self.statusBar().showMessage("preview updated" if ok else "preview push rejected (build the material first?)")


def selftest():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])          # noqa: F841

    w = VariantEditorWindow(bridge=None)                        # bridge=None -> no push
    w.set_field("brightnessBase", 1.5)
    assert abs(w.model.brightnessBase - 1.5) < 1e-6, w.model.brightnessBase
    assert abs(w._rows["brightnessBase"].value() - 1.5) < 1e-6, "widget did not follow the model"
    w.load_model(VariantModel.template())
    assert w.model.seed == 0, w.model.seed

    # int fields stay int; a user edit on the widget reaches the model
    w.set_field("seed", 9)
    assert w.model.seed == 9 and isinstance(w.model.seed, int), w.model.seed
    w._rows["complexity"].set_value(10, emit=True)
    assert w.model.complexity == 10, w.model.complexity

    # all 16 layer weights exist, individually and as a list
    assert len([k for k in w._rows if k.startswith("layerColourWeights[")]) == N_LAYERS
    w.set_field("layerColourWeights", [0.5] * N_LAYERS)
    assert w.model.layerColourWeights == [0.5] * N_LAYERS
    w._rows["layerColourWeights[3]"].set_value(0.0, emit=True)
    assert w.model.layerColourWeights[3] == 0.0, w.model.layerColourWeights[3]

    # a value beyond the default range WIDENS the row instead of clamping (open+save must not
    # alter untouched data) -- paletteScale really does reach 8.35 in shipped files
    w.set_field("paletteScale", 12.5)
    assert abs(w.model.paletteScale - 12.5) < 1e-3, w.model.paletteScale
    assert abs(w._rows["paletteScale"].value() - 12.5) < 1e-3

    # keyColour keeps the model's exact floats (no 8-bit QColor quantisation on load)
    w.set_field("keyColour", [0.274, 0.5, 1.0])
    assert w.model.keyColour == [0.274, 0.5, 1.0], w.model.keyColour

    # badge tracks the harvested-seed table
    w.load_model(VariantModel.template())
    w.set_field("seed", 9); w.set_field("complexity", 10)
    assert "exact" in w.badge_label.text(), w.badge_label.text()
    w.set_field("seed", 999)
    assert "approximate" in w.badge_label.text(), w.badge_label.text()

    # the live colour ramp tracks edits, with no Blender anywhere in sight
    w.load_model(VariantModel(seed=9, complexity=10))
    before = list(w.palette_strip._colours)
    assert len(before) == 96 and len(set(before)) > 1, "harvested seed must give a varying ramp"
    w.set_field("brightnessPalette", 3.0)
    assert w.palette_strip._colours != before, "ramp must follow an edit"
    w.set_field("seed", 999)                       # unharvested -> flat, and it says so
    assert len(set(w.palette_strip._colours)) == 1, "unharvested seed should be flat"
    assert "no coefficients" in w.palette_strip._note, w.palette_strip._note
    w.set_field("seed", 9)
    assert w.palette_strip._note == "", "note must clear when coefficients exist"

    # the body scrolls, and the window can shrink well below its content height
    assert isinstance(w.centralWidget(), QtWidgets.QScrollArea)
    assert w.centralWidget().widgetResizable()
    content_h = w.centralWidget().widget().sizeHint().height()
    assert w.minimumHeight() < content_h, (w.minimumHeight(), content_h)
    w.resize(430, 320)
    assert w.width() == 430 and w.height() == 320, (w.width(), w.height())

    # bridge=None never schedules a push; a bridge does, and the debounce collapses a drag
    assert not w._push_timer.isActive(), "bridge=None must not schedule a push"

    class _FakeBridge:
        def __init__(self): self.pushes = []
        def push(self, model): self.pushes.append(model.brightnessBase); return True

    fake = _FakeBridge()
    w2 = VariantEditorWindow(bridge=fake)
    w2.set_field("brightnessBase", 1.1)
    w2.set_field("brightnessBase", 1.2)
    assert w2._push_timer.isActive(), "edits with a bridge must schedule a push"
    assert fake.pushes == [], "push must be debounced, not immediate"
    w2._push_now()
    assert fake.pushes == [1.2], fake.pushes       # one push, carrying the latest value

    # a listener that dies mid-session degrades to a red indicator, it does not raise
    class _DeadBridge:
        def push(self, model): raise OSError("listener gone")
    w3 = VariantEditorWindow(bridge=_DeadBridge())
    w3._push_now()
    assert "not connected" in w3.blender_label.text()

    print("selftest ok")


if __name__ == "__main__":
    selftest()
