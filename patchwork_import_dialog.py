"""Confirm-table dialog for importing a painted zone map.

Shows one row per painted colour -- swatch, share of the texture, zone dropdown -- so the author
paints in whatever colours are legible and states what each one means. Nobody has to know that
zone 2 is grey 128.
"""
import os

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

import patchwork
import patchwork_import


class PatchworkImportDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import patchwork map")
        self.resize(640, 460)
        self.image = None
        self.clusters = []
        self.result_map = None

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(
            "Each painted colour becomes a zone. Anti-aliased edges are absorbed into the\n"
            "nearest colour, and the exported map has hard edges -- a soft edge between\n"
            "distant zones renders as bands of unrelated zones in game."))

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["colour", "share", "zone"])
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)

        self.preview = QtWidgets.QLabel()
        self.preview.setMinimumHeight(160)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self.preview)

        self.note = QtWidgets.QLabel("")
        lay.addWidget(self.note)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    # -- data --------------------------------------------------------------
    def load(self, path):
        img = QtGui.QImage(path)
        if img.isNull():
            self.note.setText("could not read %s" % os.path.basename(path))
            return False
        img = img.convertToFormat(QtGui.QImage.Format_RGB888)
        w, h = img.width(), img.height()
        ptr = img.constBits()
        ptr.setsize(img.byteCount())
        arr = np.frombuffer(ptr, np.uint8).reshape(h, img.bytesPerLine())[:, :w * 3]
        self.set_image(arr.reshape(h, w, 3).copy())
        return True

    def set_image(self, rgb):
        self.image = np.asarray(rgb)
        self.clusters = patchwork_import.assign_default_zones(
            patchwork_import.cluster(self.image))
        self._fill_table()
        self._refresh()

    def _fill_table(self):
        self.table.setRowCount(len(self.clusters))
        for i, c in enumerate(self.clusters):
            sw = QtWidgets.QTableWidgetItem()
            sw.setBackground(QtGui.QColor(*c.rgb))
            sw.setFlags(QtCore.Qt.ItemIsEnabled)
            sw.setToolTip("RGB %d, %d, %d" % c.rgb)
            self.table.setItem(i, 0, sw)
            pc = QtWidgets.QTableWidgetItem("%.1f%%" % (c.fraction * 100))
            pc.setFlags(QtCore.Qt.ItemIsEnabled)
            self.table.setItem(i, 1, pc)
            box = QtWidgets.QComboBox()
            box.addItem("(unassigned)", None)
            for z in range(patchwork.N_ZONES):
                box.addItem("zone %d" % z, z)
            box.setCurrentIndex(0 if c.zone is None else c.zone + 1)
            box.currentIndexChanged.connect(self._zones_changed)
            self.table.setCellWidget(i, 2, box)

    def _zones_changed(self, *_a):
        for i, c in enumerate(self.clusters):
            w = self.table.cellWidget(i, 2)
            c.zone = w.currentData() if w is not None else None
        self._refresh()

    def can_export(self):
        return bool(self.clusters) and all(c.zone is not None for c in self.clusters)

    def build_map(self):
        return patchwork_import.quantise(self.image, self.clusters)

    def _refresh(self):
        ok = self.can_export()
        self.buttons.button(QtWidgets.QDialogButtonBox.Save).setEnabled(ok)
        if not ok:
            self.note.setText("every colour needs a zone before this can be saved")
            self.preview.clear()
            return
        m = self.build_map()
        hist = patchwork.zone_histogram(m)
        self.note.setText(", ".join("zone %d %.0f%%" % (z, f * 100)
                                    for z, f in sorted(hist.items())))
        pv = np.ascontiguousarray(patchwork_import.preview_rgb(m))
        h, w = pv.shape[:2]
        qi = QtGui.QImage(pv.data, w, h, w * 3, QtGui.QImage.Format_RGB888)
        self.preview.setPixmap(QtGui.QPixmap.fromImage(qi).scaled(
            max(self.preview.width(), 1), max(self.preview.height(), 1),
            QtCore.Qt.KeepAspectRatio, QtCore.Qt.FastTransformation))

    def _accept(self):
        if self.can_export():
            self.result_map = self.build_map()
            self.accept()


def selftest():
    import numpy as np
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    img = np.zeros((8, 8, 3), np.uint8)
    img[:4] = (255, 0, 0)
    img[4:] = (0, 0, 255)
    d = PatchworkImportDialog()
    d.set_image(img)
    assert d.table.rowCount() == 2, d.table.rowCount()
    assert d.can_export() is True
    m = d.build_map()
    assert m.shape == (8, 8) and m.dtype == np.uint8, (m.shape, m.dtype)
    import patchwork
    assert set(np.unique(patchwork.region_of(m)).tolist()) == {0, 1}

    # clearing a zone must disable Save rather than export a half-assigned map
    d.clusters[0].zone = None
    d._refresh()
    assert d.can_export() is False
    assert not d.buttons.button(QtWidgets.QDialogButtonBox.Save).isEnabled()

    print("selftest ok")


if __name__ == "__main__":
    selftest()
