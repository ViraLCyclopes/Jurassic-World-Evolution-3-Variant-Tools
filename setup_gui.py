"""Setup tool: point everything at the right folders, once.

    python setup_gui.py

Writes the single config that every other tool reads (see `jwe3_config.py`) -- the editor, the
harvesting scripts and the Blender add-on all resolve their paths through it, so there is one place
to fix rather than three.

Nothing here is usually required: each row auto-detects, and the window opens showing what it found.
It exists for what detection cannot decide (two game installs on different drives) or cannot know
(where you unpacked the Swatch Library). A row you leave alone keeps auto-detecting, so it keeps
working if you move or reinstall something.

Run:  python setup_gui.py --selftest   -> selftest ok (offscreen, no interaction)
"""
import os
import sys

from PyQt5 import QtCore, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jwe3_config as cfg  # noqa: E402

try:
    import theme
except Exception:                       # theme is cosmetic; never let it stop setup
    theme = None

ROWS = [
    ("game_dir", "Game install",
     "The Jurassic World Evolution 3 folder containing Win64\\ovldata. Only needed by the "
     "harvesting tools, which modify and restore its OVL files."),
    ("cobra_tools", "cobra-tools",
     "Your cobra-tools checkout. Normally detected from the Blender add-on you already have "
     "installed, so you rarely need to set this."),
    ("swatch_dir", "Swatch Library",
     "Where you unpacked SwatchLibrary.ovl's PNGs. Game data, so it is never shipped or "
     "auto-extracted, and it is NOT packaged with this software -- you must set it here. "
     "Unpack SwatchLibrary.ovl with cobra-tools and point this at the folder of PNGs."),
]


class SetupWindow(QtWidgets.QDialog):
    """One row per setting: what is resolved now, where it came from, and a way to change it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("JWE3 Variant Tools - setup")
        self.resize(900, 460)
        if theme is not None:
            theme.apply(self)
        self._edits = {}

        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Every tool reads one config file:  <code>%s</code><br>"
            "Rows left blank keep auto-detecting, which is usually what you want."
            % cfg.config_path())
        intro.setWordWrap(True)
        lay.addWidget(intro)

        form = QtWidgets.QFormLayout()
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        for key, label, help_text in ROWS:
            form.addRow(self._label(label, help_text), self._row(key))
        lay.addLayout(form)

        self.games_box = QtWidgets.QGroupBox("Game installs found")
        gl = QtWidgets.QVBoxLayout(self.games_box)
        self.games_list = QtWidgets.QListWidget()
        self.games_list.itemDoubleClicked.connect(self._use_game)
        gl.addWidget(self.games_list)
        self.games_hint = QtWidgets.QLabel()
        self.games_hint.setWordWrap(True)
        gl.addWidget(self.games_hint)
        lay.addWidget(self.games_box, 1)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        buttons = QtWidgets.QDialogButtonBox()
        buttons.addButton("Save", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton("Re-detect all", QtWidgets.QDialogButtonBox.ResetRole)
        buttons.addButton(QtWidgets.QDialogButtonBox.Close)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.close)
        buttons.clicked.connect(self._clicked)
        lay.addWidget(buttons)

        self.refresh()

    # -- construction ------------------------------------------------------
    @staticmethod
    def _label(text, help_text):
        lbl = QtWidgets.QLabel(text)
        lbl.setToolTip(help_text)
        return lbl

    def _row(self, key):
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        edit = QtWidgets.QLineEdit()
        edit.setPlaceholderText("(auto-detect)")
        browse = QtWidgets.QPushButton("Browse...")
        browse.clicked.connect(lambda _=False, k=key: self._browse(k))
        clear = QtWidgets.QPushButton("Auto")
        clear.setToolTip("Clear this setting and go back to auto-detection")
        clear.clicked.connect(lambda _=False, k=key: self._clear(k))
        h.addWidget(edit, 1)
        h.addWidget(browse)
        h.addWidget(clear)
        self._edits[key] = edit
        return w

    # -- actions -----------------------------------------------------------
    def _browse(self, key):
        start = self._edits[key].text() or cfg.get(key) or ""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder", start)
        if not d:
            return
        if key == "game_dir" and os.path.basename(d).lower() != "ovldata":
            ovl = os.path.join(d, "Win64", "ovldata")
            if os.path.isdir(ovl):
                d = ovl
        self._edits[key].setText(d)
        self.refresh(keep_edits=True)

    def _clear(self, key):
        self._edits[key].clear()
        cfg.write(**{key: None})
        self.refresh()

    def _use_game(self, item):
        self._edits["game_dir"].setText(item.data(QtCore.Qt.UserRole))
        self.refresh(keep_edits=True)

    def _clicked(self, button):
        if button.text() == "Re-detect all":
            for edit in self._edits.values():
                edit.clear()
            cfg.write(**{k: None for k in cfg.KEYS})
            self.refresh()

    def collect(self):
        """(values, error). Split from `save` so validation is testable without a modal dialog --
        a QMessageBox cannot be answered in a headless test and takes the process down with it."""
        values = {}
        for key, edit in self._edits.items():
            text = edit.text().strip()
            if text and not os.path.isdir(text):
                return None, "This is not a folder that exists:\n\n%s" % text
            values[key] = text or None          # blank -> back to auto-detect
        return values, None

    def save(self, interactive=True):
        values, error = self.collect()
        if error:
            if interactive:
                QtWidgets.QMessageBox.warning(self, "Not a folder", error)
            self.status.setText(error.replace("\n\n", " "))
            return False
        cfg.write(**values)
        self.refresh()
        self.status.setText("Saved to %s" % cfg.config_path())
        return True

    # -- display -----------------------------------------------------------
    def refresh(self, keep_edits=False):
        stored = cfg.read()
        if not keep_edits:
            for key, edit in self._edits.items():
                edit.setText(stored.get(key, "") or "")

        lines = []
        for key, label, _help in ROWS:
            value, src = cfg.get(key), cfg.source(key)
            lines.append("%s: %s  [%s]" % (label, value or "NOT FOUND", src))
        self.status.setText("   ·   ".join(lines))

        self.games_list.clear()
        games = cfg.detect_game_dirs()
        for g in games:
            item = QtWidgets.QListWidgetItem(_describe(g))
            item.setData(QtCore.Qt.UserRole, g)
            self.games_list.addItem(item)
        if len(games) > 1:
            self.games_hint.setText(
                "More than one install — moving a Steam library leaves the old copy behind. "
                "Double-click the one you are modding. Searched: %s"
                % ", ".join(cfg.steam_libraries()))
        elif games:
            self.games_hint.setText("Double-click to pin it. Searched: %s"
                                    % ", ".join(cfg.steam_libraries()))
        else:
            self.games_hint.setText(
                "No install found in Steam's libraries (%s). Use Browse on the Game install row."
                % (", ".join(cfg.steam_libraries()) or "none"))


def _describe(ovldata):
    """One line per install, with enough to tell two copies apart."""
    import datetime
    game = os.path.dirname(os.path.dirname(ovldata))
    exe = os.path.join(game, "JWE3.exe")
    try:
        st = os.stat(exe)
        return "%s     (JWE3.exe %.0f MB, updated %s)" % (
            game, st.st_size / 1e6,
            datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"))
    except OSError:
        return game


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = SetupWindow()
    w.show()
    return app.exec_()


def selftest():
    import tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    old = os.environ.get("JWE3_CONFIG_DIR")
    os.environ["JWE3_CONFIG_DIR"] = tempfile.mkdtemp()
    try:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])   # noqa: F841
        w = SetupWindow()
        assert set(w._edits) == set(cfg.KEYS)

        # a real folder saves, and is then reported as coming from the config
        d = tempfile.mkdtemp()
        w._edits["swatch_dir"].setText(d)
        assert w.save(interactive=False) is True
        assert cfg.read().get("swatch_dir") == d, cfg.read()
        assert cfg.source("swatch_dir") == "config"

        # a bogus path must be refused, not written
        w._edits["swatch_dir"].setText("Z:\\nope\\nope")
        assert w.save(interactive=False) is False
        assert cfg.read().get("swatch_dir") == d, "invalid path must not overwrite a good one"
        w._edits["swatch_dir"].setText(d)

        # clearing a row returns it to auto-detection
        w._clear("swatch_dir")
        assert "swatch_dir" not in cfg.read()
        assert cfg.source("swatch_dir") in ("detected", "missing")

        # the install list is populated and each entry carries a real path
        for i in range(w.games_list.count()):
            p = w.games_list.item(i).data(QtCore.Qt.UserRole)
            assert os.path.isdir(p), p
        if w.games_list.count():
            w._use_game(w.games_list.item(0))
            assert w._edits["game_dir"].text()
        assert "config" in w.status.text() or w.status.text()
    finally:
        if old is None:
            os.environ.pop("JWE3_CONFIG_DIR", None)
        else:
            os.environ["JWE3_CONFIG_DIR"] = old
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        sys.exit(main())
