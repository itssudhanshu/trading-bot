#!/usr/bin/env python3
"""Sentiment for a stock, as of a date. Deterministic, from end to end.

Assembles what was visible and scores it, and BOTH halves are reproducible: the
same symbol and date give the same number every time. That was not true of the
first version, where the evidence was fixed and a model did the judging, so the
answer moved between runs and could never be measured against anything.

The scorer follows three sources, which converge on one shape -- score each item
on a bounded scale, aggregate as positive minus negative, map to a label:

  gandalf1819/Stock-Market-Sentiment-Analysis  lexicon; positive words minus
      negative words, per article.
  FinBERT (Araci 2019)  softmax over {positive, neutral, negative}, then
      P(pos) - P(neg) in [-1, 1], aggregated per ticker per day.
  tradeinsight-info/investment-analysis-skills  a model scores each headline in
      [-1, 1]; mean x 10; bands at +/-3 and +/-7.

It takes the determinism of the first two and the bands of the third. Because it
is deterministic it is, unlike the model-scored version, a legitimate CANDIDATE
for a pre-registered test against returns -- see the caveat below, which has not
changed.

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

WHAT THIS IS NOT, STILL
-----------------------
Not a backtest input as it stands, and the reason narrowed rather than went
away. The scoring is now a rule, so that objection is gone -- but the NEWS
channel still has no history, and a backtest reading it would be reading the
future. The announcement channel does have history, which is what makes this
scorer a candidate for a pre-registered test rather than only a view.

Nothing here has been measured against returns. thicket's `ann_tone` -- the
nearest thing that has -- read t = 1.71 against a bar of 2.6 and is switched
off. Treat a number from this file as a description of today, not as evidence.

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
#
# The list grew after a real miss: "Healthcare Global Enterprises" matched
# "Global Market: European shares little changed as bond yields rise" on the
# word GLOBAL, and two Eurozone bond stories were scored as coverage of an
# Indian hospital chain. Every word below appears in company names AND in
# ordinary market copy, which is exactly what makes it useless for telling them
# apart.
_STOP = {"limited", "ltd", "ltd.", "the", "india", "indian", "company",
         "industries", "corporation", "corp", "enterprises", "&", "and",
         "global", "international", "national", "group", "holdings",
         "technologies", "technology", "services", "systems", "solutions",
         "products", "projects", "ventures", "resources", "trading",
         "markets", "market", "finance", "financial", "capital", "power",
         "energy", "steel", "motors", "auto", "bank", "first", "new",
         # SECTOR nouns. A company is named for its sector and so is every
         # market wrap about that sector, so one of these can never tell them
         # apart: "China, Hong Kong stocks rebound as HEALTHCARE and tech
         # shares rally" was filed against Healthcare Global Enterprises.
         # A name that reduces to nothing but these has no usable general-feed
         # match and falls back to the per-company channel, which is exact.
         "healthcare", "health", "pharma", "pharmaceuticals", "cement",
         "textiles", "chemicals", "foods", "food", "metals", "mining",
         "hotels", "infra", "infrastructure", "engineering", "electronics",
         "telecom", "media", "retail", "agro", "petro", "labs",
         "laboratories", "constructions", "construction", "cables"}

# How many distinct name-words an untagged headline must match. One is not
# enough: a single common word is what let a bond story become hospital news.
# Applied only when the company HAS two usable words -- a one-word name like
# "Yuken" cannot clear a two-word bar and would otherwise never match at all.
MIN_NAME_HITS = 2


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


def _mentions(hay, symbol, terms):
    """-> True if this text is plausibly ABOUT the company.

    A ticker match settles it on its own -- a ticker is specific. Otherwise the
    name has to carry the match, and two distinct name-words are needed when
    two exist, because ONE common word is what let "Global Market: European
    shares..." be filed as coverage of Healthcare Global Enterprises.

    The ticker form and a name word are two spellings of ONE piece of evidence,
    not two, so they are counted separately -- an earlier version summed them
    and then rejected "20 Microns wins order" for having only one hit.

    Deliberately strict. The per-company channel carries exact attribution and
    is the reliable path; this fallback exists for general market feeds, which
    mostly should NOT match. A false positive here scores an unrelated story
    against a stock, which is worse than missing one, because nothing
    downstream can tell it apart from real coverage.
    """
    tick = symbol.lower() if symbol and len(symbol) >= 4 else None
    if tick and re.search(rf"\b{re.escape(tick)}\b", hay):
        return True
    names = [t for t in terms if t != tick]
    if not names:
        return False
    hits = sum(1 for t in names if re.search(rf"\b{re.escape(t)}\b", hay))
    return hits >= min(MIN_NAME_HITS, len(names))


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
            if _mentions(hay, symbol, terms):
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


# The scorer lives in announcements.py, beside the data it scores. It moved
# there so `research/` can import it: this file may never be imported by a
# backtest -- the news channel has no history and reading it would be reading
# the future -- but the SCORER is history-safe on filings, and forbidding the
# whole module would have forbidden the part that can legitimately be measured.
#
# Re-exported here so callers and the skill keep one name for one thing.
from announcements import (          # noqa: F401
    LEX_POS, LEX_NEG, NEGATORS, NEGATION_WINDOW, BANDS, PRIOR,
    band, text_tone, score_announcement, aggregate,
)


def stock_sentiment(symbol, day=None, ev=None):
    """-> the full picture for one stock: channel scores, composite, band."""
    ev = ev or evidence(symbol, day)
    tone_of = announcements.load_tone()
    ann = [score_announcement(r, tone_of) for r in ev["announcements"]]
    news = [text_tone(f"{r.get('title','')}") for r in ev["news"]]

    def chan(scores):
        return aggregate(scores)

    a, n = chan(ann), chan(news)
    # Weights re-normalise over the channels that HAVE data, exactly as the
    # third source does. A channel with nothing to say gets no vote; it does not
    # get a vote of zero, which would drag every composite toward Neutral.
    if a is not None and n is not None:
        comp = round(0.75 * a + 0.25 * n, 2)
    else:
        comp = a if a is not None else n
    return {
        "symbol": ev["symbol"], "company": ev["company"], "as_of": ev["as_of"],
        "announcement_score": a, "news_score": n, "composite": comp,
        "band": band(comp),
        "n_announcements": len(ann), "n_news": len(news),
        # How many items actually SAID something. A composite from two signals
        # and a composite from twelve are different claims, and the row has to
        # show which it is.
        "n_signal": sum(1 for s in ann + news if s),
        "top": _drivers(ev, ann, news),
    }


def _drivers(ev, ann, news):
    """-> the few items doing the most work, strongest absolute score first.

    A composite built from one strong item and nine zeros is a different fact
    from one built from ten agreeing items, and the reader cannot tell without
    seeing which is which.
    """
    rows = [(s, r.get("desc") or "filing", r.get("text", "")[:110])
            for s, r in zip(ann, ev["announcements"]) if s]
    rows += [(s, r.get("publisher") or "news", r.get("title", "")[:110])
             for s, r in zip(news, ev["news"]) if s]
    rows.sort(key=lambda t: -abs(t[0]))
    return rows[:4]


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
    # This asserted that "chemicals" survived as a term. It no longer does, and
    # the change is deliberate: a sector noun cannot separate a company from a
    # wrap about its sector. "IOL Chemicals" is therefore a name with NOTHING
    # usable -- a 3-letter ticker plus a sector word -- and it correctly falls
    # back to the per-company channel, which attributes exactly. Asserting the
    # PROPERTY (nothing generic survives) rather than the old membership.
    assert _match_terms("IOL", "IOL Chemicals Limited") == [], \
        _match_terms("IOL", "IOL Chemicals Limited")
    # A name with one distinctive word keeps it.
    assert _match_terms("IOL", "IOL Krishival Limited") == ["krishival"]

    # --- a generic word must not carry a match on its own -------------------
    # "Healthcare Global Enterprises" matched a Eurozone bond story on GLOBAL.
    # Every word of this name is generic -- a sector noun plus two corporate
    # fillers -- so NOTHING survives, and that is the intended answer. An
    # earlier version of this block asserted "healthcare" survived; the sector
    # fix invalidated it, and the property worth asserting is that no generic
    # word carries a match, not which particular one used to.
    t = _match_terms("HCG", "Healthcare Global Enterprises Limited")
    assert t == [], t
    # and a one-word name must still be matchable on that one word
    assert _match_terms("YUKEN", "Yuken India Limited") == ["yuken"], \
        _match_terms("YUKEN", "Yuken India Limited")

    # the real miss, both directions
    hcg = _match_terms("HCG", "Healthcare Global Enterprises Limited")
    assert not _mentions("global market: european shares little changed",
                         "HCG", hcg), "a bond story matched a hospital chain"
    # Every word of this name is generic, so it has NO usable general-feed
    # match and relies on the per-company channel. That is the correct answer,
    # not a gap: one sector noun cannot separate a company from a wrap about
    # its sector.
    assert not _mentions("stocks rebound as healthcare and tech shares rally",
                         "HCG", hcg), "a sector wrap matched a company"
    assert hcg == [], hcg
    # ...while a distinctive single word still works
    assert _mentions("yuken india wins export order", "YUKEN",
                     _match_terms("YUKEN", "Yuken India Limited"))
    # a ticker settles it alone; a name word plus the ticker spelling is ONE
    # piece of evidence, not two
    m20 = _match_terms("20MICRONS", "20 Microns Limited")
    assert _mentions("20 microns wins order", "20MICRONS", m20), \
        "two spellings of one name were counted as two hits"
    assert _mentions("20microns q1 results", "20MICRONS", m20)
    assert not _mentions("cement stocks rally", "20MICRONS", m20)

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

    # --- the scorer ---------------------------------------------------------
    # Bands, at their edges. An off-by-one here mislabels every borderline
    # stock and nothing would look wrong.
    assert band(7.0) == "Very Bullish" and band(6.99) == "Bullish"
    assert band(3.0) == "Bullish" and band(2.99) == "Neutral"
    assert band(-3.0) == "Bearish", "-3.0 is the Bearish edge, not Neutral"
    assert band(-2.99) == "Neutral"
    assert band(-7.0) == "Very Bearish" and band(-6.99) == "Bearish"
    assert band(None) == "No data", "no data must not read as Neutral"

    # Polarity, and the negation that a bag of words gets backwards.
    assert text_tone("record order, profit surges") == 1.0
    assert text_tone("fraud investigation, auditor resigned") == -1.0
    assert text_tone("not profitable") < 0, \
        "a negated positive scored positive -- the classic lexicon failure"
    assert text_tone("no growth") < 0
    assert text_tone("copy of newspaper publication") == 0.0
    assert text_tone("") == 0.0 and text_tone(None) == 0.0
    # Normalised by MATCHED words, so length alone does not dilute a verdict.
    assert text_tone("profit") == text_tone("the " * 50 + "profit")

    # The category table leads and the text adjusts.
    t = {"Resignation": -1, "Dividend": 1}
    assert score_announcement({"desc": "Resignation", "text": ""}, t) == -1.0
    assert score_announcement({"desc": "Updates",
                               "text": "bags record order"}, t) == 1.0
    mixed = score_announcement({"desc": "Dividend",
                                "text": "fraud investigation"}, t)
    assert 0 < mixed < 1, ("a signed category with contradicting text should "
                           "moderate, not flip", mixed)

    # --- aggregation: the far bands must be earned -------------------------
    assert aggregate([]) is None and aggregate([0.0, 0.0]) is None, \
        "silence produced a score"
    one = aggregate([1.0])
    three = aggregate([1.0] * 3)
    six = aggregate([1.0] * 6)
    assert band(one) == "Bullish", (one, band(one))
    assert one < three < six, (one, three, six)
    assert band(six) == "Very Bullish", (six, band(six))
    assert band(aggregate([1.0])) != "Very Bullish", \
        "a single item reached the far band without corroboration"
    assert aggregate([-1.0]) == -one, "the scale is not symmetric"
    # Procedural zeros must not dilute a real signal -- the bug that made every
    # stock Neutral on the first run.
    assert aggregate([1.0] + [0.0] * 12) == aggregate([1.0]), \
        "procedural filings diluted a genuine signal"

    # A channel with no data must not vote zero and drag the answer to Neutral.
    ev0 = {"symbol": "X", "company": "", "as_of": "2026-08-21",
           "announcements": [{"desc": "Resignation", "text": ""}],
           "news": [], "announcement_count": 1, "news_count": 0,
           "news_archive_starts": None}
    r = stock_sentiment("X", ev=ev0)
    assert r["news_score"] is None, "an empty channel produced a score"
    assert r["composite"] == r["announcement_score"], \
        "an empty channel diluted the composite"
    # Bearish, NOT Very Bearish. This asserted Very Bearish before shrinkage,
    # and the change is the intended one: one resignation is a warning, not a
    # verdict, and the far band has to be earned by more than a single filing.
    assert r["band"] == "Bearish", r
    assert r["n_signal"] == 1, r

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
    print("sentiment selftest ok (deterministic scorer; bands and negation "
          "asserted at their edges)")


def table(symbols, day=None, log=print):
    """Print one row per stock: the five bands, with what drove each."""
    rows = [stock_sentiment(s, day) for s in symbols]
    rows.sort(key=lambda r: (r["composite"] is None,
                             -(r["composite"] or 0)))
    log(f"{'stock':<12}{'company':<26}{'filings':>8}{'news':>6}"
        f"{'score':>8}   sentiment")
    log("-" * 78)
    for r in rows:
        c = "  --  " if r["composite"] is None else f"{r['composite']:+6.2f}"
        log(f"{r['symbol']:<12}{(r['company'] or '')[:25]:<26}"
            f"{r['n_announcements']:>8}{r['n_news']:>6}{c:>8}   {r['band']}")
    log("")
    for r in rows:
        if not r["top"]:
            continue
        log(f"{r['symbol']} -- {r['band']}"
            f"  (exchange {_fmt(r['announcement_score'])}, "
            f"news {_fmt(r['news_score'])})")
        for s, who, what in r["top"]:
            log(f"    [{s:+.2f}] {who}: {what}")
        log("")
    return rows


def _fmt(v):
    return "no data" if v is None else f"{v:+.2f}"


def main(argv):
    if "--table" in argv:
        import clusters
        import features
        c = features.load_corpus()
        day = sorted({d for s in c.values() for d in s.days})[-1]
        syms = [s for lst in clusters.pick(c, day).values() for s, _ in lst[:5]]
        print(f"# {paths.STRATEGY} candidates as of {day}\n")
        table(syms, day)
        return 0
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
