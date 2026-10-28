"""
The numbers.

Plain functions over a list of Sandbox, so every figure on the dashboard
can be checked by hand against this file.

Five questions:

  1. yield_curve()  - does fork number seven ever win
  2. spend()        - where the money went, and how much bought nothing
  3. attribution()  - which agent and which project burned it
  4. emptiness()    - sandboxes that exited zero and produced nothing
  5. trend()        - is any of this getting better week over week

The one that matters most is the first. Parallel exploration discards most
of what it spawns on purpose, so "wasted compute" is not automatically a
problem. The question is whether the discarded forks ever had a chance.
A fork at an index where nothing has ever won is not exploration. It is
just a bill.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median

from core.model import Cohort, Sandbox, cohorts, utcnow

# A fork index needs at least this many settled cohorts behind it before its
# win rate means anything. One cohort that forked twelve ways does not make
# fork 11 a 0% proposition, it makes it unmeasured.
MIN_COHORTS_PER_INDEX = 5

# How much of the historical win total a fork budget has to capture before
# we call it enough. 95% means: the smallest number of forks that would
# still have caught 95 wins in every 100 you actually got.
#
# A win-rate floor was the obvious thing to use here and it is the wrong
# tool. One lucky win at fork eleven drags the cutoff all the way out, and
# the figure swings every time the data refreshes. Cumulative coverage does
# not care where the single outlier landed.
COVERAGE_TARGET = 0.95


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole, 4) if whole else None


def _money(x: float) -> float:
    return round(x, 2)


def _median_or_none(v: list[float]) -> float | None:
    return round(median(v), 2) if v else None


# ------------------------------------------------------------------ 1/5

def yield_curve(sandboxes: list[Sandbox], now: datetime) -> dict:
    """Win rate and cost per win by fork index, and how many forks you need.

    Read a point as: of all the cohorts big enough to have a fork at index
    k, how often index k turned out to be the one you kept.

    `recommended_forks` is the smallest fork budget that would still have
    captured COVERAGE_TARGET of the wins you actually got. Everything past
    it is compute spent chasing the long tail of the distribution.
    """
    groups = [c for c in cohorts(sandboxes) if c.settled]

    spawned: dict[int, int] = defaultdict(int)
    wins: dict[int, int] = defaultdict(int)
    cost_at: dict[int, float] = defaultdict(float)

    for c in groups:
        for s in c.sandboxes:
            spawned[s.fork_index] += 1
            cost_at[s.fork_index] += s.cost(now)
            if s.won:
                wins[s.fork_index] += 1

    total_wins = sum(wins.values())
    total_cost = sum(cost_at.values())

    points, running = [], 0
    for i in sorted(spawned):
        n = spawned[i]
        running += wins[i]
        measured = n >= MIN_COHORTS_PER_INDEX
        points.append({
            "fork_index": i,
            "spawned": n,
            "wins": wins[i],
            # Suppressed rather than plotted when the sample is too small.
            # A spike drawn from two data points gets acted on, and it
            # should not be.
            "win_rate": _pct(wins[i], n) if measured else None,
            "measured": measured,
            "cost": _money(cost_at[i]),
            # the number that actually decides a fork budget: what one win
            # costs at this position
            "cost_per_win": _money(cost_at[i] / wins[i]) if wins[i] else None,
            "cumulative_win_share": _pct(running, total_wins),
        })

    # smallest fork count that still catches COVERAGE_TARGET of the wins
    recommended = None
    for p in points:
        if (p["cumulative_win_share"] or 0) >= COVERAGE_TARGET:
            recommended = p["fork_index"] + 1
            break

    # Sum from the raw figures, not from points[].cost, which has already
    # been rounded to cents for display. Dividing a rounded numerator by an
    # unrounded total is how a share that should read 50% comes out at
    # 49.55%, and nobody would ever find that by looking at it.
    beyond_cost = beyond_wins = 0.0
    if recommended is not None:
        for i in sorted(spawned):
            if i >= recommended:
                beyond_cost += cost_at[i]
                beyond_wins += wins[i]

    sizes = [c.size for c in groups]
    return {
        "points": points,
        "cohorts": len(groups),
        "total_wins": total_wins,
        "mean_fork_count": round(sum(sizes) / len(sizes), 1) if sizes else None,
        "max_fork_count": max(sizes) if sizes else 0,
        "recommended_forks": recommended,
        "coverage_target": COVERAGE_TARGET,
        # the headline: money spent past the point that catches nearly
        # every win, and the handful of wins it did buy
        "beyond_cost": _money(beyond_cost),
        "beyond_share": _pct(beyond_cost, total_cost),
        "beyond_wins": int(beyond_wins),
        "min_sample": MIN_COHORTS_PER_INDEX,
    }


# ------------------------------------------------------------------ 2/5

def spend(sandboxes: list[Sandbox], now: datetime) -> dict:
    """Where the money went, split by what it bought.

    Four buckets, and they are not all the same kind of spend:

      winners      - the forks that were kept. this is the product.
      exploration  - losing forks at indexes that do sometimes win. this is
                     the cost of the method, and it is worth paying.
      long_tail    - losing forks past the point that catches nearly every
                     win. this is not exploration, it is habit.
      idle         - awake and doing nothing. pause was free and nobody
                     called it. this one is pure waste in any reading.

    Idle overlaps the others, because an idle hour belongs to whichever
    fork was sitting there. It is reported separately rather than added, so
    the four do not sum to the total and the dashboard says so.
    """
    groups = [c for c in cohorts(sandboxes) if c.settled]
    curve = yield_curve(sandboxes, now)
    cutoff = curve["recommended_forks"]

    winners = exploration = tail = 0.0
    for c in groups:
        for s in c.sandboxes:
            cost = s.cost(now)
            if s.won:
                winners += cost
            elif cutoff is not None and s.fork_index >= cutoff:
                tail += cost
            else:
                exploration += cost

    total = sum(s.cost(now) for s in sandboxes)
    idle = sum(s.idle_cost(now) for s in sandboxes)
    settled_total = winners + exploration + tail

    return {
        "total": _money(total),
        "settled_total": _money(settled_total),
        "winners": _money(winners),
        "exploration": _money(exploration),
        "long_tail": _money(tail),
        "idle": _money(idle),
        "winner_share": _pct(winners, settled_total),
        "long_tail_share": _pct(tail, settled_total),
        "idle_share": _pct(idle, total),
        "cost_per_won_task": _money(settled_total / len([c for c in groups if c.winner]))
                             if any(c.winner for c in groups) else None,
        "cost_per_winner_only": _money(winners / len([c for c in groups if c.winner]))
                                if any(c.winner for c in groups) else None,
        "running_hours": round(sum(s.running_hours(now) for s in sandboxes), 1),
        "idle_hours": round(sum(s.idle_hours(now) for s in sandboxes), 1),
    }


# ------------------------------------------------------------------ 3/5

def attribution(sandboxes: list[Sandbox], now: datetime, limit: int = 12) -> dict:
    """Whose compute was it.

    The question every customer asks the first time the invoice grows, and
    the one a per-call log cannot answer.
    """
    def group_by(key) -> list[dict]:
        buckets: dict[str, list[Sandbox]] = defaultdict(list)
        for s in sandboxes:
            buckets[key(s)].append(s)
        rows = []
        for name, group in buckets.items():
            settled = [s for s in group if s.finished]
            won = [s for s in settled if s.won]
            cost = sum(s.cost(now) for s in group)
            rows.append({
                "name": name,
                "sandboxes": len(group),
                "cost": _money(cost),
                "idle_cost": _money(sum(s.idle_cost(now) for s in group)),
                "won": len(won),
                "win_rate": _pct(len(won), len(settled)),
                "empty": sum(1 for s in settled if s.empty),
                "cost_per_win": _money(cost / len(won)) if won else None,
            })
        rows.sort(key=lambda r: -r["cost"])
        return rows[:limit]

    return {
        "by_project": group_by(lambda s: s.project),
        "by_agent": group_by(lambda s: s.agent),
    }


# ------------------------------------------------------------------ 4/5

def emptiness(sandboxes: list[Sandbox], now: datetime, limit: int = 20) -> dict:
    """Sandboxes that exited zero and produced nothing.

    The invoice cannot tell these apart from the ones that worked, because
    from a billing point of view they are identical: a machine ran for a
    while and stopped cleanly. Only an artifact check separates them, and
    nobody is doing one.
    """
    settled = [s for s in sandboxes if s.finished]
    clean = [s for s in settled if s.exit_code == 0]
    empty = [s for s in clean if s.empty]

    rows = sorted(empty, key=lambda s: -s.cost(now))[:limit]
    return {
        "settled": len(settled),
        "exited_clean": len(clean),
        "empty_count": len(empty),
        # of the runs that looked successful, how many produced nothing
        "empty_share_of_clean": _pct(len(empty), len(clean)),
        "empty_cost": _money(sum(s.cost(now) for s in empty)),
        "median_empty_hours": _median_or_none(
            [s.running_hours(now) for s in empty]),
        "worst": [
            {
                "id": s.id,
                "task_id": s.task_id,
                "agent": s.agent,
                "project": s.project,
                "fork_index": s.fork_index,
                "hours": round(s.running_hours(now), 1),
                "cost": _money(s.cost(now)),
            }
            for s in rows
        ],
    }


# ------------------------------------------------------------------ 5/5

def _week(d: datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _drop_partial_week(points: list[dict], now: datetime) -> list[dict]:
    """A week that is two days old always looks like a cliff."""
    return [p for p in points if p["week"] != _week(now)]


def trend(sandboxes: list[Sandbox], now: datetime, weeks: int = 12) -> dict:
    """Week over week, bucketed by when the cohort started."""
    cutoff = now - timedelta(weeks=weeks)
    groups = [c for c in cohorts(sandboxes) if c.settled
              and c.sandboxes[0].created_at >= cutoff]

    buckets: dict[str, list[Cohort]] = defaultdict(list)
    for c in groups:
        buckets[_week(c.sandboxes[0].created_at)].append(c)

    points = []
    for week in sorted(buckets):
        cs = buckets[week]
        boxes = [s for c in cs for s in c.sandboxes]
        cost = sum(s.cost(now) for s in boxes)
        wonc = [c for c in cs if c.winner]
        sizes = [c.size for c in cs]
        points.append({
            "week": week,
            "cohorts": len(cs),
            "sandboxes": len(boxes),
            "mean_forks": round(sum(sizes) / len(sizes), 1) if sizes else None,
            "cost": _money(cost),
            "cost_per_won_task": _money(cost / len(wonc)) if wonc else None,
            "idle_cost": _money(sum(s.idle_cost(now) for s in boxes)),
        })

    return {"points": _drop_partial_week(points, now), "weeks": weeks}


# ---------------------------------------------------------------- compute

def compute(sandboxes: list[Sandbox], now: datetime | None = None,
            weeks: int = 12) -> dict:
    now = now or utcnow()
    return {
        "generated_at": now.isoformat() + "Z",
        "window_weeks": weeks,
        "sandbox_count": len(sandboxes),
        "yield_curve": yield_curve(sandboxes, now),
        "spend": spend(sandboxes, now),
        "attribution": attribution(sandboxes, now),
        "emptiness": emptiness(sandboxes, now),
        "trend": trend(sandboxes, now, weeks),
    }
