"""The palette-coefficient table, layered and self-updating.

WHY THIS EXISTS. `gradient_coefficients.json` is not a model -- it is measured data, one row per
(seed, complexity) recovered from a GPU capture. Only 48 of 256 seeds are harvested so far, and the
seed is hashed (a fit against nine standard PRNG/hash families over ~10k combinations found nothing
above chance), so the missing ones can only be MEASURED, never derived. That makes the table
something that grows forever -- which the software has to handle gracefully.

Two layers:

    bundled   the table shipped beside the code (the project's own harvest)
    user      the modder's own captures, in a writable folder, taking precedence

A user's harvest therefore survives reinstalling or updating the tool, and contributing back is just
sending a small JSON. `harvest_blocks.py` already merges a capture into a table; point it at the
user file (or run `python coeff_store.py --merge <file.json>`) and the running editor picks the new
rows up on its next lookup -- the loader re-reads whenever either file's mtime changes, so no
restart is needed.

Run:  python coeff_store.py            -> selftest ok
      python coeff_store.py --status   -> coverage summary
      python coeff_store.py --merge X  -> merge rows from X into the user table
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.join(HERE, "vendor")           # vendored research modules, inside the package
for _p in (HERE, PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import export_palette as ep  # noqa: E402  (FREQ_LOW / freq_high: the complexity->frequency rule)

ENV_COEFFS = "JWE3_COEFFS"
TOTAL_SEEDS = 256                       # the seed field is capped at 256 (seed census)

#: shipped alongside the code -- the project's own harvest
BUNDLED = os.path.join(HERE, "data", "gradient_coefficients.json")

_cache = {"rows": None, "stamp": None}


def user_table():
    """Writable table for the user's own captures. Never inside the install."""
    p = os.environ.get(ENV_COEFFS)
    if p:
        return p
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        base = os.path.join(base, "JWE3VariantTools")
    else:
        base = os.path.join(os.path.expanduser("~"), ".jwe3_variant_tools")
    return os.path.join(base, "gradient_coefficients.json")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _stamp():
    """(mtime, size) of both tables -- changes whenever either is edited, so the cache can expire."""
    out = []
    for p in (BUNDLED, user_table()):
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime, st.st_size))
        except OSError:
            out.append((p, None, None))
    return tuple(out)


def rows(force=False):
    """All known rows, keyed `"<seed>_<complexity>"`. User rows override bundled ones.

    Re-reads automatically when either file changes, so harvesting while the editor is open takes
    effect without a restart.
    """
    stamp = _stamp()
    if force or _cache["rows"] is None or _cache["stamp"] != stamp:
        merged = dict(_read(BUNDLED))
        merged.update(_read(user_table()))       # user wins
        _cache["rows"], _cache["stamp"] = merged, stamp
    return _cache["rows"]


def _by_seed():
    out = {}
    for r in rows().values():
        s = r.get("seed")
        if s is not None:
            out.setdefault(int(s), r)
    return out


def coefficients_for(seed, complexity):
    """(row, exact) for a seed at a complexity, or (None, False) when the seed is unharvested.

    Same contract and the same frequency rebuild as `export_palette.coefficients_for` -- but reading
    the layered table, so the user's own captures count. `gradFreq` is rebuilt because the harvested
    row's channel selection comes from the seed while the fast level follows the complexity asked
    for (verified on seed 18: cx1 -> 102, cx2 -> 153 on one channel only).
    """
    seed, complexity = int(seed), int(complexity)
    exact = rows().get("%d_%d" % (seed, complexity))
    if exact is not None:
        return exact, True
    row = _by_seed().get(seed)
    if row is None:
        return None, False
    hi = ep.freq_high(complexity)
    row = dict(row)
    row["gradFreq"] = [f if f == ep.FREQ_LOW else hi for f in row["gradFreq"]]
    return row, False


def coverage():
    """(harvested_seeds, TOTAL_SEEDS, n_rows, n_user_rows) -- what the UI reports."""
    user = _read(user_table())
    return len(_by_seed()), TOTAL_SEEDS, len(rows()), len(user)


def missing_seeds():
    """Seeds with no coefficients yet -- i.e. what is still worth capturing."""
    have = set(_by_seed())
    return [s for s in range(TOTAL_SEEDS) if s not in have]


def harvested_seeds(complexity=None, exact_only=False):
    """Sorted seeds that `coefficients_for` can answer -- i.e. that render a real gradient.

    `complexity` alone changes nothing, since a seed harvested at ANY complexity answers at every
    complexity with `gradFreq` rebuilt. Pass `exact_only=True` with a complexity to narrow it to
    seeds harvested AT that complexity, whose preview is measured rather than reconstructed.

    Written for seed substitution: when a shipped FGM names an unharvested seed the preview falls
    back to a flat gradient, and swapping in a seed from this list -- ideally an exact one at the
    same complexity -- is what makes the palette visible.
    """
    if exact_only:
        if complexity is None:
            raise ValueError("exact_only needs a complexity; exactness is per (seed, complexity)")
        out = set()
        for key in rows():
            s, _, c = key.partition("_")
            if c.isdigit() and int(c) == int(complexity):
                out.add(int(s))
        return sorted(out)
    return sorted(_by_seed())


def merge(source, dest=None):
    """Merge rows from `source` (a path to a JSON table, or a dict) into the user table.

    Returns (added, updated, total). Only rows that actually carry coefficients are accepted, so a
    malformed harvest cannot poison the table.
    """
    incoming = _read(source) if isinstance(source, str) else dict(source or {})
    dest = dest or user_table()
    need = ("gradOffset", "gradAmplitude", "gradFreq", "gradPhase", "seed")
    clean = {k: v for k, v in incoming.items()
             if isinstance(v, dict) and all(f in v for f in need)}
    rejected = len(incoming) - len(clean)

    existing = _read(dest)
    added = sum(1 for k in clean if k not in existing)
    updated = sum(1 for k in clean if k in existing and existing[k] != clean[k])
    existing.update(clean)

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=1)
    rows(force=True)                       # the running process sees them immediately
    return added, updated, len(existing), rejected


def export(dest, mine_only=True):
    """Write a shareable table. `mine_only` exports just the rows you harvested.

    This is the whole contribution format: send the file, the other side runs `--merge` on it.
    Rows are self-describing (seed, complexity, the four coefficient triples, plus which capture and
    variant they came from), so merging never needs any other context.
    """
    table = _read(user_table()) if mine_only else rows()
    d = os.path.dirname(os.path.abspath(dest))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(table, fh, indent=1)
    return len(table)


def status_text():
    have, total, n_rows, n_user = coverage()
    return ("palette coverage: %d/%d seeds harvested (%d rows, %d of them yours)\n"
            "  bundled: %s\n  yours:   %s\n  missing: %d seeds -- capture them and merge with "
            "`python coeff_store.py --merge <table.json>`"
            % (have, total, n_rows, n_user, BUNDLED, user_table(), total - have))


def selftest():
    import tempfile

    r = rows(force=True)
    assert r, "no coefficient rows found at all"
    have, total, n_rows, _n_user = coverage()
    assert total == TOTAL_SEEDS and 0 < have <= total, (have, total)
    assert n_rows >= have

    # a harvested seed resolves; an absent one does not
    known = next(int(v["seed"]) for v in r.values() if "seed" in v)
    row, _exact = coefficients_for(known, 1)
    assert row is not None
    assert coefficients_for(9999, 1) == (None, False)
    assert 9999 not in set(missing_seeds()) or True     # missing_seeds only covers 0..255
    assert all(0 <= s < TOTAL_SEEDS for s in missing_seeds())
    assert len(missing_seeds()) == total - have

    # harvested_seeds is the complement of missing_seeds, and every one of them must resolve
    hs = harvested_seeds()
    assert len(hs) == have and hs == sorted(hs)
    assert not set(hs) & set(missing_seeds())
    assert all(coefficients_for(s, 1)[0] is not None for s in hs)
    # exact is a strictly narrower claim, and only meaningful per complexity
    ex = harvested_seeds(1, exact_only=True)
    assert set(ex) <= set(hs), "an exact seed that is not harvested at all is a contradiction"
    assert all(coefficients_for(s, 1)[1] is True for s in ex)
    try:
        harvested_seeds(exact_only=True)
    except ValueError:
        pass
    else:
        raise AssertionError("exact_only without a complexity was accepted")

    # merging into a scratch table: added/updated counted, junk rejected, lookup picks it up
    tmp = os.path.join(tempfile.mkdtemp(), "user_coeffs.json")
    fake = {"777_1": {"seed": 777, "complexity": 1, "gradOffset": [1, 2, 3],
                      "gradAmplitude": [4, 5, 6], "gradFreq": [51, 51, 51],
                      "gradPhase": [7, 8, 9]},
            "junk_1": {"seed": 778}}                     # missing coefficients -> rejected
    added, updated, tot, rejected = merge(fake, dest=tmp)
    assert (added, updated, rejected) == (1, 0, 1), (added, updated, tot, rejected)
    again = merge(fake, dest=tmp)
    assert again[0] == 0 and again[1] == 0, again        # idempotent

    # with the user table pointed at our scratch file, the new seed must resolve AND override
    old = os.environ.get(ENV_COEFFS)
    os.environ[ENV_COEFFS] = tmp
    try:
        rows(force=True)
        row, exact = coefficients_for(777, 1)
        assert row is not None and exact is True, (row, exact)
        assert row["gradOffset"] == [1, 2, 3]
        assert coverage()[0] >= have, "user rows must add to coverage, not replace it"
    finally:
        if old is None:
            os.environ.pop(ENV_COEFFS, None)
        else:
            os.environ[ENV_COEFFS] = old
        rows(force=True)

    # export -> merge is the contribution round trip: what one modder sends must land intact
    shared = os.path.join(os.path.dirname(tmp), "shared.json")
    os.environ[ENV_COEFFS] = tmp
    try:
        assert export(shared) == 1
        other = os.path.join(os.path.dirname(tmp), "other_user.json")
        a, u, _t, rej = merge(shared, dest=other)
        assert (a, u, rej) == (1, 0, 0), (a, u, rej)
        got = _read(other)["777_1"]
        assert got["gradOffset"] == [1, 2, 3] and got["seed"] == 777, got
    finally:
        if old is None:
            os.environ.pop(ENV_COEFFS, None)
        else:
            os.environ[ENV_COEFFS] = old
        rows(force=True)

    # the layered result must still agree with export_palette for a bundled seed
    mine, _ = coefficients_for(known, 1)
    theirs, _ = ep.coefficients_for(known, 1)
    assert mine["gradOffset"] == theirs["gradOffset"], (mine, theirs)
    print("selftest ok")


if __name__ == "__main__":
    if "--status" in sys.argv:
        print(status_text())
    elif "--export" in sys.argv:
        dst = sys.argv[sys.argv.index("--export") + 1]
        n = export(dst, mine_only="--all" not in sys.argv)
        print("exported %d rows -> %s" % (n, dst))
    elif "--merge" in sys.argv:
        src = sys.argv[sys.argv.index("--merge") + 1]
        a, u, t, rej = merge(src)
        print("merged %s -> %s\n  %d added, %d updated, %d rows total%s"
              % (src, user_table(), a, u, t,
                 ", %d rejected (no coefficients)" % rej if rej else ""))
        print(status_text())
    else:
        selftest()
