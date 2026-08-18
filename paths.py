#!/usr/bin/env python3
"""Where things live. One definition, imported by everything.

Fourteen modules each derived the data directory from their own file location
(`Path(__file__).resolve().parent / "data"`). That works only while every file
sits in the repo root -- move one into a subdirectory and it silently points at
a `data/` that does not exist, and because these paths are created on demand it
would not error, it would create a fresh empty one and the live bucket would look
empty. That made the layout unchangeable, so it is fixed here first.

Also puts the source directories on sys.path, so `import features` keeps
working from anywhere. That preserves the project's convention that any module
can be run directly for its selftest:

    python3 core/clusters.py --selftest
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"

# Source directories, in import-resolution order. Root last so a stray name
# there cannot shadow a real module.
SRC = ("core", "bucket", "research", "ops")

for _d in SRC:
    _p = str(ROOT / _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def _selftest():
    assert ROOT.is_dir() and (ROOT / "paths.py").exists()
    assert DATA.name == "data" and DATA.parent == ROOT
    assert RAW == DATA / "raw"
    # every source dir must be importable, or a moved module cannot be found
    for d in SRC:
        assert str(ROOT / d) in sys.path, d
    print("paths selftest ok")


if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv else print(f"ROOT={ROOT}\nDATA={DATA}")
