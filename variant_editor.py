"""Task 7: the runnable JWE3 Variant Editor.

    python variant_editor.py [path\\to\\some_variant_01_00.fgm]

Wires the pieces together: `PreviewBridge` (socket to the Blender add-on) + `VariantEditorWindow`
(the UI) + `fgm_io` (loose .fgm read/write). See README.md for the full workflow.

The controller is deliberately split so it is testable without dialogs and without Blender: every
File action is a thin dialog wrapper around a `do_*` method that takes explicit paths, and those
`do_*` methods are what `selftest()` exercises (against a temp copy of a real shipped .fgm).

PREVIEW ASSET CONVENTIONS (discovered from the existing research folder, not invented here):

    mask_dir     the folder the MODEL (or the .fgm) was imported from -- textures follow the model,
                 not the .fgm, so previewing a Spinosaurus variant on a Baryonyx uses Baryonyx's
                 masks. `Textures/<Species>/` is only a LAST-RESORT fallback and ships EMPTY.
    mask_prefix  <species lowercased>                  e.g. "baryonyx", giving
                                                       baryonyx.playered_blendweights_[00]_A.png
    layers_json  ../LayerJSON/<Species>_<Sex>.json     produced by `python export_layers.py <Species>`

A LayerJSON alone makes a species previewable; the dropdown lists those. The curated
`Textures/<Species>` folder is OPTIONAL -- see `preview_assets.resolve`. (This block used to say a
Textures folder was REQUIRED, which stopped being true when masks moved to following the model.)
Everything else in the tool (editing, saving) works for any species.

NOT connected to the game: this tool only reads and writes loose extracted .fgm files. Injecting
the saved .fgm back into an OVL stays the user's own cobra-tools step.

Run:  python variant_editor.py --selftest   -> selftest ok
"""
import os
import sys

from PyQt5 import QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)                 # the Variant Research folder
for _p in (HERE, PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import fgm_io                                   # noqa: E402
    _FGM_IMPORT_ERROR = None
except Exception as _import_err:
    fgm_io = None
    _FGM_IMPORT_ERROR = str(_import_err)

import theme                                    # noqa: E402
from editor_ui import VariantEditorWindow       # noqa: E402
from preview_bridge import PreviewBridge        # noqa: E402
from variant_model import VariantModel          # noqa: E402

# Asset resolution is shared with the Blender add-on's importer, so it lives in a module that
# imports neither PyQt5 nor bpy. Re-exported here because callers (and the selftest) use these
# names off variant_editor.
from preview_assets import (                    # noqa: E402
    DINO_FILES_DIR, LAYERJSON_DIR, TEXTURES_DIR, AssetError, assets_for, detect_mask_prefix,
    layers_json_for, mask_dir_for, pick_target_object, preview_paths, previewable_species,
    sex_from_object_name, species_from_object_name,
)


# -- controller ------------------------------------------------------------
class EditorController:
    """Owns the window's File/Build behaviour. The `do_*` methods take explicit paths (testable);
    the `on_*` methods are the dialog wrappers the QActions are connected to."""

    def __init__(self, window, bridge=None):
        self.window = window
        self.bridge = bridge
        self.template_path = None     # set by "New from template" until the first Save As
        self.variantset = None        # cosmetic skin adopted by opening a *_variantset_*.fgm

        window.act_open.triggered.connect(self.on_open)
        window.act_new.triggered.connect(self.on_new)
        window.act_save.triggered.connect(self.on_save)
        window.act_save_as.triggered.connect(self.on_save_as)
        window.build_button.clicked.connect(self.on_build)
        window.blenderImport.connect(self.on_blender_import)
        window.from_selected_button.clicked.connect(self.on_from_selected)
        window.texture_open_button.clicked.connect(self.on_open_texture)
        window.texture_save_button.clicked.connect(self.on_save_texture)
        window.textures_button.clicked.connect(self.on_pick_textures)
        window.textures_clear.clicked.connect(self.on_clear_textures)
        self.refresh_textures_row()

        window.species_combo.addItems(previewable_species())

    # -- actions (no dialogs) ---------------------------------------------
    def do_open(self, path):
        """Load a loose .fgm into the editor and infer its species/sex for the preview dropdown.

        A VARIANTSET is handled separately: it carries no palette parameters at all, so `load_fgm`
        cannot read one and opening it used to fail outright. It is a cosmetic SKIN -- a base
        diffuse swap over the same layer stack -- so it is adopted as the skin for the next Build
        rather than loaded as a variant.
        """
        if fgm_io.is_variantset_fgm(path):
            return self.do_open_variantset(path)
        model = fgm_io.load_fgm(path)
        self.template_path = None
        self.window.load_model(model, path)
        species, sex = fgm_io.species_sex_from_filename(path)
        self.select_species(species)
        self.window.statusBar().showMessage(
            "opened %s (%s%s)" % (os.path.basename(path), species or "?", "/" + sex if sex else ""))
        return model

    def do_open_variantset(self, path):
        """Adopt a variantset as the cosmetic skin for the next Build. Returns None (no model).

        Deliberately does NOT touch the loaded variant: skin and colour grade are independent axes
        in game, so opening a skin must not discard the grade you are working on.
        """
        info = fgm_io.load_variantset_fgm(path)
        self.variantset = info if info.get("base_diffuse") else None
        species, sex = fgm_io.species_sex_from_filename(path)
        if species:
            self.select_species(species)
        if self.variantset is None:
            self.window.statusBar().showMessage(
                "%s is a cosmetic skin, but its base diffuse could not be found next to it "
                "(extract the .png beside the .fgm)" % os.path.basename(path))
            return None
        extra = ""
        if info.get("enableNormalContrast"):
            # Say it rather than silently ignore it -- the render will differ from the game by
            # however much this contributes, and nobody should have to discover that by eye.
            extra = "   (pNormalContrast %.3f is NOT applied - not yet traced)" % info["normalContrast"]
        self.window.statusBar().showMessage(
            "skin: %s -> %s. Press Build to apply it.%s"
            % (os.path.basename(path), os.path.basename(info["base_diffuse"]), extra))
        return None

    def do_new(self, template_path):
        """Start a fresh variant. The template supplies everything the model does not carry
        (shader, textures, the other ~130 attributes); Save As writes the model onto a copy of it."""
        self.template_path = template_path
        self.window.load_model(VariantModel.template(), None)
        species, _sex = fgm_io.species_sex_from_filename(template_path)
        self.select_species(species)
        self.window.statusBar().showMessage(
            "new variant from template %s - use Save As" % os.path.basename(template_path))

    def do_save(self, path):
        """Write the model to `path`. Uses the template copy path when this is a New that has never
        been saved, so the output is a complete FGM rather than a patch onto nothing."""
        # An .fgm is ~144 attributes; the editor only owns ~30 of them. So writing to a path that
        # does not exist yet must START from a real FGM (the template, or the file we opened) and
        # write the model onto a copy -- `save_fgm` alone edits a file in place and fails outright
        # on a new path. That is what broke Save As for anything except "New from template".
        source = self.template_path or self.window.current_path
        if not os.path.isfile(path):
            if not source or not os.path.isfile(source):
                raise ValueError(
                    "cannot create %s from nothing -- open a variant .fgm first (or use "
                    "File > New from template), so its other ~110 attributes can be carried over."
                    % os.path.basename(path))
            fgm_io.save_fgm_from_template(self.window.model, source, path)
        else:
            fgm_io.save_fgm(self.window.model, path)
        self.template_path = None
        self.window.current_path = path
        self.window.setWindowTitle("JWE3 Variant Editor - %s" % os.path.basename(path))
        self.window.statusBar().showMessage("saved %s" % path)
        return path

    def do_build(self):
        """Build+assign the layer material onto the imported Blender mesh named in the UI."""
        if self.bridge is None:
            self.window.statusBar().showMessage("no Blender connection - start the listener add-on")
            return False
        object_name = self.window.object_name_edit.text().strip()
        if not object_name:
            self.window.statusBar().showMessage("enter the name of the imported mesh object first")
            return False
        # Textures follow the MODEL, not the .fgm: previewing a Spinosaurus variant on a Baryonyx
        # must use Baryonyx masks. The object's own species wins over the dropdown when we
        # recognise it; the dropdown is the fallback for meshes we can't identify by name.
        species = (species_from_object_name(object_name)
                   or self.window.species_combo.currentText().strip())
        sex = sex_from_object_name(object_name) or self._current_sex()
        fgm_species = fgm_io.species_sex_from_filename(self.window.current_path or "")[0]
        try:
            mask_dir, mask_prefix, layers_json = assets_for(
                species, sex, fgm_path=self.window.current_path, fgm_species=fgm_species)
        except AssetError as e:
            self.window.statusBar().showMessage(str(e).split("\n")[0])
            return False
        vs = getattr(self, "variantset", None)
        ok = self.bridge.build_material(
            object_name, mask_dir, mask_prefix, layers_json,
            base_diffuse=(vs or {}).get("base_diffuse"),
            skin_name=(os.path.basename((vs or {}).get("path", "")) or None))
        if ok:
            skin = ("  skin %s" % os.path.basename(vs["path"])) if vs else ""
            self.window.statusBar().showMessage(
                "material built on %r%s - grading..." % (object_name, skin))
            self.window._push_now()
        else:
            self.window.statusBar().showMessage(
                "build failed - is %r the imported mesh object's name?" % object_name)
        return ok

    def do_blender_import(self, path, object_name=""):
        """Blender's File > Import loaded `path`; mirror it into this window.

        Loads the same values the importer applied, and adopts the object it targeted so a later
        Build here hits the same mesh. Does NOT re-build the material -- Blender already did.
        """
        if object_name:
            self.window.object_name_edit.setText(object_name)
        model = self.do_open(path)
        self.window.statusBar().showMessage(
            "followed Blender import: %s%s" % (os.path.basename(path),
                                               " on %s" % object_name if object_name else ""))
        return model

    def on_blender_import(self, path, object_name):
        self._guard(self.do_blender_import, path, object_name)

    def _current_sex(self):
        if not self.window.current_path:
            return None
        return fgm_io.species_sex_from_filename(self.window.current_path)[1]

    # -- the diffuse preview -------------------------------------------------
    def on_open_texture(self):
        self._guard(self.do_open_texture)

    def do_open_texture(self, path=None):
        """Pick a base diffuse PNG and preview the grade on it.

        Starts in the configured texture folder, since that is where these live once it is set.
        """
        if path is None:
            import jwe3_config
            start = jwe3_config.textures_dir() or self._start_dir() or ""
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self.window, "Base diffuse texture", start,
                "Diffuse textures (*basediffusetexture*.png);;PNG images (*.png);;All files (*)")
            if not path:
                return None
        self.window.load_texture(path)
        self.window.statusBar().showMessage("diffuse: %s" % os.path.basename(path))
        return path

    def on_save_texture(self):
        self._guard(self.do_save_texture)

    def do_save_texture(self, out_path=None):
        """Write the graded diffuse out as a PNG, at the source's full resolution."""
        src = getattr(self.window, "texture_path", None)
        if not src:
            QtWidgets.QMessageBox.information(
                self.window, "Variant Editor",
                'No diffuse loaded. Use "Diffuse..." to pick one first.')
            return None
        if out_path is None:
            stem = os.path.splitext(os.path.basename(src))[0]
            suggested = os.path.join(os.path.dirname(src), "%s.graded.png" % stem)
            out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self.window, "Save graded texture", suggested, "PNG images (*.png)")
            if not out_path:
                return None
        w, h = self.window.save_graded_texture(out_path)
        self.window.statusBar().showMessage(
            "saved %dx%d -> %s" % (w, h, os.path.basename(out_path)))
        return out_path

    # -- start from what is already in Blender ------------------------------
    def on_from_selected(self):
        self._guard(self.do_from_selected)

    def do_from_selected(self):
        """Adopt the mesh selected in Blender, and open the variant .fgm it was graded from.

        Two useful outcomes, and the weaker one is still worth having:
          * the material records `jwe3_variant_path` -> open that .fgm, so the editor is populated
            without the user finding the file on disk;
          * it does not (built by hand, or never graded) -> still adopt the object name, so a
            later Build targets the right mesh. Say so rather than failing.
        """
        info = self.bridge.selected()
        if not info:
            QtWidgets.QMessageBox.information(
                self.window, "Variant Editor",
                "Nothing selected in Blender. Click the model in the viewport and try again.")
            return None
        obj = info.get("object") or ""
        if obj:
            self.window.object_name_edit.setText(obj)
        path = info.get("variant_path")
        if not path or not os.path.isfile(path):
            self.window.statusBar().showMessage(
                "adopted %s -- no variant .fgm recorded on its material%s"
                % (obj, "" if not path else " (%s is gone)" % os.path.basename(path)))
            return None
        model = self.do_open(path)
        self.window.statusBar().showMessage(
            "from selected: %s on %s" % (os.path.basename(path), obj))
        return model

    # -- the texture folder ------------------------------------------------
    # ONE folder, repointed as you move between species -- see jwe3_config.textures_dir.
    def refresh_textures_row(self):
        self.window.textures_edit.setText(__import__("jwe3_config").textures_dir() or "")

    def on_pick_textures(self):
        import jwe3_config
        start = jwe3_config.textures_dir() or self._start_dir()
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self.window, "Texture folder (masks for the species being previewed)", start or "")
        if not d:
            return
        jwe3_config.set_textures_dir(d)
        self.refresh_textures_row()

    def on_clear_textures(self):
        import jwe3_config
        jwe3_config.set_textures_dir(None)
        self.refresh_textures_row()

    def select_species(self, species):
        if not species:
            return
        i = self.window.species_combo.findText(species)   # default flags = MatchExactly
        if i >= 0:
            self.window.species_combo.setCurrentIndex(i)

    # -- dialog wrappers ---------------------------------------------------
    def _start_dir(self):
        return os.path.dirname(self.window.current_path or "") or DINO_FILES_DIR

    def on_open(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.window, "Open variant .fgm", self._start_dir(), "FGM files (*.fgm)")
        if path:
            self._guard(self.do_open, path)

    def on_new(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.window, "Pick a template .fgm to base the new variant on",
            self._start_dir(), "FGM files (*.fgm)")
        if path:
            self._guard(self.do_new, path)

    def on_save(self):
        if self.window.current_path and not self.template_path:
            self._guard(self.do_save, self.window.current_path)
        else:
            self.on_save_as()

    def on_save_as(self):
        start = self._start_dir()
        if self.window.current_path:
            # offer a NEW name beside the original rather than the original itself, so the default
            # action of the dialog is not "overwrite the file I opened"
            stem, ext = os.path.splitext(os.path.basename(self.window.current_path))
            start = os.path.join(start, "%s_edit%s" % (stem, ext or ".fgm"))
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.window, "Save variant .fgm as", start, "FGM files (*.fgm)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".fgm"          # the dialog does not add it when the user just types a name
        self._guard(self.do_save, path)

    def on_build(self):
        self._guard(self.do_build)

    def _guard(self, fn, *args):
        """Run an action; report failures in the status bar and a message box instead of dying."""
        try:
            return fn(*args)
        except Exception as e:
            self.window.statusBar().showMessage("%s: %s" % (type(e).__name__, e))
            QtWidgets.QMessageBox.critical(self.window, "Variant Editor", "%s: %s" % (type(e).__name__, e))
            return None


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = QtWidgets.QApplication(argv)
    theme.apply(app)      # app-level too, so QFileDialog/QMessageBox match the window

    if _FGM_IMPORT_ERROR:
        QtWidgets.QMessageBox.critical(
            None,
            "JWE3 Variant Editor - Configuration Error",
            f"Could not load required modules:\n\n{_FGM_IMPORT_ERROR}\n\n"
            "Please run 'python setup_gui.py' to configure the path to your cobra-tools installation."
        )
        return 1

    bridge = PreviewBridge()
    connected = bridge.connect()
    # Not connected -> the window gets bridge=None so it never tries to push; editing and saving
    # still work fully. Preview > Reconnect re-arms it once the add-on is enabled.
    window = VariantEditorWindow(bridge=bridge if connected else None)
    controller = EditorController(window, bridge if connected else None)
    window.set_blender_connected(connected)
    window.statusBar().showMessage(
        "Blender preview connected" if connected else
        "Blender not found on 127.0.0.1:8990 - enable the listener add-on, then Preview > Reconnect")

    def reconnect():
        ok = bridge.connect()
        window.bridge = bridge if ok else None
        controller.bridge = bridge if ok else None
        window.set_blender_connected(ok)
        window.statusBar().showMessage("Blender connected" if ok else "still no listener on 127.0.0.1:8990")

    menu = window.menuBar().addMenu("&Preview")
    menu.addAction("&Reconnect to Blender").triggered.connect(reconnect)

    args = [a for a in argv[1:] if not a.startswith("-")]
    if args and os.path.isfile(args[0]):
        controller.do_open(args[0])

    window.show()
    return app.exec_()


def selftest():
    """Covers the pure path helpers and the controller's do_* methods end to end (fgm_io + UI),
    with a fake bridge. No dialogs, no Blender, no socket."""
    import shutil
    import tempfile

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])   # noqa: F841

    # -- preview asset resolution against the real research folder
    species = previewable_species()
    assert "Baryonyx" in species, species
    # A LayerJSON is what makes a species previewable; the curated Textures/ folder is an OPTIONAL
    # fallback that the packaged software does not ship (masks normally come from the model's own
    # folder). So everything below that needs masks is conditional on it being present.
    paths = preview_paths("Baryonyx", "Female")
    have_textures = paths is not None
    if have_textures:
        mask_dir, mask_prefix, layers_json = paths
        assert os.path.isdir(mask_dir) and os.path.isfile(layers_json), paths
        # NOT pinned to "baryonyx": the folder here is the configured `textures_dir`, which is one
        # folder repointed as you work, so the literal only passed while that was the folder in the
        # config. The check that matters is the next one -- the prefix matches real files.
        assert mask_prefix and mask_prefix == mask_prefix.lower(), mask_prefix
        # the prefix must actually match the mask files on disk, or build silently wires nothing
        assert any(f.startswith(mask_prefix + ".playered_blendweights_")
                   for f in os.listdir(mask_dir)), \
            "mask_prefix does not match the files in %s" % mask_dir
    assert preview_paths("Nosuchsaurus") is None

    class _FakeBridge:
        def __init__(self): self.built = None; self.pushes = 0
        def build_material(self, obj, md, mp, lj, base_diffuse=None, skin_name=None):
            self.built = (obj, md, mp, lj)
            self.skin = (base_diffuse, skin_name)     # variantset build carries the skin through
            return True
        def push(self, model): self.pushes += 1; return True

    fake = _FakeBridge()
    window = VariantEditorWindow(bridge=fake)
    ctl = EditorController(window, fake)

    # -- open a real shipped variant
    if not os.path.isfile(fgm_io.SAMPLE_FGM):
        print("selftest ok (sample FGM not present on disk, skipped file I/O tests)")
        return

    work = os.path.join(tempfile.gettempdir(), "variant_editor_test.fgm")
    shutil.copy(fgm_io.SAMPLE_FGM, work)
    model = ctl.do_open(work)
    assert model.seed == 36, model.seed                       # baryonyx_variant_01_00's real seed
    assert window.model is model and window.current_path == work
    assert window.species_combo.currentText() == "Baryonyx", window.species_combo.currentText()

    # -- edit + save + reload round-trip through the UI
    window.set_field("brightnessBase", 2.75)
    window.set_field("seed", 9)
    ctl.do_save(work)
    reloaded = fgm_io.load_fgm(work)
    assert reloaded.seed == 9, reloaded.seed
    assert abs(reloaded.brightnessBase - 2.75) < 1e-4, reloaded.brightnessBase
    # values the editor never touched must survive untouched (no clamping, no quantisation)
    assert reloaded.keyColour == [1.0, 1.0, 1.0], reloaded.keyColour
    assert abs(reloaded.paletteOffset - 4.51) < 1e-4, reloaded.paletteOffset

    # -- REGRESSION: Save As from an OPENED file to a BRAND-NEW path must work. This used to throw,
    # because save_fgm edits in place and cannot create a file -- so Save As only ever worked for
    # "New from template", and saving your own separate copy was impossible.
    ctl.do_open(work)
    fresh_out = os.path.join(tempfile.gettempdir(), "variant_editor_saveas.fgm")
    if os.path.isfile(fresh_out):
        os.remove(fresh_out)
    window.set_field("seed", 123)
    ctl.do_save(fresh_out)
    assert os.path.isfile(fresh_out), "Save As did not create the file"
    assert fgm_io.load_fgm(fresh_out).seed == 123
    assert window.current_path == fresh_out
    assert fgm_io.load_fgm(work).seed != 123, "Save As must not also write the original"
    # saving again (now that it exists) goes down the in-place path and still works
    window.set_field("seed", 124)
    ctl.do_save(fresh_out)
    assert fgm_io.load_fgm(fresh_out).seed == 124
    # with nothing open, creating a file from nothing is refused with a clear message
    blank = VariantEditorWindow(bridge=None)
    try:
        EditorController(blank, None).do_save(os.path.join(tempfile.gettempdir(), "nope.fgm"))
    except ValueError as e:
        assert "open a variant" in str(e), str(e)
    else:
        raise AssertionError("saving with no source should be refused")

    # -- New from template -> Save As writes a complete, loadable FGM
    out = os.path.join(tempfile.gettempdir(), "variant_editor_new.fgm")
    if os.path.isfile(out):
        os.remove(out)
    ctl.do_new(fgm_io.SAMPLE_FGM)
    assert window.current_path is None and ctl.template_path == fgm_io.SAMPLE_FGM
    window.set_field("seed", 77)
    ctl.do_save(out)
    assert ctl.template_path is None and window.current_path == out
    fresh = fgm_io.load_fgm(out)
    assert fresh.seed == 77, fresh.seed
    assert abs(fresh.paletteStrength - 0.265) < 1e-4, fresh.paletteStrength   # template default

    # -- build wiring passes the resolved asset paths straight through
    window.object_name_edit.setText("models")
    if have_textures:
        assert ctl.do_build() is True
        assert fake.built == ("models", mask_dir, mask_prefix, layers_json), fake.built
        assert fake.pushes >= 1, "a successful build must be followed by a grade push"

    # -- cross-species: a Spinosaurus variant previewed on the Baryonyx model must use BARYONYX
    # textures (the mesh's UVs/masks) while taking its colour from the Spino .fgm
    spino_variant = os.path.join(DINO_FILES_DIR, "Spino Female",
                                 "spinosaurus_female_variant_01_00.fgm")
    if os.path.isfile(spino_variant):
        ctl.do_open(spino_variant)
        assert window.model.seed == 120, window.model.seed        # colour from the Spino file
        window.object_name_edit.setText("baryonyx_female_ob0_L0")  # ... onto the Baryonyx mesh
        if have_textures:
            assert ctl.do_build() is True
            assert fake.built[0] == "baryonyx_female_ob0_L0"
            assert fake.built[2] == "baryonyx", fake.built        # BARYONYX masks, not spinosaurus
            assert "Baryonyx" in fake.built[3], fake.built[3]     # ... and Baryonyx LayerJSON
        else:
            # no curated Textures/: the editor cannot resolve masks for a species whose model it
            # cannot see, and must say so rather than building something wrong
            assert ctl.do_build() is False
            assert "mask" in window.statusBar().currentMessage().lower(), \
                window.statusBar().currentMessage()

    # -- following a Blender File > Import pulls that variant's settings into the open window
    class _ImportBridge(_FakeBridge):
        def __init__(self, path):
            _FakeBridge.__init__(self)
            self.state = {"path": path, "object": "baryonyx_female_ob0_L0",
                          "species": "Baryonyx", "sex": "Female", "serial": 7}
        def last_import(self): return self.state

    ib = _ImportBridge(fgm_io.SAMPLE_FGM)
    w2 = VariantEditorWindow(bridge=ib)
    c2 = EditorController(w2, ib)
    assert w2._import_serial == 7, "must prime from the CURRENT serial, not replay old imports"
    w2._poll_blender()
    assert w2.model.seed != 36 or w2.current_path is None, "same serial must not re-trigger"
    ib.state = dict(ib.state, serial=8)          # Blender imported something
    w2._poll_blender()
    assert w2.current_path == fgm_io.SAMPLE_FGM, w2.current_path
    assert w2.model.seed == 36, w2.model.seed                     # settings followed across
    assert w2.object_name_edit.text() == "baryonyx_female_ob0_L0"  # and the target object
    # unticking the follow box stops it
    w2.follow_check.setChecked(False)
    ib.state = dict(ib.state, serial=9, path="Z:/nonexistent.fgm")
    w2._poll_blender()
    assert w2.current_path == fgm_io.SAMPLE_FGM, "unticked follow must not load"
    # a bridge with no last_import (older listener) must not raise
    assert VariantEditorWindow(bridge=fake)._safe_last_import() is None

    # -- guard rails: no object name, and no bridge
    window.object_name_edit.setText("")
    assert ctl.do_build() is False
    assert EditorController(VariantEditorWindow(bridge=None), None).do_build() is False

    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        sys.exit(main())
