"""Which dinosaurs are carrying a staged seed -- i.e. what to spawn before capturing.

A sweep rewrites ~53 different species' OVLs, and the capture only sees what is drawn on screen, so
you have to spawn exactly those species. `gen_seedsweep_v2.py` prints the mapping as it stages, but
that scrolls away; this reads it back out of the manifest at any time, and marks which seeds are
already harvested so you can skip the ones that would add nothing.

    python spawn_list.py                # print the list
    python spawn_list.py --write        # also write SEEDSWEEP_SPAWN_LIST.txt beside the manifest
    python spawn_list.py --todo         # only the seeds not yet harvested

Run:  python spawn_list.py --selftest   -> selftest ok
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
import _hpaths  # noqa: E402  (package paths; puts the package + vendor/ on sys.path)

MANIFEST = _hpaths.manifest()
OUT = _hpaths.spawn_list()

HEADER = ("SEED SWEEP - spawn ONE of each, then capture. Any variant of the slot works.\n"
          "Seeds marked [have] are already harvested; spawning them adds nothing.\n"
          "==========================================================\n")


def harvested_seeds():
    """Seeds already in the coefficient table (layered: bundled + your own captures)."""
    try:
        import coeff_store
        return {int(r["seed"]) for r in coeff_store.rows().values() if "seed" in r}
    except Exception:
        return set()


def entries(manifest_path=MANIFEST):
    """[(seed, species, sex, realm, already_harvested)] sorted by seed."""
    with open(manifest_path, "r", encoding="utf-8") as fh:
        man = json.load(fh)
    have = harvested_seeds()
    rows = [(int(h["seed"]), h["species"], h.get("sex", ""), h.get("realm", ""),
             int(h["seed"]) in have)
            for h in man.get("hosts", [])]
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def render(rows, todo_only=False):
    lines = [HEADER]
    shown = 0
    for seed, species, sex, realm, have in rows:
        if todo_only and have:
            continue
        shown += 1
        lines.append("  seed %3d  ->  %s (%s)%s%s"
                     % (seed, species, sex,
                        "  [%s]" % realm if realm else "",
                        "   [have]" if have else ""))
    n_have = sum(1 for r in rows if r[4])
    lines.append("")
    lines.append("  %d staged, %d already harvested, %d worth capturing"
                 % (len(rows), n_have, len(rows) - n_have))
    if todo_only:
        lines.append("  (showing %d not-yet-harvested)" % shown)
    return "\n".join(lines)


def write(rows, dest=OUT):
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(render(rows))
        fh.write("\n")
    return dest


def selftest():
    import tempfile
    fake = {"hosts": [
        {"seed": 7, "species": "Troodon", "sex": "Male", "realm": "Land"},
        {"seed": 1, "species": "Homalocephale", "sex": "Male", "realm": "Land"},
    ]}
    p = os.path.join(tempfile.mkdtemp(), "m.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(fake, fh)

    rows = entries(p)
    assert [r[0] for r in rows] == [1, 7], rows          # sorted by seed
    assert rows[0][1] == "Homalocephale"
    txt = render(rows)
    assert "Troodon (Male)" in txt and "seed   1" in txt, txt
    assert "2 staged" in txt, txt

    dest = os.path.join(os.path.dirname(p), "spawn.txt")
    write(rows, dest)
    assert "Troodon" in open(dest, encoding="utf-8").read()

    # --todo hides harvested seeds; force one to count as harvested
    marked = [(1, "Homalocephale", "Male", "Land", True), (7, "Troodon", "Male", "Land", False)]
    todo = render(marked, todo_only=True)
    assert "Troodon" in todo and "Homalocephale" not in todo, todo
    assert "[have]" in render(marked)

    # the real manifest must parse, if one exists
    if os.path.isfile(MANIFEST):
        real = entries()
        assert real and all(isinstance(r[0], int) for r in real)
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif not os.path.isfile(MANIFEST):
        sys.exit("no sweep manifest at %s -- run gen_seedsweep_v2.py first" % MANIFEST)
    else:
        rows = entries()
        print(render(rows, todo_only="--todo" in sys.argv))
        if "--write" in sys.argv:
            print("\nwritten -> %s" % write(rows))
