#!/usr/bin/env python3
"""Point-in-time NSE corporate announcements.

Sibling of `fundamentals.py`, and deliberately the same shape: fetch raw, store
raw, parse later, and date everything by WHEN IT BECAME PUBLIC rather than by
what period it describes.

WHY THIS MODULE IS MOSTLY ABOUT ONE TIMESTAMP
---------------------------------------------
NSE stamps every announcement to the second, and **60% of them arrive after the
15:30 close** (measured on 2,168 rows across one week, market-wide: 1,292 after
the close, 100% of rows timestamped).

Date those by calendar day -- the obvious way, and the way almost every retail
backtest does it -- and 60% of the signal becomes information nobody had when
the trade was placed. It would not raise an error. It would not look wrong. It
would return a good CAGR built on knowledge that did not exist yet, which is
exactly what the circuit-lock guard turned out to be: about half the reported
return was phantom (L58), and it took three months to notice.

So the rule below is the point of this file, and everything else is plumbing:

    An announcement is visible to the signal computed on session `i` only if it
    is timestamped STRICTLY BEFORE session i's 15:30 close. Anything later
    becomes visible on session i+1.

Deliberately conservative at the margin. A 15:29 announcement counts for
session i even though acting on it would be difficult in practice -- and since
the bucket fills at the NEXT session's open, the tradeable gap is still a full
session. Erring the other way would be the mistake that flatters the result.

`fundamentals.as_of()` already does this for quarterly filings using
`broadCastDate`. This is the same idea with an intraday cutoff added, because
filings publish once a quarter and announcements publish at 22:56 on a Thursday.

WHAT IS AND IS NOT DECIDED HERE
-------------------------------
This module is SHARED data infrastructure, like price bars. It knows nothing
about any strategy. It computes features; it does not decide whether they are
worth scoring. `sentiment` decides that, and only a test that clears the
promotion bar can move a weight off zero.

    python3 src/core/announcements.py --selftest
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import re
import sys
from bisect import bisect_left, bisect_right
from datetime import date, datetime, time, timedelta

from paths import ROOT      # one definition; see paths.py

RAW = ROOT / "data" / "announcements" / "raw"
PARSED = ROOT / "data" / "announcements" / "parsed"
TONE_TABLE = ROOT / "data" / "announcements" / "tone_table.json"

# NSE continuous trading ends 15:30 IST. Announcements carry local timestamps.
CLOSE = time(15, 30)

INDEX_URL = ("https://www.nseindia.com/api/corporate-announcements"
             "?index=equities&from_date={d0:%d-%m-%Y}&to_date={d1:%d-%m-%Y}")

# NSE's own date formats, same mix as fundamentals.py sees.
_FORMATS = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y")

# The categories where NSE has DEMANDED an explanation, rather than the company
# volunteering one. An exchange-flagged anomaly is a different kind of event
# from a press release and is scored separately (`ann_flag`).
FLAG_CATEGORIES = ("Price movement", "Spurt in Volume", "News Verification")


def _dt(s):
    """-> datetime, or None. NSE mixes second-precision and minute-precision."""
    if not s:
        return None
    s = s.strip()
    for f in _FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


# --- the rule ---------------------------------------------------------------

def visible_from(ts, sessions):
    """-> the first session on which `ts` could inform a signal, or None.

    `sessions` is the sorted list of trading dates. Passed in rather than
    loaded, so this is testable without a corpus and cannot silently disagree
    with the calendar the backtest is using.

    Three cases, and the second is the one that matters:

        10:00 on a trading day  -> that day        (public before the close)
        22:56 on a trading day  -> the NEXT session
        any time on a holiday   -> the next session
    """
    if ts is None or not sessions:
        return None
    d = ts.date()
    i = bisect_left(sessions, d)
    # Public before the close of a session that actually traded.
    if i < len(sessions) and sessions[i] == d and ts.time() < CLOSE:
        return sessions[i]
    # Otherwise: the first session STRICTLY after the announcement's date.
    j = bisect_right(sessions, d)
    return sessions[j] if j < len(sessions) else None


# --- fetch and parse --------------------------------------------------------

def fetch_range(d0, d1, force=False):
    """Raw JSON for a date range, stored verbatim. -> parsed list, or [].

    Stored raw and parsed later for the same reason fundamentals.py does it: a
    parser bug found in six months should be fixable without re-fetching
    770,000 rows from someone else's server.
    """
    from snapshot import fetch
    out = RAW / f"{d0:%Y%m%d}-{d1:%Y%m%d}.json"
    if out.exists() and not force:
        try:
            return json.loads(out.read_text())
        except json.JSONDecodeError:
            pass                     # a truncated file re-fetches rather than
                                     # silently reporting an empty week
    status, body = fetch(INDEX_URL.format(d0=d0, d1=d1), timeout=60)
    if status != 200 or not body:
        return []
    try:
        rows = json.loads(body)
    except json.JSONDecodeError:
        return []
    rows = rows if isinstance(rows, list) else rows.get("data", [])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    return rows


def parse_rows(rows, sessions):
    """-> [{symbol, visible_from, an_dt, desc, text}], dropping the unusable.

    A row with no parseable timestamp is DROPPED, not defaulted to its
    `sort_date`. A default here would be indistinguishable from a real
    before-the-close announcement and would reintroduce exactly the look-ahead
    this module exists to prevent.
    """
    out = []
    for r in rows:
        ts = _dt(r.get("an_dt"))
        sym = (r.get("symbol") or "").strip().upper()
        if ts is None or not sym:
            continue
        vf = visible_from(ts, sessions)
        if vf is None:
            continue                 # after the last session we know about
        out.append({
            "symbol": sym,
            "visible_from": vf.isoformat(),
            "an_dt": ts.isoformat(sep=" "),
            "desc": (r.get("desc") or "").strip(),
            "text": (r.get("attchmntText") or "").strip()[:400],
        })
    out.sort(key=lambda x: (x["symbol"], x["visible_from"], x["an_dt"]))
    return out


def load_tone():
    """-> {category: +1|0|-1}. Empty if the table has not been frozen yet.

    The table is committed BEFORE the run that uses it. Written afterwards it
    is a hindsight machine that will 'work' every time. A category absent from
    the table scores neutral, never negative -- absence is ignorance, not bad
    news.
    """
    if not TONE_TABLE.exists():
        return {}
    try:
        d = json.loads(TONE_TABLE.read_text())
    except json.JSONDecodeError:
        return {}
    return {k: int(v) for k, v in d.get("tone", {}).items()}


# --- features ---------------------------------------------------------------

def visible(rows, day_iso, window=None):
    """-> the rows visible on `day_iso`, most recent first.

    `window` limits to the last N calendar days of visibility. Rows must
    already be sorted; parse_rows guarantees it.
    """
    got = [r for r in rows if r["visible_from"] <= day_iso]
    if window is not None:
        lo = (date.fromisoformat(day_iso) - timedelta(days=window)).isoformat()
        got = [r for r in got if r["visible_from"] >= lo]
    return sorted(got, key=lambda r: r["visible_from"], reverse=True)


def features_asof(rows, day_iso, window=30, baseline=365):
    """-> {ann_burst, ann_tone, ann_flag} visible on `day_iso`, or {}.

    ann_burst  filings in the last `window` days against THIS company's own
               rate over `baseline` days. Measured against its own history and
               not the market's, because absolute counts are a size proxy:
               bigger companies announce more, and the score already has a size
               axis. A second one smuggled in through the back door would be
               invisible and would look like a finding.

    ann_tone   the most recent visible category, mapped through the frozen
               table. Neutral where the category is unknown.

    ann_flag   1.0 if NSE demanded an explanation in the window. Kept separate
               from tone because it is not the company talking.

    Returns {} where there is not enough history to say anything -- the caller
    must score that NEUTRAL, never zero. Microcaps announce less often than
    small caps, so scoring silence as bad is the same size proxy again.
    """
    if not rows:
        return {}
    recent = visible(rows, day_iso, window=window)
    base = visible(rows, day_iso, window=baseline)
    if not base:
        return {}

    # Expected count in a window of this length, from the company's own rate.
    rate = len(base) / baseline * window
    burst = (len(recent) - rate) / (rate + 1.0)      # +1 keeps a silent
                                                     # company from dividing
                                                     # by ~0 and exploding
    tone_of = load_tone()
    tone = 0.0
    for r in recent:                                 # most recent first
        if r["desc"] in tone_of:
            tone = float(tone_of[r["desc"]])
            break

    flag = 1.0 if any(r["desc"] in FLAG_CATEGORIES for r in recent) else 0.0
    return {"ann_burst": burst, "ann_tone": tone, "ann_flag": flag}


# --- storage ----------------------------------------------------------------

def store_parsed(records):
    """Append parsed records to per-symbol files. -> {symbol: count}."""
    PARSED.mkdir(parents=True, exist_ok=True)
    by = {}
    for r in records:
        by.setdefault(r["symbol"], []).append(r)
    for sym, rs in by.items():
        p = PARSED / f"{sym}.jsonl"
        with p.open("a") as fh:
            for r in rs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {k: len(v) for k, v in by.items()}


_TIMELINE: dict = {}


def timeline(symbol):
    """-> parsed rows for a symbol, or [] if never built.

    Cached, keyed on the file's mtime. Re-reading and re-parsing per call made
    every screen that scores five stocks read five files five times, and the
    audit's "no command answers slower than 2s" guard caught it. mtime rather
    than a timer: the file changes when a backfill runs, not when a clock ticks
    -- the same reasoning as features._CORPUS.
    """
    p = PARSED / f"{symbol}.jsonl"
    if not p.exists():
        return []
    key = (str(p), p.stat().st_mtime, p.stat().st_size)
    got = _TIMELINE.get(symbol)
    if got is not None and got[0] == key:
        return got[1]
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    out.sort(key=lambda x: (x["visible_from"], x["an_dt"]))
    _TIMELINE[symbol] = (key, out)
    return out


def backfill(start=None, end=None, pause=1.5, log=print):
    """Fetch every week between `start` and `end`, then parse and store.

    RESUMABLE by construction: `fetch_range` returns the stored file when one
    exists, so re-running costs no requests for weeks already on disk. That
    matters at 328 requests against someone else's server -- an interrupted run
    that had to start over would triple the load for no reason.

    Rate-limited by default. This is a bulk read of a public archive and it
    should look like one, not like a scrape.
    """
    import time
    import features
    sessions = features.trading_days()
    if not sessions:
        raise RuntimeError("no trading calendar; data/raw is empty")
    start = start or sessions[0]
    end = end or sessions[-1]

    # Wipe any previous parse before rebuilding: store_parsed APPENDS, and a
    # second run over the same weeks would double every row. Silent duplication
    # would inflate ann_burst for exactly the companies that announce most.
    if PARSED.exists():
        for p in PARSED.glob("*.jsonl"):
            p.unlink()

    weeks, d = [], start
    while d <= end:
        weeks.append((d, min(d + timedelta(days=6), end)))
        d += timedelta(days=7)

    fetched = kept = 0
    for n, (d0, d1) in enumerate(weeks, 1):
        cached = (RAW / f"{d0:%Y%m%d}-{d1:%Y%m%d}.json").exists()
        rows = fetch_range(d0, d1)
        recs = parse_rows(rows, sessions)
        store_parsed(recs)
        fetched += len(rows)
        kept += len(recs)
        if n % 10 == 0 or n == len(weeks):
            log(f"  {n}/{len(weeks)} weeks  {d0}  raw {fetched}  kept {kept}")
        if not cached:
            time.sleep(pause)
    log(f"backfill done: {kept} announcements from {len(weeks)} weeks")
    return kept



# --- scoring ----------------------------------------------------------------
# DETERMINISTIC, and that is the point. The three sources this follows all
# reduce to the same shape -- score each item on a bounded scale, aggregate as
# positive minus negative, map to a label -- and two of the three do it without
# a model in the loop:
#
#   gandalf1819/Stock-Market-Sentiment-Analysis  counts positive minus negative
#       words from the Bing lexicon, per article.
#   FinBERT (Araci 2019)  softmax over {positive, neutral, negative}, then
#       SentimentScore = P(pos) - P(neg) in [-1, 1], aggregated per ticker per day.
#   tradeinsight-info/investment-analysis-skills  a model scores each headline
#       in [-1, 1]; mean x 10; bands at +/-3 and +/-7.
#
# This takes the first two's determinism and the third's bands. Same input, same
# number, every time -- which is what a model in the loop cannot give, and what
# a result has to have before it can ever be measured against returns.
#
# The lexicon is FINANCE-specific on purpose. General-purpose word lists rate
# "liability", "cost" and "charge" negative; in a filing they are vocabulary,
# not sentiment. These follow Loughran-McDonald, the standard finance list,
# built precisely because generic lexicons misread accounting language.
LEX_POS = frozenset("""
advance advanced advances award awarded bags beat beats benefit benefited best
better boost boosted breakthrough commissioned complete completed contract
contracts deliver delivered demand doubled efficient enhance enhanced exceed
exceeded exceeds expand expanded expansion favorable gain gained gains grew
grow growing growth high higher highest improve improved improvement improving
increase increased increases jump jumped launch launched lead leading maiden
milestone opportunity optimistic order orders outperform outperformed peak
positive premium profit profitable profits progress raise raised rally record
records recover recovered recovery reward rise rises rising robust rose secured
soar soared solid strength strengthen strong stronger strongest succeed success
successful surge surged surpassed turnaround upgrade upgraded upside win winning
wins won
""".split())

LEX_NEG = frozenset("""
adverse adversely against arrears bankruptcy breach breached cancel cancelled
caution concern concerns crisis critical cut cuts decline declined declines
decrease decreased default defaults delay delayed delays deteriorate
deteriorated difficult diminished disappointing dispute downgrade downgraded
downturn drop dropped fail failed failure fall fallen falling fell fine fined
fraud halt halted hit impairment inability insolvency insolvent investigation
lawsuit liquidation litigation lose loses losing loss losses lost lower lowered
lowest miss missed misses negative penalty plunge plunged poor pressure probe
protest qualified reject rejected resign resignation resigned restructuring
shortfall shut shutdown slow slowdown slump slumped strike struggling
subdued suspend suspended suspension terminate terminated tumble tumbled
uncertain uncertainty unable weak weaker weakness worse worst write-off writeoff
""".split())

# A negator within this many words flips the polarity of what follows. Without
# it "not profitable" scores positive and "no growth" scores positive, which is
# the single most common way a bag-of-words scorer reads a headline backwards.
NEGATORS = frozenset("no not never none cannot without fails failed lack "
                     "lacks lacking barring absent nor".split())
NEGATION_WINDOW = 3

# The five bands, from the third source. Kept identical so a reader who knows
# that vocabulary reads this without a translation.
BANDS = ((7.0, "Very Bullish"), (3.0, "Bullish"),
         (-3.0, "Neutral"), (-7.0, "Bearish"))


def band(score):
    """-> one of the five labels for a score in [-10, +10]."""
    if score is None:
        return "No data"
    if score >= 7.0:
        return "Very Bullish"
    if score >= 3.0:
        return "Bullish"
    if score > -3.0:
        return "Neutral"
    if score > -7.0:
        return "Bearish"
    return "Very Bearish"


_WORD = re.compile(r"[A-Za-z][A-Za-z\-]+")


def text_tone(text):
    """-> polarity in [-1, +1] from the finance lexicon, or 0.0 for no hits.

    `(pos - neg) / (pos + neg)`, which is the aggregation all three sources
    reduce to. Normalised by the number of MATCHED words rather than by the
    length of the text: a long filing is not less certain than a short headline
    saying the same thing, it is just longer.
    """
    words = [w.lower() for w in _WORD.findall(text or "")]
    pos = neg = 0
    for i, w in enumerate(words):
        hit = 1 if w in LEX_POS else (-1 if w in LEX_NEG else 0)
        if not hit:
            continue
        lo = max(0, i - NEGATION_WINDOW)
        if any(x in NEGATORS for x in words[lo:i]):
            hit = -hit
        if hit > 0:
            pos += 1
        else:
            neg += 1
    n = pos + neg
    return 0.0 if not n else (pos - neg) / n


def score_announcement(row, tone_of=None):
    """-> polarity in [-1, +1] for one filing.

    The frozen category table leads and the text adjusts. A category carries a
    stated, pre-registered meaning; the summary text is evidence about THIS
    instance of it. Where the table has no opinion the text stands alone, which
    is how "Updates" -- the largest category in the corpus -- gets read at all.
    """
    tone_of = load_tone() if tone_of is None else tone_of
    cat = float(tone_of.get(row.get("desc", ""), 0))
    txt = text_tone(row.get("text", ""))
    if cat and txt:
        return max(-1.0, min(1.0, 0.7 * cat + 0.3 * txt))
    return cat or txt


# How much evidence it takes to reach the far bands. The plain mean over every
# item -- what the three sources do -- is wrong for THIS corpus, and the first
# run showed it: every stock came back Neutral between +0.00 and +2.50, because
# ~90% of filings are procedural and correctly score 0, so one insolvency notice
# among thirteen becomes one-thirteenth of a signal.
#
# A filing that says nothing is not an observation of neutrality; it is an
# absence of observation, and averaging absences with observations is the same
# error as scoring an empty channel zero. But the opposite -- averaging only the
# items that DID say something -- swings to the other extreme, where a single
# mildly positive filing reads Very Bullish.
#
# So: sum the items that carry signal, divide by (PRIOR + how many there were).
# One item at +1 reaches +3.3, mildly Bullish; three reach +6.0; six reach +7.5.
# The far bands have to be earned by agreement across several items, and a
# lone data point cannot buy one.
PRIOR = 2.0


def aggregate(scores):
    """-> a channel score in [-10, +10], or None when nothing carried signal.

    None, not 0.0. "Nothing was said" and "what was said was balanced" are
    different facts and must not print the same.
    """
    sig = [s for s in scores if s]
    if not sig:
        return None
    return round(sum(sig) / (PRIOR + len(sig)) * 10, 2)

def _selftest():
    sessions = [date(2019, 11, 4), date(2019, 11, 5), date(2019, 11, 6),
                date(2019, 11, 7), date(2019, 11, 8), date(2019, 11, 11)]

    # --- THE rule. These four assertions are why this module exists. --------
    # Before the close on a trading day: visible the same session.
    assert visible_from(datetime(2019, 11, 7, 10, 0), sessions) == date(2019, 11, 7)

    # 22:56 on a trading day -> the NEXT session. This is the real timestamp of
    # a real row from the probe that shaped this design, and it is 60% of the
    # corpus. If this assertion ever passes as 11-07, every number downstream
    # is built on information nobody had.
    assert visible_from(datetime(2019, 11, 7, 22, 56), sessions) == date(2019, 11, 8), \
        "an after-hours announcement leaked into the session it was announced on"

    # Exactly at the close is NOT before the close.
    assert visible_from(datetime(2019, 11, 7, 15, 30), sessions) == date(2019, 11, 8)
    assert visible_from(datetime(2019, 11, 7, 15, 29), sessions) == date(2019, 11, 7)

    # A weekend rolls to Monday whatever the time of day.
    assert visible_from(datetime(2019, 11, 9, 9, 0), sessions) == date(2019, 11, 11)
    # Past the end of the known calendar: unusable, not "today".
    assert visible_from(datetime(2030, 1, 1, 9, 0), sessions) is None
    assert visible_from(None, sessions) is None

    # --- timestamp parsing, both of NSE's formats and the failure case ------
    assert _dt("07-Nov-2019 22:56:00") == datetime(2019, 11, 7, 22, 56)
    assert _dt("07-Nov-2019 22:56") == datetime(2019, 11, 7, 22, 56)
    assert _dt("") is None and _dt("garbage") is None

    # --- parse drops what it cannot date ------------------------------------
    raw = [
        {"symbol": "AAA", "an_dt": "07-Nov-2019 10:00:00", "desc": "Acquisition",
         "attchmntText": "bought a thing"},
        {"symbol": "AAA", "an_dt": "07-Nov-2019 22:56:00", "desc": "Resignation",
         "attchmntText": "someone left"},
        {"symbol": "BBB", "an_dt": "", "desc": "Updates", "attchmntText": "x"},
        {"symbol": "", "an_dt": "07-Nov-2019 10:00:00", "desc": "Updates"},
    ]
    recs = parse_rows(raw, sessions)
    assert len(recs) == 2, f"expected 2 usable rows, got {len(recs)}"
    assert {r["visible_from"] for r in recs} == {"2019-11-07", "2019-11-08"}
    assert all(r["symbol"] == "AAA" for r in recs)

    # --- visible() never returns the future ---------------------------------
    on7 = visible(recs, "2019-11-07")
    assert len(on7) == 1 and on7[0]["desc"] == "Acquisition", \
        "the 22:56 resignation was visible on the day it was announced"
    assert len(visible(recs, "2019-11-08")) == 2

    # --- features -----------------------------------------------------------
    assert features_asof([], "2019-11-08") == {}, "no rows must not fabricate a score"
    f = features_asof(recs, "2019-11-08", window=30, baseline=365)
    assert set(f) == {"ann_burst", "ann_tone", "ann_flag"}, sorted(f)
    assert f["ann_flag"] == 0.0
    # Two filings in 30 days against a 365-day rate of two: a real burst.
    assert f["ann_burst"] > 0, f["ann_burst"]

    # An NSE-demanded explanation raises the flag and nothing else.
    flagged = parse_rows([{"symbol": "CCC", "an_dt": "07-Nov-2019 10:00:00",
                           "desc": "Price movement", "attchmntText": "?"}], sessions)
    assert features_asof(flagged, "2019-11-07")["ann_flag"] == 1.0

    # Tone reads the frozen table. The most recent visible row on 11-08 is the
    # 22:56 Resignation, which the table signs -1; the older Acquisition is
    # deliberately unsigned. Asserting only the no-table case (as this did) meant
    # the check quietly stopped testing anything the day a table was frozen.
    tone_of = load_tone()
    if tone_of:
        assert f["ann_tone"] == float(tone_of.get("Resignation", 0)), \
            f"tone did not read the frozen table: {f['ann_tone']}"
        assert tone_of.get("Acquisition", 0) == 0, \
            "Acquisition must stay unsigned; acquirer returns are disputed"
    else:
        assert f["ann_tone"] == 0.0, "scored a tone with no frozen table"

    print("announcements selftest ok (visibility rule: 22:56 -> next session)")



def update(log=print):
    """Fetch and store every COMPLETED week missing from the raw archive.

    The daily complement of backfill(): backfill rebuilds everything and
    wipes the parse first; this only ever ADDS weeks that are not on disk,
    so the per-symbol parse files grow by append and no stored week is ever
    re-parsed (which would double every row -- see backfill's wipe).

    A week is fetched only when COMPLETE (its end date has passed), so the
    freshest filings can lag up to a week; the 30-day sentiment window
    absorbs that, and fetching a partial week would either duplicate rows
    tomorrow or block its own completion. Resumable for free: fetch_range
    returns the stored file when one exists.
    """
    import time
    import features
    sessions = features.trading_days()
    if not sessions:
        raise RuntimeError("no trading calendar; data/raw is empty")
    start, last = sessions[0], sessions[-1]
    today = date.today()

    have = {}
    for p in RAW.glob("*-*.json"):
        try:
            d0 = datetime.strptime(p.name[:8], "%Y%m%d").date()
            d1 = datetime.strptime(p.name[9:17], "%Y%m%d").date()
        except ValueError:
            continue
        # A bucket may be covered by a PARTIAL file (backfill's end date once
        # cut a week at two days). What matters is the covered END, not the
        # name: skipping on d0 alone left Aug 20-24 unfetched for weeks while
        # printing "archive current".
        have[d0] = max(have.get(d0, date.min), d1)

    weeks, d = [], start
    while d <= min(last, today - timedelta(days=1)):
        week_end = min(d + timedelta(days=6), last)
        weeks.append((d, week_end))
        d += timedelta(days=7)

    fetched = kept = 0
    new_recs = []
    for d0, d1 in weeks:
        if have.get(d0, date.min) >= d1:
            continue
        rows = fetch_range(d0, d1)
        fetched += 1
        if rows:
            recs = parse_rows(rows, sessions)
            # A refetch over a partial file re-serves days already stored.
            # Drop anything whose (symbol, an_dt, desc) the per-symbol parse
            # files already carry, or the append would double every row.
            old = set()
            for sym in {r["symbol"] for r in recs}:
                pf = PARSED / f"{sym}.jsonl"
                if pf.exists():
                    for line in pf.read_text().splitlines():
                        if line.strip():
                            try:
                                r = json.loads(line)
                                old.add((r["symbol"], r["an_dt"], r["desc"]))
                            except json.JSONDecodeError:
                                continue
            recs = [r for r in recs
                    if (r["symbol"], r["an_dt"], r["desc"]) not in old]
            new_recs.extend(recs)
            kept += len(recs)
        log(f"  week {d0}..{d1}: {len(rows)} raw, {kept} kept total")
        time.sleep(1.5)                 # bulk read of a public archive
    if new_recs:
        tally = store_parsed(new_recs)
        log(f"stored {kept} filings across {len(tally)} symbols")
    else:
        log("no missing weeks; archive current")
    return {"weeks_fetched": fetched, "filings": kept}



if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--update" in sys.argv:
        update()
    else:
        print(__doc__.strip().splitlines()[0])
        print(f"\nraw:    {RAW}")
        print(f"parsed: {PARSED}")
        print(f"tone:   {TONE_TABLE} "
              f"({'frozen' if TONE_TABLE.exists() else 'NOT YET FROZEN'})")
