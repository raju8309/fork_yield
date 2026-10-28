"""
What a sandbox is, and what a fork cohort is.

Two ideas carry this whole tool.

The first is that **paused time is free**. A sandbox that existed for nine
days and ran for forty minutes costs forty minutes. So wall-clock age tells
you nothing about the bill, and every cost figure here is built from running
intervals rather than from created_at to finished_at.

The second is that **exiting cleanly is not the same as doing the job**. A
sandbox can run to completion, return zero, and produce nothing. Billing
counts it the same as the one that shipped the feature. Keeping those two
apart is the reason the numbers in this tool differ from the numbers on an
invoice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Superserve's published rates, as of the pricing page. Overridable per
# install, since anyone self-hosting will have their own.
VCPU_HOUR = 0.0504
GIB_HOUR = 0.0162

AGENTS = ["claude-code", "codex", "opencode", "kilo-code"]

# A fork either wins, loses, or never finished. "Lost" is not a failure —
# losing is what nine out of ten forks are supposed to do. The question this
# tool answers is how many of them needed to exist.
OUTCOMES = ["won", "lost", "failed", "running"]


def utcnow() -> datetime:
    """UTC now with no tzinfo, to match every other timestamp here."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts.replace("Z", "")) if ts else None


def _fmt(ts: datetime | None) -> str | None:
    return ts.isoformat() + "Z" if ts else None


@dataclass
class Interval:
    """A stretch of time the sandbox was awake.

    `active` separates work from waiting. A sandbox that is running but idle
    is costing money for nothing, and that is the single easiest line on a
    bill to remove — pause is free and nobody remembers to call it.
    """

    start: datetime
    end: datetime | None = None
    active: bool = True

    def hours(self, now: datetime) -> float:
        return ((self.end or now) - self.start).total_seconds() / 3600

    def to_json(self) -> dict:
        return {"start": _fmt(self.start), "end": _fmt(self.end), "active": self.active}

    @classmethod
    def from_json(cls, d: dict) -> "Interval":
        return cls(_parse(d["start"]), _parse(d.get("end")), d.get("active", True))


@dataclass
class Sandbox:
    id: str
    task_id: str                      # the fork cohort this belongs to
    fork_index: int                   # 0 is the root, 1..n are its forks
    agent: str
    project: str                      # who to bill it to
    vcpu: float
    mem_gib: float
    created_at: datetime
    parent_id: str | None = None
    outcome: str = "running"
    exit_code: int | None = None
    produced_artifact: bool = False   # did anything come out of it
    intervals: list[Interval] = field(default_factory=list)

    # ------------------------------------------------------------- billing

    def running_hours(self, now: datetime) -> float:
        return sum(i.hours(now) for i in self.intervals)

    def active_hours(self, now: datetime) -> float:
        return sum(i.hours(now) for i in self.intervals if i.active)

    def idle_hours(self, now: datetime) -> float:
        """Awake and doing nothing. Pause would have made this free."""
        return sum(i.hours(now) for i in self.intervals if not i.active)

    def cost(self, now: datetime, vcpu_hour: float = VCPU_HOUR,
             gib_hour: float = GIB_HOUR) -> float:
        h = self.running_hours(now)
        return h * (self.vcpu * vcpu_hour + self.mem_gib * gib_hour)

    def idle_cost(self, now: datetime, vcpu_hour: float = VCPU_HOUR,
                  gib_hour: float = GIB_HOUR) -> float:
        h = self.idle_hours(now)
        return h * (self.vcpu * vcpu_hour + self.mem_gib * gib_hour)

    # ------------------------------------------------------------ outcomes

    @property
    def finished(self) -> bool:
        return self.outcome != "running"

    @property
    def won(self) -> bool:
        return self.outcome == "won"

    @property
    def empty(self) -> bool:
        """Exited cleanly and produced nothing.

        Not a crash. The process ended, the exit code is zero, the invoice
        charges for it, and there is nothing to show. This is the one the
        billing system cannot see.
        """
        return self.exit_code == 0 and not self.produced_artifact and self.finished

    # ---------------------------------------------------------------- json

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "fork_index": self.fork_index,
            "parent_id": self.parent_id,
            "agent": self.agent,
            "project": self.project,
            "vcpu": self.vcpu,
            "mem_gib": self.mem_gib,
            "created_at": _fmt(self.created_at),
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "produced_artifact": self.produced_artifact,
            "intervals": [i.to_json() for i in self.intervals],
        }

    @classmethod
    def from_json(cls, d: dict) -> "Sandbox":
        # An export that predates artifact tracking has no idea whether
        # anything came out. Default it to False rather than True: that
        # understates how much work got done, which is the safe direction.
        # Overstating it would quietly hide the empty-run problem, which is
        # the thing worth finding.
        return cls(
            id=d["id"],
            task_id=d["task_id"],
            fork_index=d.get("fork_index", 0),
            parent_id=d.get("parent_id"),
            agent=d.get("agent", "unknown"),
            project=d.get("project", "unassigned"),
            vcpu=float(d.get("vcpu", 1)),
            mem_gib=float(d.get("mem_gib", 1)),
            created_at=_parse(d["created_at"]),
            outcome=d.get("outcome", "running"),
            exit_code=d.get("exit_code"),
            produced_artifact=bool(d.get("produced_artifact", False)),
            intervals=[Interval.from_json(i) for i in d.get("intervals", [])],
        )


@dataclass
class Cohort:
    """One task's forks, together.

    A cohort is the unit that matters. One sandbox tells you nothing about
    whether forking was worth it; the cohort tells you how many were spawned
    and which one earned its keep.
    """

    task_id: str
    sandboxes: list[Sandbox]

    @property
    def size(self) -> int:
        return len(self.sandboxes)

    @property
    def winner(self) -> Sandbox | None:
        for s in self.sandboxes:
            if s.won:
                return s
        return None

    @property
    def settled(self) -> bool:
        """Every fork has stopped, so the cohort's verdict is final."""
        return all(s.finished for s in self.sandboxes)

    @property
    def winner_index(self) -> int | None:
        w = self.winner
        return w.fork_index if w else None

    def cost(self, now: datetime) -> float:
        return sum(s.cost(now) for s in self.sandboxes)

    def winner_cost(self, now: datetime) -> float:
        w = self.winner
        return w.cost(now) if w else 0.0

    def discarded_cost(self, now: datetime) -> float:
        """What the losing forks cost.

        Some of this is the price of the method and worth paying. The
        metrics module splits it into the part that bought an answer and the
        part that never could have.
        """
        return sum(s.cost(now) for s in self.sandboxes if not s.won)


def cohorts(sandboxes: list[Sandbox]) -> list[Cohort]:
    groups: dict[str, list[Sandbox]] = {}
    for s in sandboxes:
        groups.setdefault(s.task_id, []).append(s)
    for g in groups.values():
        g.sort(key=lambda s: s.fork_index)
    return [Cohort(task_id=k, sandboxes=v) for k, v in sorted(groups.items())]
