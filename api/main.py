"""
Serves the numbers and the dashboard.

    uvicorn api.main:app --reload
    open http://localhost:8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.metrics import compute
from core.model import utcnow
from ingest.store import load

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

app = FastAPI(title="Fork Yield", version="0.1.0")


@app.get("/api/metrics")
def metrics(weeks: int = 12):
    rows = load()
    if not rows:
        raise HTTPException(
            404,
            "No data. Run `python3 -m ingest.synthetic` for the demo, "
            "or `python3 -m ingest.csv_log yourlog.csv` for a real export.",
        )
    return compute(rows, utcnow(), weeks)


@app.get("/api/sandboxes")
def sandboxes(task_id: str | None = None, outcome: str | None = None,
              agent: str | None = None, empty: bool = False,
              beyond: bool = False, limit: int = 200):
    """The rows behind the numbers, so any figure can be checked.

    Two filters matter more than the rest:

      empty=true   the sandboxes that exited zero and produced nothing, so
                   the claim can be audited instead of trusted
      beyond=true  the forks past the recommended budget, which is where
                   the headline figure comes from
    """
    rows = load()
    now = utcnow()

    if task_id:
        rows = [s for s in rows if s.task_id == task_id]
    if outcome:
        rows = [s for s in rows if s.outcome == outcome]
    if agent:
        rows = [s for s in rows if s.agent == agent]
    if empty:
        rows = [s for s in rows if s.empty]
    if beyond:
        cutoff = compute(load(), now)["yield_curve"]["recommended_forks"]
        if cutoff is not None:
            rows = [s for s in rows if s.fork_index >= cutoff]

    rows.sort(key=lambda s: s.cost(now), reverse=True)
    return [dict(s.to_json(), cost=round(s.cost(now), 4)) for s in rows[:limit]]


@app.get("/health")
def health():
    rows = load()
    return {
        "ok": True,
        "sandboxes_loaded": len(rows),
        "tasks": len({s.task_id for s in rows}),
    }


@app.get("/")
def index():
    # no-store, or the browser cheerfully serves yesterday's dashboard
    # against today's numbers, which is its own kind of wrong answer.
    return FileResponse(
        STATIC / "index.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


app.mount("/static", StaticFiles(directory=STATIC), name="static")
