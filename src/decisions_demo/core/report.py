"""Scoreboard and cost: the part of every act's report that is the same. Reads only data/, never calls a model."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .act import Act

console = Console()

# USD per million tokens. VERIFY before the run; only Jev's is published as of 2026-09-29.
PRICES = {
    'jev': (0.042, 0.0),
    'sonnet': (3.0, 15.0),        # placeholder, check the Anthropic pricing page
    'opus': (15.0, 75.0),         # placeholder
    'luna': (0.10, 0.50),         # speculated in press, not announced
    'luna_fallback': (0.10, 0.50),
}


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {r['id']: r for r in (json.loads(l) for l in path.read_text().splitlines() if l.strip())}


def load_results(act: str, results_dir: Path) -> dict[str, dict[str, dict]]:
    """Every <act>.<name>.jsonl in results_dir, keyed by <name> (usually a backend)."""
    return {p.stem.split('.')[1]: load(p) for p in sorted(results_dir.glob(f'{act}.*.jsonl'))}


def cost(rows: list[dict], backend: str) -> float:
    pin, pout = PRICES.get(backend, (0, 0))
    return sum(r.get('input_tokens', 0) * pin + r.get('output_tokens', 0) * pout for r in rows) / 1e6


def scoreboard(act: Act, results: dict[str, dict[str, dict]], labels: dict[str, dict]) -> None:
    fields = act.compare_fields
    t = Table(title=f'{act.name}: {len(labels)} items')
    t.add_column('backend')
    for f in fields:
        t.add_column(f'{f} agree')
    t.add_column('p50 ms', justify='right')
    t.add_column('p95 ms', justify='right')
    t.add_column('cost USD', justify='right')
    t.add_column('errors', justify='right')
    for name, rows in results.items():
        ok = [r for r in rows.values() if 'error' not in r]
        agree = []
        for f in fields:
            pairs = [(r['output'][f], labels[r['id']]['label'][f]) for r in ok if r['id'] in labels]
            agree.append(f'{sum(a == b for a, b in pairs) / max(len(pairs), 1):.0%}')
        lat = sorted(r['latency_ms'] for r in ok) or [0]
        t.add_row(
            name, *agree,
            f'{statistics.median(lat):.0f}', f'{lat[int(len(lat) * 0.95) - 1]:.0f}',
            f'{cost(ok, name):.4f}', str(len(rows) - len(ok)),
        )
    console.print(t)
