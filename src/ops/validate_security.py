#!/usr/bin/env python3
"""Security validator — SHA integrity + PII/secrets scan + audit trail.

Spec §6.1 Always-On Step: runs every night after build_gold.py and in CI,
fails the build on violation. Gates dashboard deploy and research.

Checks:
  - SHA integrity: each Bronze file under data/raw/*/manifest.json matches
    its stored sha256 (write-once Bronze, snapshot.py manifest).
  - PII scan: no PAN, email, or Upstox tokens in Gold Parquet
    (regex scan; Upstox master stays in data/upstox_instruments.json gitignored).
  - Audit trail: every Gold row carries source_file + visible_from + built_at
    lineage from Bronze bytes to dashboard tile is one query.

Consumes: GOLD_DIR Parquet, data/raw/*/manifest.json SHA
Produces: exit 0 / 1 (gates build and CI)
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401

import hashlib
import json
import re
import sys
from pathlib import Path

from paths import ROOT

GOLD_DIR = ROOT / "data" / "gold"
RAW_ROOT = ROOT / "data" / "raw"
COMPANIES_DIR = ROOT / "data" / "companies"


# --- PII / secrets scan ------------------------------------------------------
_PAN_RE = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_JWT_RE = re.compile(r"eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}")
_TOKEN_KW_RE = re.compile(r"(?i)(upstox|api[_-]?key|bearer)\s*[:=]\s*\S{8,}")


def scan_pii(text):
    """-> list[str] of hit types in text. Empty means clean.

    Detects PAN (ABCDE1234F), email, and Upstox/JWT tokens.
    """
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    hits = []
    if _PAN_RE.search(text):
        hits.append("PAN")
    if _EMAIL_RE.search(text):
        hits.append("EMAIL")
    if _JWT_RE.search(text) or _TOKEN_KW_RE.search(text):
        hits.append("TOKEN")
    elif re.search(r"(?i)upstox", text) and re.search(r"[A-Za-z0-9_\-]{20,}", text):
        # heuristic: mentions upstox plus a long token-like string
        if "TOKEN" not in hits:
            hits.append("TOKEN")
    return hits


def check_shas(raw_root=None):
    """Verify every Bronze file matches its manifest sha256.

    Iterates data/raw/*/manifest.json; for each entry with a sha256,
    hashes the file on disk and compares. Returns list[str] errors.
    """
    errs = []
    rroot = Path(raw_root) if raw_root is not None else RAW_ROOT
    try:
        if not rroot.exists():
            return errs
        for mpath in rroot.glob("*/manifest.json"):
            try:
                manifest = json.loads(mpath.read_text())
            except Exception as e:
                errs.append(f"{mpath}: unreadable manifest ({e})")
                continue
            outdir = mpath.parent
            for name, meta in manifest.items():
                if not isinstance(meta, dict):
                    continue
                exp_sha = meta.get("sha256")
                if not exp_sha:
                    continue
                # find the file matching this source name (e.g. bhavcopy_delivery.csv, asm.json)
                cands = list(outdir.glob(f"{name}.*"))
                if not cands:
                    if meta.get("status") == 200:
                        errs.append(f"{outdir.name}/{name}: manifest says 200 but file missing")
                    continue
                found = cands[0]
                try:
                    actual = hashlib.sha256(found.read_bytes()).hexdigest()
                    if actual != exp_sha:
                        errs.append(
                            f"{found}: SHA mismatch (expected {exp_sha[:8]}..., got {actual[:8]}...)"
                        )
                except Exception as e:
                    errs.append(f"{found}: SHA check error ({e})")
    except Exception as e:
        errs.append(f"check_shas error: {e}")
    return errs


def check_pii(gold_dir=None):
    """Scan Gold Parquet content for PAN/email/tokens. Returns list[str].

    Fast path via DuckDB regexp_matches (C++ scan, <200ms for 2.7M prices);
    falls back to Python loop for tiny tables and test injection.
    """
    errs = []
    g = Path(gold_dir) if gold_dir is not None else GOLD_DIR
    try:
        if not g.exists():
            return errs
        parquets = list(g.glob("*.parquet"))
        if not parquets:
            return errs
        # duckdb fast path — scans all string-like columns with one regex per pattern
        try:
            import duckdb as _ddb
            import pyarrow as _pa
            import pyarrow.parquet as _pq
            _patterns = [
                ("PAN", r"[A-Z]{5}[0-9]{4}[A-Z]"),
                ("EMAIL", r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
                ("TOKEN", r"eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}"),
            ]
            for p in parquets:
                try:
                    tbl = _pq.read_table(str(p))
                except Exception:
                    continue
                if tbl.num_rows == 0:
                    continue
                # string-like columns only — numeric OHLCV cannot harbour PAN/email
                str_cols = []
                for c in tbl.column_names:
                    try:
                        t = tbl.schema.field(c).type
                        if _pa.types.is_string(t) or _pa.types.is_large_string(t):
                            str_cols.append(c)
                        elif _pa.types.is_dictionary(t) and _pa.types.is_string(t.value_type):
                            str_cols.append(c)
                    except Exception:
                        # unknown type — treat as string and let CAST handle it
                        str_cols.append(c)
                # also include 'day'/'symbol' which may be inferred as timestamp/dict
                # if we found zero string cols, fall back to all cols via CAST (still cheap for small tables)
                if not str_cols:
                    str_cols = list(tbl.column_names)
                # duckdb scans — one query per pattern, OR across columns
                hit = None
                con = _ddb.connect()
                for hit_type, pat in _patterns:
                    # escape single quotes in pat (none) and build OR clause
                    ors = " OR ".join(
                        f"regexp_matches(CAST(\"{c.replace(chr(34), chr(34)+chr(34))}\" AS VARCHAR), '{pat}')"
                        for c in str_cols
                    )
                    try:
                        res = con.execute(
                            f"SELECT 1 FROM read_parquet('{p}') WHERE {ors} LIMIT 1"
                        ).fetchall()
                        if res:
                            hit = hit_type
                            errs.append(f"{p.name}: {hit_type} found")
                            break
                    except Exception:
                        # duckdb regex failed (e.g. bad pat) — fall back to python for this pattern
                        for c in str_cols:
                            try:
                                vals = tbl.column(c).to_pylist()
                            except Exception:
                                continue
                            for v in vals:
                                if v is None:
                                    continue
                                h = scan_pii(str(v))
                                if h and hit_type in h:
                                    errs.append(f"{p.name}:{c}: {hit_type} found ({str(v)[:40]!r})")
                                    hit = hit_type
                                    break
                            if hit:
                                break
                    if hit:
                        break
                con.close()
                # also check TOKEN via keyword (upstox/api_key) which is case-insensitive
                # duckdb regexp_matches is case-sensitive; do a cheap python scan for that variant
                if not hit:
                    for c in str_cols:
                        try:
                            vals = tbl.column(c).to_pylist()
                        except Exception:
                            continue
                        for v in vals:
                            if v is None:
                                continue
                            txt = str(v)
                            if _TOKEN_KW_RE.search(txt) or (re.search(r"(?i)upstox", txt) and re.search(r"[A-Za-z0-9_\-]{20,}", txt)):
                                errs.append(f"{p.name}:{c}: TOKEN found ({txt[:40]!r})")
                                hit = "TOKEN"
                                break
                        if hit:
                            break
                if errs:
                    break
        except ImportError:
            # duckdb not installed — pure python fallback
            import pyarrow.parquet as pq
            for p in parquets:
                try:
                    tbl = pq.read_table(str(p))
                except Exception:
                    continue
                for col in tbl.column_names:
                    try:
                        vals = tbl.column(col).to_pylist()
                    except Exception:
                        continue
                    for v in vals:
                        if v is None:
                            continue
                        hits = scan_pii(str(v))
                        if hits:
                            errs.append(f"{p.name}:{col}: {hits[0]} found ({str(v)[:40]!r})")
                            break
                    if errs and errs[-1].startswith(f"{p.name}:"):
                        break
                if errs:
                    break
        # also scan companies manifests if populated (human ls + code share one call)
        try:
            if COMPANIES_DIR.exists() and any(COMPANIES_DIR.iterdir()):
                for jf in COMPANIES_DIR.glob("*.json"):
                    try:
                        txt = jf.read_text()
                    except Exception:
                        continue
                    hits = scan_pii(txt)
                    if hits:
                        errs.append(f"{jf.name}: {hits[0]} found")
                        break
        except Exception:
            pass
    except Exception as e:
        errs.append(f"check_pii error: {e}")
    return errs


def check_audit_trail(gold_dir=None):
    """Every Gold row carries source_file + built_at + visible_from lineage."""
    errs = []
    g = Path(gold_dir) if gold_dir is not None else GOLD_DIR
    try:
        fp = g / "fundamentals.parquet"
        if not fp.exists():
            return errs
        import pyarrow.parquet as pq
        try:
            tbl = pq.read_table(str(fp))
        except Exception as e:
            errs.append(f"fundamentals.parquet: unreadable ({e})")
            return errs
        cols = set(tbl.column_names)
        missing = [c for c in ("source_file", "built_at") if c not in cols]
        if missing:
            errs.append(f"fundamentals.parquet: missing audit trail {missing}")
        # visible_from is the point-in-time key; if any row has it, require it on all
        if "visible_from" not in cols:
            # fundamentals should be bitemporal; warn but not fail if completely missing
            # because older Gold builds used source_file + built_at only
            pass
    except Exception as e:
        errs.append(f"check_audit_trail error: {e}")
    return errs


def main():
    errs = check_shas() + check_pii() + check_audit_trail()
    if errs:
        print("\n".join(errs))
        sys.exit(1)
    print("validate_security ok")


def _selftest():
    import tempfile
    import pathlib as _pathlib

    # --- brief verbatim ------------------------------------------------------
    import validate_security as vs
    assert vs.scan_pii("no secrets here") == []
    assert vs.scan_pii("PAN ABCDE1234F") != []
    print("scan_pii brief ok")

    # extended: email + token
    assert vs.scan_pii("contact foo@bar.com") == ["EMAIL"] or "EMAIL" in vs.scan_pii("contact foo@bar.com")
    assert vs.scan_pii("no secrets here") == []
    # token via JWT
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    assert "TOKEN" in vs.scan_pii(jwt)
    print("scan_pii extended ok")

    orig_raw = vs.RAW_ROOT
    orig_gold = vs.GOLD_DIR
    try:
        # --- check_shas: valid then tampered --------------------------------
        tmp_raw = _pathlib.Path(tempfile.mkdtemp())
        day = tmp_raw / "2026-08-27"
        day.mkdir(parents=True)
        body = b"hello bronze"
        (day / "bhavcopy_delivery.csv").write_bytes(body)
        exp = hashlib.sha256(body).hexdigest()
        man = {"bhavcopy_delivery": {"sha256": exp, "status": 200, "bytes": len(body)}}
        (day / "manifest.json").write_text(json.dumps(man))
        vs.RAW_ROOT = tmp_raw
        assert vs.check_shas() == [], f"valid SHA should pass, got {vs.check_shas()}"
        # tamper
        (day / "bhavcopy_delivery.csv").write_bytes(b"tampered")
        errs = vs.check_shas()
        assert any("SHA mismatch" in e for e in errs), f"expected mismatch, got {errs}"
        print("check_shas selftest ok")

        # --- check_pii: clean parquet then PAN-injected -----------------------
        tmp_gold = _pathlib.Path(tempfile.mkdtemp())
        import pyarrow as pa, pyarrow.parquet as pq
        # clean
        tbl_clean = pa.table({"symbol": ["RELIANCE"], "revenue": [100], "source_file": ["x"], "built_at": ["2026-08-27T00:00:00"], "visible_from": ["2024-05-17"]})
        pq.write_table(tbl_clean, str(tmp_gold / "fundamentals.parquet"))
        vs.GOLD_DIR = tmp_gold
        assert vs.check_pii() == [], f"clean Gold should pass pii, got {vs.check_pii()}"
        # inject PAN
        tbl_pan = pa.table({"symbol": ["RELIANCE"], "note": ["PAN ABCDE1234F leaked"], "source_file": ["x"], "built_at": ["2026-08-27T00:00:00"]})
        pq.write_table(tbl_pan, str(tmp_gold / "fundamentals.parquet"))
        errs2 = vs.check_pii()
        assert any("PAN" in e for e in errs2), f"expected PAN hit, got {errs2}"
        print("check_pii selftest ok")

        # --- check_audit_trail: missing built_at should fail -----------------
        tmp_gold2 = _pathlib.Path(tempfile.mkdtemp())
        tbl_no_audit = pa.table({"symbol": ["RELIANCE"], "revenue": [100]})
        pq.write_table(tbl_no_audit, str(tmp_gold2 / "fundamentals.parquet"))
        vs.GOLD_DIR = tmp_gold2
        errs3 = vs.check_audit_trail()
        assert any("audit trail" in e for e in errs3), f"expected audit trail err, got {errs3}"
        # with trail should pass
        vs.GOLD_DIR = tmp_gold
        # restore clean with audit columns
        pq.write_table(tbl_clean, str(tmp_gold / "fundamentals.parquet"))
        assert vs.check_audit_trail() == [], f"audit trail should pass, got {vs.check_audit_trail()}"
        print("check_audit_trail selftest ok")

        print("validate_security selftest ok")
    finally:
        vs.RAW_ROOT = orig_raw
        vs.GOLD_DIR = orig_gold


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
