"""Where the harvesting tools read and write. Nothing outside the software's own folder.

Two kinds of path, and the distinction matters:

    SHIPPED (read-only, inside the package)   ../data/seedsweep_fingerprints.json
    YOURS   (written at run time, per user)   the config folder -- manifests, backups, spawn list

Run-time files must NOT go inside the install: it may be read-only, and reinstalling or updating the
tool would throw away your sweep manifest -- which is the only record of which OVLs were modified
and where their backups are. Losing it means losing the ability to restore your game.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)                          # the VariantEditor package
DATA = os.path.join(PKG, "data")

if PKG not in sys.path:
    sys.path.insert(0, PKG)
if os.path.join(PKG, "vendor") not in sys.path:
    sys.path.insert(0, os.path.join(PKG, "vendor"))


def work_dir():
    """Per-user, writable, survives reinstalling the tool."""
    import jwe3_config
    d = os.path.join(jwe3_config.config_dir(), "harvesting")
    os.makedirs(d, exist_ok=True)
    return d


def shipped(name):
    return os.path.join(DATA, name)


FINGERPRINTS = shipped("seedsweep_fingerprints.json")     # ships: a research-derived input table


#: Where these files used to live, before run-time state moved out of the install. A sweep in
#: progress must never be stranded by an upgrade: the manifest is the only record of which OVLs
#: were modified and where their backups are, so losing track of it means losing the ability to
#: restore the game. Anything found here is migrated on first use.
LEGACY_DIRS = (os.path.dirname(PKG), HERE, PKG)


def _resolve(name):
    """Path in the work folder, migrating a legacy copy across the first time it is needed."""
    import shutil
    current = os.path.join(work_dir(), name)
    if not os.path.exists(current):
        for old in LEGACY_DIRS:
            candidate = os.path.join(old, name)
            if os.path.isfile(candidate):
                shutil.copy2(candidate, current)
                print("migrated %s\n      -> %s" % (candidate, current))
                break
    return current


def manifest():
    return _resolve("seedsweep_manifest.json")


def seed_table():
    return _resolve("seedsweep_seeds.json")


def spawn_list():
    return _resolve("SEEDSWEEP_SPAWN_LIST.txt")


def backup_root():
    d = os.path.join(work_dir(), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def coeff_out():
    """Harvested rows go straight into the user's own coefficient table, so a capture is picked up
    by the editor with no extra step."""
    import coeff_store
    return coeff_store.user_table()


def selftest():
    assert os.path.isfile(FINGERPRINTS), FINGERPRINTS
    for p in (manifest(), seed_table(), spawn_list(), coeff_out()):
        parent = os.path.dirname(p)
        assert os.path.isdir(parent), parent
        # run-time files must live OUTSIDE the install, or an update destroys them
        assert not os.path.normcase(os.path.abspath(p)).startswith(
            os.path.normcase(os.path.abspath(PKG))), "run-time file inside the install: " + p
    assert os.path.isdir(backup_root())
    # shipped data must live INSIDE the install
    assert os.path.normcase(os.path.abspath(FINGERPRINTS)).startswith(
        os.path.normcase(os.path.abspath(PKG)))
    print("selftest ok")


if __name__ == "__main__":
    selftest()
