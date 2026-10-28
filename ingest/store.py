"""
Where the sandboxes live on disk. One JSON file, no database, so any row
behind any number on the dashboard can be opened in a text editor.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.model import Sandbox

DATA = Path(__file__).resolve().parents[1] / "data"
DATA.mkdir(exist_ok=True)
PATH = DATA / "sandboxes.json"


def save(sandboxes: list[Sandbox], path: Path = PATH) -> Path:
    path.write_text(json.dumps([s.to_json() for s in sandboxes], indent=2))
    return path


def load(path: Path = PATH) -> list[Sandbox]:
    if not path.exists():
        return []
    return [Sandbox.from_json(d) for d in json.loads(path.read_text())]
