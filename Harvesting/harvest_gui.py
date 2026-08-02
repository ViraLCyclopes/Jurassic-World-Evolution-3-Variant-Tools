"""A guided UI for harvesting JWE3 palette seeds.

Renders `harvest_state` and calls `harvest_runner`. Holds NO workflow logic of its own: every
decision about what comes next lives in harvest_state, where it is tested without a window.

    python harvest_gui.py              -> the window
    python harvest_gui.py --selftest   -> headless construction check
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from PyQt5 import QtWidgets  # noqa: E402

import harvest_runner  # noqa: E402
import harvest_state  # noqa: E402


def _default_confirm(parent, title, text):
    return QtWidgets.QMessageBox.question(
        parent, title, text,
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes


class HarvestWindow(QtWidgets.QWidget):
    """The whole UI.

    `confirm` is injected so selftests never reach a modal dialog: a QMessageBox opened from a
    headless test path kills the process silently (exit 5, no output).
    """

    def __init__(self, confirm=None, pick_file=None, parent=None):
        super(HarvestWindow, self).__init__(parent)
        self._confirm = confirm or (lambda title, text: _default_confirm(self, title, text))
        self._pick_file = pick_file or (lambda title, folder: QtWidgets.QFileDialog.getOpenFileName(
            self, title, folder, "RenderDoc captures (*.rdc)")[0])
        self.state = None
        self.setWindowTitle("JWE3 Seed Harvesting")
        self.resize(780, 600)

        outer = QtWidgets.QVBoxLayout(self)

        # --- coverage
        self.coverage_label = QtWidgets.QLabel()
        self.coverage_bar = QtWidgets.QProgressBar()
        self.coverage_bar.setMaximum(harvest_state.coeff_store.TOTAL_SEEDS)
        outer.addWidget(self.coverage_label)
        outer.addWidget(self.coverage_bar)

        # --- the modified-game banner. THE safety feature: visible in every state where the
        #     player's game files are modified, with restore always one click away.
        self.banner = QtWidgets.QFrame()
        self.banner.setStyleSheet(
            "QFrame { background: #7a1b1b; border-radius: 4px; }"
            "QLabel { color: white; font-weight: bold; }")
        blay = QtWidgets.QHBoxLayout(self.banner)
        self.banner_label = QtWidgets.QLabel()
        self.restore_button = QtWidgets.QPushButton("Restore game files")
        blay.addWidget(self.banner_label, 1)
        blay.addWidget(self.restore_button)
        outer.addWidget(self.banner)

        # --- the one card that changes with next_action
        self.card_title = QtWidgets.QLabel()
        self.card_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.card_body = QtWidgets.QLabel()
        self.card_body.setWordWrap(True)
        self.card_button = QtWidgets.QPushButton()
        outer.addWidget(self.card_title)
        outer.addWidget(self.card_body)
        outer.addWidget(self.card_button)

        # --- always-available action bar.
        # The card says what to do NEXT; this lets someone who already knows go straight to any
        # step. A guided-only UI is patronising to the person who just wants to harvest, and it is
        # also what makes the first-run baseline safe: if we guessed wrong about existing captures
        # being harvested, Harvest is still right here.
        bar = QtWidgets.QGroupBox("Go to")
        blay2 = QtWidgets.QHBoxLayout(bar)
        self.action_buttons = {}
        A = harvest_state.Action
        for action, label in ((A.CONFIGURE, "Settings"), (A.PLAN_SWEEP, "Prepare sweep"),
                              (A.SPAWN_AND_CAPTURE, "Spawn list"), (A.HARVEST, "Harvest"),
                              (A.RESTORE, "Restore")):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(lambda _checked=False, a=action: self.run_action(a))
            blay2.addWidget(b)
            self.action_buttons[action] = b
        # Not keyed by Action: this is a variation on Harvest, not a workflow state of its own.
        self.harvest_one_button = QtWidgets.QPushButton("Harvest one...")
        self.harvest_one_button.setToolTip(
            "Scan a single .rdc instead of the whole folder. Much faster when you have gigabytes "
            "of old captures and only want the one you just took.")
        self.harvest_one_button.clicked.connect(self.do_harvest_one)
        blay2.addWidget(self.harvest_one_button)
        outer.addWidget(bar)
        outer.addStretch(1)

        # --- log
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        outer.addWidget(self.log, 1)

        self.restore_button.clicked.connect(self.on_restore)
        self.card_button.clicked.connect(self.on_card_action)

    # ---------------------------------------------------------------- rendering
    def refresh(self):
        """Re-derive everything from disk. Cheap, and the only way state ever changes."""
        self.state = harvest_state.detect()
        have, total = self.state.coverage
        self.coverage_bar.setMaximum(total)
        self.coverage_bar.setValue(have)
        self.coverage_label.setText(
            "Coverage: %d / %d seeds - %d to go" % (have, total, len(self.state.missing_seeds)))

        self.banner.setVisible(self.state.game_modified)
        self.restore_button.setEnabled(self.state.game_modified)
        self.banner_label.setText(
            "YOUR GAME FILES ARE MODIFIED - %d backup%s held"
            % (self.state.backup_count, "" if self.state.backup_count == 1 else "s"))

        title, body, button = self.card_for(self.state.next_action)
        self.card_title.setText(title)
        self.card_body.setText(body)
        self.card_button.setText(button or "")
        self.card_button.setVisible(button is not None)

        for action, (enabled, why) in self.action_availability().items():
            b = self.action_buttons.get(action)
            if b is not None:
                b.setEnabled(enabled)
                b.setToolTip("" if enabled else why)

        # Say plainly what was assumed about captures that were already on disk, rather than
        # silently ignoring them.
        if self.state.first_run and self.state.captures:
            self.append_log(
                "%d capture(s) were already in %s. Treating them as already harvested -- press "
                "Harvest if you want them scanned anyway (it is idempotent)."
                % (len(self.state.captures), harvest_state.captures_dir()))

    def card_for(self, action):
        """(title, body, button label) for a state. Pure -- touches no widgets, so it is testable.

        `button` is None only for DONE, where there is nothing left to do.
        """
        A = harvest_state.Action
        st = self.state
        if action == A.CONFIGURE:
            return ("Set up first",
                    "\n".join(st.blockers) if st and st.blockers
                    else "Point the tool at your game folder and your RenderDoc capture folder.",
                    "Open settings")
        if action == A.PLAN_SWEEP:
            n = len(st.missing_seeds) if st else 0
            return ("Ready to prepare a sweep",
                    "%d seeds still need harvesting.\n\nPreparing a sweep MODIFIES game OVL files. "
                    "The originals are backed up first and you can restore at any time." % n,
                    "Validate plan")
        if action == A.SPAWN_AND_CAPTURE:
            return ("Spawn these animals, then capture",
                    "Load the game and get one of each listed animal on screen together, then take "
                    "a RenderDoc capture. New .rdc files are picked up automatically.",
                    "Open spawn list")
        if action == A.HARVEST:
            n = len(st.new_captures) if st else 0
            return ("%d new capture%s ready" % (n, "" if n == 1 else "s"),
                    "Scan the captures and merge any new coefficients into your table.",
                    "Harvest coefficients")
        if action == A.RESTORE:
            return ("Restore your game",
                    "Harvesting is done for this pass. Your game files are still modified - restore "
                    "them before playing normally.\n\nYou can run another capture session against "
                    "this same sweep first if you want more blocks.",
                    "Restore game files")
        return ("Nothing to do",
                "Every seed has been harvested. Coverage is complete.",
                None)

    def append_log(self, line):
        self.log.appendPlainText(line)

    # ---------------------------------------------------------------- actions
    def on_restore(self):
        if not self._confirm("Restore game files",
                             "Restore every backed-up OVL to its original state?"):
            return
        code, _out = harvest_runner.run("restore", ["--apply"], on_line=self.append_log)
        self.append_log("restore finished with code %d" % code)
        self.refresh()

    def on_card_action(self):
        """Run whatever the current card offers."""
        self.run_action(self.state.next_action)

    def run_action(self, action):
        """Run one step by name, from the card or the action bar. Dispatch lives in HANDLERS so a
        missing state is a visible failure in the selftest, not a button that does nothing."""
        handler = self.HANDLERS.get(action)
        if handler is not None:
            handler(self)

    def action_availability(self):
        """{action: (enabled, why_not)} for the action bar. Pure, so it is testable.

        Disabled buttons keep their reason in a tooltip: a greyed-out control with no explanation
        is worse than no control at all -- the user cannot tell whether it is broken or blocked.
        """
        st = self.state
        out = {}
        A = harvest_state.Action
        out[A.CONFIGURE] = (True, "")
        reasons = harvest_state.install_blockers()
        out[A.PLAN_SWEEP] = (not reasons, "\n".join(reasons))
        out[A.SPAWN_AND_CAPTURE] = (
            bool(st.swept_seeds),
            "No sweep is staged, so there is no spawn list yet.")
        out[A.HARVEST] = (
            bool(st.captures),
            "No .rdc captures in %s" % (harvest_state.captures_dir() or "(capture folder not set)"))
        out[A.RESTORE] = (
            st.game_modified,
            "Your game files are not modified, so there is nothing to restore.")
        return out

    def do_configure(self):
        setup = os.path.join(os.path.dirname(HERE), "setup_gui.py")
        harvest_runner.run(setup, on_line=self.append_log)
        self.refresh()

    def do_plan(self):
        # Validate BEFORE offering to touch a single game file.
        reasons = harvest_state.install_blockers()
        if reasons:
            for r in reasons:
                self.append_log("BLOCKED: %s" % r)
            self._confirm("Cannot install a sweep", "\n\n".join(reasons))
            return
        code, _out = harvest_runner.run("plan", ["--selftest"], on_line=self.append_log)
        if code != 0:
            self.append_log("plan validation failed (%d) - nothing was installed" % code)
            return
        if not self._confirm(
                "Install sweep",
                "The plan validated. Installing MODIFIES your game's OVL files.\n\n"
                "Originals are backed up first and you can restore at any time.\n\nInstall now?"):
            return
        code, _out = harvest_runner.run("plan", on_line=self.append_log)
        self.append_log("install finished with code %d" % code)
        # Re-check: an install that failed halfway still leaves backups, and the banner must show it.
        self.refresh()
        if code == 0 and not self.state.game_modified:
            self.append_log("WARNING: install reported success but no backups were written.")

    def do_spawn(self):
        harvest_runner.run("spawn", on_line=self.append_log)
        self.refresh()

    def do_harvest(self):
        code, _out = harvest_runner.run("harvest", on_line=self.append_log)
        if code == 0:
            # Without this the same captures look "new" forever and the card never advances.
            harvest_state.set_last_harvest_stamp()
        else:
            self.append_log("harvest failed (%d) - captures left marked as new" % code)
            self.append_log("running audit to show what the captures actually contained...")
            harvest_runner.run("audit", on_line=self.append_log)
        self.refresh()

    def do_harvest_one(self):
        """Harvest a SINGLE capture. `harvest_blocks.py <substring>` already supports this.

        Deliberately does NOT move the last-harvest stamp: the stamp means "everything up to here
        has been scanned", and a targeted scan does not establish that. Moving it would silently
        mark every other pending capture as seen. So the card keeps offering Harvest until a full
        scan runs, which is correct.
        """
        folder = harvest_state.captures_dir() or ""
        path = self._pick_file("Choose a capture to harvest", folder)
        if not path:
            return
        name = os.path.basename(path)
        self.append_log("harvesting only %s ..." % name)
        code, _out = harvest_runner.run("harvest", [name], on_line=self.append_log)
        if code != 0:
            self.append_log("harvest of %s failed (%d)" % (name, code))
        self.refresh()

    def export_harvest(self, path):
        """Write THIS user's own harvested rows to `path`. Returns how many were written.

        mine_only=True so a shared file carries what this person actually captured, rather than the
        shipped table echoed back -- otherwise merging two people's exports says nothing about who
        found what. `coeff_store.export` returns an int.
        """
        n = harvest_state.coeff_store.export(path, mine_only=True)
        self.append_log("exported %d row(s) to %s" % (n, path))
        return n

    def import_harvest(self, path):
        """Merge someone else's harvest file into this user's table. Returns rows ADDED.

        `coeff_store.merge` returns (added, updated, total, rejected). `rejected` counts rows with
        no coefficients, which merge drops so a malformed harvest cannot poison the table -- worth
        showing, because a file that merges 0 of 40 rows is a broken file, not an empty one.
        """
        added, updated, total, rejected = harvest_state.coeff_store.merge(path)
        self.append_log("merged %s: +%d new, %d updated, %d total, %d rejected"
                        % (os.path.basename(path), added, updated, total, rejected))
        self.refresh()
        return added

    #: next_action -> handler. Every action that offers a button must appear here.
    HANDLERS = {
        harvest_state.Action.CONFIGURE: do_configure,
        harvest_state.Action.PLAN_SWEEP: do_plan,
        harvest_state.Action.SPAWN_AND_CAPTURE: do_spawn,
        harvest_state.Action.HARVEST: do_harvest,
        harvest_state.Action.RESTORE: lambda self: self.on_restore(),
    }

    def closeEvent(self, event):
        """Never let this close with the game still modified without saying so."""
        st = self.state or harvest_state.detect()
        if st.game_modified and not self._confirm(
                "Game files still modified",
                "Your game files are STILL MODIFIED (%d backups held).\n\n"
                "Restore them before playing normally.\n\nQuit anyway?" % st.backup_count):
            event.ignore()
            return
        event.accept()


def main(argv=None):
    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    w = HarvestWindow()
    w.refresh()
    w.show()
    return app.exec_()


def selftest():
    """Construct the window offscreen and check it reflects state. No dialogs, no event loop.

    QT_QPA_PLATFORM=offscreen has ZERO FONTS, so never assert on rendered text -- assert on widget
    properties instead. And a modal QMessageBox reached from here kills the process SILENTLY
    (exit 5, no output), which is why `confirm` is injectable and stubbed out below.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    picked = []
    w = HarvestWindow(confirm=lambda *a, **k: False,
                      pick_file=lambda title, folder: (picked.append((title, folder)), "")[1])
    w.refresh()

    # cancelling the single-capture picker must be a no-op, not a scan of everything
    w.do_harvest_one()
    assert picked, "harvest-one must ask which capture"
    assert w.harvest_one_button.isEnabled()

    assert w.coverage_bar.maximum() == 256, w.coverage_bar.maximum()
    # the banner and the restore button must track game_modified, whatever this machine's state is
    assert w.banner.isVisibleTo(w) == w.state.game_modified, (
        w.banner.isVisibleTo(w), w.state.game_modified)
    assert w.restore_button.isEnabled() == w.state.game_modified

    # every state must produce a usable card
    A = harvest_state.Action
    for action in (A.CONFIGURE, A.PLAN_SWEEP, A.SPAWN_AND_CAPTURE, A.HARVEST, A.RESTORE, A.DONE):
        title, body, button = w.card_for(action)
        assert title and body, (action, title, body)
        assert button is None or isinstance(button, str), (action, button)
    # DONE is the only state with nothing left to do
    assert w.card_for(A.DONE)[2] is None, w.card_for(A.DONE)
    assert w.card_for(A.RESTORE)[2] is not None

    # EVERY state that offers a button must have a handler, or the button silently does nothing.
    for action in (A.CONFIGURE, A.PLAN_SWEEP, A.SPAWN_AND_CAPTURE, A.HARVEST, A.RESTORE, A.DONE):
        has_button = w.card_for(action)[2] is not None
        assert has_button == (action in HarvestWindow.HANDLERS), (
            action, has_button, action in HarvestWindow.HANDLERS)

    # the action bar: every button must dispatch somewhere, and a disabled one must say why
    avail = w.action_availability()
    assert set(w.action_buttons) == set(avail), (set(w.action_buttons), set(avail))
    for action, (enabled, why) in avail.items():
        assert action in HarvestWindow.HANDLERS, action
        assert enabled or why, (action, enabled, why)
        assert w.action_buttons[action].isEnabled() == enabled, action
        if not enabled:
            assert w.action_buttons[action].toolTip(), action

    # export -> import round trip. Uses a temp table so the real one is never touched.
    import tempfile
    old_cfg = os.environ.get("JWE3_CONFIG_DIR")
    os.environ["JWE3_CONFIG_DIR"] = tempfile.mkdtemp()
    try:
        harvest_state._reset_caches()
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "my_harvest.json")
            n = w.export_harvest(out)
            assert isinstance(n, int) and n >= 0, n
            assert os.path.isfile(out), out
            added = w.import_harvest(out)
            assert isinstance(added, int) and added >= 0, added
    finally:
        if old_cfg is None:
            os.environ.pop("JWE3_CONFIG_DIR", None)
        else:
            os.environ["JWE3_CONFIG_DIR"] = old_cfg
        harvest_state._reset_caches()

    del w
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        sys.exit(main())
