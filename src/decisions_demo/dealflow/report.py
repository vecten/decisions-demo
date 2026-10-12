"""Deal-flow report: scoreboard vs the generator's intended labels, routing fit, and what every step cost."""

from __future__ import annotations

import collections
import statistics

from rich.table import Table

from ..core.report import ROUTE, ROUTE_SETTINGS, console, cost, load, md, pct, route_label, scoreboard, table, uncertain
from .demo import DEMO, DEALS_PATH
from .schemas import Priority

PROVIDERS = {'openai': 'OpenAI', 'anthropic': 'Anthropic'}
ROUTED_FIELDS = ('next_action', 'priority')
SMALL_SAMPLE = ('{n} emails: one email is {pct:.0f} points, so differences of a few emails between runs or settings '
                'are within noise. Read these tables for the shape of the trade-off, not to rank Jev against Sonnet.')


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
            t.add_row(route_label(t1, m), pct(len(routed), n), pct(right, n), f'{1000 * (jev_usd + sonnet_usd * len(routed) / n):.2f}')
        console.print(t)
        alone = {name: sum(rows[i]['output'][field] == truth[i] for i in ids) for name, rows in (('jev', jev), ('sonnet', sonnet))}
        console.print(f'alone on {field}: jev {pct(alone["jev"], n)}, sonnet {pct(alone["sonnet"], n)}')


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


def tables(d2: dict[str, dict[str, dict]], notes: dict[str, dict], gen: dict[str, dict], deals: dict[str, dict]) -> None:
    """The Markdown tables report.md uses beyond the scoreboard: per kind and length, confusions, routed emails, notes."""
    md('# DEMO 2 EXTRA TABLES\n')
    md('## per-kind and per-length agreement (jev, sonnet) on priority and next_action and all six fields\n')
    f2 = ('stage', 'sector', 'fits_thesis', 'warm_intro', 'priority', 'next_action')
    for key in ('kind', 'length'):
        rows = []
        for val in sorted({d['brief'][key] for d in deals.values()}):
            ids2 = [i for i, d in deals.items() if d['brief'][key] == val]
            cells = [val, len(ids2)]
            for b in ('jev', 'sonnet'):
                cells.append(pct(sum(d2[b][i]['output'][f] == deals[i]['intended_label'][f] for i in ids2 for f in f2), len(ids2) * 6))
                cells.append(pct(sum(d2[b][i]['output']['priority'] == deals[i]['intended_label']['priority'] for i in ids2), len(ids2)))
                cells.append(pct(sum(d2[b][i]['output']['next_action'] == deals[i]['intended_label']['next_action'] for i in ids2), len(ids2)))
            rows.append(cells)
        table([key, 'n', 'jev all fields', 'jev priority', 'jev next_action', 'sonnet all fields', 'sonnet priority', 'sonnet next_action'], rows)
    md('## next_action confusion (model -> label)\n')
    for b in ('jev', 'sonnet'):
        cnt = collections.Counter((d2[b][i]['output']['next_action'], deals[i]['intended_label']['next_action']) for i in deals)
        md(f'- {b}: ' + ', '.join(f'{a}->{l}: {n}' for (a, l), n in sorted(cnt.items()) if a != l))
    md('## priority confusion (model -> label)\n')
    for b in ('jev', 'sonnet'):
        cnt = collections.Counter((str(d2[b][i]['output']['priority']), str(deals[i]['intended_label']['priority'])) for i in deals)
        md(f'- {b}: ' + ', '.join(f'{a}->{l}: {n}' for (a, l), n in sorted(cnt.items()) if a != l))
    md('')
    md('## emails routed by the default rule on each field, and whether Sonnet fixed them\n')
    for f in ('next_action', 'priority'):
        rows = []
        for i in deals:
            pr = d2['jev'][i]['probabilities'][f]
            if uncertain(pr):
                top = sorted(pr.items(), key=lambda x: -x[1])[:2]
                rows.append([i, deals[i]['brief']['kind'], ', '.join(f'{k} {v:.2f}' for k, v in top), d2['sonnet'][i]['output'][f], deals[i]['intended_label'][f]])
        md(f'### {f}: {len(rows)} routed\n')
        table(['email', 'kind', "Jev's top 2", 'sonnet', 'label'], rows)
    md('## notes: length check (schema asked for two sentences)\n')
    lens = [len(n['output']['note']) for n in notes.values()]
    sents = [n['output']['note'].count('. ') + 1 for n in notes.values()]
    md(f'- {len(notes)} notes; characters min/median/max {min(lens)}/{int(statistics.median(lens))}/{max(lens)}; approx sentences median {statistics.median(sents)}; output tokens mean {statistics.mean(n["output_tokens"] for n in notes.values()):.0f}; cost per note {cost(list(notes.values()), "sonnet_thinking") / len(notes):.4f}')
    md(f'- tokens per email: jev in {statistics.mean(r["input_tokens"] for r in d2["jev"].values()):.0f}; sonnet in {statistics.mean(r["input_tokens"] for r in d2["sonnet"].values()):.0f}, out {statistics.mean(r["output_tokens"] for r in d2["sonnet"].values()):.0f}, cache read {statistics.mean(r["cache_read_tokens"] for r in d2["sonnet"].values()):.0f}, cache write {statistics.mean(r["cache_write_tokens"] for r in d2["sonnet"].values()):.0f}')
    md(f'- generation: {sum(g["emails"] for g in gen.values())} emails in {len(gen)} batches, total latency {sum(g["latency_ms"] for g in gen.values()) / 1000:.0f} s, cost {cost(list(gen.values()), "gpt-6.1-sol"):.4f}')
    md(f'- jev priority distribution: {dict(collections.Counter(str(r["output"]["priority"]) for r in d2["jev"].values()))}; label: {dict(collections.Counter(str(d["intended_label"]["priority"]) for d in deals.values()))}; sonnet: {dict(collections.Counter(str(r["output"]["priority"]) for r in d2["sonnet"].values()))}')
    md(f'- notes threshold: jev priority >= 2 for {sum(int(r["output"]["priority"]) >= 2 for r in d2["jev"].values())} emails; label >= 2 for {sum(int(d["intended_label"]["priority"]) >= 2 for d in deals.values())}')
    md('')


def report(results: dict[str, dict[str, dict]]) -> None:
    notes = results.pop('notes', {})  # partner notes, not triage results
    generation = results.pop('generation', {})  # the generator's usage, one row per batch
    deals = load(DEALS_PATH)
    labels = {k: {'label': v['intended_label']} for k, v in deals.items()}
    generator = next((d['generator']['requested'] for d in deals.values() if d.get('generator')), '')
    provider = PROVIDERS.get(generator.split(':')[0], generator or 'generator')
    agree = f'vs {provider}-generated labels'
    scoreboard(DEMO, results, labels, title=f'dealflow: {len(deals)} emails, labels written by {generator or "the generator"}',
               agree=agree)
    console.print(SMALL_SAMPLE.format(n=len(deals), pct=100 / max(len(deals), 1)))
    if 'jev' in results and 'sonnet' in results:
        routing(results['jev'], results['sonnet'], labels, agree)
    if notes:
        console.print(f'partner notes written for {len(notes)} of {len(deals)} emails (Jev priority this week or today)')
    costs(results, notes, generation, generator)
    if 'jev' in results and 'sonnet' in results and notes and generation:
        tables(results, notes, generation, deals)
