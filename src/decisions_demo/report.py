"""Scoreboard, disagreement table, threshold fit. Reads only data/, never calls a model.

Usage:
  report guardrail            # scoreboard vs labels + disagreements + routing fit
  report dealflow             # scoreboard vs intended labels + cost of the two halves
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(add_completion=False)
console = Console()

# USD per million tokens. VERIFY before the run; only Jev's is published as of 2026-09-29.
PRICES = {
    'jev': (0.042, 0.0),
    'sonnet': (3.0, 15.0),        # placeholder, check the Anthropic pricing page
    'opus': (15.0, 75.0),         # placeholder
    'luna': (0.10, 0.50),         # speculated in press, not announced
    'luna_fallback': (0.10, 0.50),
}

COMPARE_FIELDS = {
    'guardrail': ['action', 'destructive', 'outside_directory', 'misleading'],
    'dealflow': ['stage', 'sector', 'fits_thesis', 'warm_intro', 'priority', 'next_action'],
}


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {r['id']: r for r in (json.loads(l) for l in path.read_text().splitlines() if l.strip())}


def cost(rows: list[dict], backend: str) -> float:
    pin, pout = PRICES.get(backend, (0, 0))
    return sum(r.get('input_tokens', 0) * pin + r.get('output_tokens', 0) * pout for r in rows) / 1e6


def scoreboard(act: str, results: dict[str, dict[str, dict]], labels: dict[str, dict]) -> None:
    fields = COMPARE_FIELDS[act]
    t = Table(title=f'{act}: {len(labels)} items')
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


def disagreements(results: dict[str, dict[str, dict]], labels: dict[str, dict], commands: dict[str, dict], field: str = 'action', a: str = 'jev', b: str = 'sonnet') -> None:
    if a not in results or b not in results:
        return
    t = Table(title=f'{a} vs {b} disagree on "{field}"')
    t.add_column('command', max_width=70)
    t.add_column(a)
    t.add_column(f'{a} conf', justify='right')
    t.add_column(b)
    t.add_column('label')
    t.add_column('why (judge)', max_width=50)
    for id_, ra in results[a].items():
        rb = results[b].get(id_)
        if not rb or 'error' in ra or 'error' in rb:
            continue
        if ra['output'][field] != rb['output'][field]:
            lab = labels.get(id_, {}).get('label', {})
            t.add_row(
                commands[id_]['command'], str(ra['output'][field]),
                f"{ra.get('confidence', {}).get(field, float('nan')):.2f}",
                str(rb['output'][field]), str(lab.get(field, '?')), lab.get('explanation', ''),
            )
    console.print(t)


def routing_fit(results: dict[str, dict[str, dict]], labels: dict[str, dict], backend: str = 'jev', field: str = 'destructive') -> None:
    """Pick a confidence band to route to the language model. Anything inside the band goes to Sonnet.

    Prints, for a few band widths, how many items get routed and the accuracy of the
    combined system (Jev outside the band, Sonnet inside). This is the slide that
    shows 'only 10-20% needed the expensive model'.
    """
    if backend not in results or 'sonnet' not in results:
        return
    rows = [r for r in results[backend].values() if 'error' not in r and r['id'] in labels]
    t = Table(title=f'routing fit on "{field}": {backend} outside band, sonnet inside')
    t.add_column('band'); t.add_column('routed', justify='right'); t.add_column('combined acc', justify='right'); t.add_column(f'{backend} alone', justify='right')
    # The band is on P(yes), but Jev's per-field confidence for a bool is its distance
    # from 0.5, scaled to 0-1 (P(yes)=0.01 -> 0.98). So P(yes) inside 0.5 +/- half
    # is the same as confidence below 2 * half.
    for half in (0.0, 0.1, 0.2, 0.3):
        lo, hi = 0.5 - half, 0.5 + half
        routed, correct, alone = 0, 0, 0
        for r in rows:
            truth = labels[r['id']]['label'][field]
            sure = r.get('confidence', {}).get(field, 1.0)
            alone += (r['output'][field] == truth)
            if sure < 2 * half:
                routed += 1
                s = results['sonnet'].get(r['id'])
                correct += bool(s and 'error' not in s and s['output'][field] == truth)
            else:
                correct += (r['output'][field] == truth)
        n = max(len(rows), 1)
        t.add_row(f'P(yes) {lo:.1f}-{hi:.1f}',f'{routed / n:.0%}', f'{correct / n:.0%}', f'{alone / n:.0%}')
    console.print(t)


@app.command()
def main(act: str = typer.Argument(...), results_dir: Path = typer.Option(Path('data/results'))):
    results = {p.stem.split('.')[1]: load(p) for p in sorted(results_dir.glob(f'{act}.*.jsonl'))}
    if act == 'guardrail':
        labels = load(Path('data/labels.jsonl'))
        commands = load(Path('data/commands.jsonl'))
        scoreboard(act, results, labels)
        disagreements(results, labels, commands)
        routing_fit(results, labels)
    else:
        results.pop('notes', None)  # partner notes, not triage results
        deals = load(Path('data/deals.jsonl'))
        labels = {k: {'label': v['intended_label']} for k, v in deals.items()}
        scoreboard(act, results, labels)
        notes = load(Path('data/results/dealflow.notes.jsonl'))
        if notes:
            console.print(f'partner notes written for {len(notes)} deals, cost USD {cost(list(notes.values()), "sonnet"):.4f}')


if __name__ == '__main__':
    app()
