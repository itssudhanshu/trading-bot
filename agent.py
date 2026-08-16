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
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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
        out = subprocess.run(["pgrep", "-f", "pbook_run.py|snapshot.py"],
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
_JOBS = {
    "snapshot": ["snapshot.py"],
    "catchup":  ["snapshot.py", "--catchup"],
    "pbook":    ["pbook_run.py"],
}
_JOB_NAMES = tuple(_JOBS)


def _cmd_for(job):
    import sys as _s
    return [_s.executable] + _JOBS[job]


def due(now=None):
    """-> list of task names outstanding right now."""
    now = now or datetime.now()
    st = _state()
    todo = []

    # Data collection: driven by what is MISSING, not by the clock. A weekday
    # with no snapshot is outstanding whether or not 19:00 has passed today.
    raw = ROOT / "data" / "raw"
    today = now.date()
    if now.weekday() < 5 and now.hour >= 18:
        if not (raw / today.isoformat() / "asm.json").exists():
            todo.append("snapshot")
    if st.get("last_catchup") != str(today):
        todo.append("catchup")
    if st.get("last_pbook") != str(today) and now.weekday() < 5 and now.hour >= 18:
        todo.append("pbook")
    return todo


def run_task(name, log=print):
    cmds = {k: [sys.executable] + v for k, v in _JOBS.items()}
    logf = ROOT / "data" / f"agent_{name}.log"
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

    # The book is queued but nothing has filled for several sessions: either
    # no candidate is triggering, which is normal, or pbook has stopped running.
    try:
        import pbook
        s = pbook.summary()
        if s["pending"] and st.get("last_pbook"):
            gap = (today - date.fromisoformat(str(st["last_pbook"])[:10])).days
            if gap > 4:
                out.append(f"book has {s['pending']} queued but pbook last ran "
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
        import pbook, portfolio
        s = pbook.summary()
        lines.append(f"- book: **{s['open']}** open, **{s['pending']}** queued, "
                     f"**{s['closed']}** closed, realised "
                     f"**Rs {s['realised']:+,.0f}** of Rs {portfolio.CAPITAL:,}")
    except Exception as e:
        lines.append(f"- book: unavailable ({type(e).__name__})")
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
                key = {"snapshot": "last_snapshot", "catchup": "last_catchup",

                       "pbook": "last_pbook"}[t]
                st[key] = str(date.today())
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
    global STATE, DIGEST, LOCK
    o = (STATE, DIGEST, LOCK)
    try:
        with tempfile.TemporaryDirectory() as td:
            STATE, DIGEST, LOCK = (Path(td) / "s.json", Path(td) / "d.md",
                                   Path(td) / "l")
            # The cycle is data collection plus the book. Nothing runs on a
            # weekend beyond catch-up, and nothing runs before the close.
            sat = datetime(2026, 8, 15, 10)      # Saturday
            assert "pbook" not in due(sat), due(sat)
            assert "snapshot" not in due(sat), "no bhavcopy is published Saturday"
            assert "pbook" not in due(datetime(2026, 8, 12, 9)), "ran before close"
            assert "pbook" in due(datetime(2026, 8, 12, 19))
            assert "snapshot" in due(datetime(2026, 8, 12, 19))

            # lock: held blocks, stale is reclaimed
            assert _lock() is True
            assert _lock() is False, "lock was re-acquired while held"
            os.utime(LOCK, (time.time() - LOCK_STALE_HOURS * 3600 - 60,) * 2)
            assert _lock() is True, "stale lock was not reclaimed"
            _unlock()
            assert not LOCK.exists()

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
    # The agent runs only data collection and the book. Assert against the
    # COMMAND TABLE, not the source text -- a source scan for forbidden names
    # matches the list of forbidden names itself and can never pass.
    allowed = {"snapshot.py", "pbook_run.py", "--catchup"}
    for job in ("snapshot", "catchup", "pbook"):
        for arg in _cmd_for(job)[1:]:
            assert arg in allowed, f"{job} runs unexpected {arg!r}"
    assert set(_JOB_NAMES) == {"snapshot", "catchup", "pbook"}, _JOB_NAMES
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
