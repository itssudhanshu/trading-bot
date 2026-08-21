#!/usr/bin/env python3
"""Assemble the sentiment evidence for a stock, as of a date. Judges nothing.

This is the data half of the `sentiment` skill. The skill reads what this
prints and applies a scoring rubric; this file decides only WHAT WAS VISIBLE,
which is the half that has to be reproducible.

That split is the point. A language model scoring a headline is not
reproducible -- ask twice, get two numbers -- and this repo's audit fails when a
recorded number moves. So the model never touches the question of what
information existed on a given day; it only reads a fixed, dated list and forms
a view. Re-run this file on the same date and you get the same evidence, always.

TWO CHANNELS, AND WHY NOT MORE
------------------------------
The skill this is adapted from (tradeinsight-info/investment-analysis-skills)
uses three: news via NewsAPI, Reddit (r/wallstreetbets, r/stocks) and StockTwits.
Those are the right three for a US large cap and the wrong three here. This book
trades NSE names in the bottom two turnover terciles -- 20 Microns, not Apple --
and StockTwits and WSB carry essentially nothing on them. Their coverage would
not be thin, it would be absent, and an absent channel scored as neutral is a
number that says "no view" while looking like "no news".

So:

  announcements   what the company told the exchange. 1,019,495 rows back to
                  2019, timestamped to the second, already point-in-time
                  (announcements.visible_from). For an NSE microcap this is
                  strictly better than any news API: it is complete, it is
                  dated, and it is what the company is legally obliged to say.

  news            headlines from data/news/, in two channels: market-wide feeds,
                  and a per-company query run over the day's candidates. The
                  second exists because the first matched ZERO headlines to a
                  microcap on day one. Items carry the publisher they came from
                  -- Moneycontrol, Business Standard, CNBCTV18, Mint and the
                  rest -- and the symbol whose own query retrieved them, so
                  attribution is exact rather than guessed. Thin by
                  construction: the archive starts the day newswatch first ran,
                  and it says so rather than implying otherwise.

WHAT THIS IS NOT
----------------
Not a backtest input. The news channel has no history, and the skill's scoring
is a model's judgement rather than a rule, so nothing here may feed a measured
result. thicket's `ann_tone` is the measured, frozen, deterministic feature and
it lives in clusters.py. This is an operator's view of today.

The selftest asserts no research or strategy module imports this file.

    python3 src/ops/sentiment.py 20MICRONS
    STRATEGY=thicket python3 src/ops/sentiment.py --picks
    python3 src/ops/sentiment.py --selftest
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import csv
import io
import json
import re
import sys
from datetime import date, timedelta

import announcements
from paths import ROOT

NEWS = ROOT / "data" / "news"

# How far back each channel looks. Announcements are sparse for a microcap, so a
# month is a reasonable window; news scrolls fast, so a week is generous.
ANN_WINDOW = 30
NEWS_WINDOW = 7

# Words that are not a company's identity. Matching on "Limited" would return
# every headline in the archive.
_STOP = {"limited", "ltd", "ltd.", "the", "india", "indian", "company",
         "industries", "corporation", "corp", "enterprises", "&", "and"}


def company_name(symbol):
    """-> the registered name from the newest equity master, or ''."""
    import universe
    d = universe.master_snapshot()
    if d is None:
        return ""
    f = d / "equity_master.csv"
    if not f.exists():
        return ""
    for r in csv.DictReader(io.StringIO(f.read_text(errors="replace"))):
        if (r.get("SYMBOL") or "").strip().upper() == symbol.upper():
            return (r.get("NAME OF COMPANY") or "").strip()
    return ""


def _match_terms(symbol, name):
    """-> the strings a headline must contain to count as about this company.

    Built from the registered name with the generic words removed, plus the
    ticker itself. A term shorter than four characters is dropped: "TCI" or
    "IOL" appear inside ordinary English words and would match the whole
    archive, which is worse than matching nothing because it looks like data.
    """
    terms = set()
    if symbol and len(symbol) >= 4:
        terms.add(symbol.lower())
    for w in re.split(r"[^A-Za-z0-9]+", name or ""):
        w = w.lower()
        if len(w) >= 4 and w not in _STOP:
            terms.add(w)
    return sorted(terms)


def announcement_evidence(symbol, day, window=ANN_WINDOW):
    """-> announcements VISIBLE on `day`, most recent first.

    Straight through announcements.visible(), so the 15:30 rule applies: an
    announcement filed at 22:56 is not visible on the day it was filed. 60% of
    them arrive after the close, so getting this wrong is not an edge case.
    """
    rows = announcements.timeline(symbol)
    if not rows:
        return []
    return announcements.visible(rows, day.isoformat(), window=window)


def news_evidence(symbol, day, window=NEWS_WINDOW):
    """-> captured headlines plausibly about this company, most recent first.

    Filtered on `captured_at` -- OUR timestamp, the only one that is evidence
    the item was publicly available -- never on the publisher's own pubDate,
    which cannot be verified and which two feeds got 848 days wrong.
    """
    name = company_name(symbol)
    terms = _match_terms(symbol, name)
    if not terms:
        return []
    out = []
    for k in range(window + 1):
        d = day - timedelta(days=k)
        p = NEWS / f"{d.isoformat()}.jsonl"
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("captured_at", "")[:10] > day.isoformat():
                continue
            # An item captured FROM this company's own query carries the
            # symbol, so it needs no guessing. Name matching is the fallback for
            # general-feed items, and it is strictly worse: it drops short
            # tickers and can attach an unrelated story to a company.
            tag = r.get("symbol")
            if tag:
                if tag.upper() == symbol.upper():
                    out.append(r)
                continue
            hay = f"{r.get('title','')} {r.get('source','')}".lower()
            if any(re.search(rf"\b{re.escape(t)}\b", hay) for t in terms):
                out.append(r)
    out.sort(key=lambda r: r.get("captured_at", ""), reverse=True)
    return out


def evidence(symbol, day=None):
    """-> everything visible about `symbol` on `day`. No scores, no opinions."""
    day = day or date.today()
    ann = announcement_evidence(symbol, day)
    news = news_evidence(symbol, day)
    return {
        "symbol": symbol.upper(),
        "company": company_name(symbol),
        "as_of": day.isoformat(),
        "announcements": ann,
        "news": news,
        "announcement_count": len(ann),
        "news_count": len(news),
        # Stated rather than left for the reader to infer. The archive starts
        # when newswatch first ran; before that the channel is not "quiet", it
        # is ABSENT, and those are different facts.
        "news_archive_starts": _archive_start(),
    }


def _archive_start():
    if not NEWS.exists():
        return None
    days = sorted(p.stem for p in NEWS.glob("*.jsonl"))
    return days[0] if days else None


def render(ev):
    """Plain text for the skill to read. Facts only."""
    L = [f"SYMBOL      {ev['symbol']}",
         f"COMPANY     {ev['company'] or '(not in the equity master)'}",
         f"AS OF       {ev['as_of']}",
         ""]
    L.append(f"ANNOUNCEMENTS visible in the last {ANN_WINDOW} days: "
             f"{ev['announcement_count']}")
    if not ev["announcements"]:
        L.append("  (none -- this is common for a microcap and is NOT bad news)")
    for r in ev["announcements"][:25]:
        L.append(f"  {r['visible_from']}  [{r['desc'] or 'uncategorised'}]")
        if r.get("text"):
            L.append(f"      {r['text'][:200]}")
    L.append("")
    start = ev["news_archive_starts"]
    L.append(f"NEWS headlines matched in the last {NEWS_WINDOW} days: "
             f"{ev['news_count']}")
    if start is None:
        L.append("  (no news archive on disk -- newswatch has never run)")
    else:
        L.append(f"  (archive begins {start}; anything earlier is ABSENT, "
                 f"not quiet)")
    for r in ev["news"][:20]:
        who = r.get("publisher") or r.get("source", "")
        how = "matched by name" if not r.get("symbol") else "this company's own feed"
        L.append(f"  {r.get('captured_at','')[:10]}  [{who}]  "
                 f"{r.get('title','')[:140]}")
        L.append(f"      ({how})")
    return "\n".join(L)


def _selftest():
    import tempfile
    # --- match terms: specific enough to be useful, not so loose as to be noise
    t = _match_terms("20MICRONS", "20 Microns Limited")
    assert "microns" in t, t
    assert "limited" not in t, "a generic word would match the whole archive"
    assert "20microns" in t, t
    # A short ticker must not become a term: "IOL" matches "biological".
    assert _match_terms("IOL", "") == [], _match_terms("IOL", "")
    # ...and the guard is real, not theoretical:
    assert re.search(r"\biol\b", "biological growth") is None
    assert "iol" not in _match_terms("IOL", "IOL Chemicals Limited"), \
        "a 3-letter ticker leaked into the match terms"
    assert "chemicals" in _match_terms("IOL", "IOL Chemicals Limited")

    # --- news matching is word-boundary, and reads captured_at not pubDate ---
    global NEWS
    real = NEWS
    try:
        with tempfile.TemporaryDirectory() as td:
            NEWS = _pl.Path(td)
            day = date(2026, 8, 20)
            rows = [
                {"title": "20 Microns wins order", "source": "et",
                 "captured_at": "2026-08-20T10:00:00", "link": "a",
                 "published": "Tue, 23 Apr 2024 00:00:00 +0530"},
                {"title": "Unrelated market wrap", "source": "et",
                 "captured_at": "2026-08-20T10:00:00", "link": "b"},
                {"title": "20 Microns later news", "source": "et",
                 "captured_at": "2026-08-21T10:00:00", "link": "c"},
            ]
            (NEWS / "2026-08-20.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows))
            # A symbol-tagged item belongs to its symbol and to no other, even
            # when the name would match. Attribution beats guessing.
            tagged = dict(rows[0], title="Tagged elsewhere", link="d",
                          symbol="OTHERCO")
            (NEWS / "2026-08-20.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows + [tagged]))
            assert not [r for r in news_evidence("20MICRONS", day)
                        if r["title"] == "Tagged elsewhere"], \
                "an item tagged to another company leaked in by name matching"
            assert len(news_evidence("OTHERCO", day)) == 1, \
                "a symbol-tagged item did not reach its own company"
            (NEWS / "2026-08-20.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows))

            got = news_evidence("20MICRONS", day)
            titles = [r["title"] for r in got]
            assert "20 Microns wins order" in titles, titles
            assert "Unrelated market wrap" not in titles, \
                "an unrelated headline matched"
            # captured AFTER the as-of date must not be visible, even though it
            # sits in the same file.
            assert "20 Microns later news" not in titles, \
                "a headline captured after as_of leaked into the evidence"
            # The stale pubDate on the first row must not have excluded it:
            # captured_at is what this reads.
            assert len(got) == 1, got
            assert _archive_start() == "2026-08-20"
    finally:
        NEWS = real

    # --- the guarantee: no backtest may read this ---------------------------
    pat = re.compile(r"^\s*(?:import\s+sentiment|from\s+sentiment\s+import)",
                     re.MULTILINE)
    bad = [str(p.relative_to(ROOT))
           for d in ("src/research", "src/strategies")
           for p in sorted((ROOT / d).rglob("*.py"))
           if pat.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert not bad, f"a backtest imports the live sentiment view: {bad}"

    # --- render must never invent a reading from nothing --------------------
    txt = render({"symbol": "X", "company": "", "as_of": "2026-08-20",
                  "announcements": [], "news": [], "announcement_count": 0,
                  "news_count": 0, "news_archive_starts": None})
    assert "NOT bad news" in txt, "silence must be labelled, not left to infer"
    assert "never run" in txt
    print("sentiment selftest ok (evidence only; no scores are produced here)")


def main(argv):
    if "--picks" in argv:
        import clusters
        import features
        c = features.load_corpus()
        day = sorted({d for s in c.values() for d in s.days})[-1]
        picked = clusters.pick(c, day)
        syms = [s for lst in picked.values() for s, _ in lst[:5]]
        print(f"# {paths.STRATEGY} candidates as of {day}: {len(syms)}\n")
        for sym in syms:
            print(render(evidence(sym, day)))
            print("\n" + "-" * 72 + "\n")
        return 0
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        print(__doc__.strip().splitlines()[0])
        print("\n  python3 src/ops/sentiment.py SYMBOL [--day YYYY-MM-DD]")
        print("  STRATEGY=thicket python3 src/ops/sentiment.py --picks")
        return 1
    day = None
    for a in argv:
        if a.startswith("--day="):
            day = date.fromisoformat(a.split("=", 1)[1])
    print(render(evidence(args[0], day)))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main(sys.argv[1:]))
