"""
Hand-checkable tests. Every expected number here was worked out on paper
before the code ran, so a failure means the code is wrong, not the test.

A 1 vCPU / 1 GiB sandbox costs 0.0504 + 0.0162 = 0.0666 per running hour.
Most of the money below is a multiple of that, so it can be checked without
a calculator.
"""

from datetime import datetime, timedelta

from core.metrics import (attribution, compute, emptiness, spend, trend,
                          yield_curve)
from core.model import Interval, Sandbox, cohorts

NOW = datetime(2026, 10, 28, 12, 0, 0)
HOURLY = 0.0504 + 0.0162          # one small sandbox, one hour


def box(task, index, *, hours=1.0, idle=0.0, outcome="lost", exit_code=0,
        artifact=True, agent="claude-code", project="p1", vcpu=1.0, mem=1.0,
        days_ago=10, running=False) -> Sandbox:
    """One sandbox, with its awake time laid out explicitly."""
    start = NOW - timedelta(days=days_ago)
    intervals = []
    if hours:
        intervals.append(Interval(start, None if running else start + timedelta(hours=hours), True))
    if idle:
        base = start + timedelta(hours=hours)
        intervals.append(Interval(base, base + timedelta(hours=idle), False))
    return Sandbox(
        id=f"sbx-{task}-{index}", task_id=task, fork_index=index,
        parent_id=None if index == 0 else f"sbx-{task}-0",
        agent=agent, project=project, vcpu=vcpu, mem_gib=mem,
        created_at=start, outcome=outcome, exit_code=exit_code,
        produced_artifact=artifact, intervals=intervals,
    )


def cohort_set(n_cohorts, size, winner_index, **kw) -> list[Sandbox]:
    """n cohorts of the same size, each won at the same fork index."""
    out = []
    for c in range(n_cohorts):
        for i in range(size):
            out.append(box(f"T{c}", i,
                           outcome="won" if i == winner_index else "lost", **kw))
    return out


# ------------------------------------------------- paused time is free

def test_paused_time_costs_nothing():
    """A sandbox alive for ten days but awake for two hours costs two hours.

    Wall clock age is not a bill. If it were, pause would not be a feature.
    """
    s = box("T1", 0, hours=2.0, days_ago=10)
    assert round(s.running_hours(NOW), 3) == 2.0
    assert round(s.cost(NOW), 4) == round(2 * HOURLY, 4)


def test_idle_time_is_billed_but_counted_separately():
    """Awake and doing nothing still costs. It just should not have to.

    Pause is free, so every idle hour is a line somebody could have removed
    by making one call. It is billed like any other hour, and reported on
    its own so it can be acted on.
    """
    s = box("T1", 0, hours=2.0, idle=3.0)
    assert round(s.running_hours(NOW), 3) == 5.0     # billed for all five
    assert round(s.active_hours(NOW), 3) == 2.0
    assert round(s.idle_hours(NOW), 3) == 3.0
    assert round(s.idle_cost(NOW), 4) == round(3 * HOURLY, 4)


def test_a_still_running_sandbox_is_billed_up_to_now():
    s = box("T1", 0, hours=0, days_ago=0, running=True)
    s.intervals = [Interval(NOW - timedelta(hours=4), None, True)]
    assert round(s.running_hours(NOW), 2) == 4.0


# ------------------------------------- clean exit is not a finished job

def test_a_clean_exit_with_no_artifact_is_empty():
    """Exit zero, nothing produced. No crash to find, and a full invoice."""
    s = box("T1", 0, exit_code=0, artifact=False)
    assert s.finished is True
    assert s.empty is True


def test_a_crash_is_not_counted_as_empty():
    """A non-zero exit is a different problem with a different fix.

    Lumping crashes in with silent empty runs hides the interesting half:
    a crash announces itself, and these do not.
    """
    s = box("T1", 0, outcome="failed", exit_code=1, artifact=False)
    assert s.empty is False


def test_a_still_running_sandbox_is_not_empty_yet():
    s = box("T1", 0, outcome="running", exit_code=None, artifact=False, running=True)
    assert s.empty is False


def test_missing_artifact_column_reads_as_empty_not_as_success():
    """An export with no artifact data must not inflate the success count.

    Defaulting it to true would make every clean exit look productive and
    quietly delete the finding. False understates the work done, which is
    the direction that gets questioned rather than believed.
    """
    raw = {
        "id": "s1", "task_id": "T1", "fork_index": 0, "agent": "codex",
        "created_at": "2026-10-20T12:00:00Z", "outcome": "lost", "exit_code": 0,
        "intervals": [{"start": "2026-10-20T12:00:00Z", "end": "2026-10-20T13:00:00Z"}],
    }
    s = Sandbox.from_json(raw)
    assert s.produced_artifact is False
    assert s.empty is True


# -------------------------------------------------------------- cohorts

def test_a_cohort_knows_which_fork_won():
    boxes = [box("T1", 0), box("T1", 1, outcome="won"), box("T1", 2)]
    c = cohorts(boxes)[0]
    assert c.size == 3
    assert c.winner_index == 1
    assert c.settled is True


def test_a_cohort_with_anything_still_running_is_not_settled():
    boxes = [box("T1", 0, outcome="won"),
             box("T1", 1, outcome="running", running=True)]
    assert cohorts(boxes)[0].settled is False
    # and an unsettled cohort is left out of the curve entirely
    assert yield_curve(boxes, NOW)["cohorts"] == 0


# ---------------------------------------------------------- yield curve

def test_win_rate_per_fork_index():
    """Five cohorts of two. Four won at fork 0, one at fork 1.

    So fork 0 wins 4 of 5 and fork 1 wins 1 of 5.
    """
    boxes = cohort_set(4, 2, 0) + cohort_set(1, 2, 1)
    # cohort_set reuses task ids, so relabel the last cohort
    for s in boxes[-2:]:
        s.task_id = "T-last"
    y = yield_curve(boxes, NOW)
    pts = {p["fork_index"]: p for p in y["points"]}
    assert y["cohorts"] == 5
    assert pts[0]["spawned"] == 5 and pts[0]["wins"] == 4
    assert pts[0]["win_rate"] == 0.8
    assert pts[1]["win_rate"] == 0.2
    assert y["total_wins"] == 5


def test_recommended_fork_count_is_the_smallest_budget_that_keeps_the_wins():
    """Every win lands at fork 0, so one fork would have been enough.

    Five cohorts of two: fork 0 wins all five, fork 1 wins none. Cumulative
    coverage hits 100% at index 0, so the recommendation is one fork and
    everything at index 1 is spend past the point that caught every win.
    """
    boxes = cohort_set(5, 2, 0, hours=1.0)
    for i, s in enumerate(boxes):
        s.task_id = f"T{i // 2}"
    y = yield_curve(boxes, NOW)
    assert y["recommended_forks"] == 1
    assert y["beyond_wins"] == 0
    # five losing forks, one hour each
    assert y["beyond_cost"] == round(5 * HOURLY, 2)
    assert y["beyond_share"] == round(5 / 10, 4)      # half the sandboxes


def test_one_lucky_win_in_the_tail_does_not_move_the_recommendation():
    """This is why coverage beats a win-rate floor.

    Nineteen cohorts won at fork 0 and one won at fork 5. A floor on win
    rate would drag the cutoff all the way out to 5 on the strength of that
    single win. Coverage does not care where the outlier landed.
    """
    boxes = []
    for c in range(19):
        for i in range(6):
            boxes.append(box(f"T{c}", i, outcome="won" if i == 0 else "lost"))
    for i in range(6):
        boxes.append(box("T-lucky", i, outcome="won" if i == 5 else "lost"))

    y = yield_curve(boxes, NOW)
    assert y["total_wins"] == 20
    assert y["recommended_forks"] == 1       # 19/20 = 95%, exactly the target
    pts = {p["fork_index"]: p for p in y["points"]}
    assert pts[5]["wins"] == 1               # the outlier is still reported
    assert pts[5]["win_rate"] == 0.05


def test_an_index_with_too_few_cohorts_is_not_given_a_win_rate():
    """Four cohorts is not enough to call a fork position dead.

    Drawing a 0% bar from four samples invites someone to cut a fork budget
    on noise.
    """
    boxes = []
    for c in range(8):                       # eight cohorts, plenty at 0 and 1
        for i in range(2):
            boxes.append(box(f"T{c}", i, outcome="won" if i == 0 else "lost"))
    for i in range(3):                       # one wide cohort: index 2 seen once
        boxes.append(box("T-wide", i, outcome="won" if i == 0 else "lost"))

    pts = {p["fork_index"]: p for p in yield_curve(boxes, NOW)["points"]}
    assert pts[2]["spawned"] == 1
    assert pts[2]["measured"] is False
    assert pts[2]["win_rate"] is None        # suppressed, not plotted as zero
    assert pts[0]["measured"] is True


def test_cost_per_win_is_none_where_nothing_won():
    boxes = cohort_set(5, 2, 0, hours=1.0)
    for i, s in enumerate(boxes):
        s.task_id = f"T{i // 2}"
    pts = {p["fork_index"]: p for p in yield_curve(boxes, NOW)["points"]}
    # fork 0 won five times for five hours of compute
    assert pts[0]["cost_per_win"] == round(5 * HOURLY / 5, 2)
    assert pts[1]["cost_per_win"] is None


# ---------------------------------------------------------------- spend

def test_spend_splits_winners_from_exploration_from_the_long_tail():
    """Every win at fork 0, cohorts of three, one hour each.

    Recommendation is one fork, so forks 1 and 2 are both past it and all
    of their compute is long tail. Nothing is left for exploration.
    """
    boxes = []
    for c in range(5):
        for i in range(3):
            boxes.append(box(f"T{c}", i, hours=1.0,
                             outcome="won" if i == 0 else "lost"))
    sp = spend(boxes, NOW)
    assert sp["winners"] == round(5 * HOURLY, 2)        # 5 winning forks
    assert sp["long_tail"] == round(10 * HOURLY, 2)     # 10 losing forks
    assert sp["exploration"] == 0.0
    assert sp["total"] == round(15 * HOURLY, 2)


def test_idle_is_reported_separately_and_does_not_double_count():
    """An idle hour already belongs to whichever fork sat there.

    So idle is not a fourth slice of the same pie, and the dashboard says
    as much rather than presenting four numbers that do not add up.
    """
    boxes = [box("T1", 0, hours=1.0, idle=2.0, outcome="won")]
    sp = spend(boxes, NOW)
    assert sp["total"] == round(3 * HOURLY, 2)          # all three billed
    assert sp["idle"] == round(2 * HOURLY, 2)
    assert sp["winners"] == round(3 * HOURLY, 2)        # the same hours again


def test_cost_per_won_task_ignores_tasks_nobody_won():
    """Two cohorts, one won. The cost of both bought one keeper.

    Dividing by every task instead would flatter the number by pretending
    the failed task produced something.
    """
    boxes = ([box("T1", 0, hours=1.0, outcome="won")]
             + [box("T2", 0, hours=1.0, outcome="lost")])
    sp = spend(boxes, NOW)
    assert sp["cost_per_won_task"] == round(2 * HOURLY, 2)


# ---------------------------------------------------------- attribution

def test_attribution_splits_by_agent_and_project():
    boxes = [
        box("T1", 0, hours=2.0, agent="codex", project="alpha", outcome="won"),
        box("T2", 0, hours=1.0, agent="codex", project="alpha", outcome="lost"),
        box("T3", 0, hours=1.0, agent="opencode", project="beta", outcome="won"),
    ]
    at = attribution(boxes, NOW)
    codex = next(r for r in at["by_agent"] if r["name"] == "codex")
    assert codex["sandboxes"] == 2
    assert codex["cost"] == round(3 * HOURLY, 2)
    assert codex["win_rate"] == 0.5
    assert codex["cost_per_win"] == round(3 * HOURLY, 2)   # three hours, one win
    assert [r["name"] for r in at["by_project"]][0] == "alpha"   # biggest first


# ------------------------------------------------------------ emptiness

def test_empty_share_counts_only_the_runs_that_looked_successful():
    """Three clean exits, one with nothing to show. Crashes are excluded.

    The denominator is clean exits, not all runs, because the claim being
    made is about the runs a billing system would call successful.
    """
    boxes = [
        box("T1", 0, exit_code=0, artifact=True),
        box("T2", 0, exit_code=0, artifact=True),
        box("T3", 0, exit_code=0, artifact=False),
        box("T4", 0, outcome="failed", exit_code=1, artifact=False),
    ]
    em = emptiness(boxes, NOW)
    assert em["settled"] == 4
    assert em["exited_clean"] == 3
    assert em["empty_count"] == 1
    assert em["empty_share_of_clean"] == round(1 / 3, 4)


def test_the_worst_empty_runs_are_the_expensive_ones():
    boxes = [
        box("T1", 0, hours=1.0, artifact=False),
        box("T2", 0, hours=9.0, artifact=False),
    ]
    worst = emptiness(boxes, NOW)["worst"]
    assert worst[0]["task_id"] == "T2"


# ---------------------------------------------------------------- trend

def test_the_half_finished_current_week_is_left_off():
    """A week that is two days old always looks like a cliff."""
    boxes = [box("T1", 0, days_ago=0, outcome="won"),      # this week
             box("T2", 0, days_ago=12, outcome="won")]     # a week or two back
    weeks = [p["week"] for p in trend(boxes, NOW)["points"]]
    y, w, _ = NOW.isocalendar()
    assert f"{y}-W{w:02d}" not in weeks
    assert len(weeks) == 1


# ------------------------------------------------------------------ all

def test_compute_runs_on_nothing_without_falling_over():
    m = compute([], NOW)
    assert m["sandbox_count"] == 0
    assert m["yield_curve"]["recommended_forks"] is None
    assert m["spend"]["total"] == 0
    assert m["trend"]["points"] == []


def test_compute_returns_every_section():
    m = compute([box("T1", 0, outcome="won")], NOW)
    for key in ("yield_curve", "spend", "attribution", "emptiness", "trend"):
        assert key in m


def test_shares_are_computed_from_raw_figures_not_rounded_ones():
    """Five of ten sandboxes past the cutoff is 50%, not 49.55%.

    points[].cost is rounded to cents for display. Summing those and
    dividing by the unrounded total drifts, and the drift is small enough
    that nobody would catch it by eye.
    """
    boxes = cohort_set(5, 2, 0, hours=1.0)
    for i, s in enumerate(boxes):
        s.task_id = f"T{i // 2}"
    y = yield_curve(boxes, NOW)
    assert y["beyond_share"] == 0.5
