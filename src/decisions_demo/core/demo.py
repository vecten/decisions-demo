"""What a demo is, as far as the runner and the scoreboard care, plus the JSONL plumbing they share."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

RESULTS_DIR = Path('data/results')


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')


@dataclass(frozen=True)
class Demo:
    """One question asked of every record in a dataset, answered by every backend in the same schema."""

    name: str
    data: Path
    state: Callable[[dict], str]
    """Turns one input record into the text every backend sees."""
    schema: type[BaseModel] | None = None
    """The question run-demo asks. None for a demo whose runs each ask a different one (domains)."""
    instructions: str | None = None
    sonnet_thinking: bool = True
    """What the backend name `sonnet` means in this demo: adaptive thinking on, or off. The explicit
    `sonnet_thinking` and `sonnet_no_thinking` backends are always available as comparison runs."""
    compare_fields: tuple[str, ...] = ()
    """Schema fields the scoreboard checks against the reference labels, in column order."""

    def records(self, limit: int = 0) -> list[dict]:
        records = read_jsonl(self.data)
        return records[:limit] if limit else records

    def results_path(self, backend: str) -> Path:
        return RESULTS_DIR / f'{self.name}.{backend}.jsonl'
