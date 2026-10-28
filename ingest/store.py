"""
Where the sandboxes live on disk. One JSON file, no database, so any row
behind any number on the dashboard can be opened in a text editor.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.model import Sandbox

DATA = Path(__file__).resolve().parents[1] / "data"
PATH = DATA / "sandboxes.json"

# Filled once, on a host that has no json file. Vercel cannot create data/
# at import time: the deployment filesystem is read-only, and an empty
# directory never made it into git.
_demo: list[Sandbox] | None = None


def save(sandboxes: list[Sandbox], path: Path = PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([s.to_json() for s in sandboxes], indent=2))
    return path


def load(path: Path = PATH) -> list[Sandbox]:
    """The on-disk log, or the seeded demo when nothing has been loaded.

    A real export wins whenever the file is there. When it is not, the
    server still has something to show: serverless hosts ship no json and
    cannot write one.
    """
    global _demo
    if path.exists():
        return [Sandbox.from_json(d) for d in json.loads(path.read_text())]
    if path != PATH:
        return []
    if _demo is None:
        from ingest.synthetic import generate
        _demo = generate()
    return _demo
