# Fork Yield

Parallel exploration means forking a workspace ten ways, running ten approaches,
and keeping the best one. Which means nine get thrown away on purpose.

Nobody measures whether the tenth one ever wins.

This reads a log of sandbox runs and answers four questions:

1. **Does fork number seven ever win?** Win rate and cost per win by fork
   position, and the smallest fork budget that would still have caught almost
   every win you actually got.
2. **Where did the money go?** Split by what it bought: the forks you kept, the
   losing forks at positions that do sometimes win, and the losing forks past
   the point where almost nothing ever has.
3. **Whose compute was it?** By project and by agent, because that is the first
   question anyone asks when the invoice grows and a per-call log cannot answer it.
4. **What ran clean and produced nothing?** Exit code zero is not the same as
   having done the job.

## Run it

Demo, no account needed:

```bash
pip install -r requirements.txt
python3 -m ingest.synthetic
uvicorn api.main:app --reload
```

Open http://localhost:8000

Against a real export:

```bash
python3 -m ingest.csv_log path/to/sandboxes.csv
uvicorn api.main:app --reload
```

`ingest/csv_log.py` documents the columns. Two of them carry every interesting
number in the tool: `intervals`, which is what the sandbox was *awake* for, and
`produced_artifact`, which is whether anything came out.

## What the demo data shows

The synthetic log is built to contain the situation this exists to catch, so the
demo shows a finding rather than noise:

| | |
|---|---|
| Mean forks per task | 5.8, up to 12 |
| Forks needed to catch 95% of wins | **4** |
| Spend past that point | **$297 of $967 — 31%, for 3 wins out of 150** |
| Cost per win at fork 0 | $2.05 |
| Cost per win at fork 4 | $102.70 |
| Awake and idle, where pause was free | $252 — 26% of the bill |
| Clean exits that produced nothing | 439, costing $346 |

Read together: forking is working, and a third of the compute is going to fork
positions that have paid out three times in twelve weeks.

## The two decisions this tool rests on

**Paused time is free, so wall-clock age is not a bill.** A sandbox that existed
for nine days and ran for forty minutes costs forty minutes. Every cost figure
here is built from running intervals rather than from created to finished, which
is why these numbers differ from a naive reading of the same log.

**Exiting cleanly is not the same as doing the job.** A sandbox can run to
completion, return zero, and produce nothing. Billing cannot tell that apart
from the run that shipped the feature, because from a billing point of view they
are identical: a machine was awake for a while and then stopped. Only an artifact
check separates them. In the demo data, 40% of successful-looking runs produced
nothing.

When an export carries no artifact data at all, the loader defaults it to false
rather than true. That understates how much work got done, which is the safe
direction: a number that is too low gets questioned, a number that is too high
gets believed.

## How the headline number is defined

**Recommended forks** is the smallest fork budget that would still have captured
95% of the wins you actually got, based on where winners historically landed.

A win-rate floor was the obvious thing to use and it is the wrong tool. One lucky
win at fork eleven drags the cutoff all the way out, and the figure then swings
every time the data refreshes. Cumulative coverage does not care where the single
outlier landed. There's a test named after that case.

**Cost per win by fork position** is the number that should actually set a fork
budget. A fork that wins once in a hundred tries is not cheap just because the
machine was small.

**Fork positions with fewer than five cohorts behind them get no win rate at
all** — they render as a grey stub rather than a zero. Drawing a 0% bar from two
samples invites someone to cut a fork budget on noise.

**Idle is reported separately rather than as a fourth slice**, because an idle
hour already belongs to whichever fork was sitting there. The dashboard says so
instead of presenting four numbers that do not add up.

**Cost per won task** divides by tasks that produced a keeper, not by all tasks.
Dividing by everything flatters the figure by pretending the failed tasks
produced something.

The current half-finished week is left off the trend. A week that is two days old
always looks like a cliff, and someone always reads that cliff as an improvement.

## Layout

```
core/model.py        Sandbox, Interval, Cohort — running vs paused, and the
                     clean-exit vs produced-something distinction
core/metrics.py      the five numbers, plain functions, checkable by hand
ingest/synthetic.py  seeded fake twelve weeks for the demo
ingest/csv_log.py    load a real export
ingest/store.py      one JSON file on disk, no database
api/main.py          FastAPI: /api/metrics, /api/sandboxes, /health
static/index.html    the dashboard, one file, no build step, no CDN
tests/               24 tests, every expected value worked out on paper first
```

```bash
python3 -m pytest tests/ -q
```

## Notes

The dashboard is one HTML file with hand-drawn SVG and no dependencies. Every
chart has a table view behind it, the fork-budget cut line is drawn and labelled
rather than left implicit, and the palette was validated for colour-blind
separation on both the light and dark surface.

`/api/sandboxes?beyond=true` returns the exact forks behind the headline figure,
and `?empty=true` returns the clean runs that produced nothing, so both claims
can be audited rather than trusted.
