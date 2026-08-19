#!/usr/bin/env python3
"""Self-running agent. One entry point that decides what is DUE.

Runs often (hourly), does only what is outstanding, and is safe to run twice.
That is deliberately different from several fixed-time schedules: a machine that
was asleep at 19:00 misses a fixed job forever, but an agent that asks "is
today's snapshot missing?" simply catches up on the next wake. NSE publishes
surveillance state for the current day only, so a missed session is permanent.

  python3 agent.py --once      # do what is due, exit  (for launchd/systemd/cron)
  python3 agent.py --daemon    # loop, for a machine left running
  python3 agent.py --status    # what is due, change nothing

DOES NOT spend holdout budget. Research cycles stop at the shortlist; a person
runs `pipeline.py --consult` deliberately. An unattended loop would exhaust 50
lifetime consultations in a weekend.
"""

# First: puts core/, bucket/, research/ and ops/ on sys.path.
import paths  # noqa: F401
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from paths import ROOT      # one definition; see paths.py
STATE = ROOT / "data" / "agent_state.json"
DIGEST = ROOT / "data" / "DIGEST.md"
LOCK = ROOT / "data" / "agent.lock"
RESEARCH_EVERY_DAYS = 6
LOCK_STALE_HOURS = 6


def _state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, default=str))


def _busy():
    """True if a heavy job is already running. Two searches at once would
    thrash memory and interleave writes to candidates.jsonl."""
    try:
        out = subprocess.run(["pgrep", "-f", "daily.py|snapshot.py"],
                             capture_output=True, text=True).stdout
        return bool([p for p in out.split() if p and int(p) != os.getpid()])
    except Exception:
        return False


def _lock():
    """Crash-safe: a lock older than LOCK_STALE_HOURS is assumed abandoned,
    otherwise one killed run would block the agent forever."""
    if LOCK.exists():
        age = (time.time() - LOCK.stat().st_mtime) / 3600
        if age < LOCK_STALE_HOURS:
            return False
        LOCK.unlink()
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))
    return True


def _unlock():
    if LOCK.exists():
        LOCK.unlink()


# The complete set of things the agent may run. Anything not here is not the
# agent's business -- the strategy search that used to live here is retired.
# Paths go through paths.script() because these are SPAWNED, not imported: a
# bad string here fails inside a subprocess whose rc lands in a log file, so the
# agent goes on reporting healthy while nothing runs. _selftest asserts every
# one of them exists on disk.
_S = paths.script
_JOBS = {
    "snapshot": [_S("ops/snapshot.py")],
    "catchup":  [_S("ops/snapshot.py"), "--catchup"],
    "pbook":    ["daily.py"],
    # Morning: fill pending orders at the day's actual open, rather than
    # leaving the bucket nine hours behind the market.
    "fill":     ["daily.py", "--fill-live"],
    # Nothing refreshed data/audit.log, so /review reported whatever number was
    # written the last time someone ran it by hand -- it was still claiming
    # "21 passed" after the suite had grown to 30. A self-check nobody runs is
    # not a self-check.
    "audit":    [_S("ops/audit.py")],
    # Evening, after the bucket has stepped and the audit has run: push the day's
    # ranking, the evidence so far, and any weight change that has EARNED
    # itself. It proposes and never applies -- see tg.cmd_review.
    "review":   ["tg.py", "--review"],
}
_JOB_NAMES = tuple(_JOBS)


def _cmd_for(job):
    import sys as _s
    return [_s.executable] + _JOBS[job]


def _gaps_outstanding(now=None):
    """-> True if a weekday inside the collected range has no bhavcopy.

    Today counts only after 18:00, since the file does not exist before then.
    """
    from datetime import datetime as _dt, timedelta as _td
    now = now or _dt.now()
    raw = ROOT / "data" / "raw"
    if not raw.exists():
        return False
    have = {p.name for p in raw.iterdir()
            if (p / "bhavcopy_delivery.csv").exists()}
    if not have:
        return True
    hol = set()
    hf = ROOT / "data" / "holidays.json"
    if hf.exists():
        try:
            hol = {str(x) for x in json.loads(hf.read_text())}
        except Exception:
            pass
    last = date.fromisoformat(max(have))
    d, end = last + _td(days=1), now.date()
    if now.hour < 18:
        end -= _td(days=1)
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in hol and d.isoformat() not in have:
            return True
        d += _td(days=1)
    return False


def due(now=None):
    """-> list of task names outstanding right now."""
    now = now or datetime.now()
    st = _state()
    todo = []

    # Data collection: driven by what is MISSING, not by the clock.
    #
    # This used to test for asm.json, which is served all day. Running the
    # snapshot once in the MORNING therefore created asm.json while the
    # bhavcopy was still 404 -- and the evening check then saw asm.json, judged
    # the day collected, and never fetched the prices. The bucket sat unfilled
    # with the job reporting "ok". Test for the file that actually matters.
    raw = ROOT / "data" / "raw"
    today = now.date()
    if now.weekday() < 5 and now.hour >= 18:
        if not (raw / today.isoformat() / "bhavcopy_delivery.csv").exists():
            todo.append("snapshot")

    # Catch-up must be driven by whether gaps REMAIN, not by whether it has run
    # today: a morning run cannot collect an evening file, and once-per-day
    # bookkeeping made it look done.
    if st.get("last_catchup") != str(today) or _gaps_outstanding(now):
        todo.append("catchup")
    if st.get("last_pbook") != str(today) and now.weekday() < 5 and now.hour >= 18:
        todo.append("pbook")
    # After pbook, so both read a bucket that has already stepped today. Order
    # matters: audit writes the log that review quotes.
    if st.get("last_audit") != str(today) and now.weekday() < 5 and now.hour >= 18:
        todo.append("audit")
    if st.get("last_review") != str(today) and now.weekday() < 5 and now.hour >= 18:
        todo.append("review")
    # The open is at 09:15. Starting at 09:00 asked for a bar that did not
    # exist yet; the quote source correctly refused, the job exited clean, and
    # the agent recorded the fill as DONE for the day -- so the orders never
    # filled at all. Wait until the open has actually printed.
    if (st.get("last_fill") != str(today) and now.weekday() < 5
            and (now.hour > 9 or (now.hour == 9 and now.minute >= 20))
            and now.hour < 18):
        todo.append("fill")
    return todo


def run_task(name, log=print):
    cmds = {k: [sys.executable] + v for k, v in _JOBS.items()}
    # audit's output IS the artefact other things read; keep it at its
    # canonical path rather than agent_audit.log.
    logf = ROOT / "data" / ("audit.log" if name == "audit"
                            else f"agent_{name}.log")
    with open(logf, "w") as f:
        rc = subprocess.run(cmds[name], stdout=f, stderr=subprocess.STDOUT,
                            cwd=ROOT, timeout=6 * 3600).returncode
    log(f"  {name}: {'ok' if rc == 0 else f'rc={rc}'}")
    return rc == 0


def _jobs_loaded():
    """Are the launchd/systemd jobs actually registered? A plist sitting in the
    repo is not a running agent, and that gap is invisible otherwise."""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=10).stdout
        return [l.split()[-1] for l in out.splitlines() if "tradingbot" in l]
    except Exception:
        return []


def attention():
    """Things a person should know about. Empty is the normal, good state.

    A monitor that only reports success trains you to stop reading it. This
    reports what is WRONG or waiting, so an empty list is the signal.
    """
    out = []
    st = _state()
    today = date.today()

    ok, why = health()
    if not ok:
        out.append(f"agent {why}")
    if not _jobs_loaded():
        out.append("no launchd job registered -- nothing runs on a schedule")

    # Surveillance is the one unrecoverable data stream: NSE serves the current
    # day only, so a gap is permanent and silent.
    #
    # Judge it on the DATA, not on the agent's bookkeeping. "the agent has never
    # run one" fired every hour while three snapshots sat on disk and no weekday
    # evening had yet passed since install -- reporting "not due yet" as though
    # it were "broken". A monitor that fires in normal conditions gets ignored,
    # and then the real alarm is missed too.
    snaps = sorted(p.parent.name for p in (ROOT / "data" / "raw").glob("*/asm.json"))
    if not snaps:
        out.append("no surveillance snapshot has EVER been collected")
    else:
        gap = (today - date.fromisoformat(snaps[-1])).days
        # Only weekdays owe a snapshot; a Monday morning is 3 days after Friday.
        if gap > 4:
            out.append(f"newest surveillance snapshot is {gap} days old "
                       f"({snaps[-1]}) -- these gaps are PERMANENT")

    try:
        import snapshot as _s
        mb, ms = _s.gaps()
        if ms:
            out.append(f"{len(ms)} trading days missing surveillance, unrecoverable")
        if mb:
            out.append(f"{len(mb)} days missing bhavcopy (recoverable: --catchup)")
    except Exception:
        pass

    # Can the bucket still fill its mix? If fewer names survive the
    # 200-day-average gate, the surveillance flags and the sizing cap than the
    # mix needs, the bucket quietly under-fills -- and that looks identical to
    # "nothing triggered today", which is normal. The threshold is read from
    # TAKE_PER_CLUSTER, so it follows the mix instead of restating it.
    #
    # This used to need micro rank 12, because the retired deeper buckets read
    # the same list four levels down (L56). One bucket needs 3 micro / 2 small,
    # so the margin is now large and this should stay silent. If it ever fires,
    # the gate has emptied a whole cluster.
    try:
        import clusters as _c
        import features as _f
        import selection as _p
        _corpus = _f.load_corpus()
        _day = max(d for s in _corpus.values() for d in s.days)
        _rows = _p.build(_corpus, _day)
        for _cl, _need in _p.TAKE_PER_CLUSTER.items():
            _have = len([r for r in _rows if r["cluster"] == _cl])
            if _have < _need:
                out.append(f"only {_have} {_cl} stocks passed the filters, but the "
                           f"bucket needs {_need} -- it will hold fewer than 5 "
                           f"(raise clusters.PER_CLUSTER, currently "
                           f"{_c.PER_CLUSTER})")
    except Exception:
        pass

    # The bucket is queued but nothing has filled for several sessions: either
    # no candidate is triggering, which is normal, or pbook has stopped running.
    try:
        import positions
        s = positions.summary()
        if s["pending"] and st.get("last_pbook"):
            gap = (today - date.fromisoformat(str(st["last_pbook"])[:10])).days
            if gap > 4:
                out.append(f"bucket has {s['pending']} queued but daily last ran "
                           f"{gap} days ago")
    except Exception:
        pass
    return out


def digest():
    """Human-readable state. The logs hold detail; this holds the answer."""
    st = _state()
    lines = [f"# Agent digest", f"_{datetime.now():%Y-%m-%d %H:%M}_", ""]
    att = attention()
    lines += ["## needs attention"] + (
        [f"- {a}" for a in att] if att else ["- nothing"]) + [""]
    nxt = due()
    lines += [f"**due now:** {', '.join(nxt) if nxt else 'nothing'}", ""]
    days = len(list((ROOT / "data" / "raw").glob("*/bhavcopy_delivery.csv")))
    surv = len(list((ROOT / "data" / "raw").glob("*/asm.json")))
    lines += [f"- corpus: **{days}** trading days, **{surv}** with surveillance"]
    try:
        import positions, selection
        s = positions.summary()
        lines.append(f"- bucket: **{s['open']}** open, **{s['pending']}** queued, "
                     f"**{s['closed']}** closed, realised "
                     f"**Rs {s['realised']:+,.0f}** of Rs {selection.CAPITAL:,}")
    except Exception as e:
        lines.append(f"- bucket: unavailable ({type(e).__name__})")
    lines += ["", f"last tasks: {json.dumps({k: str(v) for k, v in st.items()})}"]
    DIGEST.write_text("\n".join(lines) + "\n")
    return DIGEST


HEARTBEAT = ROOT / "data" / "agent_heartbeat.json"


def beat():
    """Written on EVERY run, including runs with nothing to do.

    Without it there is no way to distinguish "the agent is running and nothing
    was due" from "the agent has not run for a week". Those look identical from
    the outside and only one of them is fine.
    """
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(json.dumps({"at": datetime.now().isoformat()}))


def last_beat():
    if not HEARTBEAT.exists():
        return None
    try:
        return datetime.fromisoformat(json.loads(HEARTBEAT.read_text())["at"])
    except Exception:
        return None


def health():
    """-> (alive: bool, human explanation). Expects an hourly timer."""
    lb = last_beat()
    if lb is None:
        return False, "agent has NEVER run (timer not installed?)"
    mins = (datetime.now() - lb).total_seconds() / 60
    if mins < 90:
        return True, f"alive, last ran {mins:.0f} min ago"
    if mins < 60 * 24:
        return False, f"STALE: last ran {mins/60:.1f} hours ago (expected hourly)"
    return False, f"DEAD: last ran {mins/60/24:.1f} days ago"


def once(log=print):
    beat()
    todo = due()
    if not todo:
        log("nothing due")
        digest()
        return []
    if _busy():
        log(f"due {todo} but a heavy job is already running; skipping")
        return []
    if not _lock():
        log("another agent holds the lock; skipping")
        return []
    done = []
    try:
        st = _state()
        for t in todo:
            log(f"running {t}")
            if run_task(t, log=log):
                done.append(t)
                # Every job name must be here. "fill" was missing: the lookup
                # raised KeyError after a successful morning fill, so digest()
                # never ran and last_fill was never stored -- due() then
                # re-queued the job on every hourly tick.
                st[f"last_{t}"] = str(date.today())
                _save(st)
    finally:
        _unlock()
    digest()
    if done:
        log(f"done: {', '.join(done)}")
    return done


def notify(msg, title="trading-bot"):
    """Local macOS banner. Local only -- nothing leaves the machine."""
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification {json.dumps(msg)} with title '
                        f'{json.dumps(title)}'], timeout=10)
    except Exception:
        pass


def _selftest():
    import tempfile
    # Every job the agent may spawn must exist on disk. These run through
    # subprocess with cwd=ROOT, so a stale path fails where nobody looks --
    # moving ops/ under src/ broke snapshot, catchup and audit at once and
    # nothing said so.
    for _name, _argv in _JOBS.items():
        # paths.ROOT, not ROOT: _selftest rebinds ROOT with a `global` further
        # down, and reading it here would be a syntax error.
        assert (paths.ROOT / _argv[0]).exists(), \
            f"job {_name!r} spawns {_argv[0]}, which does not exist"
    global STATE, DIGEST, LOCK
    o = (STATE, DIGEST, LOCK)
    try:
        with tempfile.TemporaryDirectory() as td:
            STATE, DIGEST, LOCK = (Path(td) / "s.json", Path(td) / "d.md",
                                   Path(td) / "l")
            # The cycle is data collection plus the bucket. Nothing runs on a
            # weekend beyond catch-up, and nothing runs before the close.
            sat = datetime(2026, 8, 15, 10)      # Saturday
            assert "pbook" not in due(sat), due(sat)
            assert "snapshot" not in due(sat), "no bhavcopy is published Saturday"
            assert "pbook" not in due(datetime(2026, 8, 12, 9)), "ran before close"
            assert "pbook" in due(datetime(2026, 8, 12, 19))

            # Snapshot is due on the FILE, not the clock, and specifically on
            # the bhavcopy. Regression for the bug where a morning run created
            # asm.json, the evening check saw it and declared the day
            # collected, and the day's prices were never fetched.
            global ROOT
            oroot = ROOT
            try:
                ROOT = Path(td)
                (ROOT / "data").mkdir(parents=True, exist_ok=True)
                day = datetime(2026, 8, 12, 19)
                folder = ROOT / "data" / "raw" / "2026-08-12"
                folder.mkdir(parents=True)
                assert "snapshot" in due(day), "missing bhavcopy must be due"
                (folder / "asm.json").write_text("{}")
                assert "snapshot" in due(day), \
                    "asm.json alone must NOT count as collected"
                (folder / "bhavcopy_delivery.csv").write_text("x")
                assert "snapshot" not in due(day), \
                    "a present bhavcopy must settle the day"
            finally:
                ROOT = oroot

            # lock: held blocks, stale is reclaimed
            assert _lock() is True
            assert _lock() is False, "lock was re-acquired while held"
            os.utime(LOCK, (time.time() - LOCK_STALE_HOURS * 3600 - 60,) * 2)
            assert _lock() is True, "stale lock was not reclaimed"
            _unlock()
            assert not LOCK.exists()

            # Every job must record its own completion. "fill" did not: the
            # state key was looked up in a table that omitted it, so a
            # successful morning fill raised KeyError and re-ran hourly.
            _save({})
            o_run, o_due, o_busy = run_task, due, _busy
            try:
                globals()["run_task"] = lambda name, log=print: True
                globals()["_busy"] = lambda: False
                for job in _JOB_NAMES:
                    globals()["due"] = lambda now=None, j=job: [j]
                    assert once(log=lambda m: None) == [job], job
                    assert _state().get(f"last_{job}") == str(date.today()), \
                        f"{job} ran but recorded nothing; it would re-run every tick"
            finally:
                globals().update(run_task=o_run, due=o_due, _busy=o_busy)

            # attention() must judge DATA, not bookkeeping: a fresh agent with
            # snapshots on disk is healthy and must stay silent about them.
            _save({})
            msgs = attention()
            assert not any("never run a snapshot" in m for m in msgs), (
                f"fired on the agent's own bookkeeping: {msgs}")
            # and an already-consulted promotion is settled, not pending
            assert not any("waiting for a deliberate" in m for m in msgs), msgs
    finally:
        STATE, DIGEST, LOCK = o
    # The agent runs only data collection and the bucket. Assert against the
    # COMMAND TABLE, not the source text -- a source scan for forbidden names
    # matches the list of forbidden names itself and can never pass.
    # Through paths.script(), the same way _JOBS builds them. Writing the
    # resolved strings here instead would make this assertion a copy of the
    # layout, and it would have to be hand-edited every time the layout moved --
    # which is how it read before ops/ went under src/. The PROPERTY is "only
    # these four scripts and these three flags", and that is what survives.
    allowed = {_S("ops/snapshot.py"), _S("daily.py"), _S("tg.py"),
               _S("ops/audit.py"), "--catchup", "--fill-live", "--review"}
    for job in ("snapshot", "catchup", "pbook", "fill", "review", "audit"):
        for arg in _cmd_for(job)[1:]:
            assert arg in allowed, f"{job} runs unexpected {arg!r}"
    assert set(_JOB_NAMES) == {"snapshot", "catchup", "pbook", "fill",
                              "review", "audit"}, _JOB_NAMES
    # The fill must not be attempted before the open has printed, and must
    # still be due later in the day if it was. Against an EMPTY state file --
    # reading the live one made this pass or fail depending on whether today's
    # fill had already run, which is a test of the clock, not of the rule.
    _o = STATE
    try:
        STATE = Path(tempfile.mkdtemp()) / "s.json"
        assert "fill" not in due(datetime(2026, 8, 18, 9, 0)), "tried before the open"
        assert "fill" in due(datetime(2026, 8, 18, 9, 20)), "never tries after it"
        assert "fill" in due(datetime(2026, 8, 18, 14, 0)), "gave up mid-session"
    finally:
        STATE = _o

    # review must run AFTER pbook and AFTER audit on the same tick: pbook so it
    # reports today's bucket, audit so it quotes today's self-check.
    _t = due(datetime(2026, 8, 12, 19))
    assert "review" in _t and _t.index("review") > _t.index("pbook"), _t
    assert "audit" in _t and _t.index("review") > _t.index("audit"), _t
    print("agent selftest ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--interval", type=int, default=3600)
    ap.add_argument("--notify", action="store_true",
                    help="macOS banner when something needs attention")
    ap.add_argument("--telegram", action="store_true",
                    help="push attention items and completions to Telegram")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest()
    elif a.status:
        att = attention()
        print("=" * 56)
        print(f"  trading-bot agent   {datetime.now():%Y-%m-%d %H:%M}")
        print("=" * 56)
        print(f"  needs attention : {'NOTHING' if not att else ''}")
        for x in att:
            print(f"      ! {x}")
        print(f"  due now         : {', '.join(due()) or 'nothing'}")
        print(f"  busy            : {'yes (job running)' if _busy() else 'no'}")
        st = _state()
        ok, why = health()
        print(f"  agent health    : {why}")
        jobs = _jobs_loaded()
        print(f"  scheduled jobs  : {', '.join(jobs) if jobs else 'NONE INSTALLED'}")
        for k in ("last_snapshot", "last_runner", "last_research"):
            print(f"  {k:<16}: {st.get(k, 'never')}")
        print()
        digest()
        print(DIGEST.read_text())
    elif a.daemon:
        print(f"agent daemon, checking every {a.interval}s", flush=True)
        while True:
            try:
                once(log=lambda m: print(f"[{datetime.now():%H:%M}] {m}", flush=True))
            except Exception as e:
                print(f"cycle error: {type(e).__name__}: {e}", flush=True)
            time.sleep(a.interval)
    else:
        done = once()
        if a.telegram:
            try:
                import tg
                att = attention()
                if att:
                    tg.send("*needs attention*\n" + "\n".join(f"• {x}" for x in att))
                elif done:
                    tg.send(f"*agent* completed: {', '.join(done)}")
            except Exception as e:
                print(f"telegram push failed: {type(e).__name__}", flush=True)
        if a.notify:
            att = attention()
            if att:
                notify(att[0])
            elif done:
                notify(f"completed: {', '.join(done)}")
