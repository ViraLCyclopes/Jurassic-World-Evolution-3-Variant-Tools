"""Qt theme matching the SpeciesGenerator UI.

The SpeciesGenerator front end is HTML/CSS (`SpeciesGenerator/species_gen_ui/app.css`); this is a
straight port of its `:root` custom properties to a Qt stylesheet, so the two tools look like one
suite. COLOURS holds the ported tokens -- read them from here rather than hard-coding hex anywhere
else, so a palette change stays a one-file edit.

The CSS tokens are rgba() over a black page; Qt widgets are opaque, so each one is flattened onto
black to the equivalent solid hex (e.g. bg-card rgba(22,28,38,0.95) -> #151b25).

Run:  python theme.py   -> selftest ok
"""

# -- ported from species_gen_ui/app.css :root ------------------------------
COLOURS = {
    "bg_main":     "#0c1016",   # --bg-main    rgba(12, 16, 22, .97)
    "bg_card":     "#151b25",   # --bg-card    rgba(22, 28, 38, .95)
    "bg_sidebar":  "#080c12",   # --bg-sidebar rgba(8, 12, 18, .98)
    "bg_input":    "#182028",   # --bg-input   rgba(30, 38, 50, .8)
    "bg_hover":    "#283241",   # --bg-hover   rgba(40, 50, 65, .8)
    "text_main":   "#e0e6ed",
    "text_muted":  "#8a96a8",
    "accent":      "#40a0ff",   # --accent-start
    "accent_end":  "#2080ff",
    "accent_hover": "#60c0ff",
    "warn":        "#ffd275",
    "error":       "#ff6060",
    "success":     "#60ff90",
    "border":      "#2c3a48",   # --border-color rgba(60, 80, 100, .4) flattened
    "font":        '"Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif',
}

STYLESHEET = """
QWidget {{
    background-color: {bg_main};
    color: {text_main};
    font-family: {font};
    font-size: 9pt;
}}

QMainWindow, QDialog {{ background-color: {bg_main}; }}

/* --- menu bar: the app.css sidebar tone --- */
QMenuBar {{
    background-color: {bg_sidebar};
    border-bottom: 1px solid {border};
    padding: 2px;
}}
QMenuBar::item {{ background: transparent; padding: 5px 12px; border-radius: 4px; }}
QMenuBar::item:selected {{ background-color: {bg_hover}; color: {accent_hover}; }}
QMenu {{ background-color: {bg_card}; border: 1px solid {border}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {bg_hover}; color: {accent_hover}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}

/* --- cards --- */
QGroupBox {{
    background-color: {bg_card};
    border: 1px solid {border};
    border-radius: 6px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: normal;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: {accent};
    letter-spacing: 1px;
}}
QGroupBox::indicator {{
    width: 13px; height: 13px;
    border: 1px solid {border};
    border-radius: 3px;
    background-color: {bg_input};
}}
QGroupBox::indicator:checked {{
    background-color: {accent};
    border: 1px solid {accent};
}}

QLabel {{ background: transparent; }}

/* --- inputs --- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {bg_input};
    border: 1px solid {border};
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: {accent_end};
    selection-color: #ffffff;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {accent};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {text_muted};
    background-color: {bg_main};
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{
    /* Qt has no built-in caret glyph in a stylesheet; a small accent wedge reads well enough */
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {text_muted};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {bg_card};
    border: 1px solid {border};
    selection-background-color: {bg_hover};
    selection-color: {accent_hover};
    outline: none;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background-color: {bg_hover};
    border: none;
    width: 14px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {accent_end};
}}

/* --- buttons: app.css .btn-primary gradient --- */
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 {accent}, stop:1 {accent_end});
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 {accent_hover}, stop:1 {accent});
}}
QPushButton:pressed {{ background: {accent_end}; }}
QPushButton:disabled {{
    background: {bg_input};
    color: {text_muted};
    border: 1px solid {border};
}}

/* --- sliders --- */
QSlider::groove:horizontal {{
    height: 4px;
    background: {bg_input};
    border: 1px solid {border};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    height: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {accent_end}, stop:1 {accent});
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {accent};
    border: 2px solid {bg_card};
    width: 12px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {accent_hover}; }}

/* --- chrome --- */
QStatusBar {{
    background-color: {bg_sidebar};
    border-top: 1px solid {border};
    color: {text_muted};
}}
QStatusBar::item {{ border: none; }}
QToolTip {{
    background-color: {bg_card};
    color: {text_main};
    border: 1px solid {accent};
    padding: 4px;
}}
QScrollArea {{ border: none; background-color: {bg_main}; }}
QScrollBar:vertical {{ background: {bg_main}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {bg_hover}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {accent_end}; }}
QScrollBar:horizontal {{ background: {bg_main}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {bg_hover}; border-radius: 5px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {accent_end}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def stylesheet():
    """The Qt stylesheet with COLOURS substituted."""
    return STYLESHEET.format(**COLOURS)


def apply(widget):
    """Apply the theme to a QApplication or a single widget (cascades to its children)."""
    widget.setStyleSheet(stylesheet())
    return widget


def swatch_style(r, g, b, object_name="colourSwatch"):
    """Stylesheet for the key-colour swatch button.

    Scoped to the object name so it beats the generic QPushButton gradient rule, and the label
    flips to dark text on light swatches so the numbers stay readable.
    """
    hexc = "#%02x%02x%02x" % (max(0, min(255, int(round(r * 255)))),
                              max(0, min(255, int(round(g * 255)))),
                              max(0, min(255, int(round(b * 255)))))
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    fg = "#101418" if luma > 0.55 else COLOURS["text_main"]
    return ("QPushButton#%s { background: %s; color: %s; border: 1px solid %s; "
            "border-radius: 4px; padding: 5px; }" % (object_name, hexc, fg, COLOURS["border"]))


def selftest():
    import re
    qss = stylesheet()
    # The output still contains real CSS braces, so look specifically for a leftover {token}.
    # (A typo'd key raises KeyError inside format() before we get here; this catches a token that
    # survived because its braces were accidentally doubled.)
    left = re.search(r"\{[a-z_]+\}", qss)
    assert left is None, "unsubstituted placeholder in QSS: %s" % (left and left.group())
    for key in ("bg_main", "accent", "border"):
        assert COLOURS[key] in qss, key
    assert "qlineargradient" in qss

    # the swatch style must be scoped and must flip its text colour on light backgrounds
    light = swatch_style(1.0, 1.0, 1.0)
    dark = swatch_style(0.0, 0.0, 0.0)
    assert "QPushButton#colourSwatch" in light
    assert "#ffffff" in light and "#101418" in light, light      # white swatch -> dark text
    assert "#000000" in dark and COLOURS["text_main"] in dark, dark

    # it must actually apply to a real widget without Qt rejecting it
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])           # noqa: F841
    w = apply(QWidget())
    assert w.styleSheet() == qss
    print("selftest ok")


if __name__ == "__main__":
    selftest()
