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
stability, latency, and a Nouls heatmap saved as images. Then, as Markdown, the remaining tables the research
report uses (`tables`), ending with the domain-level views.

The B2B Noul is reported apart from the five sector Nouls (see B2B_NOTE): it asks whether a company sells to
businesses, which is a different question from YC's B2B label, so it is shown as a fire rate and kept out of
precision/recall, Jaccard and duality.
"""

from __future__ import annotations

import collections
import math
import re
import statistics
from dataclasses import replace
from pathlib import Path

from rich.table import Table

from ..core.demo import RESULTS_DIR
from ..core.report import ROUTE, ROUTE_SETTINGS, console, cost, ece, load, load_results, md, pct, route_label, scoreboard, table, uncertain
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
        """The decision models with a run of this shape that answered something, e.g. ['jev', 'luna'] for 'flat'."""
        return [m for m in DECISION_MODELS if self.ok.get(f'{m}_{shape_}')]

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
            cells.append(pct(sum(hits), len(hits)))
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
        t.add_row(dom, str(len(ids)), *(pct(sum(d.picks[r][i] == d.ref[i] for i in ids if i in d.picks[r]),
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
        t.add_row(SHORT[dom], sub, str(n), *(pct(sum(d.picks[r][i] == d.ref[i] for i in ids if i in d.picks[r]),
                                                   sum(i in d.picks[r] for i in ids)) for r in runs))
    console.print(t)


def routing(d: Data) -> None:
    """Jev flat (or Luna flat) everywhere, Sonnet where it is unsure. Accuracy at level 2 against the reference."""
    models = d.models('flat')
    if not models or 'sonnet' not in d.picks:
        return
    sonnet_cost = cost(list(d.ok['sonnet'].values()), 'sonnet') / len(d.ok['sonnet'])
    ids = {m: [i for i in d.picks[f'{m}_flat'] if i in d.picks['sonnet']] for m in models}
    models = [m for m in models if ids[m]]
    if not models:
        return
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
            cells += [pct(k, n), pct(l1, n), pct(l2, n), f'{1000 * (unit_cost[m] + sonnet_cost * k / n):.2f}']
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
            cells += [f'{best[0]:.2f}', f'{best[1]:.2f}', pct(best[2], n), pct(best[4], n)]
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
        t.add_row(f'{a} vs {b}', str(len(ids)), pct(len(same_dom), len(ids)), pct(sum(d.picks[a][i] == d.picks[b][i] for i in ids), len(ids)),
                  pct(sum(d.picks[a][i] == d.picks[b][i] for i in same_dom), len(same_dom)))
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
                row += [str(len(idx)), pct(sum(right[k] for k in idx), len(idx))]
        t.add_row(*row)
    console.print(t)
    t = Table(title='expected calibration error (lower is better; L2 for sequential and fan-out is P(domain) x P(sub))')
    t.add_column('run'); t.add_column('ECE L1', justify='right'); t.add_column('ECE L2', justify='right')
    for run in runs:
        t.add_row(run, *(f'{ece(*cal[run][level]):.3f}' for level in ('L1', 'L2')))
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
        t.add_row(dom, str(len(group)), *(pct(sum(noul[m][i][B2B] > 0.5 for i in group), len(group)) for m in models))
    t.add_row('all', str(len(ids)), *(pct(sum(noul[m][i][B2B] > 0.5 for i in ids), len(ids)) for m in models))
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
                row += [str(len(pos)), pct(sum(dom in truth[i] for i in yes), len(yes)), pct(sum(dom in pred[m][i] for i in pos), len(pos))]
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
        if not diffs:
            continue
        flips = sum(d.picks[run][i] != d.picks[f'{run}_rerun'][i] for i in ids) if run in d.picks else None
        t.add_row(run, pct(sum(a[i]['output'] == b[i]['output'] for i in ids), len(ids)),
                  pct(flips, len(ids)) if flips is not None else '–', f'{statistics.mean(diffs):.3f}', f'{max(diffs):.2f}')
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


# ------------------------------------------------------------------ the Markdown tables report.md uses

def _mark(d: Data, p: Pair, i: str) -> str:
    return _name(p) + (' ✓' if p == d.ref[i] else '')


def sample_facts(d: Data) -> None:
    """Sample and reference counts, adjudication cost, and which runs disputed YC."""
    n = len(d.companies)
    md('## sample and reference facts\n')
    md(f'- companies {n}; adjudicated {len(d.adjudicated)}; reference differs from YC on {sum(d.ref[i] != d.yc[i] for i in d.companies)} companies (domain differs on {sum(d.ref[i][0] != d.yc[i][0] for i in d.companies)}); secondary pair given on {sum(1 for i in d.secondary if d.secondary[i])}')
    md(f'- reference per domain: {dict(collections.Counter(d.ref[i][0] for i in d.companies))}')
    md(f'- YC per domain: {dict(collections.Counter(d.yc[i][0] for i in d.companies))}')
    adj = list(d.adjudicated.values())
    md(f'- adjudication cost {cost(adj, "opus"):.2f} USD; input tokens {sum(r["input_tokens"] for r in adj):,}, output {sum(r["output_tokens"] for r in adj):,}; latency p50 {statistics.median(r["latency_ms"] for r in adj):.0f} ms')
    md(f'- corrected rows: {sum(bool(r.get("corrected")) for r in adj)}')
    md('')
    md('## which runs disputed (per company, how many runs disagreed with YC), and who sided with whom\n')
    cnt = collections.Counter()
    for i, r in d.adjudicated.items():
        for run, p in r['picks'].items():
            if tuple(p) != d.yc[i]:
                cnt[run] += 1
    md(f'- disagreements with YC per run among adjudicated companies: {dict(cnt)}')
    for run in d.runs():
        both = [i for i in d.adjudicated if i in d.picks[run]]
        with_yc = sum(d.picks[run][i] == d.yc[i] for i in both)
        with_ref = sum(d.picks[run][i] == d.ref[i] for i in both)
        md(f'- {run} on the {len(both)} adjudicated companies it answered: agrees with YC {with_yc}, with the reference {with_ref}')
    md('')


def scoreboard_detail(d: Data) -> None:
    """The scoreboard with tokens and cost per 1,000, the same-100 Sonnet comparison, level 1 per domain."""
    md('## main scoreboard with cost per 1000 and tokens per company\n')
    rows = []
    for run in (*d.runs(), 'jev_nouls', 'luna_nouls'):
        rs = list(d.ok[run].values())
        lat = sorted(r['latency_ms'] for r in rs)
        cells = [run, len(rs), len(d.results[run]) - len(rs)]
        if run in d.picks:
            for ref in (d.yc, d.ref):
                cells += [pct(sum(d.picks[run][i][0] == ref[i][0] for i in d.picks[run]), len(rs)), pct(sum(d.picks[run][i] == ref[i] for i in d.picks[run]), len(rs))]
        else:
            cells += ['–'] * 4
        cells += [f'{statistics.median(lat):.0f}', f'{lat[int(len(lat) * .95) - 1]:.0f}', f'{statistics.mean(r["input_tokens"] for r in rs):.0f}', f'{statistics.mean(r["output_tokens"] for r in rs):.0f}',
                  f'{statistics.mean(r.get("cache_read_tokens", 0) for r in rs):.0f}', f'{cost(rs, run):.4f}', f'{1000 * cost(rs, run) / len(rs):.2f}']
        rows.append(cells)
    table(['run', 'n', 'errors', 'L1 vs YC', 'L2 vs YC', 'L1 vs ref', 'L2 vs ref', 'p50 ms', 'p95 ms', 'in tok', 'out tok', 'cache read', 'USD total', 'USD/1000'], rows)

    md('## sonnet vs sonnet_thinking on the same first 100 companies\n')
    sub = [i for i in d.picks['sonnet_thinking']]
    rows = []
    for run in ('sonnet', 'sonnet_thinking', 'jev_flat', 'luna_flat'):
        rs = [d.ok[run][i] for i in sub if i in d.ok[run]]
        cells = [run, len(rs)]
        for ref in (d.yc, d.ref):
            cells += [pct(sum(d.picks[run][i][0] == ref[i][0] for i in sub if i in d.picks[run]), len(rs)), pct(sum(d.picks[run][i] == ref[i] for i in sub if i in d.picks[run]), len(rs))]
        lat = sorted(r['latency_ms'] for r in rs)
        cells += [f'{statistics.median(lat):.0f}', f'{lat[int(len(lat) * .95) - 1]:.0f}', f'{statistics.mean(r["output_tokens"] for r in rs):.0f}', f'{1000 * cost(rs, run) / len(rs):.2f}']
        rows.append(cells)
    table(['run (first 100)', 'n', 'L1 vs YC', 'L2 vs YC', 'L1 vs ref', 'L2 vs ref', 'p50 ms', 'p95 ms', 'out tok', 'USD/1000'], rows)
    md(f'- sonnet vs sonnet_thinking identical pick on the 100: {pct(sum(d.picks["sonnet"][i] == d.picks["sonnet_thinking"][i] for i in sub), len(sub))}')
    md(f'- sonnet (thinking off) rationale present: {sum(bool(r.get("rationale")) for r in d.ok["sonnet"].values())} of {len(d.ok["sonnet"])}; thinking on: {sum(bool(r.get("rationale")) for r in d.ok["sonnet_thinking"].values())}')
    md('')

    md('## level 1 per domain (domain right, vs reference)\n')
    rows = []
    for dom in (*d.s.domains, OTHER):
        ids3 = [i for i in d.companies if d.ref[i][0] == dom]
        rows.append([dom, len(ids3), *(pct(sum(d.picks[r][i][0] == d.ref[i][0] for i in ids3 if i in d.picks[r]), sum(i in d.picks[r] for i in ids3)) for r in d.runs())])
    table(['domain', 'n', *d.runs()], rows)


def calibration_detail(d: Data) -> None:
    """Calibration buckets with mean confidence, and ECE against YC as well as the reference."""
    md('## calibration buckets at both levels: jev_flat and luna_flat (vs reference) with mean confidence per bucket\n')
    cal = {run: _confidences(d, run) for run in ('jev_flat', 'luna_flat')}
    rows = []
    for b in range(10):
        cells = [f'{b / 10:.1f}-{(b + 1) / 10:.1f}']
        for run in ('jev_flat', 'luna_flat'):
            for level in ('L1', 'L2'):
                confs, right = cal[run][level]
                idx = [k for k, c in enumerate(confs) if b / 10 <= c < (b + 1) / 10 or (b == 9 and c == 1.0)]
                cells += [len(idx), pct(sum(right[k] for k in idx), len(idx)), f'{statistics.mean(confs[k] for k in idx):.2f}' if idx else '–']
        rows.append(cells)
    table(['bucket', 'jev n L1', 'jev acc L1', 'jev mean p', 'jev n L2', 'jev acc L2', 'jev mean p', 'luna n L1', 'luna acc L1', 'luna mean p', 'luna n L2', 'luna acc L2', 'luna mean p'], rows)
    for run in ('jev_flat', 'luna_flat'):
        for level in ('L1', 'L2'):
            confs, right = cal[run][level]
            md(f'- {run} {level}: mean confidence {statistics.mean(confs):.3f}, accuracy {statistics.mean(right):.3f}, ECE {ece(confs, right):.3f}, share with p >= 0.9: {pct(sum(c >= .9 for c in confs), len(confs))}')
    md('')
    md('## calibration vs YC instead of the reference (ECE)\n')
    rows = []
    for run in ('jev_flat', 'luna_flat', 'jev_sequential', 'luna_sequential', 'jev_fanout', 'luna_fanout'):
        conf = _confidences(d, run)
        cells = [run]
        for level in ('L1', 'L2'):
            confs, _ = conf[level]
            ids3 = list(d.ok[run])
            right_yc = [d.picks[run][i][0] == d.yc[i][0] if level == 'L1' else d.picks[run][i] == d.yc[i] for i in ids3]
            cells += [f'{ece(confs, right_yc):.3f}', f'{ece(*conf[level]):.3f}']
        rows.append(cells)
    table(['run', 'ECE L1 vs YC', 'ECE L1 vs ref', 'ECE L2 vs YC', 'ECE L2 vs ref'], rows)


def jev_vs_luna(d: Data) -> None:
    """Jev flat against Luna flat: counts, who was right, and every disagreement."""
    md('## Jev flat vs Luna flat: disagreements\n')
    ids3 = [i for i in d.picks['jev_flat'] if i in d.picks['luna_flat']]
    dis1 = [i for i in ids3 if d.picks['jev_flat'][i][0] != d.picks['luna_flat'][i][0]]
    dis2 = [i for i in ids3 if d.picks['jev_flat'][i] != d.picks['luna_flat'][i] and i not in dis1]
    md(f'- n {len(ids3)}; same domain {pct(len(ids3) - len(dis1), len(ids3))}; same subindustry {pct(len(ids3) - len(dis1) - len(dis2), len(ids3))}; domain disagreements {len(dis1)}; same domain but different subindustry {len(dis2)}')
    md(f'- on the {len(dis1)} domain disagreements, right vs reference: jev {sum(d.picks["jev_flat"][i][0] == d.ref[i][0] for i in dis1)}, luna {sum(d.picks["luna_flat"][i][0] == d.ref[i][0] for i in dis1)}, neither {sum(d.picks["jev_flat"][i][0] != d.ref[i][0] and d.picks["luna_flat"][i][0] != d.ref[i][0] for i in dis1)}; sonnet right {sum(d.picks["sonnet"][i][0] == d.ref[i][0] for i in dis1)}')
    md(f'- on the {len(dis2)} subindustry-only disagreements, right vs reference: jev {sum(d.picks["jev_flat"][i] == d.ref[i] for i in dis2)}, luna {sum(d.picks["luna_flat"][i] == d.ref[i] for i in dis2)}, neither {sum(d.picks["jev_flat"][i] != d.ref[i] and d.picks["luna_flat"][i] != d.ref[i] for i in dis2)}; sonnet right {sum(d.picks["sonnet"][i] == d.ref[i] for i in dis2)}')
    md(f'- routed by the default rule among domain disagreements: jev {sum(d.uncertain(i) for i in dis1)}, luna {sum(d.uncertain(i, model="luna") for i in dis1)}, either {sum(d.uncertain(i) or d.uncertain(i, model="luna") for i in dis1)}')
    md(f'- routed among sub-only disagreements: jev {sum(d.uncertain(i) for i in dis2)}, luna {sum(d.uncertain(i, model="luna") for i in dis2)}, either {sum(d.uncertain(i) or d.uncertain(i, model="luna") for i in dis2)}')
    md('')

    md(f'### domain disagreements ({len(dis1)})\n')
    rows = [[d.companies[i]['name'], _name(d.yc[i]), _name(d.ref[i]), _mark(d, d.picks['jev_flat'][i], i) + f' ({d.top(i, 1)[0][1]:.2f})', _mark(d, d.picks['luna_flat'][i], i) + f' ({d.top(i, 1, "luna")[0][1]:.2f})', _mark(d, d.picks['sonnet'][i], i)]
            for i in sorted(dis1, key=lambda i: d.companies[i]['name'].lower())]
    table(['company', 'YC', 'reference', 'jev_flat (p)', 'luna_flat (p)', 'sonnet'], rows)
    md(f'### same domain, different subindustry ({len(dis2)})\n')
    rows = [[d.companies[i]['name'], _name(d.yc[i]), _name(d.ref[i]), _mark(d, d.picks['jev_flat'][i], i) + f' ({d.top(i, 1)[0][1]:.2f})', _mark(d, d.picks['luna_flat'][i], i) + f' ({d.top(i, 1, "luna")[0][1]:.2f})', _mark(d, d.picks['sonnet'][i], i)]
            for i in sorted(dis2, key=lambda i: d.companies[i]['name'].lower())]
    table(['company', 'YC', 'reference', 'jev_flat (p)', 'luna_flat (p)', 'sonnet'], rows)


def routed_detail(d: Data) -> None:
    """Every company the default rule routes (Jev), and Luna's routed set against it."""
    md('## routed companies at the default rule (Jev flat), all, least sure first\n')
    routed = sorted((i for i in d.picks['jev_flat'] if i in d.picks['sonnet'] and d.uncertain(i)), key=lambda i: d.top(i, 1)[0][1])
    rows = [[d.companies[i]['name'], _name(d.yc[i]), _name(d.ref[i]), ', '.join(f'{k} {p:.2f}' for k, p in d.top(i)), _mark(d, d.picks['sonnet'][i], i), _brief(d.ok['sonnet'][i].get('rationale', ''), 200)] for i in routed]
    table(['company', 'YC', 'reference', "Jev's top 3", 'Sonnet', "Sonnet's rationale"], rows)
    md(f'- {len(routed)} routed; Jev top-1 right on them: {sum(d.picks["jev_flat"][i] == d.ref[i] for i in routed)}; Sonnet right: {sum(d.picks["sonnet"][i] == d.ref[i] for i in routed)}; reference in Jev\'s top 3: {sum(any(k == (d.ref[i][1] or OTHER) for k, _ in d.top(i)) for i in routed)}')
    md(f'- at level 1 on the routed: jev right {sum(d.picks["jev_flat"][i][0] == d.ref[i][0] for i in routed)}, sonnet right {sum(d.picks["sonnet"][i][0] == d.ref[i][0] for i in routed)}')
    md('')
    md('## Luna routed companies at the default rule (Luna flat)\n')
    routed_l = sorted((i for i in d.picks['luna_flat'] if i in d.picks['sonnet'] and d.uncertain(i, model='luna')), key=lambda i: d.top(i, 1, 'luna')[0][1])
    md(f'- {len(routed_l)} routed; Luna right on them {sum(d.picks["luna_flat"][i] == d.ref[i] for i in routed_l)}; Sonnet right {sum(d.picks["sonnet"][i] == d.ref[i] for i in routed_l)}; overlap with Jev\'s routed set {len(set(routed) & set(routed_l))}')
    md('')


def fanout_refusals(d: Data) -> None:
    """The companies Luna refused a fan-out question for."""
    md('## Luna fan-out refusals\n')
    ref_rows = [(i, r) for i, r in d.results['luna_fanout'].items() if 'error' in r]
    qs = collections.Counter()
    for i, r in ref_rows:
        m = re.search(r"refused question '([^']+)'", r['error'])
        qs[m.group(1) if m else r['error'][:60]] += 1
    md(f'- {len(ref_rows)} refused rows; refused question field: {dict(qs)}')
    md(f'- YC domain of the refused companies: {dict(collections.Counter(d.yc[i][0] for i, _ in ref_rows))}')
    md('- companies: ' + ', '.join(d.companies[i]['name'] for i, _ in ref_rows))
    md('')


def nouls_detail(d: Data) -> None:
    """Nouls against the flat Choice (2x2 and the off-diagonal companies), the B2B gap, how decisive the Nouls are."""
    md('## Nouls: 2x2 and off-diagonal companies, Jev (all) and Luna (counts)\n')
    secs = sectors(d)
    for m in ('jev', 'luna'):
        ids3 = [i for i in d.ok[f'{m}_flat'] if i in d.ok[f'{m}_nouls']]
        dual = {i: sum(d.noul_p(i, m)[dom] > 0.5 for dom in secs) >= 2 for i in ids3}
        uns = {i: d.uncertain(i, model=m) for i in ids3}
        md(f'- {m}: dual&uncertain {sum(dual[i] and uns[i] for i in ids3)}, dual&sure {sum(dual[i] and not uns[i] for i in ids3)}, single&uncertain {sum(not dual[i] and uns[i] for i in ids3)}, single&sure {sum(not dual[i] and not uns[i] for i in ids3)}')
        # how often dual coincides with Opus giving a secondary domain
        has_sec = {i: bool(d.secondary.get(i)) for i in ids3}
        md(f'- {m}: of the dual companies, Opus gave a secondary pair for {sum(has_sec[i] for i in ids3 if dual[i])} of {sum(dual.values())}; of the non-dual, {sum(has_sec[i] for i in ids3 if not dual[i])} of {sum(not v for v in dual.values())} (secondary exists only for adjudicated companies)')
        md(f'- {m}: dual companies whose two sector Nouls match reference primary+secondary domains exactly: {sum(dual[i] and {dom for dom in secs if d.noul_p(i, m)[dom] > .5} == ({d.ref[i][0]} | ({d.secondary[i][0]} if d.secondary.get(i) else set())) & set(secs) for i in ids3)}')
        if m == 'jev':
            for title, sel in (('dual and uncertain', lambda i: dual[i] and uns[i]), ('dual but the Choice is sure', lambda i: dual[i] and not uns[i]), ('Choice uncertain but 0-1 sector Nouls', lambda i: uns[i] and not dual[i])):
                rows = []
                for i in sorted((i for i in ids3 if sel(i)), key=lambda i: d.companies[i]['name'].lower()):
                    yes = ', '.join(f'{dom} {p:.2f}' for dom, p in sorted(d.noul_p(i).items(), key=lambda x: -x[1]) if p > 0.5 and dom in secs)
                    rows.append([d.companies[i]['name'], _name(d.yc[i]), _name(d.ref[i]) + (f' + {d.secondary[i][0]}' if d.secondary.get(i) else ''), yes or '–', ', '.join(f'{k} {p:.2f}' for k, p in d.top(i, 2))])
                md(f'### Jev, {title}: {len(rows)} companies\n')
                table(['company', 'YC', 'reference (+ secondary domain)', 'sector Nouls yes', "Jev's top 2"], rows)
    md('')
    md('## B2B Noul vs "YC B2B": what the gap is made of\n')
    for m in ('jev', 'luna'):
        ids3 = list(d.ok[f'{m}_nouls'])
        fires = [i for i in ids3 if d.noul_p(i, m)['B2B'] > 0.5]
        md(f'- {m}: fires on {len(fires)} of {len(ids3)}; of those, YC B2B {sum(d.yc[i][0] == "B2B" for i in fires)}, YC Fintech {sum(d.yc[i][0] == "Fintech" for i in fires)}, Healthcare {sum(d.yc[i][0] == "Healthcare" for i in fires)}, Industrials {sum(d.yc[i][0] == "Industrials" for i in fires)}, RE {sum(d.yc[i][0] == "Real Estate and Construction" for i in fires)}, Consumer {sum(d.yc[i][0] == "Consumer" for i in fires)}, Other {sum(d.yc[i][0] == "Other" for i in fires)}; precision vs YC B2B would be {pct(sum(d.yc[i][0] == "B2B" for i in fires), len(fires))}, recall {pct(sum(d.yc[i][0] == "B2B" for i in fires), sum(d.yc[i][0] == "B2B" for i in ids3))}')
    md('')
    md('## Noul P(yes) distribution: how decisive are the Nouls\n')
    for m in ('jev', 'luna'):
        ps = [p for i in d.ok[f'{m}_nouls'] for dom, p in d.noul_p(i, m).items()]
        md(f'- {m}: {len(ps)} Noul answers; share in 0.3-0.7: {pct(sum(.3 <= p <= .7 for p in ps), len(ps))}; share > 0.9 or < 0.1: {pct(sum(p > .9 or p < .1 for p in ps), len(ps))}')
    md('')


def stability_detail(d: Data) -> None:
    """Flips between the first pass and the rerun, and how much the confidences moved."""
    md('## stability detail\n')
    for run in ('jev_flat', 'jev_sequential', 'jev_fanout', 'jev_nouls'):
        a, b = d.ok[run], d.ok[f'{run}_rerun']
        ids3 = [i for i in a if i in b]
        if run in d.picks:
            flips = [i for i in ids3 if d.picks[run][i] != d.picks[f'{run}_rerun'][i]]
            md(f'- {run}: {len(flips)} label flips: ' + '; '.join(f'{d.companies[i]["name"]} {_name(d.picks[run][i])} -> {_name(d.picks[run + "_rerun"][i])} (top-1 {a[i]["confidence"].get("subindustry", a[i]["confidence"].get("domain")):.2f})' for i in flips))
            md(f'  flips that were routed by the default rule in the first run: {sum(d.uncertain(i) if run == "jev_flat" else False for i in flips)} (rule defined on flat only)')
        diffs = [abs(a[i]['confidence'][k] - b[i]['confidence'][k]) for i in ids3 for k in a[i]['confidence'] if k in b[i]['confidence']]
        md(f'- {run}: confidence deltas: mean {statistics.mean(diffs):.4f}, median {statistics.median(diffs):.4f}, share exactly 0: {pct(sum(x == 0 for x in diffs), len(diffs))}, share > 0.05: {pct(sum(x > .05 for x in diffs), len(diffs))}')
    for run in ('luna_flat', 'luna_sequential', 'luna_fanout', 'luna_nouls'):
        a, b = d.ok[run], d.ok[f'{run}_rerun']
        ids3 = [i for i in a if i in b]
        same = sum(a[i]['output'] == b[i]['output'] and a[i]['confidence'] == b[i]['confidence'] and a[i].get('probabilities') == b[i].get('probabilities') for i in ids3)
        md(f'- {run}: outputs, confidences and probabilities all identical on {same} of {len(ids3)}; input tokens identical on {sum(a[i]["input_tokens"] == b[i]["input_tokens"] for i in ids3)}; rerun errors {sum("error" in r for r in d.results[run + "_rerun"].values())}, same error ids as first run: {set(i for i, r in d.results[run].items() if "error" in r) == set(i for i, r in d.results[run + "_rerun"].items() if "error" in r)}')
    md('')


def latency_detail(d: Data) -> None:
    """Latency per run with min and p99, reruns included."""
    md('## latency detail: min and p99, and the rerun latencies\n')
    rows = []
    for run in sorted(d.ok):
        lat = sorted(r['latency_ms'] for r in d.ok[run].values())
        rows.append([run, len(lat), f'{lat[0]:.0f}', f'{statistics.median(lat):.0f}', f'{lat[int(len(lat) * .95) - 1]:.0f}', f'{lat[int(len(lat) * .99) - 1]:.0f}', f'{lat[-1]:.0f}', f'{statistics.mean(r["input_tokens"] for r in d.ok[run].values()):.0f}'])
    table(['run', 'n', 'min ms', 'p50', 'p95', 'p99', 'max', 'mean in tok'], rows)


def other_demos_latency() -> None:
    """Demos 1 and 2 by the same measure; report.md reads them next to demo 3's. Demo 1's results are local only
    (gitignored), so without them only demo 2's rows print."""
    md('## guardrail and dealflow latency detail\n')
    rows = []
    for demo in ('guardrail', 'dealflow'):
        res = {b: {i: r for i, r in rows_.items() if 'error' not in r} for b, rows_ in load_results(demo, RESULTS_DIR).items() if b not in ('notes', 'generation')}
        for b, rs in res.items():
            lat = sorted(r['latency_ms'] for r in rs.values())
            rows.append([demo, b, len(lat), f'{lat[0]:.0f}', f'{statistics.median(lat):.0f}', f'{lat[int(len(lat) * .95) - 1]:.0f}', f'{lat[-1]:.0f}', f'{statistics.mean(r["input_tokens"] for r in rs.values()):.0f}'])
    table(['demo', 'backend', 'n', 'min ms', 'p50', 'p95', 'max', 'mean in tok'], rows)


def adjudication_detail(d: Data) -> None:
    """Sonnet errors, how Opus's secondary pairs relate to YC, and the ANORIA and Roofr cases."""
    md('## sonnet thinking (demo 3): errors and retries\n')
    md(f'- sonnet errors {sum("error" in r for r in d.results["sonnet"].values())}, sonnet_thinking errors {sum("error" in r for r in d.results["sonnet_thinking"].values())}')
    md('## Opus adjudication: secondary domains, and how often the secondary equals YC\n')
    sec_eq_yc = sum(1 for i, r in d.adjudicated.items() if d.secondary.get(i) and d.secondary[i] == d.yc[i])
    md(f'- secondary pair equals YC\'s label on {sec_eq_yc} companies; primary equals YC on {sum(d.ref[i] == d.yc[i] for i in d.adjudicated)}; neither on {sum(d.ref[i] != d.yc[i] and (d.secondary.get(i) != d.yc[i]) for i in d.adjudicated)}')
    md(f'- Opus primary domain distribution on adjudicated: {dict(collections.Counter(d.ref[i][0] for i in d.adjudicated))}; YC domain distribution on the same: {dict(collections.Counter(d.yc[i][0] for i in d.adjudicated))}')
    md('')
    md('## the ANORIA case\n')
    for i, r in d.adjudicated.items():
        if d.companies[i]['name'].upper().startswith('ANORIA'):
            md(f'- {d.companies[i]["name"]}: YC {d.yc[i]}, Opus {d.ref[i]}, secondary {d.secondary.get(i)}, picks {r["picks"]}, explanation: {r["label"]["explanation"]}')
    md('')
    md('## Roofr\n')
    i = 'roofr'
    md(f'- Roofr: YC {d.yc[i]}, reference {d.ref[i]}, secondary {d.secondary.get(i)}; picks: ' + ', '.join(f'{run} {_name(d.picks[run][i])}' for run in d.runs() if i in d.picks[run]) + (f'; Opus: {d.adjudicated[i]["label"]["explanation"]}' if i in d.adjudicated else ''))
    md('')


def pct1(k: int, n: int) -> str:
    return f'{k / n:.1%}' if n else '–'


def dom_p(d: Data, run: str, i: str) -> collections.Counter:
    """A flat run's probability per domain: the sum of its subindustries' probabilities; Other stands alone."""
    out = collections.Counter()
    for k, v in d.ok[run][i]['probabilities']['subindustry'].items():
        out[k if k == OTHER else d.s.parent[k]] += v
    return out


def dom_top(d: Data, run: str, i: str, k: int = 3) -> list[tuple[str, float]]:
    return dom_p(d, run, i).most_common(k)


def binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided binomial test against p = 0.5: the probability of every outcome no likelier than k."""
    pmf = [math.comb(n, x) / 2 ** n for x in range(n + 1)]
    return sum(q for q in pmf if q <= pmf[k] * (1 + 1e-9))


def answer4(d: Data, m: str, routed: set[str], i: str) -> Pair:
    return (d.picks['sonnet'] if i in routed else d.picks[f'{m}_flat'])[i]


def change4(d: Data, m: str, i: str) -> str:
    a, s, r = d.picks[f'{m}_flat'][i][0], d.picks['sonnet'][i][0], d.ref[i][0]
    return 'same' if a == s else 'fixed' if s == r else 'broken' if a == r else 'changed, both wrong'


# Eligible companies per YC domain: the 2026-10-09 recount in report.md section 2 (draw-time pools were not recorded).
POOL = {'B2B': 1171, 'Consumer': 122, 'Fintech': 204, 'Healthcare': 278, 'Industrials': 220, 'Real Estate and Construction': 47, OTHER: 75}


def domain_level(d: Data) -> None:
    """Demo 3 at the domain level, with summed domain probabilities: routing, agreement, stability, sample vs pool."""
    md('# DEMO 3 DOMAIN-LEVEL VIEWS\n')

    both4 = {m: [i for i in d.picks[f'{m}_flat'] if i in d.picks['sonnet']] for m in ('jev', 'luna')}
    unit4 = {m: cost(list(d.ok[f'{m}_flat'].values()), m) / len(d.ok[f'{m}_flat']) for m in both4}
    sonnet_avg = cost(list(d.ok['sonnet'].values()), 'sonnet') / len(d.ok['sonnet'])

    md('## domain level: the companies the default rule routes (Jev flat), least sure domain first\n')
    routed4 = sorted((i for i in both4['jev'] if d.uncertain(i)), key=lambda i: (dom_top(d, 'jev_flat', i, 1)[0][1], d.companies[i]['name'].lower()))
    rows = [[d.companies[i]['name'], d.yc[i][0], d.ref[i][0], ', '.join(f'{k} {p:.2f}' for k, p in dom_top(d, 'jev_flat', i)),
             d.picks['sonnet'][i][0] + (' ✓' if d.picks['sonnet'][i][0] == d.ref[i][0] else ''), change4(d, 'jev', i)] for i in routed4]
    table(['company', 'YC domain', 'reference domain', "Jev's top 3 domains (summed p)", 'Sonnet domain', 'routing'], rows)
    changes = collections.Counter(change4(d, 'jev', i) for i in routed4)
    md(f'- {len(routed4)} routed; already sure at domain level (the same rule on summed probabilities does not fire): {sum(not uncertain(dom_p(d, "jev_flat", i)) for i in routed4)}')
    md(f'- domain changed by routing: {len(routed4) - changes["same"]} (' + ', '.join(f'{k} {v}' for k, v in changes.items() if k != 'same')
      + f'); broken: {", ".join(d.companies[i]["name"] for i in routed4 if change4(d, "jev", i) == "broken") or "none"}')
    md('')

    md('## domain-level routing rule: the routing grid on summed domain probabilities (Jev flat, domain vs reference)\n')
    rows = []
    for t1, margin in ROUTE_SETTINGS:
        routed = {i for i in both4['jev'] if uncertain(dom_p(d, 'jev_flat', i), t1, margin)}
        n4 = len(both4['jev'])
        rows.append([route_label(t1, margin), len(routed), pct1(len(routed), n4), pct1(sum(answer4(d, 'jev', routed, i)[0] == d.ref[i][0] for i in both4['jev']), n4),
                     f'{1000 * (unit4["jev"] + cost([d.ok["sonnet"][i] for i in routed], "sonnet") / n4):.2f}'])
    table(['route when (summed domain p)', 'routed', 'share routed', 'L1 vs reference', 'USD/1000 (routed at actual Sonnet cost)'], rows)

    md('## subindustry rule (the report\'s), domain accuracy at one decimal, cost by Sonnet average and by actual routed cost\n')
    rows = []
    for t1, margin in ROUTE_SETTINGS:
        cells = [route_label(t1, margin)]
        for m in both4:
            n4 = len(both4[m])
            routed = {i for i in both4[m] if d.uncertain(i, t1, margin, model=m)}
            cells += [pct1(len(routed), n4), pct1(sum(answer4(d, m, routed, i)[0] == d.ref[i][0] for i in both4[m]), n4),
                      f'{1000 * (unit4[m] + sonnet_avg * len(routed) / n4):.2f}', f'{1000 * (unit4[m] + cost([d.ok["sonnet"][i] for i in routed], "sonnet") / n4):.2f}']
        rows.append(cells)
    table(['route when', 'jev routed', 'jev L1', 'jev USD/1000 avg', 'jev USD/1000 actual', 'luna routed', 'luna L1', 'luna USD/1000 avg', 'luna USD/1000 actual'], rows)

    md('## combined system against YC at domain level (default rule)\n')
    rows = []
    for m in both4:
        routed = {i for i in both4[m] if d.uncertain(i, model=m)}
        n4 = len(both4[m])
        rows.append([m, n4, pct1(sum(d.picks[f'{m}_flat'][i][0] == d.yc[i][0] for i in both4[m]), n4), pct1(sum(answer4(d, m, routed, i)[0] == d.yc[i][0] for i in both4[m]), n4),
                     pct1(sum(d.picks['sonnet'][i][0] == d.yc[i][0] for i in both4[m]), n4)])
    table(['decision model', 'n', 'alone L1 vs YC', '+ Sonnet (default rule) L1 vs YC', 'Sonnet alone L1 vs YC'], rows)

    md('## the adjudicated companies at domain level: agreement with Opus\'s domain and with YC\'s\n')
    rows = []
    for run in d.runs():
        adj4 = [i for i in d.adjudicated if i in d.picks[run]]
        rows.append([run, len(adj4), sum(d.picks[run][i][0] == d.ref[i][0] for i in adj4), sum(d.picks[run][i][0] == d.yc[i][0] for i in adj4)])
    table(['run', 'adjudicated answered', 'same domain as Opus', 'same domain as YC'], rows)

    md('## domain-level stability: first pass vs rerun (flat)\n')
    for m in ('jev', 'luna'):
        a, b = f'{m}_flat', f'{m}_flat_rerun'
        flips = sorted((i for i in d.picks[a] if i in d.picks[b] and d.picks[a][i][0] != d.picks[b][i][0]), key=lambda i: d.companies[i]['name'].lower())
        cell = lambda run, i: f'{d.picks[run][i][0]} {dom_p(d, run, i)[d.picks[run][i][0]]:.2f}' + ('' if dom_top(d, run, i, 1)[0][0] == d.picks[run][i][0] else f' (highest sum {dom_top(d, run, i, 1)[0][0]})')
        md(f'### {m}: {len(flips)} companies changed domain\n')
        if flips:
            table(['company', 'reference domain', 'first pass (summed p)', 'rerun (summed p)'], [[d.companies[i]['name'], d.ref[i][0], cell(a, i), cell(b, i)] for i in flips])
    roofr = sorted(d.ok['jev_flat_rerun']['roofr']['probabilities']['subindustry'].items(), key=lambda x: -x[1])[:2]
    md('- Roofr, jev_flat_rerun top two subindustries: ' + ', '.join(f'{k} {p:.2f}' for k, p in roofr))
    md('')

    md('## sample vs pool: share of the eligible companies taken per YC domain\n')
    sample4 = collections.Counter(d.yc[i][0] for i in d.companies)
    rows = [[dom, sample4[dom], f'{POOL[dom]:,}', pct1(sample4[dom], POOL[dom]), pct1(sample4[dom], len(d.companies)), pct1(POOL[dom], sum(POOL.values()))] for dom in POOL]
    rows.append(['all', len(d.companies), f'{sum(POOL.values()):,}', pct1(len(d.companies), sum(POOL.values())), '100.0%', '100.0%'])
    table(['YC domain', 'sample', 'pool', 'share taken', 'share of sample', 'share of pool'], rows)

    md('## domain accuracy vs reference per YC domain, and reweighted to the pool\'s domain mix\n')
    acc4 = {run: {dom: [d.picks[run][i][0] == d.ref[i][0] for i in d.picks[run] if d.yc[i][0] == dom] for dom in POOL} for run in ('jev_flat', 'luna_flat', 'sonnet')}
    rows = [[dom, len(acc4['jev_flat'][dom]), *(pct1(sum(acc4[run][dom]), len(acc4[run][dom])) for run in acc4)] for dom in POOL]
    rows.append(['sample mix', len(d.companies), *(pct1(sum(map(sum, acc4[run].values())), sum(map(len, acc4[run].values()))) for run in acc4)])
    rows.append(['pool mix (reweighted)', '', *(f'{sum(statistics.mean(acc4[run][dom]) * POOL[dom] for dom in POOL) / sum(POOL.values()):.1%}' for run in acc4)])
    table(['YC domain', 'n', 'jev_flat', 'luna_flat', 'sonnet'], rows)
    dis4 = [i for i in d.picks['jev_flat'] if i in d.picks['luna_flat'] and d.picks['jev_flat'][i][0] != d.picks['luna_flat'][i][0]]
    jr, lr = (sum(d.picks[run][i][0] == d.ref[i][0] for i in dis4) for run in ('jev_flat', 'luna_flat'))
    md(f'- {len(dis4)} Jev/Luna domain disagreements: jev right {jr}, luna right {lr}; exact two-sided binomial test of {jr} vs {lr} against even odds: p = {binom_two_sided(jr, jr + lr):.3f}')
    md('')

    md('## domain of the top subindustry vs the domain with the highest summed probability\n')
    for m in ('jev', 'luna'):
        run = f'{m}_flat'
        odd = sorted((i for i in d.picks[run] if d.picks[run][i][0] != dom_top(d, run, i, 1)[0][0]), key=lambda i: d.companies[i]['name'].lower())
        md(f'### {m}: {len(odd)} companies\n')
        if odd:
            table(['company', 'top subindustry (p)', 'its domain (summed p)', 'highest-sum domain (p)', 'reference domain'],
                  [[d.companies[i]['name'], f'{d.top(i, 1, m)[0][0]} {d.top(i, 1, m)[0][1]:.2f}', f'{d.picks[run][i][0]} {dom_p(d, run, i)[d.picks[run][i][0]]:.2f}',
                    f'{dom_top(d, run, i, 1)[0][0]} {dom_top(d, run, i, 1)[0][1]:.2f}', d.ref[i][0]] for i in odd])

    md('## same 100 companies (sonnet_thinking\'s): domain agreement with YC\n')
    sub4 = list(d.picks['sonnet_thinking'])
    md('- ' + ', '.join(f'{run} {sum(d.picks[run][i][0] == d.yc[i][0] for i in sub4 if i in d.picks[run])} of {sum(i in d.picks[run] for i in sub4)}' for run in ('sonnet', 'sonnet_thinking', 'jev_flat')))
    md('')

    md('## calibration bucket arithmetic: Jev flat, summed probability of the picked domain under 0.6\n')
    confs, right = _confidences(d, 'jev_flat')['L1']
    low = [k for k, c in enumerate(confs) if c < 0.6]
    md(f'- {len(low)} companies under 0.6; domain right vs reference on {sum(right[k] for k in low)}')
    md('')


def tables(d: Data) -> None:
    md('# DEMO 3 EXTRA TABLES\n')
    sample_facts(d)
    scoreboard_detail(d)
    calibration_detail(d)
    jev_vs_luna(d)
    routed_detail(d)
    fanout_refusals(d)
    nouls_detail(d)
    stability_detail(d)
    latency_detail(d)
    other_demos_latency()
    adjudication_detail(d)
    domain_level(d)
    md('## largest definition / input sizes\n')
    md(f'- flat question: {len(d.s.parent)} subindustries + Other = {len(d.s.parent) + 1} options')
    md(f'- domains: {d.s.domains}')
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
    tables(d)
