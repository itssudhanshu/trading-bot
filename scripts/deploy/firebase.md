# Firebase deployment plan — NOT ADOPTED

**Decision, 2026-08-19: this stays on the Mac.** Kept for the measurements
below, which are real and were not cheap to get; read it as a costed option that
was declined, not as work in progress. The module names were updated when the
code moved (`pbook.py` -> `positions.py`, `portfolio.py` -> `selection.py`,
`pbook.db` -> `positions.db`); the numbers are untouched and pre-date the
circuit-lock guard, which changed results but not sizes or timings.

Goal was: the bucket runs without this Mac being awake, at zero (or near-zero)
cost, with every Telegram command answering exactly as it does today.

Measured on this repo, 2026-08-17, not assumed:

| fact | value | why it decides something |
|---|---|---|
| dependencies | **stdlib only** — no pandas, no requests | image stays tiny; fits Artifact Registry's 0.5 GB free tier |
| `features.load_corpus()` | **18.3 s, 2.5–2.8 GB RSS** | rules out every 1 GB free VM; forces a memory-sized serverless run |
| `selection.build()` after load | **0.2 s** | the corpus load IS the entire cost. Nothing else matters |
| runtime data | `data/raw` 450 MB (1,698 day-dirs) + `data/fundamentals/parsed` 9.7 MB | must live off-instance |
| non-runtime data | `data/fundamentals/xbrl` 1.5 GB + `index` 116 MB | backfill-only. **Never uploaded.** Cuts 2.1 GB to 460 MB |
| growth | ~270 KB/session ≈ 68 MB/year | 5 GB free storage lasts years |
| macOS-only calls | 4 (`osascript`, `launchctl`, 2× `pgrep`) | small, listed below |

---

## The verdict, stated plainly

**1. Firebase's actual free plan (Spark) cannot run this.** The pricing page
lists Cloud Functions as *"Not applicable"* on Spark. Spark gives you Hosting,
Firestore and Storage — no compute. There is no configuration of Spark that
executes Python.

So "free on Firebase" means: **Blaze plan, card on file, usage kept inside the
free quotas.** That is a real distinction and it cannot be engineered away.

**2. Inside those quotas this workload is small.** ~4–8% of the Cloud Functions
free allowance. The arithmetic is below.

**3. Whether it is literally Rs 0 or roughly Rs 5/month turns on one untested
question:** does NSE serve a Google datacenter IP? That is Step 0.

---

## Step 0 — the go/no-go test (do this before anything else)

`snapshot.py` scrapes `nseindia.com` with a browser User-Agent and Referer. NSE
is known to rate-limit and geo-filter. If it refuses cloud IPs, the entire plan
is void and no amount of Firebase configuration fixes it.

Test it directly, from the region you would deploy to:

```bash
gcloud run deploy nsetest --source . --region us-central1 --command python3 \
  --args=-c,"import snapshot;print([(k,snapshot.fetch(u.format(d=__import__('datetime').date.today()))[0]) for k,(u,_) in snapshot.SOURCES.items()])"
```

Read the actual HTTP statuses. A 200 on `bhavcopy_delivery` and `asm` is a pass;
403 or 0 (timeout) is a fail. **Do not accept "it deployed" as evidence** — the
function must print seven statuses.

| result | what it means |
|---|---|
| 200s from `us-central1` | best case. Everything below is genuinely Rs 0 |
| 403/timeout from US, 200 from `asia-south1` | works, but GCS Always Free is US-regions-only → ~Rs 2–8/month |
| 403/timeout from both | **stop.** Serverless is dead; you need a residential/Indian IP. Revisit as a fetch-only relay from home + compute in cloud |

---

## The shape

Three pieces, one bucket, one schedule.

```
Cloud Scheduler (1 job, weekdays 19:00 IST)
        │
        ▼
  fn: nightly            8 GiB, ~4 min          fn: telegram        512 MiB, ~1 s
  ─────────────          ↓ pull corpus.tar.gz   ────────────        ↑ pull state + ui.json
  snapshot → catchup     ↑ push corpus + state  webhook handler     ↓ Telegram sendMessage
  → daily.py             ↑ push ui.json
        │                        │                      │
        └────────────────────────┴──────────────────────┘
                    GCS bucket  gs://<proj>-book
                      corpus.tar.gz   (~130 MB gzipped)
                      state.tar.gz    (~700 KB: positions.db, agent_state, offsets, findings)
                      ui.json         (~200 KB: last day, last closes, rendered /clusters + /bucket)
```

**Why a tarball and not 12,000 individual objects.** GCS Always Free includes
**50,000 Class B (read) operations/month**. Syncing `data/raw` file-by-file is
~12,000 reads per run × 22 runs = 264,000/month — five times over the free
allowance. One tarball is one read. The design is forced by the quota, not by
taste.

**Why `ROOT` needs no code change.** Every module resolves paths as
`Path(__file__).resolve().parent / "data" / ...`. Symlink once at cold start:

```python
os.path.exists("/workspace/data") or os.symlink("/tmp/data", "/workspace/data")
```

Nothing in `features.py`, `positions.py`, `selection.py` or `clusters.py` is touched.

---

## What has to change in the code (the "no impact" question, answered honestly)

Four changes. Three are mechanical; one is real.

### 1. Telegram: long-poll → webhook  *(the real one)*

`tg.py --listen` blocks on `getUpdates` forever. Serverless has no forever.
Telegram's webhook mode is the supported alternative and is functionally
identical from your phone's side.

- Extract the body of `poll_once()`'s loop into `handle_update(upd)` — the
  owner check, the `@botname` strip, the `COMMANDS` lookup. ~15 lines moved,
  zero logic changed. `poll_once()` keeps calling it, so `--listen` still works
  locally for testing.
- The HTTP function returns `200` immediately, then does the work and replies
  via the existing `tg.send()`. Telegram does not wait on the reply.
- `tg_offset.json` becomes dead weight in webhook mode (Telegram tracks
  delivery). Keep the file; it costs nothing and `--listen` still needs it.
- **Set Telegram's `secret_token`** on `setWebhook` and reject any POST without
  the matching `X-Telegram-Bot-Api-Secret-Token` header. Without it the URL is
  public and every stray POST burns an invocation. This is a cost control as
  much as a security one.

### 2. Precompute the two corpus-heavy commands

Only `/clusters` and `/bucket` need the full ranking. `/wallet`, `/next_orders`
and `/open_orders` need `positions.db` plus the **last day's closes only**;
`/closed_orders`, `/findings` and `/help` need no corpus at all.

If the webhook loaded the corpus it would take ~40 s and 3 GB to answer
`/wallet`. So the nightly run — which already has the corpus in memory — writes
`ui.json`: last trading day, `{symbol: last_close}`, and the rendered `/clusters`
and `/bucket` text. The webhook reads that and answers in under a second.

**This is faster than today, and changes no output.** The numbers are the same
numbers, computed by the same code, at the same moment the book is stepped.

`_lag_note()` reads the last corpus day — serve it from `ui.json` too.

### 3. Four macOS calls

| file | call | replacement |
|---|---|---|
| `agent.py:285` | `osascript` desktop banner | delete, or route to `tg.send()` |
| `agent.py:122` | `launchctl list` | return `[]`; the check is meaningless off-Mac |
| `agent.py:48` | `pgrep` busy-check | the GCS state lock already covers this; keep the existing `_lock()` |
| `tg.py:416` | `pgrep tg.py --listen` in `/health` | report webhook registration from `getWebhookInfo` instead |

Note `tg.py:539` asserts `"pgrep" in handlers` — that selftest must be
re-derived to assert the property ("health observes, does not start"), not the
literal string. Per CLAUDE.md, re-derive it; do not overwrite it.

### 4. Runtime version

Local is Python **3.14.6**; Firebase Functions supports **3.10–3.13** (3.13
default). Stdlib-only code almost certainly runs unchanged, but run every
`--selftest` under 3.13 before deploying. Cheap to check, expensive to discover
in production.

**The agent's hourly cadence is no longer needed.** `agent.py --once` runs
hourly because a sleeping Mac misses fixed schedules. A cloud instance does not
sleep — one weekday run at 19:00 IST plus Cloud Scheduler's retry policy covers
it, and `due()`/`catchup()` still handle a genuinely missed session on the next
run. That drops the schedule from 24 runs/day to 1, which is most of why this
stays inside the free tier.

---

## The free-tier arithmetic

Assumptions: 22 trading days/month, nightly at 8 GiB / 2 vCPU / ~240 s;
webhook at 512 MiB / ~1 s, 50 commands/day.

| resource | free allowance | this workload | headroom used |
|---|---|---|---|
| Functions GB-seconds | 400,000/mo | 8 × 240 × 22 = 42,240 | **11%** |
| Functions CPU-seconds | 200,000/mo | 2 × 240 × 22 = 10,560 | **5%** |
| Functions invocations | 2,000,000/mo | 22 + ~1,500 | **0.1%** |
| Functions egress | 5 GB/mo | Telegram replies ~8 MB | **0.2%** |
| GCS storage | 5 GB-months (US only) | 130 MB, +20 MB/yr gzipped | **3%** |
| GCS Class A ops (write) | 5,000/mo | ~90 | **2%** |
| GCS Class B ops (read) | 50,000/mo | 22 + ~3,000 | **6%** |
| Cloud Scheduler | 3 jobs/billing account | 1 | **33%** |
| Cloud Build | 2,500 min/mo | ~10 min/deploy | negligible |
| Artifact Registry | 0.5 GB/mo | ~250–400 MB per image version | **50–80% — the tight one** |

Sensitivity on the one number I am guessing at — nightly wall-clock. 240 s is
18 s measured locally, scaled for a slower shared vCPU, plus download, extract,
NSE fetch and re-upload:

| nightly duration | GB-s/month | headroom used |
|---|---|---|
| 120 s | 21,120 | 5% |
| 240 s (planned) | 42,240 | 11% |
| 480 s | 84,480 | 21% |
| 900 s | 158,400 | 40% |

Free across the whole range. That is the useful finding: the plan does not
depend on the estimate being right.

---

## What could actually cost money

| risk | cost if it bites | guard |
|---|---|---|
| **Artifact Registry fills up** — every deploy stores a new image version, 0.5 GB free | Rs 10–30/mo | set a cleanup policy keeping the 2 most recent versions. Do this on day one; after ~3 deploys you are over |
| **NSE needs an Indian IP** → `asia-south1`, where GCS Always Free (US-only) does not apply | Rs 2–8/mo | accept it, or keep the bucket in `us-central1` and eat cross-region reads (worse: ~Rs 15/mo) |
| **Runaway webhook** — a retry loop or public URL scanned | unbounded | `maxInstances=3` on webhook, `maxInstances=1` on nightly, plus the Telegram `secret_token` check |
| **Someone re-enables hourly scheduling at 8 GiB** | Rs 300+/mo | 528 runs/mo × 1,920 GB-s = 1,013,760 GB-s — **2.5× over the free quota**. The 1-run-a-day schedule is load-bearing, not a tidy-up |
| **`fundamentals/xbrl` gets uploaded** | 1.5 GB → over the 5 GB free bucket with room to spare, but it grows | exclude it explicitly in the tar command, not by convention |

Set a billing budget alert at Rs 100 with an email trigger. Not a kill-switch —
budget alerts do not stop spend — but it is the notification that matters.

---

## Order of work

1. **Step 0**: the NSE-from-GCP test. Everything else is void without it.
2. Bucket + first upload. `tar czf` excluding `fundamentals/xbrl` and
   `fundamentals/index`; upload `corpus.tar.gz` and `state.tar.gz`.
3. `cloudstate.py` — ~40 lines of stdlib `urllib` against
   `storage.googleapis.com` using the metadata-server token. `pull()` at cold
   start, `push()` at end. No new dependency; falls back to
   `google-cloud-storage` only if resumable uploads get fiddly.
4. Nightly function: symlink `/tmp/data`, pull, `agent.once()`, write `ui.json`,
   push. Deploy at 8 GiB. Run it once manually and diff its `positions.db` against
   the local one — **the run is only correct if the book matches.**
5. Selftests under Python 3.13.
6. Webhook function: extract `handle_update`, add the secret-token check, serve
   from `ui.json`. `setWebhook`, then send `/health` from your phone.
7. Cloud Scheduler job, weekdays 19:00 IST, retry ×3.
8. Cleanup policy on Artifact Registry, `maxInstances` on both functions,
   budget alert.
9. Stop the Mac's launchd jobs — **only after** a full week of cloud runs whose
   `positions.db` matches what the Mac would have produced. Two writers to one bucket
   is the one failure mode here that corrupts state rather than just erroring.

---

## What this plan does NOT claim

- **It does not make the strategy work.** There are still 0 closed forward
  paper trades. Moving the runner to a datacenter changes where the code
  executes and nothing about whether the edge is real.
- **It is not verified end to end.** Every number above about *this repo* is
  measured; every number about Google's free tier is quoted from their pricing
  pages as of 2026-08-17; the nightly wall-clock in the cloud is an estimate
  with a sensitivity table around it. The NSE-from-GCP question is untested and
  is the one that decides go/no-go.
- **A "deployed" status message is not evidence.** Per CLAUDE.md: verify the
  bhavcopy bytes arrived, verify the book stepped, verify the Telegram reply
  landed. Each of those has failed silently on this project before.
