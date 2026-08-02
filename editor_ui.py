"""Task 6: the PyQt5 editor window.

A `VariantEditorWindow` bound to one `VariantModel`. Every control writes straight into the model
and schedules a debounced (~100 ms) `PreviewBridge.push(model)`; with `bridge=None` nothing is
pushed, which is what makes this file testable headless (`QT_QPA_PLATFORM=offscreen`) with no
Blender and no socket.

This module owns NO file I/O and NO socket calls -- `variant_editor.py` (Task 7) connects the File
actions to `fgm_io` and the Build button to `PreviewBridge.build_material`. The window only exposes
them (`act_open`, `act_new`, `act_save`, `act_save_as`, `build_button`, `species_combo`,
`object_name_edit`, `from_selected_button`, `textures_edit`, `textures_button`, `textures_clear`).

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


# What each control actually does, in terms of the shader. Read off the disassembly of
# `0238_ps_DinosaurFur_Vanilla_BaseLayered_GBuffer`, not inferred from the names -- several names
# are actively misleading (see `keyTolerance`). docs/SLIDERS.md has the long version.
#
# The whole model, for orientation:
#
#     mask     = saturate(1 - saturate(distance(rawDiffuse, keyColour) / keyThreshold))
#     keyBlend = saturate(1 - mask / keyTolerance)      # or without the "1 -" if keyType is clear
#     graded   = lerp(BASE grade, PALETTE grade, keyBlend)
#     out      = lerp(ungraded albedo, graded, colourWeight)
#
# so every texel is graded TWICE, by two independent sets of brightness/saturation/hue, and
# `keyBlend` decides which one it ends up on.
TOOLTIPS = {
    "seed": (
        "Chooses the palette. NOT a colour: it is an index into a table baked inside the game "
        "executable, which maps (seed, complexity) to twelve cosine-gradient coefficients.\n\n"
        "Only seeds that have been HARVESTED from a capture have known coefficients. An "
        "unharvested seed grades FLAT -- which looks identical to 'not graded at all', so check "
        "the gradient badge before concluding a setting does nothing."),
    "complexity": (
        "The other half of the palette lookup: coefficients come from (seed, complexity), not "
        "from the seed alone. The same seed at a different complexity is a different palette.\n\n"
        "Higher values generally mean a busier gradient with more cycles across the body."),
    "keyThreshold": (
        "Divides the distance between each texel's RAW base diffuse and the key colour.\n\n"
        "Bigger = more of the animal counts as 'near' the key colour. Together with key tolerance "
        "this decides which texels take the base grade and which take the palette grade."),
    "keyTolerance": (
        "MISLEADINGLY NAMED. It divides the resulting MASK, not the colour distance -- the shader "
        "uploads it as 1/tolerance and multiplies.\n\n"
        "SMALL values make a HARD split: at 0.10 a texel must sit more than 90% of the way to the "
        "threshold before it starts blending. Large values give a soft gradient between the two "
        "grades."),
    "brightnessBase": (
        "Straight multiplier on the albedo for the BASE grade -- the side that pale texels take "
        "when keyType is set.\n\n"
        "Applied before saturation and the hue matrix. Values above ~2 will clip bright texels to "
        "white once the result leaves [0,1]."),
    "brightnessPalette": (
        "Straight multiplier on the albedo for the PALETTE grade -- the side that also receives "
        "the cosine gradient.\n\n"
        "On many variants this is well below 1 (0.5-0.8), because the gradient supplies the "
        "colour and the multiplier only sets the level."),
    "saturationBase": (
        "Pulls the BASE grade toward or away from its own brightness.\n\n"
        "1.0 leaves it unchanged, 0.0 is fully greyscale, above 1.0 oversaturates. The grey it "
        "moves toward is an RMS luma, sqrt(dot(c, c * Rec709)) -- not the usual linear dot."),
    "saturationPalette": (
        "The same, for the PALETTE grade. Independent of the base value: a variant can be "
        "near-greyscale on one side and vivid on the other, and several shipped ones are."),
    "hueRotationBase": (
        "Rotates the hue of the BASE grade. Expanded into a circulant 3x3 matrix and uploaded as "
        "ten-bit integers over 511.\n\n"
        "Shipped variants keep this tiny -- Pyroraptor's are -0.007 and 0.072, i.e. near-identity. "
        "It is a nudge, not a way to recolour an animal."),
    "hueRotationPalette": "The same, for the PALETTE grade.",
    "paletteScale": (
        "Multiplies the composited HEIGHT before it is fed into the gradient, so it sets how many "
        "cycles of the palette run across the body.\n\n"
        "Larger = tighter banding. Past roughly one cycle the gradient converges toward its own "
        "mean, which is a flat grey -- so more is NOT more colourful."),
    "paletteOffset": (
        "Added to the scaled height, sliding the whole gradient along the body. Changes which part "
        "of the palette lands on the back versus the belly, without changing the palette itself."),
    "paletteStrength": (
        "How strongly the cosine gradient contributes on the PALETTE side.\n\n"
        "It is gated: the effective strength is colourWeight x paletteStrength x keyBlend, so on "
        "texels that take the base grade (keyBlend near 0) the gradient contributes NOTHING no "
        "matter what this is set to."),
    "keyColour": (
        "The reference colour the key mask measures distance FROM, compared against each texel's "
        "RAW base diffuse -- not the composited albedo.\n\n"
        "White on every Pyroraptor variant, which makes the mask effectively 'how dark is this "
        "texel'."),
}


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


class _TextureView(QtWidgets.QWidget):
    """The diffuse before and after grading, side by side.

    Holds the LINEAR source array (`source`) as the thing the grade is computed from, and two
    QImages purely for painting. Keeping the linear array means re-grading never re-decodes the
    PNG, which is what makes dragging the height slider interactive.
    """

    #: emitted on DOUBLE-click of a loaded preview -- the window opens a bigger detached copy.
    #: Double rather than single so it does not fight the hover probe below.
    clicked = QtCore.pyqtSignal()
    #: emitted with (col, row) into the source array as the pointer moves over an image, or
    #: (-1, -1) when it leaves one. Lets the window report what the grade does to that texel.
    probed = QtCore.pyqtSignal(int, int)

    def __init__(self, parent=None, expandable=True):
        super().__init__(parent)
        self.source = None          # linear float (H, W, 3), or None
        self._before = None
        self._after = None
        self._expandable = expandable
        self._image_rects = [None, None]     # where the two images were last painted
        # Zoom/pan only on the DETACHED copy. The inline strip is 190 px tall and shares a scroll
        # area, where a wheel event belongs to the scroll, not to us.
        self._zoomable = not expandable
        self._zoom = 1.0
        self._pan = QtCore.QPoint(0, 0)
        self._drag_from = None
        self.setMouseTracking(True)          # probe on hover, with no button held
        self.setMinimumHeight(190)
        if expandable:
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.setCursor(QtCore.Qt.PointingHandCursor)
        else:
            # the detached copy fills its window instead of sitting at a fixed height
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def mouseDoubleClickEvent(self, event):
        if self._expandable and self._before is not None:
            self.clicked.emit()
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        """Zoom about the pointer, so the texel under the cursor stays under it."""
        if not self._zoomable or self._before is None:
            super().wheelEvent(event)
            return
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        old = self._zoom
        self._zoom = max(1.0, min(24.0, old * (1.15 ** steps)))
        if self._zoom != old:
            # Keep the point under the cursor fixed: pan must scale about that point, not about
            # the widget origin, or zooming walks the image away from what you were looking at.
            c = event.pos()
            k = self._zoom / old
            self._pan = QtCore.QPoint(int(c.x() - k * (c.x() - self._pan.x())),
                                      int(c.y() - k * (c.y() - self._pan.y())))
            if self._zoom == 1.0:
                self._pan = QtCore.QPoint(0, 0)     # snap back to a clean fit
            self.update()
        event.accept()

    def mousePressEvent(self, event):
        if self._zoomable and event.button() == QtCore.Qt.LeftButton and self._zoom > 1.0:
            self._drag_from = event.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_from = None
        if self._zoomable:
            self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_from is not None:
            self._pan += event.pos() - self._drag_from
            self._drag_from = event.pos()
            self.update()
        else:
            self.probed.emit(*self._pixel_at(event.pos()))
        super().mouseMoveEvent(event)

    def reset_view(self):
        self._zoom, self._pan = 1.0, QtCore.QPoint(0, 0)
        self.update()

    def leaveEvent(self, event):
        self.probed.emit(-1, -1)
        super().leaveEvent(event)

    def _pixel_at(self, pos):
        """Widget point -> (col, row) in the SOURCE array, or (-1, -1) if not over an image.

        Uses the rects recorded during the last paint, so it cannot drift from what is drawn --
        recomputing the layout here would be a second implementation of the same arithmetic.
        """
        if self.source is None:
            return -1, -1
        h, w = self.source.shape[:2]
        for rect in self._image_rects:
            if rect is not None and rect.contains(pos) and rect.width() and rect.height():
                col = int((pos.x() - rect.left()) / rect.width() * w)
                row = int((pos.y() - rect.top()) / rect.height() * h)
                return max(0, min(w - 1, col)), max(0, min(h - 1, row))
        return -1, -1

    def set_source(self, linear, before_image):
        self.source, self._before, self._after = linear, before_image, None
        self.update()

    def set_graded(self, after_image):
        self._after = after_image
        self.update()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        rect = self.rect().adjusted(0, 0, -1, -1)
        p.fillRect(rect, QtGui.QColor(28, 28, 30))
        if self._before is None:
            p.setPen(QtGui.QColor(150, 150, 150))
            p.drawText(rect, QtCore.Qt.AlignCenter,
                       'no diffuse loaded  --  "Diffuse..." to pick one')
            return
        half = rect.width() // 2
        self._image_rects = [None, None]
        for i, (img, label) in enumerate(((self._before, "diffuse"), (self._after, "graded"))):
            cell = QtCore.QRect(rect.left() + i * half, rect.top(), half - 4, rect.height() - 16)
            if img is not None:
                fit = img.size().scaled(cell.size(), QtCore.Qt.KeepAspectRatio)
                # Zoom past 1:1 uses FastTransformation so texels stay crisp squares -- smoothing
                # a magnified texture hides exactly the per-pixel detail you zoomed in to see.
                mode = (QtCore.Qt.SmoothTransformation if self._zoom <= 1.0
                        else QtCore.Qt.FastTransformation)
                scaled = img.scaled(fit * self._zoom, QtCore.Qt.KeepAspectRatio, mode)
                ox = cell.left() + (cell.width() - scaled.width()) // 2 + self._pan.x()
                oy = cell.top() + (cell.height() - scaled.height()) // 2 + self._pan.y()
                p.save()
                p.setClipRect(cell)          # zoomed images must not spill into each other
                p.drawImage(ox, oy, scaled)
                p.restore()
                # record for the hover probe -- see _pixel_at
                self._image_rects[i] = QtCore.QRect(ox, oy, scaled.width(), scaled.height())
            p.setPen(QtGui.QColor(160, 160, 160))
            p.drawText(QtCore.QRect(cell.left(), rect.bottom() - 14, cell.width(), 14),
                       QtCore.Qt.AlignHCenter, label)


class _PaletteGraph(QtWidgets.QWidget):
    """The palette as three per-channel curves -- the cosine gradient, graphed.

    JWE3's palette is `a + b*cos(2pi*(c*t + d))` per channel, i.e. exactly the Inigo Quilez cosine
    palette that thi.ng/gradients is built on, with the FGM's gradOffset/Amplitude/Freq/Phase as
    (a, b, c, d). Plotting the ramp's channels therefore graphs the coefficients directly -- there
    is no separate maths here, it reads the SAME sampled ramp the strip does, so the two can never
    disagree about what the palette is.

    Reading it: amplitude is the height of a curve's swing, offset its centre line, frequency how
    many humps fit in the window, and phase where they sit. Channels that swing in opposite
    directions give a hue shift across the body; channels that swing together give light-to-dark.
    """

    PENS = ((255, 96, 96), (96, 220, 96), (110, 150, 255))

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colours = []
        self._marker = None
        self.setMinimumHeight(96)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_ramp(self, colours):
        self._colours = list(colours)
        self.update()

    def set_marker(self, frac):
        """Where the Height slider is sampling, as 0..1 across the displayed cycle (None hides)."""
        self._marker = frac
        self.update()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        p.fillRect(rect, QtGui.QColor(28, 28, 30))

        # grid at 0.0 / 0.5 / 1.0 so amplitude and offset can be read off by eye
        p.setPen(QtGui.QPen(QtGui.QColor(70, 70, 74), 1, QtCore.Qt.DotLine))
        for frac in (0.0, 0.5, 1.0):
            y = rect.top() + (1.0 - frac) * rect.height()
            p.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))

        n = len(self._colours)
        if n < 2:
            p.setPen(QtGui.QColor(150, 150, 150))
            p.drawText(rect, QtCore.Qt.AlignCenter, "no palette")
            return

        for ch, (cr, cg, cb) in enumerate(self.PENS):
            path = QtGui.QPainterPath()
            for i, rgb in enumerate(self._colours):
                x = rect.left() + rect.width() * (i / float(n - 1))
                y = rect.top() + rect.height() * (1.0 - rgb[ch] / 255.0)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QtGui.QPen(QtGui.QColor(cr, cg, cb), 1.6))
            p.drawPath(path)

        if self._marker is not None:
            x = rect.left() + rect.width() * max(0.0, min(1.0, self._marker))
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 190), 1))
            p.drawLine(int(x), rect.top(), int(x), rect.bottom())
            tri = QtGui.QPolygon([QtCore.QPoint(int(x), rect.top() + 7),
                                  QtCore.QPoint(int(x) - 5, rect.top()),
                                  QtCore.QPoint(int(x) + 5, rect.top())])
            p.setBrush(QtGui.QColor(255, 255, 255, 220))
            p.setPen(QtCore.Qt.NoPen)
            p.drawPolygon(tri)


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
        outer.addWidget(self._build_texture_box())
        outer.addWidget(self._build_colour_box())
        outer.addWidget(self._build_grade_box())
        outer.addWidget(self._build_layers_box())
        outer.addStretch(1)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)          # content follows the window's width
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.scroll.setWidget(content)

        # Variant and Pattern are separate cosmetic axes in game and either may be applied alone,
        # so they get separate tabs rather than one long pane. The pattern tab feeds the SAME
        # diffuse preview -- see _refresh_texture -- so its edits show on the graded texture live.
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.scroll, "Variant")
        try:
            from pattern_tab import PatternTab
            self.pattern_tab = PatternTab()
            self.pattern_tab.changed.connect(self._refresh_texture)
            self.tabs.addTab(self.pattern_tab, "Pattern")
        except Exception as e:                       # never let the pattern tab break the editor
            self.pattern_tab = None
            print("pattern tab unavailable: %s: %s" % (type(e).__name__, e))
        self.setCentralWidget(self.tabs)

        # A scroll area's own minimum would otherwise grow to fit its content, which is exactly
        # what stops the window shrinking. Set our own floor and a comfortable opening size.
        self.setMinimumSize(420, 300)
        self.resize(820, 900)   # wider: the diffuse preview is two images side by side

        self.statusBar().showMessage("ready")

    def _build_preview_box(self):
        box = QtWidgets.QGroupBox("Preview target")
        form = QtWidgets.QFormLayout(box)

        self.species_combo = QtWidgets.QComboBox()
        self.species_combo.setEditable(True)
        form.addRow("Species", self.species_combo)

        # Per-species texture folder. Replaces copying extracted texture sets into the install's
        # `Textures/<Species>` folder, which was lost on every reinstall and could not be shared.
        # Read-only field + Browse: the path is chosen with a picker so a typo cannot silently
        # produce "no masks found" with a plausible-looking path in the box.
        self.textures_edit = QtWidgets.QLineEdit()
        self.textures_edit.setReadOnly(True)
        self.textures_edit.setPlaceholderText("(not set - masks come from the imported model's folder)")
        self.textures_button = QtWidgets.QPushButton("Browse...")
        self.textures_clear = QtWidgets.QPushButton("Clear")
        self.textures_clear.setToolTip("Forget this species' texture folder")
        _tex_row = QtWidgets.QWidget()
        _tex_lay = QtWidgets.QHBoxLayout(_tex_row)
        _tex_lay.setContentsMargins(0, 0, 0, 0)
        _tex_lay.addWidget(self.textures_edit, 1)
        _tex_lay.addWidget(self.textures_button)
        _tex_lay.addWidget(self.textures_clear)
        form.addRow("Textures", _tex_row)

        self.object_name_edit = QtWidgets.QLineEdit()
        self.object_name_edit.setPlaceholderText("name of the imported .ms2 mesh object in Blender")
        # "From selected" saves hunting for the .fgm on disk when the model is already in the
        # viewport: the material records where it was graded from, so the editor can just open it.
        self.from_selected_button = QtWidgets.QPushButton("From selected")
        self.from_selected_button.setToolTip(
            "Adopt the mesh selected in Blender, and open the variant .fgm it was graded from")
        _obj_row = QtWidgets.QWidget()
        _obj_lay = QtWidgets.QHBoxLayout(_obj_row)
        _obj_lay.setContentsMargins(0, 0, 0, 0)
        _obj_lay.addWidget(self.object_name_edit, 1)
        _obj_lay.addWidget(self.from_selected_button)
        form.addRow("Blender object", _obj_row)

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

    def _build_texture_box(self):
        box = QtWidgets.QGroupBox("Diffuse  (before / after, at colourWeight 1)")
        lay = QtWidgets.QVBoxLayout(box)

        self.texture_view = _TextureView()
        self.texture_view.clicked.connect(self._open_texture_window)
        self.texture_view.probed.connect(self._probe_pixel)
        self.texture_view.setToolTip(
            "Click to open a larger, resizable copy that follows your edits live.\n\n"
            "The variant's grade applied to a real base diffuse, pixel by pixel.\n\n"
            "EXACT here: the albedo (it is the texture), the key mask (the shader keys off this\n"
            "same raw diffuse), both grades, and the gradient maths.\n\n"
            "ASSUMED: colourWeight = 1, so this is the grade at FULL strength -- the layer stack\n"
            "that would veto it in places is not available here. And the height driving the\n"
            "gradient is the slider, not the model's real composited height. Sweep it.\n\n"
            "Blender stays the authority for a finished skin.")
        lay.addWidget(self.texture_view)

        row = QtWidgets.QHBoxLayout()
        self.texture_open_button = QtWidgets.QPushButton("Diffuse...")
        self.texture_open_button.setToolTip("Pick a *.pbasediffusetexture.png to preview against")
        row.addWidget(self.texture_open_button)
        self.texture_save_button = QtWidgets.QPushButton("Save graded...")
        self.texture_save_button.setToolTip(
            "Write the graded texture out as a PNG.\n\n"
            "Saved at the source's FULL resolution, re-graded from the original file -- not the\n"
            "downscaled copy shown here, which exists only to keep the preview interactive.\n\n"
            "Same caveats as the preview: colourWeight 1, and the gradient at the Height slider.")
        row.addWidget(self.texture_save_button)
        row.addWidget(QtWidgets.QLabel("Height"))
        self.texture_height = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.texture_height.setRange(0, 1000)
        self.texture_height.setValue(500)
        self.texture_height.setToolTip(
            "Which point of the palette the gradient is sampled at.\n\n"
            "The real value is the model's composited layer height, which is not available in the\n"
            "editor -- so this is a sweep, not a prediction. A variant runs many cycles across a\n"
            "dinosaur, so dragging this shows the range the gradient covers.")
        row.addWidget(self.texture_height, 1)
        self.texture_note = QtWidgets.QLabel("")
        self.texture_note.setStyleSheet("color: #999;")
        lay.addLayout(row)
        lay.addWidget(self.texture_note)

        self.texture_height.valueChanged.connect(lambda _v: self._refresh_texture())
        return box

    def _colour_weight_for(self, shape):
        """The layer stack's per-pixel colourWeight for this species, or None if unavailable.

        Without it the preview grades EVERYTHING, including teeth, tongue, mouth flesh and eye --
        which the game never does, because those swatches carry a colouring weight of 0. Needs the
        species' LayerJSON and its blend-weight masks; falls back to a flat 1.0 (and says so) when
        either is missing, rather than failing to preview at all.
        """
        try:
            import json
            import texture_preview as tp
            from preview_assets import layers_json_for, mask_dir_for, detect_mask_prefix
            species = (self.species_combo.currentText() or "").strip()
            if not species:
                return None
            lj = layers_json_for(species)
            md = mask_dir_for(species)
            if not lj or not md:
                return None
            data = json.load(open(lj, encoding="utf-8"))
            layers = data["layers"] if isinstance(data, dict) else data
            prefix = detect_mask_prefix(md)
            if not prefix:
                return None
            return tp.colour_weight_map(layers, md, "%s.playered_blendweights" % prefix,
                                        list(self.model.layerColourWeights), shape)
        except Exception:
            return None

    def _probe_pixel(self, col, row):
        """Report what the grade does to one texel: its colour in and out, and WHICH grade it took.

        `keyBlend` is the interesting number and the one that cannot be seen by eye: it decides
        whether a texel is repainted by the palette or keeps its base grade, and it is computed from
        the raw diffuse rather than from anything visible in the result.
        """
        if col < 0 or self.texture_view.source is None:
            self.texture_note.setText("height %.3f" % (self.texture_height.value() / 1000.0))
            return
        try:
            import texture_preview as tp
            from preview_bridge import model_to_block
            block = model_to_block(self.model)
            src = self.texture_view.source[row:row + 1, col:col + 1, :]
            h = self.texture_height.value() / 1000.0
            cwmap = getattr(self, 'texture_weight', None)
            cw = 1.0 if cwmap is None else cwmap[row:row + 1, col:col + 1, :]
            out = tp.grade_image(src, block, height=h, colour_weight=cw)
            kb = float(tp.key_blend(block, src)[0, 0, 0])
            enc = lambda a: tuple(int(round(v * 255)) for v in tp.linear_to_srgb(a)[0, 0])
            self.texture_note.setText(
                "(%d,%d)  %s -> %s   keyBlend %.2f  (%s grade)   height %.3f"
                % (col, row, enc(src), enc(out), kb,
                   "palette" if kb >= 0.5 else "base", h))
        except Exception as e:
            self.texture_note.setText("probe failed: %s: %s" % (type(e).__name__, e))

    def save_graded_texture(self, out_path):
        """Grade the source at FULL resolution and write it to `out_path`. Returns (w, h).

        Deliberately re-reads the original rather than upscaling the preview: the preview is a
        512 px copy that exists only so dragging a slider stays interactive, and saving that would
        produce a blurry texture that looks like the grade went wrong.
        """
        import texture_preview as tp
        from PIL import Image
        from preview_bridge import model_to_block
        path = getattr(self, "texture_path", None)
        if not path:
            raise RuntimeError("no diffuse loaded")
        full = tp.load_texture(path, max_side=None)
        cw = self._colour_weight_for(full.shape)
        graded = tp.grade_image(full, model_to_block(self.model),
                                height=self.texture_height.value() / 1000.0,
                                colour_weight=1.0 if cw is None else cw)
        # Apply the pattern here TOO. This path grades independently of _refresh_texture, so
        # without this the saved file silently lacks the overlay the screen is showing -- the
        # worst kind of mismatch, because the preview looks like proof the file is right.
        # composite_onto resamples the index map to whatever resolution it is handed, so full-res
        # works the same as the 512 px preview.
        pt = getattr(self, "pattern_tab", None)
        if pt is not None:
            graded = pt.composite_onto(graded)
        buf = (tp.linear_to_srgb(graded) * 255.0 + 0.5).astype("uint8")
        Image.fromarray(buf, mode="RGB").save(out_path)
        return full.shape[1], full.shape[0]

    def _open_texture_window(self):
        """A larger, resizable copy of the diffuse preview that follows edits live.

        Non-modal on purpose: the point is to drag sliders in the main window and watch this. It
        shares nothing but the images -- the grade is still computed once, in `_refresh_texture`,
        and pushed to both views, so the two can never show different results.
        """
        win = getattr(self, "texture_window", None)
        if win is None:
            # A QDialog gets no maximise button on Windows, so it cannot be full-screened. Ask for
            # the full window frame explicitly -- this is a viewer, and being able to fill the
            # screen is most of the point of detaching it.
            win = QtWidgets.QDialog(self, QtCore.Qt.Window
                                    | QtCore.Qt.WindowMinimizeButtonHint
                                    | QtCore.Qt.WindowMaximizeButtonHint
                                    | QtCore.Qt.WindowCloseButtonHint)
            win.setWindowTitle("Diffuse  -  before / after     "
                               "(wheel = zoom, drag = pan, F11 = full screen, Esc = reset)")
            win.setSizeGripEnabled(True)
            lay = QtWidgets.QVBoxLayout(win)
            lay.setContentsMargins(4, 4, 4, 4)
            view = _TextureView(expandable=False)
            lay.addWidget(view)
            win._view = view

            def _keys(event, _w=win, _v=view):
                if event.key() == QtCore.Qt.Key_F11:
                    _w.showNormal() if _w.isFullScreen() else _w.showFullScreen()
                elif event.key() == QtCore.Qt.Key_Escape:
                    # Esc resets the zoom rather than closing: closing on Esc is the QDialog
                    # default and it is the wrong reflex for a viewer you are panning around.
                    if _w.isFullScreen():
                        _w.showNormal()
                    else:
                        _v.reset_view()
                else:
                    QtWidgets.QDialog.keyPressEvent(_w, event)
            win.keyPressEvent = _keys
            win.resize(1100, 620)
            self.texture_window = win
        win._view.set_source(self.texture_view.source, self.texture_view._before)
        win._view.set_graded(self.texture_view._after)
        win.show()
        win.raise_()
        win.activateWindow()

    def _refresh_texture(self):
        """Re-grade the loaded diffuse. Cheap on a downscaled copy; skipped when none is loaded."""
        if not hasattr(self, "texture_view") or self.texture_view.source is None:
            return
        try:
            import texture_preview as tp
            from preview_bridge import model_to_block
            block = model_to_block(self.model)
            h = self.texture_height.value() / 1000.0
            cw = getattr(self, 'texture_weight', None)
            graded = tp.grade_image(self.texture_view.source, block, height=h,
                                    colour_weight=1.0 if cw is None else cw)
            # The pattern composites AFTER the grade -- they are independent cosmetic axes, and
            # blender_parts.splice_at orders the node chain the same way (CHAIN_POS grade 10,
            # pattern 20), so the two previews agree about ordering as well as arithmetic.
            pt = getattr(self, "pattern_tab", None)
            if pt is not None:
                graded = pt.composite_onto(graded)
            after = tp.to_qimage(graded)
            self.texture_view.set_graded(after)
            self.texture_note.setText("height %.3f" % h)
            win = getattr(self, "texture_window", None)
            if win is not None and win.isVisible():
                win._view.set_graded(after)
        except Exception as e:
            self.texture_note.setText("preview unavailable: %s: %s" % (type(e).__name__, e))

    def load_texture(self, path):
        """Load a diffuse PNG into the preview. Returns True on success."""
        import texture_preview as tp
        self.texture_path = path                # remembered so Save can re-grade at FULL res
        linear = tp.load_texture(path)          # decode ONCE; it is the expensive part
        self.texture_weight = self._colour_weight_for(linear.shape)
        before = tp.to_qimage(linear)
        self.texture_view.set_source(linear, before)
        win = getattr(self, "texture_window", None)
        if win is not None:
            win._view.set_source(linear, before)   # keep a detached window on the new texture
        self._refresh_texture()
        return True

    def _build_colour_box(self):
        box = QtWidgets.QGroupBox("Palette  (one full cycle, on neutral grey)")
        lay = QtWidgets.QVBoxLayout(box)
        self.palette_graph = _PaletteGraph()
        self.palette_graph.setToolTip(
            "The same cycle as the strip below, drawn as one curve per channel.\n\n"
            "JWE3's palette is a COSINE GRADIENT, the same form popularised by Inigo Quilez and\n"
            "used by thi.ng/gradients:\n\n"
            "    colour(t) = a + b * cos(2pi * (c*t + d))\n\n"
            "and the FGM stores exactly those four coefficients per channel:\n"
            "    a = gradOffset/511      b = gradAmplitude/511\n"
            "    c = gradFreq            d = gradPhase/511\n\n"
            "So a variant's palette IS a set of IQ coefficients, and this is their graph. A flat\n"
            "line means zero amplitude -- an unharvested seed, not a black palette.")
        lay.addWidget(self.palette_graph)
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
            tip = TOOLTIPS.get(field)
            # On the LABEL as well as the row: the label is the wider hover target, and it is what
            # people point at when they want to know what something means.
            label_widget = QtWidgets.QLabel(label)
            if tip:
                row.setToolTip(tip)
                label_widget.setToolTip(tip)
            form.addRow(label_widget, row)

        self.colour_button = QtWidgets.QPushButton()
        self.colour_button.setObjectName("colourSwatch")   # theme.swatch_style targets this
        self.colour_button.setFixedWidth(90)
        self.colour_button.clicked.connect(self._pick_colour)
        self.colour_button.setToolTip(TOOLTIPS["keyColour"])
        _kc_label = QtWidgets.QLabel("Key colour")
        _kc_label.setToolTip(TOOLTIPS["keyColour"])
        form.addRow(_kc_label, self.colour_button)
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
        self._refresh_texture()

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
        self._refresh_texture()
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
        self._refresh_texture()
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
        self._refresh_texture()
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
            if hasattr(self, "palette_graph"):
                self.palette_graph.set_ramp([])
            return
        self.palette_strip.set_ramp(colours, note)
        if hasattr(self, "palette_graph"):
            # A denser sweep than the strip: the strip only has to read as a smooth band, the graph
            # has to show frequency, and 96 samples visibly aliases on a high-freq palette.
            try:
                from palette_preview import ramp as _ramp
                self.palette_graph.set_ramp(_ramp(block, steps=256))
            except Exception:
                self.palette_graph.set_ramp(colours)
            self.palette_graph.set_marker(self._palette_marker(block))

    def _palette_marker(self, block):
        """Where the Height slider samples, as 0..1 across the cycle shown -- or None.

        DELIBERATELY None on most real variants, and that is the honest answer rather than a
        limitation. The model does not sample a point, it sweeps a window: `t` runs from
        `paletteOffset` at height 0 to `100*paletteScale + paletteOffset` at height 1. Pyroraptor
        v00 has paletteScale 5.0 and a period of 0.0196, so the body spans **500 full cycles** --
        two texels a hair apart in height land on unrelated palette colours, and `% 1` on such a
        span is numerically arbitrary rather than informative.

        So the marker is only drawn when the whole model fits inside a couple of cycles, where a
        single position genuinely means something. Otherwise use the pixel probe: hover the diffuse
        preview to see what the grade does to a specific texel.
        """
        if not hasattr(self, "texture_height"):
            return None
        try:
            from palette_preview import palette_period, ts_for_height
            period = palette_period(block)
            if not period or period <= 0 or period != period:      # 0, negative or NaN
                return None
            span = abs(ts_for_height(block, 1.0) - ts_for_height(block, 0.0))
            if span / period > 2.0:
                return None
            h = self.texture_height.value() / 1000.0
            return ((ts_for_height(block, h) - ts_for_height(block, 0.0)) / period) % 1.0
        except Exception:
            return None

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
