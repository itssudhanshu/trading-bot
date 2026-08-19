#!/usr/bin/env python3
"""Assert-or-die source patching.

`str.replace` on a missing anchor silently returns the original string. That
has now produced three no-op patches in this project, each of which looked like
a successful edit and was only caught later by a result that made no sense --
the --no-fundamentals flag parsed cleanly and did nothing for a full 25-minute
search. Anchors drift as files evolve; the failure has to be loud.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import sys
from pathlib import Path


def sub(path, old, new, count=1, must_exist=True):
    p = Path(path)
    s = p.read_text()
    n = s.count(old)
    if n == 0:
        if must_exist:
            raise SystemExit(f"ANCHOR NOT FOUND in {path}:\n  {old.splitlines()[0][:90]}")
        return False
    if count and n != count:
        raise SystemExit(f"anchor appears {n}x in {path}, expected {count}")
    p.write_text(s.replace(old, new, count or -1))
    return True


def _selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.py"
        f.write_text("alpha\nbeta\n")
        assert sub(f, "beta", "gamma")
        assert f.read_text() == "alpha\ngamma\n"
        try:
            sub(f, "nope", "y")
            raise AssertionError("missing anchor did not raise")
        except SystemExit:
            pass
        assert sub(f, "nope", "y", must_exist=False) is False
        f.write_text("a\na\n")
        try:
            sub(f, "a", "b", count=1)
            raise AssertionError("duplicate anchor did not raise")
        except SystemExit:
            pass
    print("patch_helper selftest ok")


if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv else print(__doc__)
