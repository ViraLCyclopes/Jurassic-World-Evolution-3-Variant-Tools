"""What state is a harvest pass in? Pure logic -- no Qt, no side effects, only reads.

WHY THIS IS SEPARATE FROM THE GUI. The harvesting workflow spans a game session: install a sweep,
quit the tool, play for two hours, come back. Nothing about "where am I" may live in app memory, so
every field here is derived from disk on each call and the GUI is a pure renderer of it. That is
also what makes the whole thing testable without opening a window.
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _hpaths  # noqa: E402  (puts the package and its vendor/ folder on sys.path)

import coeff_store  # noqa: E402
import jwe3_config  # noqa: E402

UI_STATE = "harvest_ui.json"

#: The game's process name, for the "close the game first" guard.
GAME_PROCESS = "JWE3.exe"


class Action(object):
    """What the user should do next. Strings so they survive JSON and print readably."""
    CONFIGURE = "CONFIGURE"
    PLAN_SWEEP = "PLAN_SWEEP"
    SPAWN_AND_CAPTURE = "SPAWN_AND_CAPTURE"
    HARVEST = "HARVEST"
    RESTORE = "RESTORE"
    DONE = "DONE"


HarvestState = collections.namedtuple("HarvestState", [
    "game_modified", "backup_count", "manifest_hosts", "swept_seeds",
    "coverage", "missing_seeds", "captures", "new_captures", "blockers", "next_action",
    "first_run",
])


def _reset_caches():
    """Drop cached tables so a caller that moved JWE3_CONFIG_DIR sees the new folder."""
    coeff_store.rows(force=True)


def _ui_state_path():
    return os.path.join(_hpaths.work_dir(), UI_STATE)


def _read_ui_state():
    try:
        with open(_ui_state_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def has_stamp():
    """True once a harvest has been recorded through this tool."""
    return "last_harvest" in _read_ui_state()


def last_harvest_stamp():
    """When harvest_blocks last ran, as an mtime.

    FIRST RUN RETURNS A BASELINE, NOT 0. Someone who has been harvesting from the command line
    already has a capture folder full of .rdc files -- 32 of them, in the case this was found on --
    and every one of those was already harvested. Returning 0 marked all of them "new" and sent the
    user straight to Harvest on first open, when they expected to be walked from the start.

    So with no stamp recorded, the baseline is the NEWEST existing capture: nothing on disk is
    claimed as new, and anything that arrives later is. Re-harvesting is idempotent (`merge` skips
    rows it already has), so if the assumption is wrong the cost is one extra scan -- run
    `python harvest_blocks.py`, or Harvest after taking a new capture.

    This reads only; it never writes a stamp, so `detect()` stays side-effect free.
    """
    state = _read_ui_state()
    if "last_harvest" in state:
        try:
            return float(state["last_harvest"])
        except (TypeError, ValueError):
            return 0.0
    caps = _captures()
    return max((os.path.getmtime(c) for c in caps), default=0.0)


def set_last_harvest_stamp(ts=None):
    """Record when a harvest ran. This is the ONLY thing the UI persists, and it is not
    safety-critical -- losing it just means captures look new again."""
    import time
    ts = time.time() if ts is None else float(ts)
    d = _read_ui_state()
    d["last_harvest"] = ts
    with open(_ui_state_path(), "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)
    return ts


def backups():
    """Every backed-up OVL. THE authoritative signal that the game is modified.

    Deliberately NOT the manifest: gen_seedsweep_v2.stage_females() appended its hosts to the
    in-memory manifest without writing it back, so the manifest can list 53 Males while backups
    exist for far more -- restore_seedsweep_all.py exists because of exactly that. A UI that
    trusted the manifest would tell someone their game was clean when it was not.
    """
    root = _hpaths.backup_root()
    out = []
    for dirpath, _dirs, files in os.walk(root):
        out.extend(os.path.join(dirpath, f) for f in files if f.lower().endswith(".ovl"))
    return sorted(out)


def modified_files():
    """Backups whose LIVE file still DIFFERS. This is the real "is the game modified" answer.

    NOT "do backups exist". `restore_seedsweep_all.py` copies each backup back and LEAVES IT ON
    DISK, so the backup folder survives a successful restore forever. Treating existence as
    "modified" pinned the banner on after a restore AND made `install_blockers()` refuse every
    future sweep -- reported by a user whose game was verifiably clean (14 backups, 14 identical)
    while the UI insisted it was modified.

    Reuses `restore_seedsweep_all.plan()` rather than reimplementing it: that function already
    knows how to find a live path for a backup the manifest never recorded, which is the hard part.

    Falls back to "every backup" if the comparison cannot run (no manifest, game folder not found).
    Unknown must never read as clean -- calling a modified game clean is what lets a second sweep
    install over it and destroy the restore path.
    """
    if not backups():
        return []
    try:
        import restore_seedsweep_all as rs
        # `rs.MANIFEST` is a module-level constant bound at FIRST import, so it goes stale the
        # moment the config folder changes -- which is every test, and any session where the user
        # repoints setup_gui at another install. Rebind before asking.
        rs.MANIFEST = _hpaths.manifest()
        rows = rs.plan()
    except BaseException:
        # plan() raises SystemExit when the manifest or game folder is missing, and SystemExit is
        # not an Exception -- catch BaseException or this takes the process with it.
        return backups()
    return [live or bak for bak, live, same in rows if not same]


def _manifest_hosts():
    """Species named in the manifest. Informational ONLY -- see backups()."""
    try:
        with open(_hpaths.manifest(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return [h.get("species") for h in data.get("hosts", []) if h.get("species")]


def _swept_seeds():
    try:
        with open(_hpaths.seed_table(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("seeds", [])
    return [int(s) for s in data if str(s).lstrip("-").isdigit()]


def captures_dir():
    return jwe3_config.get("captures_dir")


def _captures():
    d = captures_dir()
    if not d or not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".rdc"))


def game_is_running(process_name=GAME_PROCESS):
    """True if the game appears to be running. Windows-only; False elsewhere.

    `gen_seedsweep_v2` already fails on a locked file, but it fails PARTWAY THROUGH and leaves a
    half-installed sweep behind. Checking first turns that into a clean refusal.
    """
    if sys.platform != "win32":
        return False
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % process_name],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return False        # cannot tell -> do not block; the install itself still fails safely
    return process_name.lower() in (out or "").lower()


def install_blockers():
    """Why a sweep must NOT be installed right now. Empty list means it is safe to install."""
    reasons = []
    if modified_files():
        # Deliberately modified_files(), not backups(): a restored game keeps its backup folder,
        # and blocking on mere existence made every sweep after the first one impossible.
        reasons.append(
            "Your game files are ALREADY MODIFIED. Installing another sweep would back up the "
            "modified files as if they were the originals, permanently destroying the restore "
            "path. Restore first.")
    if game_is_running():
        reasons.append(
            "Jurassic World Evolution 3 is running. Close it before installing a sweep, or the "
            "install will fail halfway and leave the game partly modified.")
    if not jwe3_config.get("game_dir"):
        reasons.append("The game folder is not set. Run setup_gui.py, or set JWE3_GAME_DIR.")
    return reasons


def detect():
    """Read everything off disk and decide what the user should do next."""
    bak = backups()
    # "backups exist" is NOT "modified" -- restore leaves them on disk. See modified_files().
    modified = bool(modified_files())
    caps = _captures()
    stamp = last_harvest_stamp()
    new_caps = [c for c in caps if os.path.getmtime(c) > stamp]

    # coverage() returns FOUR values -- (have, total, n_rows, n_user) -- not two.
    have, total, _n_rows, _n_user = coeff_store.coverage()
    missing = coeff_store.missing_seeds()

    blockers = []
    if not jwe3_config.get("game_dir"):
        blockers.append("The game folder is not set. Run setup_gui.py, or set JWE3_GAME_DIR.")
    if not captures_dir():
        blockers.append("The RenderDoc capture folder is not set.")

    if new_caps:
        # Unharvested captures come FIRST, whether or not the game is currently modified.
        # Harvesting only reads .rdc files and writes the coefficient table -- it never touches a
        # game file -- so there is no reason to make someone re-install a sweep before banking
        # captures they already have. Found on real data: a clean game with 32 unharvested
        # captures was being sent to PLAN_SWEEP, silently leaving those blocks on the floor.
        action = Action.HARVEST
    elif modified and not caps and _swept_seeds():
        # A sweep is staged and nothing has been captured yet: go and spawn them.
        action = Action.SPAWN_AND_CAPTURE
    elif modified:
        # Captures exist and are all harvested (or none are coming). Either way the game is
        # modified and restoring is the way out. Advisory, not forced -- the user may deliberately
        # run another capture session against the same installed sweep.
        action = Action.RESTORE
    elif blockers:
        action = Action.CONFIGURE
    elif not missing:
        action = Action.DONE
    else:
        action = Action.PLAN_SWEEP

    return HarvestState(
        game_modified=modified,
        backup_count=len(bak),
        manifest_hosts=_manifest_hosts(),
        swept_seeds=_swept_seeds(),
        coverage=(have, total),
        missing_seeds=missing,
        captures=caps,
        new_captures=new_caps,
        blockers=blockers,
        next_action=action,
        first_run=not has_stamp(),
    )


def selftest():
    """Build a temp config folder for each state and assert what detect() reports."""
    import shutil
    import tempfile

    def fresh():
        """A temp JWE3_CONFIG_DIR with nothing in it. Returns (tempdir, work_dir).

        `_hpaths._resolve()` MIGRATES a legacy copy of each file out of the research folder EVERY
        time that path is asked for and the file is missing -- so a brand-new work dir is not empty,
        and deleting the migrated copy does not help because the next call brings it straight back
        (the first run of this test picked up the real 53-host sweep manifest). Writing an explicit
        empty file is what actually neutralises it.
        """
        td = tempfile.mkdtemp()
        os.environ["JWE3_CONFIG_DIR"] = td
        _reset_caches()
        work = _hpaths.work_dir()
        for p in (_hpaths.manifest(), _hpaths.seed_table()):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{}")
        with open(_hpaths.spawn_list(), "w", encoding="utf-8") as fh:
            fh.write("")
        return td, work

    old_cfg = os.environ.get("JWE3_CONFIG_DIR")
    old_caps = os.environ.get("JWE3_CAPTURES_DIR")
    try:
        # --- backups present but NO manifest -> STILL modified.
        # This is the case the whole design exists to catch: gen_seedsweep_v2.stage_females()
        # appended hosts to the IN-MEMORY manifest without writing it back, so the manifest can be
        # absent or short while the game is very much modified. restore_seedsweep_all.py exists
        # because of exactly that. Never weaken this to read the manifest.
        td, work = fresh()
        backups = os.path.join(work, "backups")
        os.makedirs(backups)
        with open(os.path.join(backups, "Albertosaurus_Male.ovl"), "wb") as fh:
            fh.write(b"x")
        st = detect()
        assert st.game_modified is True, "backups present must mean modified"
        assert st.backup_count == 1, st.backup_count
        assert st.manifest_hosts == [], st.manifest_hosts
        shutil.rmtree(td, ignore_errors=True)

        # --- A RESTORED GAME MUST NOT REPORT MODIFIED, even though the backups are still there.
        # restore_seedsweep_all.py copies backups back and LEAVES THEM ON DISK. Treating existence
        # as "modified" stuck the banner on permanently and blocked every later sweep. Reported by
        # a user whose game was clean (14 backups, all identical) while the UI said otherwise.
        td, work = fresh()
        bdir = os.path.join(work, "backups")
        os.makedirs(bdir)
        live_dir = os.path.join(td, "ovldata", "Content0", "Dinosaurs", "Land", "Testosaurus", "Male")
        os.makedirs(live_dir)
        live = os.path.join(live_dir, "Testosaurus_Male.ovl")
        for p in (live, os.path.join(bdir, "Testosaurus_Male.ovl")):
            with open(p, "wb") as fh:
                fh.write(b"ORIGINAL BYTES")
        with open(_hpaths.manifest(), "w", encoding="utf-8") as fh:
            json.dump({"backup": bdir,
                       "hosts": [{"species": "Testosaurus", "realm": "Land", "ovl": live}]}, fh)
        st = detect()
        assert st.backup_count == 1, st.backup_count
        assert st.game_modified is False, "identical backup and live file means RESTORED"
        assert not any("already modified" in r.lower() for r in install_blockers()), install_blockers()

        # ...and once the live file really differs, it must report modified again
        with open(live, "wb") as fh:
            fh.write(b"SWEPT BYTES!!")
        st = detect()
        assert st.game_modified is True, "differing live file means modified"
        assert any("already modified" in r.lower() for r in install_blockers()), install_blockers()
        shutil.rmtree(td, ignore_errors=True)

        # --- manifest present, NO backups -> not modified
        td, work = fresh()
        with open(_hpaths.manifest(), "w", encoding="utf-8") as fh:
            json.dump({"hosts": [{"species": "Albertosaurus"}]}, fh)
        st = detect()
        assert st.game_modified is False, "no backups must mean not modified"
        assert st.manifest_hosts == ["Albertosaurus"], st.manifest_hosts
        shutil.rmtree(td, ignore_errors=True)

        # --- a capture NEWER than the stamp routes to HARVEST
        td, work = fresh()
        caps = os.path.join(td, "caps")
        os.makedirs(caps)
        os.environ["JWE3_CAPTURES_DIR"] = caps
        os.makedirs(os.path.join(work, "backups"))
        with open(os.path.join(work, "backups", "h.ovl"), "wb") as fh:
            fh.write(b"x")
        rdc = os.path.join(caps, "cap1.rdc")
        with open(rdc, "wb") as fh:
            fh.write(b"x")
        set_last_harvest_stamp(os.path.getmtime(rdc) - 10)
        st = detect()
        assert st.new_captures and st.new_captures[0].endswith("cap1.rdc"), st.new_captures
        assert st.next_action == Action.HARVEST, st.next_action

        # --- the SAME capture, already harvested -> the way out is RESTORE, not capture again.
        # (Restore is advisory, not forced: the user may deliberately run another capture session
        # against the same installed sweep, which is why restore is manual.)
        set_last_harvest_stamp(os.path.getmtime(rdc) + 10)
        st = detect()
        assert st.new_captures == [], st.new_captures
        assert st.game_modified is True
        assert st.next_action == Action.RESTORE, st.next_action
        shutil.rmtree(td, ignore_errors=True)

        # --- a sweep staged with NO captures yet -> go and spawn them
        td, work = fresh()
        caps = os.path.join(td, "caps")
        os.makedirs(caps)
        os.environ["JWE3_CAPTURES_DIR"] = caps
        os.makedirs(os.path.join(work, "backups"))
        with open(os.path.join(work, "backups", "h.ovl"), "wb") as fh:
            fh.write(b"x")
        with open(_hpaths.seed_table(), "w", encoding="utf-8") as fh:
            json.dump([3, 4, 5], fh)
        st = detect()
        assert st.swept_seeds == [3, 4, 5], st.swept_seeds
        assert st.next_action == Action.SPAWN_AND_CAPTURE, st.next_action
        shutil.rmtree(td, ignore_errors=True)

        # --- captures NEWER than the stamp on a CLEAN game must still route to HARVEST.
        # Harvesting is read-only with respect to game files, so there is no reason to require a
        # sweep first. (Real data caught this: a clean install with 32 captures was sent to
        # PLAN_SWEEP.)
        td, work = fresh()
        caps = os.path.join(td, "caps")
        os.makedirs(caps)
        os.environ["JWE3_CAPTURES_DIR"] = caps
        rdc = os.path.join(caps, "old.rdc")
        with open(rdc, "wb") as fh:
            fh.write(b"x")
        set_last_harvest_stamp(os.path.getmtime(rdc) - 10)
        st = detect()
        assert st.game_modified is False, st.game_modified
        assert st.first_run is False, st.first_run
        assert st.next_action == Action.HARVEST, st.next_action
        shutil.rmtree(td, ignore_errors=True)

        # --- FIRST RUN with captures already on disk must NOT claim they are new.
        # Someone arriving from the command line already has a full capture folder, all of it
        # harvested. Calling those "new" sent them straight to Harvest on first open instead of
        # walking them from the start.
        td, work = fresh()
        caps = os.path.join(td, "caps")
        os.makedirs(caps)
        os.environ["JWE3_CAPTURES_DIR"] = caps
        for name in ("a.rdc", "b.rdc"):
            with open(os.path.join(caps, name), "wb") as fh:
                fh.write(b"x")
        st = detect()
        assert st.first_run is True, st.first_run
        assert len(st.captures) == 2, st.captures
        assert st.new_captures == [], st.new_captures
        assert st.next_action == Action.PLAN_SWEEP, st.next_action
        shutil.rmtree(td, ignore_errors=True)

        # --- installing a sweep over an ALREADY-MODIFIED game must be refused.
        # This is the one action here that does lasting damage: gen_seedsweep_v2 would back up the
        # already-modified OVLs as if they were pristine, permanently destroying the restore path.
        td, work = fresh()
        os.makedirs(os.path.join(work, "backups"))
        with open(os.path.join(work, "backups", "h.ovl"), "wb") as fh:
            fh.write(b"x")
        reasons = install_blockers()
        assert any("already modified" in r.lower() for r in reasons), reasons
        shutil.rmtree(td, ignore_errors=True)

        # --- with no backups, "already modified" must NOT be among the reasons.
        # (Other blockers may legitimately apply on a machine with no game installed, so assert on
        # the specific reason rather than an empty list.)
        td, work = fresh()
        reasons = install_blockers()
        assert not any("already modified" in r.lower() for r in reasons), reasons
        shutil.rmtree(td, ignore_errors=True)
    finally:
        for name, val in (("JWE3_CONFIG_DIR", old_cfg), ("JWE3_CAPTURES_DIR", old_caps)):
            if val is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = val
        _reset_caches()
    print("selftest ok")


if __name__ == "__main__":
    selftest()
