# CLAUDE.md — working agreement for this repo

## Autonomy

**Run the next step without being asked.** Do not end a turn with "shall I
proceed?" or wait for "run the next step". The plan is agreed; execute it.
Reserve questions for decisions that genuinely change the work and that cannot
be resolved from the code, the data, or `STATE.md`.

**Run simulations automatically.** Whenever a parameter, rule, or selection
input changes, re-run `simulate.py` and store the results. A change that has not
been simulated is unmeasured, and unmeasured changes are how this project has
repeatedly shipped bugs that looked like findings.

**Always close a response with an explicit next-step section**, even mid-task
and even while jobs run in the background.

## Restart the Telegram listener after ANY code change

`tg.py --listen` imports the project modules and holds them in memory. Editing
`agent.py`, `learning.py`, `simulate.py` or `tg.py` leaves the bot serving stale
logic while looking perfectly healthy — this has caused three separate wrong
answers already (`/health` returning help text, false attention alarms, missing
commands).

    pkill -f "tg.py --listen"    # run_listener.sh restarts it automatically

Then verify the command actually works before reporting it as fixed.

## Discipline that must not be relaxed

- **Holdout budget is 50 for the project's life.** Never spend it from an
  automated loop. `pipeline.py --consult` is a deliberate act.
- **Criteria may be tightened, never loosened.** Tightening a test that let
  something through is defensible; relaxing one that rejected a candidate is how
  this fails.
- **Invariants in `engine.py` are never searched.** A generator that can vary its
  own risk limits will discover that removing them improves backtest returns.
- **PBO > 0.5 is a stop**, not a warning.
- **A status message is not evidence.** Verify the thing itself: a flag that
  prints "enabled" may do nothing, a panel that reports "open" may be closed, an
  HTTP 200 may not be the file requested. Every one of these has happened here.
- **`patch_helper.sub()` for every source edit** — `str.replace` on a missing
  anchor silently returns the original and has produced multiple no-op "fixes".

## Reporting

Report per-regime block, never a blended number. A total is not a finding when
one period supplies all of it. State the trial count alongside any performance
figure: after ~2,500 hypotheses, a best-of-N result is inflated by construction.

See `STATE.md` for current status and `lessons.md` for the evidence behind each
rule above.
