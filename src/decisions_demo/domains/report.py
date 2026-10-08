"""Demo 3 report: two-level domain classification against YC's labels and the adjudicated reference.

Reads data/company_sample.jsonl (names and YC labels only, so it runs without `domains fetch`),
data/results/domains.*.jsonl and data/domain_adjudicated.jsonl; never calls a model.

Two references:
  YC          YC's own industry -> subindustry. Noisy: on disputed companies Opus often disagrees with it.
  reference   Opus's primary label (or your hand correction) where a company was adjudicated, YC elsewhere.
              Every company that wasn't adjudicated had all main runs agreeing with YC. Opus is Claude, so
              Sonnet is likely flattered against this reference; it is "Claude's reading", not ground truth.

Two decision models, Jev and Luna (OpenAI's Decisions API), are asked the same four question shapes. Every
section that reads their probabilities (routing, shapes, calibration, Nouls, duality, stability, latency)
shows Luna next to Jev; the routed-companies list, the confusion tables and the heatmap stay Jev's.

Sections: scoreboards, level 2 given level 1, per domain and per subindustry (n >= 10), routing Jev or
Luna -> Sonnet, routed companies, confusion pairs and matrix, flat vs fan-out, calibration, Nouls, duality,
stability, latency, and a Nouls heatmap saved as images.

The B2B Noul is reported apart from the five sector Nouls (see B2B_NOTE): it asks whether a company sells to
businesses, which is a different question from YC's B2B label, so it is shown as a fire rate and kept out of
precision/recall, Jaccard and duality.
"""

from __future__ import annotations

import collections
import re
import statistics
from dataclasses import replace
from pathlib import Path

from rich.table import Table

from ..core.report import ROUTE, ROUTE_SETTINGS, console, cost, load, route_label, scoreboard, uncertain
from ..core.runner import concurrency
from .demo import DEMO, ADJUDICATED_PATH, SAMPLE_PATH
from .runs import CHOICE_RUNS, DECISION_MODELS, MAIN_RUNS, pick, shape
from .schemas import OTHER, Schemas, build, field_name, load_definitions

FIGURES = Path('data/figures')
SHORT = {'B2B': 'B2B', 'Consumer': 'Consumer', 'Fintech': 'Fintech', 'Healthcare': 'Health',
         'Industrials': 'Industrials', 'Real Estate and Construction': 'RE & Constr.', OTHER: 'Other'}

Pair = tuple[str, str | None]

B2B = 'B2B'
B2B_NOTE = (
    "The B2B Noul is reported separately. It asks whether the company sells to businesses, and most fintech, "
    "health and industrial companies do. YC's B2B label is a residual: business software that none of the five "
    "sector labels covers. Precision and recall against that residual would measure the gap between the two "
    "definitions, not Jev, so B2B is shown only as the share of companies it fires for, and the sector tables, "
    "Jaccard and duality use the five sector Nouls."
)


def _pct(hits: int, n: int) -> str:
    return f'{hits / n:.0%}' if n else '–'


def _name(p: Pair) -> str:
    return p[1] or p[0]


def _brief(text: str, limit: int = 240) -> str:
    """The first sentences of a rationale, up to about limit characters, markdown stripped."""
    words = ' '.join(text.replace('**', '').replace('- ', '').split())
    sentences = re.split(r'(?<=[.!?])\s+', words)
    out = sentences[0]
    for sentence in sentences[1:]:
        if len(out) + len(sentence) + 1 > limit:
            break
        out += ' ' + sentence
    return out if len(out) <= limit + 60 else out[:limit].rsplit(' ', 1)[0] + ' …'


class Data:
    """Everything the sections share, loaded once."""

    def __init__(self, results: dict[str, dict[str, dict]]):
        self.s: Schemas = build(load_definitions())
        self.companies = load(SAMPLE_PATH)  # names and labels only, so the report runs without `domains fetch`
        self.results = results
        self.ok = {run: {i: r for i, r in rows.items() if 'error' not in r} for run, rows in results.items()}
        self.picks: dict[str, dict[str, Pair]] = {
            run: {i: pick(run, r['output'], self.s) for i, r in rows.items()}
            for run, rows in self.ok.items() if shape(run) != 'nouls'
        }
        self.yc: dict[str, Pair] = {i: (c['label'], c['sub_label']) for i, c in self.companies.items()}
        self.adjudicated = {i: r for i, r in load(ADJUDICATED_PATH).items() if 'error' not in r}
        self.ref = dict(self.yc)
        self.secondary: dict[str, Pair | None] = {}
        for i, r in self.adjudicated.items():
            self.ref[i] = pick('opus', r['label'], self.s)
            sec = r['label'].get('secondary')
            self.secondary[i] = pick('opus', {'primary': sec}, self.s) if sec else None

    def runs(self, names=MAIN_RUNS) -> list[str]:
        return [n for n in names if n in self.picks]

    def models(self, shape_: str) -> list[str]:
        """The decision models with a run of this shape, e.g. ['jev', 'luna'] for 'flat'."""
        return [m for m in DECISION_MODELS if f'{m}_{shape_}' in self.ok]

    def top(self, i: str, k: int = 3, model: str = 'jev') -> list[tuple[str, float]]:
        """A decision model's flat run: its top-k subindustries for a company."""
        probs = self.ok[f'{model}_flat'][i]['probabilities']['subindustry']
        return sorted(probs.items(), key=lambda x: -x[1])[:k]

    def uncertain(self, i: str, min_top1: float = ROUTE[0], min_margin: float = ROUTE[1], model: str = 'jev') -> bool:
        """The shared routing rule (core.report.ROUTE) on a decision model's flat subindustry distribution."""
        return uncertain(self.ok[f'{model}_flat'][i]['probabilities']['subindustry'], min_top1, min_margin)

    def noul_p(self, i: str, model: str = 'jev') -> dict[str, float]:
        """P(yes) per domain. Decision models report a bool's confidence as its distance from 0.5, scaled to 0-1."""
        r = self.ok[f'{model}_nouls'][i]
        return {d: 0.5 + r['confidence'][field_name(d)] / 2 * (1 if r['output'][field_name(d)] else -1) for d in self.s.domains}


def scoreboards(d: Data) -> None:
    demo = replace(DEMO, compare_fields=('domain', 'subindustry'))
    norm = {run: {i: {**d.ok[run][i], 'output': {'domain': p[0], 'subindustry': p[1]}} for i, p in d.picks[run].items()}
            for run in d.runs()}
    for run in norm:  # failed rows still count as errors on the board
        norm[run].update({i: r for i, r in d.results[run].items() if 'error' in r})
    as_labels = lambda ref: {i: {'label': {'domain': p[0], 'subindustry': p[1]}} for i, p in ref.items()}
    n = len(d.companies)
    scoreboard(demo, norm, as_labels(d.yc), title=f'domains: agreement with YC, {n} companies (sonnet_thinking: first 100)')
    scoreboard(demo, norm, as_labels(d.ref), title=f'domains: accuracy against the reference, YC corrected by Opus on {len(d.adjudicated)} disputed')

    t = Table(title='level 2 given level 1: subindustry right when the domain is right')
    t.add_column('run')
    for col in ('vs YC', 'vs reference'):
        t.add_column(col, justify='right')
    for run in d.runs():
        cells = []
        for ref in (d.yc, d.ref):
            hits = [p[1] == ref[i][1] for i, p in d.picks[run].items() if ref[i][1] and p[0] == ref[i][0]]
            cells.append(_pct(sum(hits), len(hits)))
        t.add_row(run, *cells)
    console.print(t)


def breakdown(d: Data, min_n: int = 10) -> None:
    runs = d.runs()
    t = Table(title='level 2 per domain (subindustry right, against the reference)')
    t.add_column('domain'); t.add_column('n', justify='right')
    for run in runs:
        t.add_column(run, justify='right')
    for dom in (*d.s.domains, OTHER):
        ids = [i for i in d.companies if d.ref[i][0] == dom]
        t.add_row(dom, str(len(ids)), *(_pct(sum(d.picks[r][i] == d.ref[i] for i in ids if i in d.picks[r]),
                                              sum(i in d.picks[r] for i in ids)) for r in runs))
    console.print(t)

    subs = collections.Counter(d.ref[i] for i in d.companies if d.ref[i][1])
    t = Table(title=f'level 2 per subindustry, n >= {min_n} in the reference ({sum(n >= min_n for n in subs.values())} of {len(subs)})')
    t.add_column('domain'); t.add_column('subindustry'); t.add_column('n', justify='right')
    for run in runs:
        t.add_column(run, justify='right')
    for (dom, sub), n in sorted(subs.items()):
        if n < min_n:
            continue
        ids = [i for i in d.companies if d.ref[i] == (dom, sub)]
        t.add_row(SHORT[dom], sub, str(n), *(_pct(sum(d.picks[r][i] == d.ref[i] for i in ids if i in d.picks[r]),
                                                   sum(i in d.picks[r] for i in ids)) for r in runs))
    console.print(t)


def routing(d: Data) -> None:
    """Jev flat (or Luna flat) everywhere, Sonnet where it is unsure. Accuracy at level 2 against the reference."""
    models = d.models('flat')
    if not models or 'sonnet' not in d.picks:
        return
    sonnet_cost = cost(list(d.ok['sonnet'].values()), 'sonnet') / len(d.ok['sonnet'])
    ids = {m: [i for i in d.picks[f'{m}_flat'] if i in d.picks['sonnet']] for m in models}
    unit_cost = {m: cost(list(d.ok[f'{m}_flat'].values()), m) / len(d.ok[f'{m}_flat']) for m in models}

    def evaluate(m: str, t1: float, margin: float) -> tuple[int, int, int]:
        routed = {i for i in ids[m] if d.uncertain(i, t1, margin, model=m)}
        answer = lambda i: (d.picks['sonnet'] if i in routed else d.picks[f'{m}_flat'])[i]
        return len(routed), sum(answer(i)[0] == d.ref[i][0] for i in ids[m]), sum(answer(i) == d.ref[i] for i in ids[m])

    names = ' or '.join(f'{m.capitalize()} flat' for m in models)
    t = Table(title=f'routing: {names} answers, Sonnet takes the uncertain ones ({", ".join(f"{m} {len(ids[m])}" for m in models)} companies, vs reference)')
    t.add_column('route when', justify='right')
    for m in models:
        for col in ('routed', 'L1', 'L2', 'USD/1000'):
            t.add_column(f'{m} {col}', justify='right')
    for t1, margin in ROUTE_SETTINGS:
        cells = []
        for m in models:
            k, l1, l2 = evaluate(m, t1, margin)
            n = len(ids[m])
            cells += [_pct(k, n), _pct(l1, n), _pct(l2, n), f'{1000 * (unit_cost[m] + sonnet_cost * k / n):.2f}']
        t.add_row(route_label(t1, margin), *cells)
    console.print(t)
    console.print('alone at level 2: ' + ', '.join(
        f'{run} {sum(d.picks[run][i] == d.ref[i] for i in ids[m]) / len(ids[m]):.0%}'
        for m in models for run in (f'{m}_flat', 'sonnet')))

    # Fit, as demo 1 fits its band: the best rule for a given share of companies sent to Sonnet.
    grid = [(a / 20, b / 20) for a in range(21) for b in range(11)]
    scored = {m: [(t1, margin, *evaluate(m, t1, margin)) for t1, margin in grid] for m in models}
    t = Table(title='fit: best rule per routing budget (level 2 vs reference)')
    t.add_column('budget', justify='right')
    for m in models:
        for col in ('top-1 <', 'margin <', 'routed', 'L2'):
            t.add_column(f'{m} {col}', justify='right')
    for budget in (0.1, 0.2, 0.3, 0.5):
        cells = []
        for m in models:
            n = len(ids[m])
            best = max((x for x in scored[m] if x[2] <= budget * n), key=lambda x: (x[4], -x[2]))
            cells += [f'{best[0]:.2f}', f'{best[1]:.2f}', _pct(best[2], n), _pct(best[4], n)]
        t.add_row(f'<= {budget:.0%}', *cells)
    console.print(t)


def routed_companies(d: Data, limit: int = 25) -> None:
    if 'jev_flat' not in d.picks or 'sonnet' not in d.picks:
        return
    routed = sorted((i for i in d.picks['jev_flat'] if i in d.picks['sonnet'] and d.uncertain(i)), key=lambda i: d.top(i, 1)[0][1])
    t = Table(title=f'routed at the default rule: {len(routed)} companies, least sure first (first {min(limit, len(routed))})')
    t.add_column('company', max_width=18); t.add_column('reference', max_width=20)
    t.add_column("Jev's top 3", max_width=34); t.add_column('Sonnet', max_width=20); t.add_column("Sonnet's rationale", max_width=60)
    for i in routed[:limit]:
        mark = lambda p: _name(p) + (' ✓' if p == d.ref[i] else '')
        t.add_row(d.companies[i]['name'], _name(d.ref[i]), ', '.join(f'{k} {p:.2f}' for k, p in d.top(i)),
                  mark(d.picks['sonnet'][i]), _brief(d.ok['sonnet'][i].get('rationale', '')))
    console.print(t)


def confusion(d: Data, run: str = 'jev_flat', limit: int = 15) -> None:
    if run not in d.picks:
        return
    wrong = [(d.ref[i], p, i) for i, p in d.picks[run].items() if p != d.ref[i]]
    pairs = collections.Counter((ref, p) for ref, p, _ in wrong)
    t = Table(title=f'{run} vs the reference: most common confusions ({len(wrong)} wrong at level 2)')
    t.add_column('reference'); t.add_column(run); t.add_column('n', justify='right'); t.add_column('YC said', max_width=24)
    t.add_column('examples', max_width=40)
    for (ref, p), n in pairs.most_common(limit):
        ids = [i for r, q, i in wrong if (r, q) == (ref, p)]
        yc = collections.Counter(_name(d.yc[i]) for i in ids).most_common(1)[0][0]
        t.add_row(_name(ref), _name(p), str(n), yc, ', '.join(d.companies[i]['name'] for i in ids[:3]))
    console.print(t)

    doms = (*d.s.domains, OTHER)
    t = Table(title=f'domain confusion matrix: reference (rows) vs {run} (columns)')
    t.add_column('')
    for dom in doms:
        t.add_column(SHORT[dom], justify='right')
    counts = collections.Counter((d.ref[i][0], p[0]) for i, p in d.picks[run].items())
    for a in doms:
        t.add_row(SHORT[a], *(f'[bold]{counts[a, b]}[/bold]' if a == b else (str(counts[a, b]) if counts[a, b] else '·') for b in doms))
    console.print(t)


def shapes(d: Data) -> None:
    """Do the question shapes agree with each other on the same company? Within each decision model, then Jev
    against Luna on the same shape."""
    pairs = [(f'{m}_{a}', f'{m}_{b}') for m in DECISION_MODELS for a, b in (('flat', 'fanout'), ('flat', 'sequential'), ('sequential', 'fanout'))]
    pairs += [(f'jev_{x}', f'luna_{x}') for x in ('flat', 'sequential', 'fanout')]
    t = Table(title='question shapes against each other (on the companies both answered)')
    t.add_column('pair'); t.add_column('n', justify='right'); t.add_column('same domain', justify='right')
    t.add_column('same subindustry', justify='right'); t.add_column('same sub, when same domain', justify='right')
    for a, b in pairs:
        if a not in d.picks or b not in d.picks:
            continue
        ids = [i for i in d.picks[a] if i in d.picks[b]]
        same_dom = [i for i in ids if d.picks[a][i][0] == d.picks[b][i][0]]
        t.add_row(f'{a} vs {b}', str(len(ids)), _pct(len(same_dom), len(ids)), _pct(sum(d.picks[a][i] == d.picks[b][i] for i in ids), len(ids)),
                  _pct(sum(d.picks[a][i] == d.picks[b][i] for i in same_dom), len(same_dom)))
    console.print(t)


def _confidences(d: Data, run: str) -> dict[str, tuple[list[float], list[bool]]]:
    """A decision model's probability for what it picked, at each level, and whether the pick was right (vs reference).

    Luna rounds every probability to 0.01, so a sum of them can pass 1 by a rounding step; it is capped there.
    """
    out = {'L1': ([], []), 'L2': ([], [])}
    for i, r in d.ok[run].items():
        probs, p = r['probabilities'], d.picks[run][i]
        if shape(run) == 'flat':
            sub = probs['subindustry']
            p2 = sub[p[1] or OTHER]
            p1 = sub[OTHER] if p[0] == OTHER else sum(v for k, v in sub.items() if d.s.parent.get(k) == p[0])
        else:
            p1 = probs['domain'][p[0]]
            sub_field = 'subindustry' if shape(run) == 'sequential' else f'{field_name(p[0])}_subindustry'
            p2 = p1 * probs[sub_field][p[1]] if p[1] else p1  # joint: P(domain) x P(sub | domain)
        for level, conf, right in (('L1', p1, p[0] == d.ref[i][0]), ('L2', p2, p == d.ref[i])):
            out[level][0].append(min(conf, 1.0))
            out[level][1].append(right)
    return out


def _ece(confs: list[float], right: list[bool], bins: int = 10) -> float:
    """Expected calibration error: |accuracy - confidence| per 0.1 bucket, weighted by bucket size."""
    total = 0.0
    for b in range(bins):
        idx = [k for k, c in enumerate(confs) if b / bins <= c < (b + 1) / bins or (b == bins - 1 and c == 1.0)]
        if idx:
            total += len(idx) * abs(sum(right[k] for k in idx) / len(idx) - statistics.mean(confs[k] for k in idx))
    return total / len(confs)


def calibration(d: Data) -> None:
    runs = [run for m in DECISION_MODELS for run in d.runs(CHOICE_RUNS[m])]
    if not runs:
        return
    cal = {run: _confidences(d, run) for run in runs}
    flats = [f'{m}_flat' for m in d.models('flat')]
    t = Table(title=f"calibration of the decision models' choice: accuracy per probability bucket ({', '.join(flats)}, vs reference)")
    t.add_column('probability', justify='right')
    for run in flats:
        for col in ('n L1', 'acc L1', 'n L2', 'acc L2'):
            t.add_column(f'{run.split("_")[0]} {col}', justify='right')
    for b in range(10):
        row = [f'{b / 10:.1f}-{(b + 1) / 10:.1f}']
        for run in flats:
            for level in ('L1', 'L2'):
                confs, right = cal[run][level]
                idx = [k for k, c in enumerate(confs) if b / 10 <= c < (b + 1) / 10 or (b == 9 and c == 1.0)]
                row += [str(len(idx)), _pct(sum(right[k] for k in idx), len(idx))]
        t.add_row(*row)
    console.print(t)
    t = Table(title='expected calibration error (lower is better; L2 for sequential and fan-out is P(domain) x P(sub))')
    t.add_column('run'); t.add_column('ECE L1', justify='right'); t.add_column('ECE L2', justify='right')
    for run in runs:
        t.add_row(run, *(f'{_ece(*cal[run][level]):.3f}' for level in ('L1', 'L2')))
    console.print(t)


def sectors(d: Data) -> tuple[str, ...]:
    return tuple(dom for dom in d.s.domains if dom != B2B)


def nouls(d: Data) -> None:
    models = d.models('nouls')
    if not models:
        return
    ids = sorted(set.intersection(*(set(d.ok[f'{m}_nouls']) for m in models)))
    noul = {m: {i: d.noul_p(i, m) for i in ids} for m in models}
    console.print(B2B_NOTE)

    t = Table(title='B2B Noul ("sells to businesses"): share of companies it fires for, by YC domain')
    t.add_column('YC domain'); t.add_column('n', justify='right')
    for m in models:
        t.add_column(f'{m} B2B yes', justify='right')
    for dom in (*d.s.domains, OTHER):
        group = [i for i in ids if d.yc[i][0] == dom]
        t.add_row(dom, str(len(group)), *(_pct(sum(noul[m][i][B2B] > 0.5 for i in group), len(group)) for m in models))
    t.add_row('all', str(len(ids)), *(_pct(sum(noul[m][i][B2B] > 0.5 for i in ids), len(ids)) for m in models))
    console.print(t)

    secs = set(sectors(d))
    pred = {m: {i: {dom for dom, p in noul[m][i].items() if p > 0.5} & secs for i in ids} for m in models}
    sets = {
        'YC': {i: {d.yc[i][0]} & secs for i in ids},
        'reference': {i: ({d.ref[i][0]} | ({d.secondary[i][0]} if d.secondary.get(i) else set())) & secs for i in ids},
    }
    t = Table(title="sector Nouls: precision and recall per domain (reference set adds Opus's secondary domain where adjudicated)")
    t.add_column('domain'); t.add_column('model'); t.add_column('yes', justify='right')
    for name in sets:
        t.add_column(f'n {name}', justify='right'); t.add_column(f'precision {name}', justify='right'); t.add_column(f'recall {name}', justify='right')
    for dom in sectors(d):
        for m in models:
            yes = [i for i in ids if dom in pred[m][i]]
            row = [dom if m == models[0] else '', m, str(len(yes))]
            for truth in sets.values():
                pos = [i for i in ids if dom in truth[i]]
                row += [str(len(pos)), _pct(sum(dom in truth[i] for i in yes), len(yes)), _pct(sum(dom in pred[m][i] for i in pos), len(pos))]
            t.add_row(*row, end_section=m == models[-1])
    console.print(t)
    jacc = lambda a, b: 1.0 if not a and not b else len(a & b) / len(a | b)
    for m in models:
        console.print(f'{m}: mean per-company Jaccard over the five sector Nouls (YC B2B and Other companies have an empty sector set): '
                      + ', '.join(f'{name} {statistics.mean(jacc(pred[m][i], truth[i]) for i in ids):.2f}' for name, truth in sets.items())
                      + f'; companies with 2+ sector Nouls yes: {sum(len(p) >= 2 for p in pred[m].values())}, with none: {sum(not p for p in pred[m].values())}')


def duality(d: Data, limit: int = 12) -> None:
    """Is a split Choice a confused model, or a company that is two things? The Nouls tell them apart.

    Each decision model's own Nouls against its own flat Choice; the company lists below are Jev's.
    """
    models = [m for m in d.models('nouls') if m in d.models('flat')]
    if 'jev' not in models:
        return
    secs = sectors(d)
    ids, dual, unsure = {}, {}, {}
    for m in models:
        ids[m] = [i for i in d.ok[f'{m}_flat'] if i in d.ok[f'{m}_nouls']]
        dual[m] = {i: sum(d.noul_p(i, m)[dom] > 0.5 for dom in secs) >= 2 for i in ids[m]}
        unsure[m] = {i: d.uncertain(i, model=m) for i in ids[m]}
    t = Table(title=f'duality: 2+ sector Nouls yes (B2B excluded) vs Choice uncertain (top-1 < {ROUTE[0]} or margin < {ROUTE[1]})')
    t.add_column('')
    for m in models:
        t.add_column(f'{m}: Choice uncertain', justify='right'); t.add_column(f'{m}: Choice sure', justify='right')
    for label, flag in (('2+ sector Nouls yes (dual)', True), ('0-1 sector Nouls yes', False)):
        t.add_row(label, *(str(sum(dual[m][i] == flag and unsure[m][i] == u for i in ids[m])) for m in models for u in (True, False)))
    console.print(t)
    console.print('flagged "this company is two things" (dual and uncertain): '
                  + ', '.join(f'{m} {sum(dual[m][i] and unsure[m][i] for i in ids[m])}' for m in models))
    ids, dual, unsure = ids['jev'], dual['jev'], unsure['jev']
    for title, sel in (('dual but the Choice is sure', lambda i: dual[i] and not unsure[i]),
                       ('Choice uncertain but only 0-1 sector Nouls: confusion, not two things', lambda i: unsure[i] and not dual[i])):
        rows = [i for i in ids if sel(i)]
        t = Table(title=f'Jev, {title}: {len(rows)} companies (first {min(limit, len(rows))})')
        t.add_column('company', max_width=20); t.add_column('reference', max_width=24); t.add_column('sector Nouls yes', max_width=36)
        t.add_column("Jev's top 2", max_width=40)
        for i in rows[:limit]:
            yes = ', '.join(f'{SHORT[dom]} {p:.2f}' for dom, p in sorted(d.noul_p(i).items(), key=lambda x: -x[1]) if p > 0.5 and dom in secs)
            t.add_row(d.companies[i]['name'], _name(d.ref[i]), yes or '–', ', '.join(f'{k} {p:.2f}' for k, p in d.top(i, 2)))
        console.print(t)


def stability(d: Data) -> None:
    t = Table(title='stability: the same run twice')
    for col in ('run', 'identical output', 'different label', 'mean |Δ confidence|', 'max |Δ confidence|'):
        t.add_column(col, justify='right' if col != 'run' else 'left')
    for run in (f'{m}_{x}' for m in DECISION_MODELS for x in ('flat', 'sequential', 'fanout', 'nouls')):
        a, b = d.ok.get(run), d.ok.get(f'{run}_rerun')
        if not a or not b:
            continue
        ids = [i for i in a if i in b]
        diffs = [abs(a[i]['confidence'][k] - b[i]['confidence'][k]) for i in ids for k in a[i]['confidence'] if k in b[i]['confidence']]
        flips = sum(d.picks[run][i] != d.picks[f'{run}_rerun'][i] for i in ids) if run in d.picks else None
        t.add_row(run, _pct(sum(a[i]['output'] == b[i]['output'] for i in ids), len(ids)),
                  _pct(flips, len(ids)) if flips is not None else '–', f'{statistics.mean(diffs):.3f}', f'{max(diffs):.2f}')
    console.print(t)


def latency(d: Data) -> None:
    t = Table(title='latency as measured: wall clock per company, with this many requests in flight')
    for col in ('run', 'in flight', 'calls per company', 'p50 ms', 'p95 ms', 'USD per 1000 companies'):
        t.add_column(col, justify='right' if col != 'run' else 'left')
    for run in (*MAIN_RUNS, *(f'{m}_nouls' for m in DECISION_MODELS)):
        rows = list(d.ok.get(run, {}).values())
        if not rows:
            continue
        lat = sorted(r['latency_ms'] for r in rows)
        calls = statistics.mean(2 if r['output'].get('subindustry') else 1 for r in rows) if shape(run) == 'sequential' else 1
        t.add_row(run, str(concurrency(run)), f'{calls:.2f}', f'{statistics.median(lat):.0f}', f'{lat[int(len(lat) * 0.95) - 1]:.0f}',
                  f'{1000 * cost(rows, run) / len(rows):.2f}')
    console.print(t)


def heatmap(d: Data) -> list[Path]:
    """Companies (rows, grouped by YC label) x the six Nouls, P(yes). One PNG per theme (light, dark)."""
    if 'jev_nouls' not in d.ok:
        return []
    import matplotlib

    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    order = (*d.s.domains, OTHER)
    ids = sorted(d.ok['jev_nouls'], key=lambda i: (order.index(d.yc[i][0]), d.yc[i][1] or '', d.companies[i]['name'].lower()))
    secs = sectors(d)
    # B2B, an empty spacer column (drawn as the surface), then the five sector Nouls.
    columns = [B2B, None, *secs]
    grid = [[d.noul_p(i)[dom] if dom else float('nan') for dom in columns] for i in ids]
    groups = [(dom, [k for k, i in enumerate(ids) if d.yc[i][0] == dom]) for dom in order]

    # Sequential blue, one hue light -> dark (reference palette); near-zero recedes into the surface.
    themes = {
        'light': {'surface': '#fcfcfb', 'text': '#0b0b0b', 'muted': '#52514e',
                  'ramp': ['#fcfcfb', '#cde2fb', '#86b6ef', '#3987e5', '#1c5cab', '#0d366b']},
        'dark': {'surface': '#1a1a19', 'text': '#ffffff', 'muted': '#c3c2b7',
                 'ramp': ['#1a1a19', '#104281', '#1c5cab', '#2a78d6', '#6da7ec', '#cde2fb']},
    }
    FIGURES.mkdir(parents=True, exist_ok=True)
    paths = []
    for theme, c in themes.items():
        fig, ax = plt.subplots(figsize=(7.5, 10), dpi=200, facecolor=c['surface'])
        ax.set_facecolor(c['surface'])
        cmap = LinearSegmentedColormap.from_list('seq', c['ramp'])
        cmap.set_bad(c['surface'])
        im = ax.imshow(grid, aspect='auto', cmap=cmap, vmin=0, vmax=1, interpolation='nearest')
        ax.set_xticks([k for k, dom in enumerate(columns) if dom],
                      ['sells to\nbusinesses' if dom == B2B else SHORT[dom].replace(' & ', ' &\n') for dom in columns if dom],
                      color=c['text'], fontsize=9)
        ax.xaxis.tick_top()
        ax.tick_params(length=0)
        ax.set_yticks([statistics.mean(rows) for _, rows in groups if rows],
                      [f'YC: {SHORT[dom]} ({len(rows)})' for dom, rows in groups if rows], color=c['text'], fontsize=9)
        for _, rows in groups[:-1]:  # 2px surface gap between label groups
            if rows:
                ax.axhline(max(rows) + 0.5, color=c['surface'], linewidth=2)
        for k in range(3, len(columns)):  # 2px surface gaps between the sector columns
            ax.axvline(k - 0.5, color=c['surface'], linewidth=2)
        ax.annotate('sector Nouls', xy=(2 + (len(secs) - 1) / 2, 1), xycoords=ax.get_xaxis_transform(), xytext=(0, 30),
                    textcoords='offset points', ha='center', va='bottom', color=c['muted'], fontsize=8)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_title("Jev's domain Nouls: does the company operate in X?\n"
                     f'{len(ids)} YC companies, grouped by YC\'s own label', color=c['text'], fontsize=11, loc='left', pad=52)
        bar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02, ticks=[0, 0.5, 1])
        bar.set_label('P(yes)', color=c['muted'], fontsize=9)
        bar.outline.set_visible(False)
        bar.ax.tick_params(colors=c['muted'], labelsize=8, length=0)
        fig.tight_layout()
        path = FIGURES / f'domains_nouls_heatmap_{theme}.png'
        fig.savefig(path, facecolor=c['surface'])
        plt.close(fig)
        paths.append(path)
    return paths


def report(results: dict[str, dict[str, dict]]) -> None:
    d = Data(results)
    scoreboards(d)
    breakdown(d)
    routing(d)
    routed_companies(d)
    confusion(d)
    shapes(d)
    calibration(d)
    nouls(d)
    duality(d)
    stability(d)
    latency(d)
    for path in heatmap(d):
        console.print(f'heatmap -> {path}')
