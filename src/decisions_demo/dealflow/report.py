"""Deal-flow report: scoreboard vs the generator's intended labels, routing fit, and what every step cost."""

from __future__ import annotations

import statistics

from rich.table import Table

from ..core.report import ROUTE, ROUTE_SETTINGS, console, cost, load, route_label, scoreboard, uncertain
from .act import ACT, DEALS_PATH
from .schemas import Priority

PROVIDERS = {'openai': 'OpenAI', 'anthropic': 'Anthropic'}
ROUTED_FIELDS = ('next_action', 'priority')
SMALL_SAMPLE = ('{n} emails: one email is {pct:.0f} points, so differences of a few emails between runs or settings '
                'are within noise. Read these tables for the shape of the trade-off, not to rank Jev against Sonnet.')


def _pct(k: int, n: int) -> str:
    return f'{k / n:.0%}' if n else '–'


def per_email_cost(rows: dict[str, dict], priced_as: str) -> float:
    ok = [r for r in rows.values() if 'error' not in r]
    return cost(ok, priced_as) / len(ok) if ok else 0.0


def routing(jev: dict[str, dict], sonnet: dict[str, dict], labels: dict[str, dict], agree: str) -> None:
    """Jev triages every email; where Jev is unsure of a field, by the shared rule, Sonnet's answer is used instead.

    Same rule and table as demo 3's routing: share routed, combined agreement, and cost per 1,000 emails, where a
    routed email costs one Jev call plus one Sonnet triage call.
    """
    ids = [i for i in jev if 'error' not in jev[i] and i in sonnet and 'error' not in sonnet[i] and i in labels]
    n = len(ids)
    jev_usd, sonnet_usd = per_email_cost(jev, 'jev'), per_email_cost(sonnet, 'sonnet')
    for field in ROUTED_FIELDS:
        truth = {i: labels[i]['label'][field] for i in ids}
        t = Table(title=f'routing on {field}: Jev answers, Sonnet takes the emails Jev is unsure of ({n} emails, {agree})')
        for col in ('route when', 'routed', f'combined {field}', 'USD per 1000 emails'):
            t.add_column(col, justify='right')
        for t1, m in ROUTE_SETTINGS:
            routed = {i for i in ids if uncertain(jev[i]['probabilities'][field], t1, m)}
            right = sum((sonnet if i in routed else jev)[i]['output'][field] == truth[i] for i in ids)
            t.add_row(route_label(t1, m), _pct(len(routed), n), _pct(right, n), f'{1000 * (jev_usd + sonnet_usd * len(routed) / n):.2f}')
        console.print(t)
        alone = {name: sum(rows[i]['output'][field] == truth[i] for i in ids) for name, rows in (('jev', jev), ('sonnet', sonnet))}
        console.print(f'alone on {field}: jev {_pct(alone["jev"], n)}, sonnet {_pct(alone["sonnet"], n)}')


def routed_on(jev: dict[str, dict], sonnet: dict[str, dict], fields: tuple[str, ...]) -> set[str]:
    """Emails Jev is unsure of, by the default rule, on any of these fields."""
    return {i for i, r in jev.items() if 'error' not in r and i in sonnet and 'error' not in sonnet[i]
            and any(uncertain(r['probabilities'][f]) for f in fields)}


def pipeline(jev: dict[str, dict], sonnet: dict[str, dict], notes: dict[str, dict],
             fields: tuple[str, ...] = ROUTED_FIELDS) -> tuple[int, int, float]:
    """Jev triages all; an email Jev is unsure of on any of `fields` goes to Sonnet's triage; the final priority
    decides which emails get a partner note. Returns routed, notes, USD.

    Notes were only written for Jev's own high-priority emails, so the notes half is priced at the measured mean
    cost per note.
    """
    ids = [i for i in jev if 'error' not in jev[i]]
    routed = routed_on(jev, sonnet, fields)
    final = {i: (sonnet if i in routed else jev)[i]['output']['priority'] for i in ids}
    noted = sum(int(p) >= Priority.this_week for p in final.values())
    note_usd = per_email_cost(notes, 'sonnet_thinking')
    usd = per_email_cost(jev, 'jev') * len(ids) + per_email_cost(sonnet, 'sonnet') * len(routed) + note_usd * noted
    return len(routed), noted, usd


def costs(triage: dict[str, dict[str, dict]], notes: dict[str, dict], generation: dict[str, dict], generator: str) -> None:
    """Generating the data is a one-off; the pipeline is Jev's triage, Sonnet on the unsure ones, and the notes."""
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
        t.add_row('Jev triage + notes, no routing', '', '', '', '', f'{spent["jev"] + spent["notes"]:.4f}')
    routable = 'jev' in triage and 'sonnet' in triage and notes
    if routable:
        n = len(triage['jev'])
        for label, fields in (('[bold]full pipeline as designed[/bold], routed on either field', ROUTED_FIELDS),
                              ('full pipeline, routed on priority only', ('priority',))):
            routed, noted, usd = pipeline(triage['jev'], triage['sonnet'], notes, fields)
            t.add_row(label, f'jev on {n}, sonnet on {routed} routed, notes for {noted}', str(n + routed + noted), '', '',
                      f'{usd:.4f}')
    console.print(t)
    if routable:
        per_field = {f: routed_on(triage['jev'], triage['sonnet'], (f,)) for f in ROUTED_FIELDS}
        either = set().union(*per_field.values())
        console.print(
            f'Full pipeline: routed by the default rule (top-1 < {ROUTE[0]} or margin < {ROUTE[1]}); notes priced at the '
            f'measured mean per note, USD {statistics.mean(cost([r], "sonnet_thinking") for r in notes.values() if "error" not in r):.4f}.\n'
            f'Routing on two fields compounds: an email goes to Sonnet if Jev is unsure of either, so '
            + ' and '.join(f'{len(ids)} on {f}' for f, ids in per_field.items())
            + f' became {len(either)}, not {min(len(ids) for ids in per_field.values())}. A real pipeline would route per '
            'field, or route once on the field that gates the expensive step: here priority, which decides who gets a '
            'partner note.')


def report(results: dict[str, dict[str, dict]]) -> None:
    notes = results.pop('notes', {})  # partner notes, not triage results
    generation = results.pop('generation', {})  # the generator's usage, one row per batch
    deals = load(DEALS_PATH)
    labels = {k: {'label': v['intended_label']} for k, v in deals.items()}
    generator = next((d['generator']['requested'] for d in deals.values() if d.get('generator')), '')
    provider = PROVIDERS.get(generator.split(':')[0], generator or 'generator')
    agree = f'vs {provider}-generated labels'
    scoreboard(ACT, results, labels, title=f'dealflow: {len(deals)} emails, labels written by {generator or "the generator"}',
               agree=agree)
    console.print(SMALL_SAMPLE.format(n=len(deals), pct=100 / max(len(deals), 1)))
    if 'jev' in results and 'sonnet' in results:
        routing(results['jev'], results['sonnet'], labels, agree)
    if notes:
        console.print(f'partner notes written for {len(notes)} of {len(deals)} emails (Jev priority this week or today)')
    costs(results, notes, generation, generator)
