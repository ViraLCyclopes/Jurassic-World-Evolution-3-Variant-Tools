"""Restore EVERY seed-sweep host OVL, including the ones the manifest never recorded.

WHY THIS EXISTS. `gen_seedsweep_v2.py --restore` walks `seedsweep_manifest.json`, but
`stage_females()` appended its hosts to the in-memory manifest without writing it back to disk. So
after a Male sweep + a Female staging pass, the manifest holds only the 53 Males while the backup
folder holds 82 files -- 53 Male + 29 Female. Running --restore alone silently leaves 29 modified
Female OVLs in the game install, which is exactly the kind of half-restore you only notice much
later.

This script treats the BACKUP FOLDER as the source of truth instead: every `<Species>_<Sex>.ovl` in
it is restored to its live path. The live path needs the species' realm, which is read from the
manifest's Male rows (same species, same realm) and otherwise found by scanning the dino dir.

    python restore_seedsweep_all.py              # dry run: print exactly what WOULD be copied
    python restore_seedsweep_all.py --apply      # do it (THE GAME MUST BE CLOSED)

Close JWE3 first. cobra-tools holds the .ovl/.aux open while the game runs and the copy fails with
PermissionError partway through, leaving a mixed set.
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
import _hpaths  # noqa: E402
# The manifest is the ONLY record of which OVLs were modified and where their backups are, so it
# lives in your config folder, not inside the install where an update would delete it.
MANIFEST = _hpaths.manifest()


def _dino_dir(manifest):
    """The game's dinosaur folder, inferred from any live path the manifest recorded."""
    for h in manifest["hosts"]:
        # ...\ovldata\Dinosaurs\<Realm>\<Species>\<Sex>\<Species>_<Sex>.ovl  -> up 4
        d = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(h["ovl"]))))
        if os.path.isdir(d):
            return d
    raise SystemExit("cannot locate the game dinosaur folder from the manifest")


def plan():
    """[(backup_path, live_path, already_identical)] for every backup on disk."""
    man = json.load(open(MANIFEST))
    backup_dir = man["backup"]
    if not os.path.isdir(backup_dir):
        raise SystemExit("backup folder is gone: %s" % backup_dir)

    known = {}                      # Species -> realm, from the rows the manifest did record
    live_by_name = {}               # <Species>_<Sex>.ovl -> live path
    for h in man["hosts"]:
        known[h["species"]] = h["realm"]
        live_by_name[os.path.basename(h["ovl"])] = h["ovl"]

    dino_dir = _dino_dir(man)
    rows = []
    for name in sorted(os.listdir(backup_dir)):
        if not name.lower().endswith(".ovl"):
            continue
        bak = os.path.join(backup_dir, name)
        live = live_by_name.get(name)
        if live is None:
            # not in the manifest (the Females): rebuild the path from the species' realm
            species, sex = name[:-4].rsplit("_", 1)
            realm = known.get(species)
            candidates = ([os.path.join(dino_dir, realm, species, sex, name)] if realm else
                          [os.path.join(dino_dir, r, species, sex, name)
                           for r in sorted(os.listdir(dino_dir))
                           if os.path.isdir(os.path.join(dino_dir, r))])
            live = next((c for c in candidates if os.path.isfile(c)), None)
        if live is None:
            rows.append((bak, None, False))
            continue
        same = (os.path.getsize(bak) == os.path.getsize(live)
                and open(bak, "rb").read() == open(live, "rb").read())
        rows.append((bak, live, same))
    return rows


def main(apply_it):
    rows = plan()
    missing = [b for b, l, _ in rows if l is None]
    modified = [(b, l) for b, l, same in rows if l is not None and not same]
    clean = [1 for _b, l, same in rows if l is not None and same]

    print("backups found : %d" % len(rows))
    print("already clean : %d" % len(clean))
    print("still modified: %d" % len(modified))
    if missing:
        print("NO LIVE PATH  : %d  %s" % (len(missing), [os.path.basename(m) for m in missing]))

    for _bak, live in modified:
        print("  restore <- %s" % os.path.basename(live))
    if not apply_it:
        print("\nDRY RUN. Nothing was written. Re-run with --apply (with JWE3 CLOSED) to restore.")
        return 0

    done = 0
    for bak, live in modified:
        try:
            shutil.copy2(bak, live)
            done += 1
        except PermissionError:
            print("\nPERMISSION DENIED on %s -- JWE3 is still running. Close it and re-run; the %d "
                  "already restored are fine." % (os.path.basename(live), done))
            return 1
    print("\nrestored %d OVLs" % done)
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
