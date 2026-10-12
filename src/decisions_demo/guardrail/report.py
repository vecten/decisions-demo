"""Guardrail report: scoreboard vs reference labels, disagreement tables, routing fit for Jev and Luna, then the
Markdown tables report.md uses (confusions, calibration of P(destructive), Jev vs Luna, literal readings)."""

from __future__ import annotations

import collections
import statistics

from rich.table import Table

from ..core.report import console, cost, ece, load, md, pct, scoreboard, table
from .demo import DEMO, COMMANDS_PATH, LABELS_PATH

FIELDS = ('action', 'destructive', 'outside_directory', 'misleading')


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
    # Every backend scored on the same commands: those each decision model answered and the labels cover.
    ids = set(labels).intersection(*({i for i, r in results[b].items() if 'error' not in r} for b in backends))
    t = Table(title=f'routing fit on "{field}": {" / ".join(backends)} outside band, sonnet inside ({len(ids)} commands)')
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
            rows = [r for r in results[b].values() if r['id'] in ids]
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


def p_yes(r: dict, f: str) -> float:
    """A decision model's P(yes) for a bool: confidence is |P-0.5|*2."""
    c = r['confidence'][f]
    return 0.5 + c / 2 * (1 if r['output'][f] else -1)


def scoreboard_detail(g: dict[str, dict[str, dict]], ok: dict[str, dict[str, dict]], labels: dict[str, dict]) -> None:
    md('## scoreboard with per 100 / per 1000 and tokens\n')
    rows = []
    for b in ('jev', 'luna', 'luna_fallback', 'sonnet', 'sonnet_no_thinking'):
        rs = list(ok[b].values())
        lat = sorted(r['latency_ms'] for r in rs)
        c = cost(rs, b)
        agree = [pct(sum(r['output'][f] == labels[r['id']]['label'][f] for r in rs), len(rs)) for f in FIELDS]
        rows.append([b, len(rs), *agree, f'{statistics.median(lat):.0f}', f'{lat[int(len(lat) * .95) - 1]:.0f}',
                     f'{statistics.mean(r["input_tokens"] for r in rs):.0f}', f'{statistics.mean(r["output_tokens"] for r in rs):.0f}',
                     f'{statistics.mean(r.get("cache_read_tokens", 0) for r in rs):.0f}', f'{statistics.mean(r.get("cache_write_tokens", 0) for r in rs):.0f}',
                     f'{c:.4f}', f'{c / len(rs) * 100:.4f}', f'{c / len(rs) * 1000:.3f}', len(g[b]) - len(rs)])
    table(['backend', 'n ok', *FIELDS, 'p50 ms', 'p95 ms', 'in tok/cmd', 'out tok/cmd', 'cache read/cmd', 'cache write/cmd', 'USD total', 'USD/100', 'USD/1000', 'errors'], rows)

    md('## label distribution\n')
    for f in FIELDS:
        md(f'- {f}: {dict(collections.Counter(str(labels[i]["label"][f]) for i in labels))}')
    md('')

    md('## per-field confusion vs labels (model answer -> label), decision models and sonnet\n')
    for f in FIELDS:
        rows = []
        for b in ('jev', 'luna', 'sonnet', 'sonnet_no_thinking', 'luna_fallback'):
            cnt = collections.Counter((str(r['output'][f]), str(labels[r['id']]['label'][f])) for r in ok[b].values())
            rows.append([b, ', '.join(f'{a}->{l}: {n}' for (a, l), n in sorted(cnt.items()))])
        md(f'### {f}\n')
        table(['backend', 'model answer -> label: count'], rows)


def calibration(ok: dict[str, dict[str, dict]], labels: dict[str, dict], commands: dict[str, dict], ids: list[str]) -> None:
    md('## calibration of P(destructive=yes): Jev and Luna, vs reference labels (1 of 100 is destructive)\n')
    bins = [(0, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.01)]
    rows = []
    for lo, hi in bins:
        cells = [f'{lo:.1f}-{min(hi, 1):.1f}']
        for b in ('jev', 'luna'):
            sel = [i for i in ids if lo <= p_yes(ok[b][i], 'destructive') < hi]
            yes = sum(labels[i]['label']['destructive'] for i in sel)
            cells += [len(sel), f'{yes}', pct(yes, len(sel)), f'{statistics.mean(p_yes(ok[b][i], "destructive") for i in sel):.2f}' if sel else '–']
        rows.append(cells)
    table(['P(yes) bucket', 'jev n', 'jev labelled yes', 'jev observed rate', 'jev mean P', 'luna n', 'luna labelled yes', 'luna observed rate', 'luna mean P'], rows)
    for b in ('jev', 'luna'):
        confs = [p_yes(ok[b][i], 'destructive') for i in ids]
        right = [bool(labels[i]['label']['destructive']) for i in ids]
        # ECE on the yes-probability (reliability of P(yes) against the yes event)
        md(f'- {b}: ECE of P(yes) vs the destructive label {ece(confs, right):.3f}; '
           f'ECE of the top-class confidence vs correctness {ece([max(c, 1 - c) for c in confs], [(c > 0.5) == r for c, r in zip(confs, right)]):.3f}; '
           f'mean P(yes) {statistics.mean(confs):.3f}; commands with P(yes) > 0.5: {sum(c > .5 for c in confs)}; '
           f'the one labelled destructive got P(yes) {[round(c, 2) for c, r in zip(confs, right) if r]}')
        pd = [ok[b][i]['output']['p_destructive'] for i in ids]
        diffs = [abs(a - c) for a, c in zip(pd, confs)]
        md(f'- {b}: p_destructive (float field asked as P(yes)) vs the bool\'s P(yes): mean |diff| {statistics.mean(diffs):.3f}, max {max(diffs):.2f}')
    md('')
    md('### the destructive command and what each model said\n')
    for i in ids:
        if labels[i]['label']['destructive']:
            md(f'command: `{commands[i]["command"]}`')
            md(f'judge: {labels[i]["label"]["explanation"]}')
            for b in ('jev', 'luna', 'sonnet', 'sonnet_no_thinking', 'luna_fallback'):
                r = ok[b].get(i)
                if r:
                    md(f'- {b}: {r["output"]}, confidence {r.get("confidence")}' + (f', P(yes)={p_yes(r, "destructive"):.2f}' if r.get('confidence') else ''))
    md('')
    md('### commands where either decision model has P(destructive) in 0.2-0.8\n')
    rows = []
    for i in ids:
        pj, pl = p_yes(ok['jev'][i], 'destructive'), p_yes(ok['luna'][i], 'destructive')
        if 0.2 <= pj <= 0.8 or 0.2 <= pl <= 0.8:
            s = ok['sonnet'].get(i)
            rows.append([commands[i]['command'][:160], f'{pj:.2f}', f'{pl:.2f}', s['output']['destructive'] if s else '–', labels[i]['label']['destructive']])
    table(['command', 'jev P(yes)', 'luna P(yes)', 'sonnet', 'label'], rows)


def jev_vs_luna(ok: dict[str, dict[str, dict]], labels: dict[str, dict], commands: dict[str, dict], ids: list[str]) -> None:
    md('## Jev vs Luna agreement per field, and disagreements on action\n')
    md('- ' + ', '.join(f'{f} {pct(sum(ok["jev"][i]["output"][f] == ok["luna"][i]["output"][f] for i in ids), len(ids))}' for f in FIELDS))
    md(f'- all four fields identical: {pct(sum(all(ok["jev"][i]["output"][f] == ok["luna"][i]["output"][f] for f in FIELDS) for i in ids), len(ids))}')
    md(f'- Jev vs Sonnet all four identical: {pct(sum(i in ok["sonnet"] and all(ok["jev"][i]["output"][f] == ok["sonnet"][i]["output"][f] for f in FIELDS) for i in ids), len(ids))}')
    md('')
    rows = []
    for i in ids:
        a, b = ok['jev'][i], ok['luna'][i]
        if a['output']['action'] != b['output']['action']:
            s = ok['sonnet'].get(i)
            rows.append([commands[i]['command'], a['output']['action'], f'{a["confidence"]["action"]:.2f}', b['output']['action'], f'{b["confidence"]["action"]:.2f}',
                         s['output']['action'] if s else '–', labels[i]['label']['action'], labels[i]['label']['explanation']])
    table(['command', 'jev', 'jev conf', 'luna', 'luna conf', 'sonnet', 'label', 'judge explanation'], rows)
    md('### Jev vs Luna action: who was right\n')
    for b in ('jev', 'luna'):
        md(f'- {b} right on the {len(rows)} disagreed: {sum(ok[b][i]["output"]["action"] == labels[i]["label"]["action"] for i in ids if ok["jev"][i]["output"]["action"] != ok["luna"][i]["output"]["action"])}')
    md('')

    for a, b in (('jev', 'sonnet'), ('luna', 'sonnet')):
        md(f'## full disagreement table on action: {a} vs {b}\n')
        rows = []
        for i in ok[a]:
            rb = ok[b].get(i)
            if rb and ok[a][i]['output']['action'] != rb['output']['action']:
                lab = labels[i]['label']
                rows.append([commands[i]['command'], ok[a][i]['output']['action'], f'{ok[a][i]["confidence"]["action"]:.2f}', rb['output']['action'], lab['action'], lab['explanation']])
        table(['command', a, f'{a} conf', b, 'reference', 'judge explanation'], rows)
        md(f'- {len(rows)} disagreements; {a} right {sum(r[1] == r[4] for r in rows)}, {b} right {sum(r[3] == r[4] for r in rows)}, neither {sum(r[1] != r[4] and r[3] != r[4] for r in rows)}\n')


def literal_readings(ok: dict[str, dict[str, dict]], labels: dict[str, dict], commands: dict[str, dict], ids: list[str]) -> None:
    md('## outside_directory: Jev true / label false examples (literal reading)\n')
    rows = []
    for i in ids:
        if ok['jev'][i]['output']['outside_directory'] and not labels[i]['label']['outside_directory']:
            rows.append([commands[i]['command'][:200], f'{p_yes(ok["jev"][i], "outside_directory"):.2f}', f'{p_yes(ok["luna"][i], "outside_directory"):.2f}',
                         ok['sonnet'][i]['output']['outside_directory'] if i in ok['sonnet'] else '–', labels[i]['label']['explanation'][:200]])
    table(['command', 'jev P(yes)', 'luna P(yes)', 'sonnet', 'judge'], rows)
    md('## misleading: Jev true / label false examples\n')
    rows = []
    for i in ids:
        if ok['jev'][i]['output']['misleading'] and not labels[i]['label']['misleading']:
            rows.append([commands[i]['command'][:160], commands[i]['description'][:100], f'{p_yes(ok["jev"][i], "misleading"):.2f}', f'{p_yes(ok["luna"][i], "misleading"):.2f}'])
    table(['command', 'agent description', 'jev P(yes)', 'luna P(yes)'], rows)


def routing_detail(ok: dict[str, dict[str, dict]], labels: dict[str, dict], ids: list[str]) -> None:
    md('## routing fit on destructive, with counts\n')
    rows = []
    for half in (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4):
        cells = [f'{0.5 - half:.2f}-{0.5 + half:.2f}']
        for b in ('jev', 'luna'):
            routed = correct = alone = 0
            for i in ids:
                r = ok[b][i]
                truth = labels[i]['label']['destructive']
                alone += r['output']['destructive'] == truth
                if r['confidence']['destructive'] < 2 * half:
                    routed += 1
                    s = ok['sonnet'].get(i)
                    correct += bool(s and s['output']['destructive'] == truth)
                else:
                    correct += r['output']['destructive'] == truth
            cells += [routed, pct(correct, len(ids)), pct(alone, len(ids))]
        rows.append(cells)
    table(['P(yes) band', 'jev routed (of 100)', 'jev + sonnet', 'jev alone', 'luna routed', 'luna + sonnet', 'luna alone'], rows)
    md('## routing on action (choice) with the shared rule, Jev and Luna (Luna has full probabilities; Jev has only confidence here)\n')
    md('- jev guardrail rows carry confidence only (no probabilities field, run of 2026-10-02); Luna rows carry both.')
    rows = []
    for thr in (0.5, 0.6, 0.7, 0.8, 0.9):
        cells = [f'conf < {thr:.1f}']
        for b in ('jev', 'luna'):
            routed = [i for i in ids if ok[b][i]['confidence']['action'] < thr]
            right = sum((ok['sonnet'][i] if i in routed and i in ok['sonnet'] else ok[b][i])['output']['action'] == labels[i]['label']['action'] for i in ids)
            alone = sum(ok[b][i]['output']['action'] == labels[i]['label']['action'] for i in ids)
            cells += [len(routed), pct(right, len(ids)), pct(alone, len(ids))]
        rows.append(cells)
    table(['route when', 'jev routed', 'jev + sonnet action', 'jev alone', 'luna routed', 'luna + sonnet action', 'luna alone'], rows)
    md(f'- sonnet alone on action: {pct(sum(ok["sonnet"][i]["output"]["action"] == labels[i]["label"]["action"] for i in ids if i in ok["sonnet"]), sum(i in ok["sonnet"] for i in ids))}\n')


def confidences_and_errors(g: dict[str, dict[str, dict]], ok: dict[str, dict[str, dict]], labels: dict[str, dict], commands: dict[str, dict], ids: list[str]) -> None:
    md('## confidence distribution per field (decision models)\n')
    rows = []
    for b in ('jev', 'luna'):
        for f in FIELDS:
            cs = sorted(ok[b][i]['confidence'][f] for i in ids)
            rows.append([b, f, f'{statistics.mean(cs):.2f}', f'{statistics.median(cs):.2f}', f'{cs[int(len(cs) * .1)]:.2f}', f'{cs[0]:.2f}', sum(c < 0.2 for c in cs), sum(c < 0.5 for c in cs)])
    table(['model', 'field', 'mean conf', 'median', 'p10', 'min', 'n < 0.2', 'n < 0.5'], rows)
    md('## Luna guardrail probabilities rounding check\n')
    vals = {v for r in ok['luna'].values() for v in r['probabilities']['action'].values()}
    md(f'- distinct action probabilities Luna returned: {len(vals)}; all multiples of 0.01: {all(abs(v * 100 - round(v * 100)) < 1e-9 for v in vals)}')
    vals = {v for r in ok['jev'].values() for v in r['confidence'].values()}
    md(f'- distinct Jev confidences: {len(vals)}; sample {sorted(vals)[:8]}')
    md('')
    md('## sonnet errors\n')
    for b in g:
        for i, r in g[b].items():
            if 'error' in r:
                md(f'- {b} {i}: {r["error"][:300]} | command: `{commands[i]["command"][:120]}`')
    md('')
    md('## Jev vs Sonnet thinking off/on: where sonnet_no_thinking beats sonnet on action\n')
    rows = []
    for i in ids:
        s, n = ok['sonnet'].get(i), ok['sonnet_no_thinking'].get(i)
        if s and n and s['output']['action'] != n['output']['action']:
            rows.append([commands[i]['command'][:140], s['output']['action'], n['output']['action'], labels[i]['label']['action']])
    table(['command', 'sonnet (thinking)', 'sonnet_no_thinking', 'label'], rows)
    md('## thinking-on output tokens vs off (sonnet)\n')
    for b in ('sonnet', 'sonnet_no_thinking'):
        rs = list(ok[b].values())
        md(f'- {b}: mean in {statistics.mean(r["input_tokens"] for r in rs):.0f}, out {statistics.mean(r["output_tokens"] for r in rs):.0f}, cache write {statistics.mean(r["cache_write_tokens"] for r in rs):.0f}, cache read {statistics.mean(r["cache_read_tokens"] for r in rs):.0f}; rationale present in {sum(bool(r.get("rationale")) for r in rs)} rows; p_destructive mean {statistics.mean(r["output"]["p_destructive"] for r in rs):.3f}')
    md('')


def tables(g: dict[str, dict[str, dict]], labels: dict[str, dict], commands: dict[str, dict]) -> None:
    """The Markdown tables report.md uses beyond the scoreboard. They quote commands, so they stay local like the data."""
    ok = {b: {i: r for i, r in rows.items() if 'error' not in r} for b, rows in g.items()}
    ids = [i for i in labels if i in ok['jev'] and i in ok['luna']]
    md('# DEMO 1 EXTRA TABLES\n')
    scoreboard_detail(g, ok, labels)
    calibration(ok, labels, commands, ids)
    jev_vs_luna(ok, labels, commands, ids)
    literal_readings(ok, labels, commands, ids)
    routing_detail(ok, labels, ids)
    confidences_and_errors(g, ok, labels, commands, ids)


def report(results: dict[str, dict[str, dict]]) -> None:
    # Demo 1's data comes from your own sessions and is never committed, so a fresh clone has none.
    missing = [str(p) for p in (COMMANDS_PATH, LABELS_PATH) if not p.exists()] + ([] if results else ['data/results/guardrail.*.jsonl'])
    if missing:
        console.print(f'No guardrail data ({", ".join(missing)} absent): demo 1 is mined from your own Claude Code sessions and never committed; '
                      'docs/guardrail.md says how to make it.', soft_wrap=True, markup=False)
        return
    labels = load(LABELS_PATH)
    commands = load(COMMANDS_PATH)
    scoreboard(DEMO, results, labels)
    disagreements(results, labels, commands)
    disagreements(results, labels, commands, a='luna')
    routing_fit(results, labels)
    tables(results, labels, commands)
