"""Run the harvesting CLI scripts as SUBPROCESSES and stream their output.

WHY NOT IMPORT THEM. Every script in this folder raises SystemExit as an ordinary user-facing
message -- "set JWE3_DINO_ROOTS", "the game is still running", "no matching .rdc captures". Inside
a GUI process SystemExit terminates the application, with no traceback and no dialog. The identical
pattern killed Blender twice through the Python bridge on 2026-08-01: `except Exception` does not
catch it, because SystemExit inherits BaseException.

Running them out-of-process also keeps the CLI authoritative, so the GUI and the command line can
never drift apart.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: Short name -> filename, so callers say run("harvest") rather than repeating paths.
SCRIPTS = {
    "plan": "gen_seedsweep_v2.py",
    "spawn": "spawn_list.py",
    "harvest": "harvest_blocks.py",
    "audit": "audit_captures.py",
    "restore": "restore_seedsweep_all.py",
}


def script_path(name):
    """Absolute path for a short name in SCRIPTS; any other value is passed through unchanged."""
    return os.path.join(HERE, SCRIPTS[name]) if name in SCRIPTS else name


def run(script, args=(), on_line=None):
    """Run `script` with `args`. Returns (returncode, combined stdout+stderr).

    `on_line` is called with each line as it arrives, for a live log pane. stderr is merged into
    stdout so the log reads in the order the script actually produced it -- these scripts interleave
    progress and warnings, and separating the streams reorders them confusingly.
    """
    cmd = [sys.executable, script_path(script)] + [str(a) for a in args]
    proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, encoding="utf-8", errors="replace")
    chunks = []
    for line in proc.stdout:
        chunks.append(line)
        if on_line is not None:
            on_line(line.rstrip("\r\n"))
    proc.stdout.close()
    return proc.wait(), "".join(chunks)


def selftest():
    import tempfile
    import textwrap

    # A script that prints, then exits non-zero via SystemExit -- exactly how every script in this
    # folder reports a user error ("set JWE3_DINO_ROOTS", "no matching .rdc captures"). Importing
    # one of those into a GUI process TERMINATES it, with no traceback and no dialog. Run as a
    # subprocess it must simply come back as a return code and some text.
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "boom.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent("""
                print("working")
                raise SystemExit("no capture folder at C:/nope")
            """))
        lines = []
        code, out = run(p, on_line=lines.append)
        assert code != 0, code
        assert "working" in out, out
        assert "no capture folder" in out, out
        assert any("working" in l for l in lines), lines

        # a clean exit comes back as 0
        ok = os.path.join(td, "fine.py")
        with open(ok, "w", encoding="utf-8") as fh:
            fh.write("print('all good')\n")
        code, out = run(ok)
        assert code == 0, (code, out)
        assert "all good" in out, out

    # every short name must point at a file that actually exists
    for name in SCRIPTS:
        assert os.path.isfile(script_path(name)), (name, script_path(name))
    print("selftest ok")


if __name__ == "__main__":
    selftest()
