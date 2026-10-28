"""
Load real sandbox runs from CSV.

One row per sandbox:

    id,task_id,fork_index,parent_id,agent,project,vcpu,mem_gib,
    created_at,outcome,exit_code,produced_artifact,intervals

  outcome            won | lost | failed | running
  produced_artifact  true/false, 1/0, yes/no
  intervals          semicolon separated, each start|end|active
                     e.g. 2026-10-01T09:00:00Z|2026-10-01T11:30:00Z|1;
                          2026-10-02T08:00:00Z||1
                     an empty end means it is still awake
  timestamps         ISO 8601, UTC

Two columns decide every interesting number in this tool, so they are worth
getting right:

  `intervals` is what the sandbox was awake for. Paused time is the gap
  between them and costs nothing, so a row with created_at and finished_at
  alone cannot produce a bill.

  `produced_artifact` is whether anything came out. Without it, a run that
  exited zero and left nothing behind looks exactly like one that shipped.
  If your export cannot supply it, the loader defaults it to false, which
  understates how much work got done rather than overstating it.

    python3 -m ingest.csv_log path/to/sandboxes.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from core.model import OUTCOMES, Interval, Sandbox, _parse
from ingest.store import save

REQUIRED = ["id", "task_id", "agent", "created_at", "outcome"]
TRUTHY = {"1", "true", "t", "yes", "y"}


def _intervals(raw: str, line: int) -> list[Interval]:
    out: list[Interval] = []
    for chunk in (c for c in raw.split(";") if c.strip()):
        parts = chunk.split("|")
        if len(parts) < 2:
            raise ValueError(f"line {line}: interval '{chunk}' needs start|end")
        start = _parse(parts[0].strip())
        if start is None:
            raise ValueError(f"line {line}: interval '{chunk}' has no start")
        end = _parse(parts[1].strip()) if parts[1].strip() else None
        if end is not None and end < start:
            raise ValueError(f"line {line}: interval ends before it starts")
        active = parts[2].strip().lower() in TRUTHY if len(parts) > 2 else True
        out.append(Interval(start, end, active))
    return out


def _row(row: dict, line: int) -> Sandbox:
    missing = [c for c in REQUIRED if not row.get(c)]
    if missing:
        raise ValueError(f"line {line}: missing {', '.join(missing)}")

    outcome = row["outcome"].strip()
    if outcome not in OUTCOMES:
        raise ValueError(
            f"line {line}: outcome '{outcome}' is not one of {', '.join(OUTCOMES)}")

    exit_raw = (row.get("exit_code") or "").strip()
    return Sandbox(
        id=row["id"].strip(),
        task_id=row["task_id"].strip(),
        fork_index=int(row.get("fork_index") or 0),
        parent_id=(row.get("parent_id") or "").strip() or None,
        agent=row["agent"].strip(),
        project=(row.get("project") or "unassigned").strip(),
        vcpu=float(row.get("vcpu") or 1),
        mem_gib=float(row.get("mem_gib") or 1),
        created_at=_parse(row["created_at"]),
        outcome=outcome,
        exit_code=int(exit_raw) if exit_raw else None,
        produced_artifact=(row.get("produced_artifact") or "").strip().lower() in TRUTHY,
        intervals=_intervals(row.get("intervals", ""), line),
    )


def read(path: Path) -> list[Sandbox]:
    rows: list[Sandbox] = []
    problems: list[str] = []
    with open(path, newline="") as fh:
        for line, row in enumerate(csv.DictReader(fh), start=2):
            try:
                rows.append(_row(row, line))
            except (ValueError, KeyError) as e:
                problems.append(str(e))

    if problems:
        # Load what parsed and say loudly what did not, rather than failing
        # the whole import on one bad row. Three broken lines in forty
        # thousand should still produce a dashboard.
        print(f"skipped {len(problems)} bad rows:")
        for p in problems[:10]:
            print(f"  {p}")
        if len(problems) > 10:
            print(f"  ... and {len(problems) - 10} more")

    no_artifact_col = rows and all(not r.produced_artifact for r in rows)
    if no_artifact_col:
        print("note: no produced_artifact values were true. if your export does "
              "not carry that column, every clean exit will read as empty.")

    return rows


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    src = Path(sys.argv[1])
    boxes = read(src)
    if not boxes:
        print("nothing loaded")
        raise SystemExit(1)
    out = save(boxes)
    print(f"loaded {len(boxes)} sandboxes from {src} -> {out}")
