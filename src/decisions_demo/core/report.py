"""Scoreboard and cost: the part of every demo's report that is the same. Reads only data/, never calls a model."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from genai_prices import Usage, calc_price
from rich.console import Console
from rich.table import Table

from .demo import Demo

console = Console()

# Backend family -> the (provider, model) genai-prices prices it as. genai-prices is the package Pydantic AI
# prices runs with; its data ships with the installed version (uv.lock), so a report gives the same numbers on
# every machine and never goes online for them.
MODELS = {
    'jev': ('typesafe', 'jev-latest'),
    # genai-prices has gpt-6-luna's chat price. The Decisions API bills the same $0.10 per million input tokens
    # and nothing for output or caching; core/luna.py records neither, so the chat price comes out the same.
    'luna': ('openai', 'gpt-6-luna'),
    'sonnet': ('anthropic', 'claude-sonnet-5-5'),
    'opus': ('anthropic', 'claude-opus-5-5'),
    'gpt-6.1-sol': ('openai', 'gpt-6.1-sol'),
}
# USD per million (input, output, cache read, cache write), used instead of genai-prices: for models the installed
# version doesn't know yet, or prices wrong.
PRICE_OVERRIDES = {
    'gpt-6.1-sol': (2.0, 10.0, 0.10, 2.50),  # OpenAI list price, short context, standard tier (2026-10-06); not in genai-prices 0.1.9
    # genai-prices 0.1.9 prices claude-sonnet-5-5 as claude-sonnet-5 by prefix match (cache read 0.20).
    'sonnet': (2.0, 10.0, 0.10, 2.50),  # Anthropic's listed rate for Sonnet 5.5 (2026-10-09)
}


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {r['id']: r for r in (json.loads(l) for l in path.read_text().splitlines() if l.strip())}


def load_results(demo: str, results_dir: Path) -> dict[str, dict[str, dict]]:
    """Every <demo>.<name>.jsonl in results_dir, keyed by <name> (usually a backend)."""
    return {p.stem.split('.')[1]: load(p) for p in sorted(results_dir.glob(f'{demo}.*.jsonl'))}


def family(backend: str) -> str:
    """The model family a backend is billed as: sonnet_no_thinking as sonnet, luna_fallback as luna."""
    return backend if backend in MODELS else backend.split('_')[0]


def cost(rows: list[dict], backend: str) -> float:
    """USD for these rows' usage. input_tokens includes cached tokens (Pydantic AI's and genai-prices'
    convention), so those are repriced, not added."""
    provider, model = MODELS.get(family(backend), (None, family(backend)))
    total = 0.0
    for r in rows:
        read, write = r.get('cache_read_tokens', 0), r.get('cache_write_tokens', 0)
        if family(backend) in PRICE_OVERRIDES:
            pin, pout, pread, pwrite = PRICE_OVERRIDES[family(backend)]
            uncached = r.get('input_tokens', 0) - read - write
            total += (uncached * pin + read * pread + write * pwrite + r.get('output_tokens', 0) * pout) / 1e6
            continue
        usage = Usage(input_tokens=round(r.get('input_tokens', 0)), output_tokens=round(r.get('output_tokens', 0)),
                      cache_read_tokens=round(read), cache_write_tokens=round(write))
        try:
            total += float(calc_price(usage, model, provider_id=provider).total_price)
        except LookupError:
            raise LookupError(f'no price for {backend!r}: add it to MODELS or PRICE_OVERRIDES in core/report.py') from None
    return total


# Routing a Choice to the language model, shared by every demo that routes on Jev's distribution.
ROUTE = (0.6, 0.2)
"""Default rule: send an item to Sonnet when Jev's top-1 probability is under 0.6 or its margin over the
runner-up is under 0.2. ROUTE_SETTINGS are the alternatives the routing tables show."""
ROUTE_SETTINGS = [(0.0, 0.0), (0.5, 0.1), (0.6, 0.2), (0.7, 0.3), (0.8, 0.4), (0.9, 0.5), (1.01, 1.01)]


def uncertain(probabilities: dict[str, float], min_top1: float = ROUTE[0], min_margin: float = ROUTE[1]) -> bool:
    """Whether Jev's distribution over one field's options is unsure enough to route, by the rule above."""
    p1, p2 = (sorted(probabilities.values(), reverse=True) + [0.0, 0.0])[:2]
    return p1 < min_top1 or p1 - p2 < min_margin


def route_label(min_top1: float, min_margin: float) -> str:
    label = 'never' if min_top1 == 0 else 'always' if min_top1 > 1 else f'top-1 < {min_top1:.1f} or margin < {min_margin:.1f}'
    return label + (' (default)' if (min_top1, min_margin) == ROUTE else '')


def scoreboard(demo: Demo, results: dict[str, dict[str, dict]], labels: dict[str, dict], title: str | None = None,
               agree: str = 'agree') -> None:
    """One row per run. `agree` names what the agreement columns compare against."""
    fields = demo.compare_fields
    t = Table(title=title or f'{demo.name}: {len(labels)} items')
    t.add_column('backend')
    for f in fields:
        t.add_column(f'{f} {agree}')
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
