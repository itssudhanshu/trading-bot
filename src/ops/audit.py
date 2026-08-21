#!/usr/bin/env python3
"""Cross-check every load-bearing property of the system.

Not a selftest: selftests run on fixtures and prove a function does what its
author meant. This runs on the REAL corpus and the REAL portfolio, and checks the
properties that would cost money if they were quietly wrong -- lookahead,
survivorship, cost arithmetic, gap fills, and whether the headline number
still reproduces.

Every check prints PASS or FAIL with the number behind it. A check that cannot
run prints SKIP and says why; it never silently passes.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import statistics
import sys
from datetime import date, timedelta

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    return ok


def skip(name, why):
    RESULTS.append((name, None))
    print(f"  [SKIP] {name}")
    print(f"         {why}")


def section(t):
    print(f"\n{t}\n{'-' * len(t)}")


def main():
    import clusters, engine, features, learning, positions, selection, simulate, universe

    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    as_of = days[-1]

    # ---------------------------------------------------------------- DATA
    section("DATA INTEGRITY")

    hol = set()
    p = features.ROOT / "data" / "holidays.json"
    if p.exists():
        import json
        hol = {str(x) for x in json.loads(p.read_text())}
    have, gaps, d = set(days), [], days[0]
    while d <= days[-1]:
        if d.weekday() < 5 and d not in have and d.isoformat() not in hol:
            gaps.append(d)
        d += timedelta(days=1)
    check("no unexplained weekday gaps", not gaps,
          f"{len(days)} sessions {days[0]}..{days[-1]}, {len(gaps)} gaps")

    # every stored bhavcopy must carry the date of the folder it sits in
    import snapshot
    bad_dates = []
    for folder in sorted((features.ROOT / "data" / "raw").iterdir())[-40:]:
        f = folder / "bhavcopy_delivery.csv"
        if not f.exists():
            continue
        inside = snapshot.bhavcopy_date(f.read_bytes())
        if inside and inside.isoformat() != folder.name:
            bad_dates.append((folder.name, str(inside)))
    check("stored bhavcopy matches its folder date", not bad_dates,
          f"last 40 snapshots checked; {len(bad_dates)} mismatched "
          f"(this is the holiday stale-serve trap)")

    # survivorship: symbols that stop trading must remain in history
    ended = [s for s in corpus.values() if s.days[-1] < days[-40]]
    check("delisted symbols retained in history", len(ended) > 0,
          f"{len(ended)} symbols stop before the last 40 sessions and are kept")

    # no bar may hold a price from the future
    fwd = [s.symbol for s in corpus.values() if s.days and s.days[-1] > date.today()]
    check("no bar dated in the future", not fwd, f"{len(fwd)} offenders")

    # .env.example is a COPY of .env's key names, and this project's most
    # frequent defect is a copy that was right when written and never re-checked.
    # A key added to .env and not here means the next person to set the bot up
    # gets a silent half-configuration. The reverse -- a value left in the
    # example -- is a secret in git history, which cannot be undone by deleting
    # it later.
    def _keys(p):
        return {ln.split("=", 1)[0].strip(): ln.split("=", 1)[1].strip()
                for ln in p.read_text().splitlines()
                if "=" in ln and not ln.lstrip().startswith("#")}
    ex, envf = features.ROOT / ".env.example", features.ROOT / ".env"
    if ex.exists():
        exk = _keys(ex)
        filled = [k for k, v in exk.items() if v]
        missing = sorted(set(_keys(envf)) - set(exk)) if envf.exists() else []
        check(".env.example lists every key and carries no values",
              not filled and not missing,
              f"{len(exk)} keys documented; {len(filled)} carry a value "
              f"{filled}; {len(missing)} in .env but undocumented {missing}")

    # ---------------------------------------------------------- SELECTION
    section("SELECTION")

    bands = clusters.size_clusters(corpus, as_of)
    sizes = {k: len(v) for k, v in bands.items()}
    overlap = set(bands["micro"]) & set(bands["small"])
    check("clusters are disjoint", not overlap, f"{sizes}, {len(overlap)} overlap")

    ranked = sorted(corpus, key=lambda s: statistics.median(
        [x for x in corpus[s].turnover if x > 0] or [0]))
    allc = set(bands["micro"]) | set(bands["small"])
    check("most liquid names are excluded", ranked[-1] not in allc,
          f"top-turnover name {ranked[-1]} is not tradeable; "
          f"{len(allc)} of {len(corpus)} are ({len(allc)/len(corpus)*100:.0f}%)")

    picks = clusters.pick(corpus, as_of)
    check("only tradeable clusters are scored", set(picks) == set(clusters.CLUSTERS),
          f"scored: {sorted(picks)}")

    # the 200-DMA gate must EXCLUDE, never merely score down
    below = 0
    for sym, _ in [x for v in picks.values() for x in v]:
        s = corpus[sym]
        i = s.index_of(as_of)
        sma = features.sma(s.close, 200)
        if i is not None and sma[i] and s.close[i] < sma[i]:
            below += 1
    check("no pick sits below its 200-day average", below == 0,
          f"{below} of {sum(len(v) for v in picks.values())} picks below the gate")

    # surveillance must bind where it is known
    rows = selection.build(corpus, as_of)
    bad = []
    for r in rows:
        s = corpus[r["symbol"]]
        i = s.index_of(as_of)
        if (i is not None and i < len(s.restricted) and s.restricted[i]
                and s.surveillance_known[i]):
            bad.append(r["symbol"])
    check("no restricted stock is a candidate", not bad, f"{bad or 'none'}")

    # the bucket must honour the configured mix and never exceed it
    sel = selection.allocate(rows)
    from collections import Counter
    mix = Counter(r["cluster"] for r in sel)
    ok = all(mix[c] <= k for c, k in selection.TAKE_PER_CLUSTER.items())
    check("bucket never exceeds its per-cluster quota", ok,
          f"selected {dict(mix)} against {selection.TAKE_PER_CLUSTER}")
    check("bucket never exceeds MAX_POSITIONS", len(sel) <= selection.MAX_POSITIONS,
          f"{len(sel)} of {selection.MAX_POSITIONS}")

    # every selected name must have actually triggered
    untrig = [r["symbol"] for r in sel if not r.get("triggered")]
    check("every selected name has triggered", not untrig, f"{untrig or 'none'}")

    # ------------------------------------------------------------- MONEY
    section("MONEY")

    per = selection.CAPITAL * selection.DEPLOY_PCT / 100 / selection.MAX_POSITIONS
    q, risk = selection.position_size(selection.CAPITAL, 100.0)
    check("position size matches the deployment cap", abs(q * 100.0 - per) < 100,
          f"Rs {q*100:,.0f} per stock vs cap Rs {per:,.0f}")
    check("a full bucket stays inside the deployment cap",
          per * selection.MAX_POSITIONS <= selection.CAPITAL * selection.DEPLOY_PCT / 100 + 1,
          f"Rs {per*selection.MAX_POSITIONS:,.0f} of Rs {selection.CAPITAL:,}")
    big, _ = selection.position_size(selection.CAPITAL, 100.0, mult=99)
    check("the risk rule caps any single position", big * 100.0 < selection.CAPITAL * 0.25,
          f"hard cap Rs {big*100:,.0f} = {big*100/selection.CAPITAL*100:.0f}% of capital")

    # cost arithmetic, checked by hand
    c = engine.Costs()
    v = 100_000.0
    manual = (20 + v*0.001 + v*0.0000297 + v*1e-6
              + 0.18*(20 + v*0.0000297 + v*1e-6) + v*0.00015)
    check("buy-side cost matches a hand calculation",
          abs(c.charge(v, "BUY") - manual) < 0.01,
          f"Rs {c.charge(v,'BUY'):.2f} vs Rs {manual:.2f} computed independently")
    # Independent of c.dp_sell. The earlier version compared the sell charge
    # against a formula that itself read c.dp_sell, so setting DP to zero moved
    # both sides together and the check passed while the charge had vanished --
    # a test that could not fail for the reason it claimed to test.
    EXPECT_DP = 15.93
    sell_minus_buy = c.charge(v, "SELL") - c.charge(v, "BUY")
    check("sell side pays DP and no stamp duty",
          abs(sell_minus_buy - (EXPECT_DP - v * 0.00015)) < 0.01,
          f"sell - buy = Rs {sell_minus_buy:+.2f}; expected "
          f"Rs {EXPECT_DP - v*0.00015:+.2f} (DP {EXPECT_DP} less stamp "
          f"Rs {v*0.00015:.2f})")
    check("the DP charge is actually applied", c.charge(v, "SELL") - c.charge(v, "BUY")
          + v * 0.00015 > 10.0,
          f"DP component Rs {sell_minus_buy + v*0.00015:.2f}, must be non-zero")

    # ------------------------------------------------------------- FILLS
    section("FILLS AND EXITS")

    import tempfile, shutil
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    orig_db, orig_led = positions.DB, learning.LEDGER
    positions.DB, learning.LEDGER = tmp / "p.db", tmp / "l.jsonl"
    try:
        conn = positions.db()
        sym = sel[0]["symbol"] if sel else rows[0]["symbol"]
        positions.queue([{"symbol": sym, "cluster": "small", "qty": 10,
                      "stop": 1.0, "target": 999.0}], as_of, conn)
        filled, _ = positions.step(corpus, as_of, conn)
        check("an order cannot fill on its own signal day", not filled,
              f"queued and stepped on {as_of}; filled {len(filled)}")

        # A HELD name must not consume the room meant for a new pick. daily.py
        # used to slice allocate()[:room], so a candidate already in the bucket
        # spent the only free position and was then skipped as a duplicate --
        # queueing nothing, every session, while the cash sat idle. simulate.py
        # has always `continue`d past a held name without spending room, so the
        # forward book was running a rule the backtest never ran.
        # sym is already pending from the queue() above. Room for one: the
        # SECOND row must be the one that takes it.
        other = next((x["symbol"] for x in rows if x["symbol"] != sym), "ZZZTEST")
        n = positions.queue([{"symbol": sym, "cluster": "small", "qty": 10,
                              "stop": 1.0, "target": 999.0},
                             {"symbol": other, "cluster": "micro", "qty": 10,
                              "stop": 1.0, "target": 999.0}],
                            as_of, conn, limit=1)
        queued = {r[0] for r in conn.execute(
            "SELECT symbol FROM pos WHERE status='pending'")}
        check("a held name does not consume the room for a new pick",
              n == 1 and other in queued,
              f"{sym} held, room 1 -> queued {n} ({other} in: {other in queued})")
    finally:
        positions.DB, learning.LEDGER = orig_db, orig_led
        shutil.rmtree(tmp, ignore_errors=True)

    # gap handling, on the real simulator
    now_config = (f"{selection.STOP_PCT:g}/{selection.TARGET_PCT:g}/"
                  f"{selection.HOLD_DAYS}d")
    r = simulate.run(corpus, days, stop_pct=selection.STOP_PCT,
                     target_pct=selection.TARGET_PCT, hold=selection.HOLD_DAYS,
                     max_pos=5, refresh=5, trigger="breakout", impact_c=1.0)
    t = r["trades"]
    stops = [x["ret"] for x in t if x["why"] == "stop"]
    check("gapped stops fill worse than the nominal stop",
          bool(stops) and statistics.fmean(stops) < -10.0,
          f"{len(stops)} stops, mean {statistics.fmean(stops) if stops else 0:+.2f}% "
          f"against a -10% nominal")
    holds = [x["why"] for x in t]
    check("every trade exits by stop, target or time",
          set(holds) <= {"stop", "target", "time"}, f"{Counter(holds)}")

    # The wallet must add up, FOR EVERY BUCKET: value = cash + holdings.
    #
    # This check went SKIPPED, not failed, when /wallet was rewritten per
    # bucket: it parsed "*Total value*" and the labels became an indented
    # "Value" under a bucket header, so the regex matched nothing and the
    # check quietly stopped protecting the arithmetic. A skipped check is worse
    # than a failing one -- it reports as "0 failed" and nobody looks. It now
    # parses each bucket section and asserts one of them exists, so the same
    # drift fails loudly next time.
    import tg as _tg
    import re as _re
    txt = _tg.COMMANDS["/wallet"]()
    _sections = _re.split(r"^\*([A-Z ]+)\* — ", txt, flags=_re.M)[1:]
    _wallets = {}
    for _i in range(0, len(_sections) - 1, 2):
        _name, _body = _sections[_i], _sections[_i + 1]
        _n = {k: float(v.replace(",", "")) for k, v in _re.findall(
            r"^\s+(Value|Cash|Invested)\s+Rs ([\d,\-]+)", _body, _re.M)}
        if len(_n) == 3:
            _wallets[_name] = _n
    _bad = [f"{k}: total {v['Value']:,.0f} vs cash {v['Cash']:,.0f} + invested "
            f"{v['Invested']:,.0f}" for k, v in _wallets.items()
            if abs(v["Value"] - (v["Cash"] + v["Invested"])) >= 2]
    check("every bucket's wallet total equals cash plus holdings",
          bool(_wallets) and not _bad,
          f"{len(_wallets)} bucket(s) check out: {', '.join(sorted(_wallets))}"
          if _wallets and not _bad else
          (" | ".join(_bad) if _bad else "no bucket section parsed from /wallet"))

    # every tg.* call made from the daily runner must actually resolve
    import ast, importlib.util, tg

    def _src(mod):
        """Read a module's source by RESOLVING it, not by guessing where it sits.

        These were `features.ROOT / "daily.py"`, correct while every file lived
        at the repo root and a FileNotFoundError the day they moved into
        src/ops/. find_spec goes through sys.path -- the same mechanism the
        import above uses -- and does not execute the module.
        """
        s = importlib.util.find_spec(mod)
        assert s and s.origin, f"cannot locate {mod}.py to audit it"
        return Path(s.origin).read_text()

    missing = []
    for f in ("daily", "agent"):
        src = _src(f)
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "tg" and not hasattr(tg, node.attr)):
                missing.append(f"{f}: tg.{node.attr}")
    check("every tg.* call from the daily runner resolves", not missing,
          f"{missing or 'none'}")

    # ...and tg.py against ITSELF. The check above only looked at other files,
    # so `tg.py --overview` called a cmd_overview() that had been deleted and
    # the audit stayed green. A CLI branch nobody runs daily is exactly where a
    # dead name survives.
    own = []
    for node in ast.walk(ast.parse(_src("tg"))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id.startswith("cmd_") and not hasattr(tg, node.func.id)):
            own.append(f"tg.py: {node.func.id}()")
    check("every cmd_* called inside tg.py exists", not own, f"{own or 'none'}")

    # Every advertised command must dispatch, and every dispatchable one must be
    # advertised or deliberately aliased -- /help is the only map the operator has.
    _help = {tg.canon(c) for c in
             __import__("re").findall(r"/[a-z][a-z_-]*", tg.cmd_help())}
    undoc = sorted(c for c in set(tg.COMMANDS) - tg.ALIASES
                   if tg.canon(c) not in _help)
    check("every command appears in /help", not undoc, f"{undoc or 'none'}")

    # RENDER every one of them and check the markup Telegram will parse. This
    # lived only inside send(), so the only way to find an unbalanced * or _ was
    # to post the message -- three of them reached the user's phone that way.
    # Here because the audit already pays for the corpus; a command that raises
    # while building its own text also fails, which is the other half of it.
    bad = []
    for name in sorted(set(tg.COMMANDS) - {"/start"}):
        try:
            tg.check_markup(tg.COMMANDS[name](None))
        except Exception as e:
            bad.append(f"{name}: {type(e).__name__} {str(e)[:90]}")
    check("every command renders and its markup is balanced", not bad,
          f"{len(tg.COMMANDS) - 1} rendered" if not bad else " | ".join(bad))

    # The INSTALLED plists, not the ones in the repo. paths.script() and its
    # selftest cover the paths agent.py spawns; nothing covered the two paths
    # launchd spawns, and both pointed at the deleted root tg.py and agent.py
    # for a day after the src/ move -- the listener simply stopped and the
    # scheduler never started. launchd's failure is a line in a log nobody
    # reads, so the check has to live somewhere a person looks.
    # plutil, not plistlib: these plists carry "--" inside their XML comments,
    # which expat rejects and CFPropertyList accepts. The authority on whether
    # launchd can read a plist is the parser launchd uses, so asking Python's
    # would have failed both files for a reason launchd does not care about.
    import json as _json, subprocess as _sp
    la = _pl.Path.home() / "Library" / "LaunchAgents"
    plists = sorted(la.glob("*tradingbot*.plist")) + sorted(la.glob("trading-bot*.plist"))
    if not plists:
        skip("every installed launchd job points at a file that exists",
             f"nothing installed in {la} -- run the cp/launchctl step in README")
    else:
        stale = []
        for p in plists:
            # NOT `r` -- main() reuses that name for a stored result 100 lines
            # down, and shadowing it here raised where the shadow was invisible.
            conv = _sp.run(["plutil", "-convert", "json", "-o", "-", str(p)],
                           capture_output=True, text=True)
            if conv.returncode:
                stale.append(f"{p.name}: launchd cannot parse it -- "
                             f"{conv.stderr.strip()[:80]}")
                continue
            d = _json.loads(conv.stdout)
            # A plist whose filename is not its Label loads by path but is not
            # found by label, so `launchctl list` shows nothing and /health is
            # right to say no job is registered.
            if p.stem != d.get("Label"):
                stale.append(f"{p.name}: Label is {d.get('Label')}")
            for a in d.get("ProgramArguments", []):
                if str(a).startswith(str(paths.ROOT)) and not _pl.Path(a).exists():
                    stale.append(f"{p.name}: {a} does not exist")
        check("every installed launchd job points at a file that exists",
              not stale, f"{len(plists)} installed" if not stale else " | ".join(stale))

    # The shell scripts, which are the same defect as the plists above and were
    # the one place nothing looked. Every path inside a script is relative to
    # wherever it cd's to, and `cd "$(dirname "$0")"` lands in scripts/ -- so
    # `python3 src/ops/tg.py` resolved from the repo root before the src/ move
    # and has resolved to nothing since. Neither script complained: run_listener
    # is the by-hand fallback launchd makes unnecessary, and setup.sh's
    # `for f in *.py` iterated an EMPTY glob and printed no failures at all.
    # A loop over nothing is not a passing test, and that is the whole reason
    # this check resolves the path rather than trusting the script to say so.
    # EVERY local here is _sh_-prefixed, and that is not style. main() is one
    # long function: `t` is the trade list from the simulation 150 lines up and
    # `len(t)` is the headline trade count 100 lines down, so a plain `for t in`
    # here reported n=22 -- the length of the string "tests/run_selftests.py" --
    # against a baseline of 195, with the CAGR still matching to the penny. The
    # plist check above carries the same warning about `r`. The baseline check
    # caught it; nothing else would have.
    import re as _re
    stale_sh, n_sh = [], 0
    for _sh_p in sorted((paths.ROOT / "scripts").rglob("*.sh")):
        _sh_text = _sh_p.read_text()
        _sh_cd = _re.search(r'^\s*cd\s+"\$\(dirname\s+"\$0"\)([^"]*)"', _sh_text, _re.M)
        _sh_cwd = ((_sh_p.parent / _sh_cd.group(1).lstrip("/")).resolve() if _sh_cd
                   else _sh_p.parent)
        # Two shapes, and both are needed. A bare `$PY backfill.py` carries no
        # slash to recognise it by, and a bare `tg.py` inside a COMMENT would be
        # a false alarm if every bare name were checked -- so: things a shell
        # actually runs, plus things written as paths.
        _sh_toks = set(_re.findall(
            r"(?:\$PY|python3?)\s+(?:-\S+\s+)*([\w./-]+\.py)", _sh_text))
        _sh_toks |= set(_re.findall(r"[\w.-]*/[\w./-]+\.(?:py|sh)\b", _sh_text))
        for _sh_t in sorted(_sh_toks):
            if "*" in _sh_t:
                continue
            n_sh += 1
            if not (_sh_cwd / _sh_t).exists():
                stale_sh.append(
                    f"{_sh_p.name}: {_sh_t} (cd lands in {_sh_cwd.name}/)")
    check("every script a shell script runs exists where it cd's to",
          not stale_sh,
          f"{n_sh} paths in {len(list((paths.ROOT / 'scripts').rglob('*.sh')))} scripts"
          if not stale_sh else " | ".join(stale_sh))

    # -------------------------------------------------------------- BUCKET
    section("THE BUCKET")
    import positions
    conn = positions.db()

    # One bucket, buying the top of the ranking. The deeper buckets that ran
    # for a day are gone; what they bought is still open and still labelled,
    # so the check is that nothing NEW is created outside the one bucket.
    sel = {r["symbol"] for r in selection.allocate(rows)}
    top = set()
    for c, k in selection.TAKE_PER_CLUSTER.items():
        top |= {r["symbol"] for r in rows if r["cluster"] == c}.intersection(
            {r["symbol"] for r in [x for x in rows if x["cluster"] == c][:k]})
    check("the bucket only buys the top of each cluster", sel <= top,
          f"selected {sorted(sel)}; outside the top ranks: {sorted(sel - top) or 'none'}")

    queued = {r["bucket"] for r in positions.summary(conn, which=None)["rows"]
              if r["status"] == "pending"}
    check("nothing is queued outside the one bucket",
          queued <= {positions.MAIN}, f"queued into {sorted(queued) or 'nothing'}")

    # A STALE text record is worse than none: it reads as the forward evidence
    # while missing whatever the last tick did. Checked by replaying it into a
    # throwaway database and comparing rows, not by trusting its timestamp.
    COLS = ("id,symbol,cluster,status,queued_on,entry_day,entry_px,qty,stop,"
            "target,exit_day,exit_px,exit_reason,net,bucket,origin")
    if positions.RECORD.exists():
        import sqlite3 as _sq
        import tempfile as _tf
        conn.row_factory = None
        with _tf.TemporaryDirectory() as _td:
            _t = _sq.connect(Path(_td) / "replay.db")
            _t.executescript(positions.RECORD.read_text())
            _live = list(conn.execute(f"SELECT {COLS} FROM pos ORDER BY id"))
            _back = list(_t.execute(f"SELECT {COLS} FROM pos ORDER BY id"))
            _nlog = _t.execute("SELECT count(*) FROM pos_log").fetchone()[0]
            _mlog = conn.execute("SELECT count(*) FROM pos_log").fetchone()[0]
        check("the committed order record matches the live database",
              _live == _back and _nlog == _mlog,
              f"{len(_live)} live rows / {len(_back)} replayed, "
              f"audit trail {_mlog} / {_nlog}")
    else:
        skip("the committed order record matches the live database",
             f"{positions.RECORD.name} not written yet -- run daily.py")

    # /wallet tells the operator "holding 4 of 5, typical is X" from a STORED
    # baseline, and nothing in the pipeline ever wrote that file. It said 2.83
    # against a true 3.09, and 20% of sessions against a true 27% -- a derived
    # number with no writer, which is this project's most repeated defect. The
    # live simulation is already in hand two hundred lines up, so comparing them
    # costs nothing; --rebaseline rewrites it the same way it rewrites the
    # headline.
    import analysis
    _occ = analysis.load_occupancy()
    if _occ:
        _live_occ = {"dist": {int(k): v for k, v in r["occ_dist"].items()},
                     "mean": round(r["occupancy"], 3),
                     "config": now_config}
        _drift = abs(_occ["mean"] - _live_occ["mean"])
        if "--rebaseline" in sys.argv and _drift > 0.05:
            analysis.save_occupancy(_live_occ["dist"], _live_occ["mean"], now_config)
            print(f"         REBASELINED occupancy to {_live_occ['mean']}")
            _occ = analysis.load_occupancy()
            _drift = abs(_occ["mean"] - _live_occ["mean"])
        check("the occupancy /wallet quotes matches the live simulation",
              _drift <= 0.05 and _occ.get("config") == now_config,
              f"stored {_occ['mean']:.2f} ({_occ.get('config')}) vs live "
              f"{_live_occ['mean']:.2f} ({now_config})")
    else:
        skip("the occupancy /wallet quotes matches the live simulation",
             "no occupancy baseline stored")

    # The tighter-stop counterfactual must be exact -- computed on positions
    # that really existed -- and must refuse a level at or above the entry.
    sh = positions.shadow_stop(features.load_corpus(), conn, pct=5.0)
    bad = [x for x in sh if not (0 < x["level"] < x["entry"])]
    check("the shadow stop sits below every real entry", not bad,
          f"{len(sh)} positions checked, {len(bad)} malformed")

    # -------------------------------------------------------- REPRODUCES
    section("HEADLINE NUMBER")
    # A hardcoded number cannot tell a REGRESSION from ordinary drift: every new
    # trading session shifts the result slightly. Store the session count with
    # the baseline. Same corpus and a different number is a break; a bigger
    # corpus and a small change is just Monday happening.
    import json as _j
    bf = paths.SDATA / "baseline.json"   # this strategy's headline, not the repo's
    now_b = {"sessions": len(days), "cagr": round(r["cagr"], 2),
             "n": len(t), "maxdd": round(r["maxdd"], 1),
             "config": now_config}
    if not bf.exists():
        bf.write_text(_j.dumps(now_b, indent=1))
        skip("the recorded baseline still reproduces",
             f"no baseline stored; recorded {now_b}")
    else:
        old = _j.loads(bf.read_text())
        if old.get("config", now_b["config"]) != now_b["config"]:
            # A rule change is NOT drift. Absorbing it would quietly re-record
            # whatever the new rules produce, which is how a regression and a
            # deliberate change become indistinguishable. Re-record on purpose:
            #     python3 audit.py --rebaseline
            check("the recorded baseline still reproduces", False,
                  f"exit rules changed {old.get('config', '?')} -> "
                  f"{now_b['config']}; CAGR {old['cagr']:+.2f}% -> "
                  f"{now_b['cagr']:+.2f}%. Re-record deliberately with "
                  f"`python3 audit.py --rebaseline` once the change is intended")
        elif old["sessions"] == now_b["sessions"]:
            check("the recorded baseline still reproduces",
                  abs(now_b["cagr"] - old["cagr"]) < 0.01 and now_b["n"] == old["n"],
                  f"CAGR {now_b['cagr']:+.2f}% vs {old['cagr']:+.2f}%, "
                  f"n={now_b['n']} vs {old['n']}, same {old['sessions']} sessions")
        else:
            grew = now_b["sessions"] - old["sessions"]
            moved = abs(now_b["cagr"] - old["cagr"])
            check("baseline drift is proportionate to new data", moved < 0.5 * grew + 0.5,
                  f"corpus grew {grew} session(s); CAGR {old['cagr']:+.2f}% -> "
                  f"{now_b['cagr']:+.2f}% (moved {moved:.2f})")
        # --rebaseline applies to EVERY branch, not just a config change. The
        # circuit-lock guard (L58) moved the headline from +14.14% to +7.59%
        # with `config` untouched -- stop, target and hold did not change, the
        # set of fills the engine believes in did. That landed in the
        # same-sessions branch, which had no way to re-record, so the audit
        # would have failed forever until someone hand-edited baseline.json.
        # The flag IS the deliberate act; it does not need a blessed branch.
        if "--rebaseline" in sys.argv:
            bf.write_text(_j.dumps(now_b, indent=1))
            print(f"         REBASELINED to {now_b}")
            bf.write_text(_j.dumps(now_b, indent=1))

    # ------------------------------------------------------------ SUMMARY
    section("SUMMARY")
    p_ = sum(1 for _, o in RESULTS if o is True)
    f_ = sum(1 for _, o in RESULTS if o is False)
    s_ = sum(1 for _, o in RESULTS if o is None)
    print(f"  {p_} passed, {f_} failed, {s_} skipped")
    if f_:
        print("\n  FAILED:")
        for n, o in RESULTS:
            if o is False:
                print(f"    - {n}")
    return 1 if f_ else 0


if __name__ == "__main__":
    sys.exit(main())
