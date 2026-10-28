"""
Twelve weeks of fake sandbox runs, so the dashboard has something to show
without a real account.

Built to contain the thing this tool exists to find:

  * cohorts fork wide, but winners cluster in the first few forks
  * so compute piles up at fork positions that have never paid out
  * some cohorts nobody wins at all
  * some sandboxes exit zero having produced nothing
  * some sit awake for hours because nobody called pause

Seeded, so the numbers are the same every run.

    python3 -m ingest.synthetic
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from core.model import AGENTS, Interval, Sandbox, utcnow
from ingest.store import save

random.seed(23)

WEEKS = 12

PROJECTS = [
    "acme-migration", "orion-refactor", "billing-rewrite",
    "mobile-parity", "search-rerank", "infra-cleanup", "docs-pipeline",
]

# Agents differ. Some fork wide and cheap, some fork narrow and expensive,
# and they do not succeed at the same rate. Averaging them together is how
# you end up with a number that describes nobody.
AGENT_SHAPE = {
    "claude-code": {"forks": (3, 9),  "win": 0.86, "artifact": 0.94, "idle": 0.18},
    "codex":       {"forks": (4, 12), "win": 0.78, "artifact": 0.88, "idle": 0.26},
    "opencode":    {"forks": (2, 7),  "win": 0.71, "artifact": 0.81, "idle": 0.22},
    "kilo-code":   {"forks": (2, 6),  "win": 0.69, "artifact": 0.76, "idle": 0.31},
}

SIZES = [(1, 2), (2, 4), (2, 8), (4, 8), (4, 16), (8, 16)]


def _winner_index(size: int) -> int:
    """Which fork turns out to be the one you keep.

    Heavily weighted to the first few. That is the finding: people fork
    eight ways and the answer is almost always in the first three, so the
    back half of every cohort is compute spent on a position that has never
    paid out.
    """
    weights = [1 / ((i + 1) ** 1.9) for i in range(size)]
    return random.choices(range(size), weights=weights, k=1)[0]


def _intervals(start: datetime, active_h: float, idle_h: float,
               now: datetime) -> list[Interval]:
    """Split a sandbox's life into awake stretches.

    Real usage is bursty: run for a while, pause overnight, resume. The
    gaps between these intervals are paused time and cost nothing, which is
    why wall-clock age is useless for working out a bill.
    """
    out: list[Interval] = []
    cursor = start

    chunks = max(1, int(active_h / random.uniform(0.4, 2.2)))
    per = active_h / chunks
    for i in range(chunks):
        end = cursor + timedelta(hours=per)
        if end > now:
            out.append(Interval(cursor, None, True))
            return out
        out.append(Interval(cursor, end, True))
        cursor = end
        if i < chunks - 1:
            # paused in between, for anything from a coffee to a weekend
            cursor += timedelta(hours=random.uniform(0.5, 40))

    if idle_h > 0.05:
        end = cursor + timedelta(hours=idle_h)
        out.append(Interval(cursor, end if end <= now else None, False))

    return out


def generate() -> list[Sandbox]:
    now = utcnow()
    out: list[Sandbox] = []
    task_n = 7000

    for week in range(WEEKS):
        tasks_this_week = int(9 + week * 1.6)

        for _ in range(tasks_this_week):
            task_n += 1
            task_id = f"T-{task_n}"
            agent = random.choice(AGENTS)
            shape = AGENT_SHAPE[agent]
            project = random.choice(PROJECTS)
            vcpu, mem = random.choice(SIZES)

            lo, hi = shape["forks"]
            size = random.randint(lo, hi)

            started = (now - timedelta(weeks=WEEKS - week)
                       + timedelta(days=random.uniform(0, 6.8)))
            if started > now:
                continue

            # does anyone win this task at all
            cohort_won = random.random() < shape["win"]
            win_at = _winner_index(size) if cohort_won else None

            for i in range(size):
                # the winner runs longest, because it is the one that got
                # taken all the way. losing forks get abandoned early.
                base = random.lognormvariate(0.2, 0.7)
                active_h = base * (2.6 if i == win_at else 1.0)
                idle_h = (random.lognormvariate(0.6, 0.9)
                          if random.random() < shape["idle"] else 0.0)

                created = started + timedelta(minutes=random.uniform(0, 25))
                intervals = _intervals(created, active_h, idle_h, now)
                still_running = any(iv.end is None for iv in intervals)

                if still_running:
                    outcome, exit_code, artifact = "running", None, False
                elif i == win_at:
                    outcome, exit_code, artifact = "won", 0, True
                elif random.random() < 0.09:
                    # genuinely crashed. these are the easy ones to find.
                    outcome, exit_code, artifact = "failed", 1, False
                else:
                    outcome, exit_code = "lost", 0
                    # a losing fork usually still produced *something*, but
                    # a fair share ran clean and left nothing behind. those
                    # are indistinguishable from real work on an invoice.
                    artifact = random.random() < shape["artifact"] * 0.62

                out.append(Sandbox(
                    id=f"sbx-{task_n}-{i}",
                    task_id=task_id,
                    fork_index=i,
                    parent_id=None if i == 0 else f"sbx-{task_n}-0",
                    agent=agent,
                    project=project,
                    vcpu=vcpu,
                    mem_gib=mem,
                    created_at=created,
                    outcome=outcome,
                    exit_code=exit_code,
                    produced_artifact=artifact,
                    intervals=intervals,
                ))

    return out


if __name__ == "__main__":
    from core.metrics import compute

    boxes = generate()
    path = save(boxes)
    m = compute(boxes, utcnow())
    y, sp = m["yield_curve"], m["spend"]

    print(f"{len(boxes)} sandboxes across {y['cohorts']} settled cohorts -> {path}")
    print(f"  mean forks per task   {y['mean_fork_count']}  (max {y['max_fork_count']})")
    print(f"  forks that catch {int(y['coverage_target'] * 100)}%  {y['recommended_forks']}"
          f"  (of {y['total_wins']} wins)")
    print(f"  total spend           ${sp['total']}")
    print(f"  of which won the task ${sp['winners']}")
    print(f"  exploration           ${sp['exploration']}")
    print(f"  the long tail         ${sp['long_tail']}   <- past that point,"
          f" bought {y['beyond_wins']} wins")
    print(f"  awake and idle        ${sp['idle']}   <- pause was free")
    print(f"  empty clean exits     {m['emptiness']['empty_count']}"
          f"  (${m['emptiness']['empty_cost']})")
