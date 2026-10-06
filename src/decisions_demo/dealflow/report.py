"""Deal-flow report: scoreboard vs the generator's intended labels, and what every step cost."""

from __future__ import annotations

from rich.table import Table

from ..core.report import console, cost, load, scoreboard
from .act import ACT, DEALS_PATH

PROVIDERS = {'openai': 'OpenAI', 'anthropic': 'Anthropic'}


def costs(triage: dict[str, dict[str, dict]], notes: dict[str, dict], generation: dict[str, dict], generator: str) -> None:
    """Generating the data is a one-off; the pipeline as designed is Jev's triage plus the notes it asks for."""
    t = Table(title='cost per step')
    for col in ('step', 'model', 'calls', 'input tokens', 'output tokens', 'USD'):
        t.add_column(col, justify='left' if col in ('step', 'model') else 'right')

    def row(step: str, model: str, rows: list[dict], priced_as: str) -> float:
        usd = cost(rows, priced_as)
        t.add_row(step, model, str(len(rows)), f'{sum(r.get("input_tokens", 0) for r in rows):,}',
                  f'{sum(r.get("output_tokens", 0) for r in rows):,}', f'{usd:.4f}')
        return usd

    if generation:
        models = ', '.join(sorted({r['model'] for r in generation.values()}))
        row('generate the emails (one-off)', models, list(generation.values()), generator.split(':')[-1])
    spent = {}
    for name, rows in triage.items():
        spent[name] = row(f'triage: {name}', name, [r for r in rows.values() if 'error' not in r], name)
    if notes:
        spent['notes'] = row('partner notes', 'sonnet_thinking', [r for r in notes.values() if 'error' not in r], 'sonnet_thinking')
    if 'jev' in spent and 'notes' in spent:
        t.add_row('[bold]as designed: Jev triage + notes[/bold]', '', '', '', '', f'[bold]{spent["jev"] + spent["notes"]:.4f}[/bold]')
    console.print(t)


def report(results: dict[str, dict[str, dict]]) -> None:
    notes = results.pop('notes', {})  # partner notes, not triage results
    generation = results.pop('generation', {})  # the generator's usage, one row per batch
    deals = load(DEALS_PATH)
    labels = {k: {'label': v['intended_label']} for k, v in deals.items()}
    generator = next((d['generator']['requested'] for d in deals.values() if d.get('generator')), '')
    provider = PROVIDERS.get(generator.split(':')[0], generator or 'generator')
    scoreboard(ACT, results, labels, title=f'dealflow: {len(deals)} emails, labels written by {generator or "the generator"}',
               agree=f'vs {provider}-generated labels')
    if notes:
        console.print(f'partner notes written for {len(notes)} of {len(deals)} emails (Jev priority this week or today)')
    costs(results, notes, generation, generator)
