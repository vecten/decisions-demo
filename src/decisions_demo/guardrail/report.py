"""Guardrail report: scoreboard vs reference labels, disagreement tables, routing fit for Jev and Luna."""

from __future__ import annotations

from rich.table import Table

from ..core.report import console, load, scoreboard
from .demo import DEMO, COMMANDS_PATH, LABELS_PATH


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


def routing_fit(results: dict[str, dict[str, dict]], labels: dict[str, dict], backends: tuple[str, ...] = ('jev', 'luna'), field: str = 'destructive') -> None:
    """Pick a confidence band to route to the language model. Anything inside the band goes to Sonnet.

    Prints, for a few band widths, how many items get routed and the accuracy of the
    combined system (the decision model outside the band, Sonnet inside): how many items
    actually need the expensive model, and what routing them buys. One group of columns
    per decision model, Jev and Luna, each routing on its own probability.
    """
    backends = tuple(b for b in backends if b in results)
    if not backends or 'sonnet' not in results:
        return
    t = Table(title=f'routing fit on "{field}": {" / ".join(backends)} outside band, sonnet inside')
    t.add_column('band')
    for b in backends:
        t.add_column(f'{b} routed', justify='right'); t.add_column(f'{b} + sonnet', justify='right'); t.add_column(f'{b} alone', justify='right')
    # The band is on P(yes), but a decision model's per-field confidence for a bool is its distance
    # from 0.5, scaled to 0-1 (P(yes)=0.01 -> 0.98). So P(yes) inside 0.5 +/- half
    # is the same as confidence below 2 * half.
    for half in (0.0, 0.1, 0.2, 0.3):
        lo, hi = 0.5 - half, 0.5 + half
        cells = []
        for b in backends:
            rows = [r for r in results[b].values() if 'error' not in r and r['id'] in labels]
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
            cells += [f'{routed / n:.0%}', f'{correct / n:.0%}', f'{alone / n:.0%}']
        t.add_row(f'P(yes) {lo:.1f}-{hi:.1f}', *cells)
    console.print(t)


def report(results: dict[str, dict[str, dict]]) -> None:
    # Demo 1's data comes from your own sessions and is never committed, so a fresh clone has none.
    if not results or not LABELS_PATH.exists():
        console.print('No guardrail data yet. It is mined from your own Claude Code sessions, never committed: '
                      'run `mine-sessions`, then `label`, then `run-demo --demo guardrail --backends jev,sonnet`.')
        return
    labels = load(LABELS_PATH)
    commands = load(COMMANDS_PATH)
    scoreboard(DEMO, results, labels)
    disagreements(results, labels, commands)
    disagreements(results, labels, commands, a='luna')
    routing_fit(results, labels)
