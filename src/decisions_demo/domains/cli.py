"""Demo 3: two-level investing-domain classification of real YC companies.

  domains fetch          # rebuild the published 500-company sample from the YC mirror -> data/companies.jsonl
  domains fetch --new    # or draw a fresh one (writes data/company_sample.jsonl too)
  domains define         # Sonnet writes one line per sampled subindustry, once -> data/domain_definitions.json
  domains classify       # every run in domains/runs.py -> data/results/domains.<run>.jsonl
  domains classify --runs jev_flat,jev_sequential,jev_fanout,jev_nouls --tag rerun   # Jev stability run
  domains classify --runs luna_flat,luna_sequential,luna_fanout,luna_nouls          # the same four shapes on Luna
  domains adjudicate --dry-run   # how many companies a run disputes with YC, and what Opus would cost
  domains adjudicate             # Opus labels those, blind -> data/domain_adjudicated.jsonl (hand-editable)
  domains adjudicate --add       # after adding a run: label only the newly disputed, keep every existing label
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re

import httpx2
import typer
from pydantic import BaseModel
from pydantic_ai.tools import GenerateToolJsonSchema

from ..core.demo import RESULTS_DIR, read_jsonl, write_jsonl
from ..core.backends import configure_logfire, make_backends
from ..core.report import cost
from ..core.runner import concurrency
from . import runs as domain_runs
from .demo import DEMO, ADJUDICATED_PATH, COMPANIES_PATH, DEFINITIONS_PATH, INSTRUCTIONS, SAMPLE_FIELDS, SAMPLE_PATH, state
from .schemas import OTHER, SEED_DOMAIN_DEFINITIONS, build, load_definitions

app = typer.Typer(add_completion=False)

# Unofficial mirror of YC's public company directory (github.com/yc-oss/api), refreshed
# daily. It declares no licence and the descriptions belong to YC and the companies, so they are
# fetched locally and never committed; see DATA.md.
YC_COMPANIES_URL = 'https://yc-oss.github.io/api/companies/all.json'
DOMAINS = tuple(d for d in SEED_DOMAIN_DEFINITIONS if d != OTHER)


def load_mirror() -> list[dict]:
    return httpx2.get(YC_COMPANIES_URL, timeout=60, follow_redirects=True).raise_for_status().json()


def reference_label(company: dict) -> tuple[str, str | None] | None:
    """YC's (industry, subindustry leaf). Education and Government become Other, which has no level 2.

    None when there is no usable label: Unspecified, or one of the six domains with no subindustry.
    """
    industry = company.get('industry')
    if industry in (None, '', 'Unspecified'):
        return None
    if industry not in DOMAINS:
        return OTHER, None
    sub = company.get('subindustry') or ''
    return (industry, sub.split(' -> ', 1)[1]) if ' -> ' in sub else None


def batch_year(company: dict) -> int:
    m = re.search(r'\d{4}', company.get('batch') or '')
    return int(m.group()) if m else 0


def allocate_even[K](pool_sizes: dict[K, int], total: int) -> dict[K, int]:
    """Spread total as evenly as the pools allow; what a small pool can't take goes to the larger ones."""
    counts = dict.fromkeys(pool_sizes, 0)
    order = sorted(pool_sizes, key=lambda k: (-pool_sizes[k], str(k)))
    while (left := total - sum(counts.values())) > 0:
        open_ = [k for k in order if counts[k] < pool_sizes[k]]
        if not open_:
            raise typer.BadParameter(f'only {sum(pool_sizes.values())} eligible companies, need {total}')
        for k in open_[:left]:
            counts[k] += 1
    return counts


def allocate_proportional[K](pool_sizes: dict[K, int], total: int, floor: int) -> dict[K, int]:
    """A floor per key where the pool allows, the rest in proportion to what each pool has left (largest remainder)."""
    counts = {k: min(floor, n) for k, n in pool_sizes.items()}
    spare = {k: n - counts[k] for k, n in pool_sizes.items()}
    rest = total - sum(counts.values())
    if not 0 <= rest <= sum(spare.values()):
        raise typer.BadParameter(f'cannot place {total} with a floor of {floor} across pools {pool_sizes}')
    if rest:
        shares = {k: rest * spare[k] / sum(spare.values()) for k in spare}
        for k, share in shares.items():
            counts[k] += int(share)
        for k in sorted(shares, key=lambda k: (-(shares[k] % 1), str(k)))[: total - sum(counts.values())]:
            counts[k] += 1
    return counts


# Rough per-request figures for the cost preview; the real numbers come from each run's usage.
# Calibrated on the 2026-10-04 runs: Claude counted ~27% more tokens than chars/4, Jev ~29% fewer.
# Luna on the 2026-10-09 smoke test: ~0.8x Jev's count on the Choice shapes, ~1.3x on the Nouls, whose
# instructions are repeated in every question.
CHARS_PER_TOKEN = {'jev': 5.1, 'luna': 5.6}
CLAUDE_CHARS_PER_TOKEN = 3.15
TOOL_OVERHEAD_TOKENS = 400  # Anthropic's tool-use system prompt, sent with every structured-output call
# Output tokens per call, measured on Sonnet 5.5 / Opus 5.5 (2026-10-04 smoke test): thinking off
# writes a rationale before the tool call, thinking on barely thinks on this task.
OUTPUT_TOKENS = {'sonnet': 180, 'sonnet_thinking': 35, 'opus': 160}


def _tokens(text: str, backend: str) -> float:
    return len(text) / CHARS_PER_TOKEN.get(backend.split('_')[0], CLAUDE_CHARS_PER_TOKEN)


def _schema_tokens(model, backend: str) -> float:
    return _tokens(json.dumps(model.model_json_schema(schema_generator=GenerateToolJsonSchema)), backend)


def estimated_rows(backend: str, *models, records: list[dict], per_record=None, cached=False) -> list[dict]:
    """Usage rows as a run would record them, estimated, so core.report.cost can price them."""
    out = []
    for i, r in enumerate(records):
        requests = [*models, *(per_record(r) if per_record else [])]
        overhead = _tokens(INSTRUCTIONS, backend) + (TOOL_OVERHEAD_TOKENS if backend not in domain_runs.DECISION_MODELS else 0)
        row = {'input_tokens': sum(_tokens(state(r), backend) + overhead + _schema_tokens(m, backend) for m in requests),
               'output_tokens': OUTPUT_TOKENS.get(backend, 0) * len(requests)}
        if cached:
            # Thinking off sends the schema as a cached tool: the first concurrent wave writes it, later calls read it.
            row['cache_write_tokens' if i < concurrency(backend) else 'cache_read_tokens'] = sum(_schema_tokens(m, backend) for m in requests)
        out.append(row)
    return out


def estimate_costs(sample: list[dict]) -> None:
    """What each planned run would cost on this sample, from the real schemas and genai-prices (core/report.py)."""
    if not DEFINITIONS_PATH.exists():
        typer.echo(f'no {DEFINITIONS_PATH} yet; run `domains define` for a cost preview')
        return
    s = build(load_definitions())

    def rows(backend: str, *models, per_record=None, records=sample, cached=False) -> list[dict]:
        return estimated_rows(backend, *models, records=records, per_record=per_record, cached=cached)

    sonnet = 'sonnet_thinking' if DEMO.sonnet_thinking else 'sonnet'
    subset = sample[:domain_runs.SUBSET]
    runs = [
        run for m in domain_runs.DECISION_MODELS for run in (
            (f'{m}_flat', m, rows(m, s.flat)),
            # The sub question only follows when the domain isn't Other; assume the domain picked matches YC's.
            (f'{m}_sequential', m, rows(m, s.domain, per_record=lambda r: [s.sub[r['label']]] if r['label'] in s.sub else [])),
            (f'{m}_fanout', m, rows(m, s.fan_out)),
            (f'{m}_nouls', m, rows(m, s.footprint)),
        )
    ] + [
        (f'sonnet (thinking {"on" if DEMO.sonnet_thinking else "off"})', sonnet, rows(sonnet, s.nested, cached=not DEMO.sonnet_thinking)),
        (f'sonnet_thinking, first {len(subset)}', 'sonnet_thinking', rows('sonnet_thinking', s.nested, records=subset)),
        ('opus adjudication, if every company is disputed', 'opus', rows('opus', s.adjudication)),
    ]
    typer.echo(f'estimated cost for {len(sample)} companies (prices from genai-prices, see core/report.py; Jev and Luna reruns cost the same again):')
    for name, backend, rs in runs:
        typer.echo(f'  {name:48} {sum(r["input_tokens"] for r in rs) / len(rs):6.0f} in-tokens/company   USD {cost(rs, backend):8.4f}')


@app.callback()
def main():
    """Demo 3: investing-domain classification."""


def text_sha256(company: dict) -> str:
    """Fingerprint of the text a model sees, so a restore can tell when YC has since edited a description."""
    text = f"{company.get('one_liner') or ''}\n{(company.get('long_description') or '').strip()}"
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def record(c: dict, label: tuple[str, str | None]) -> dict:
    """One row of the local sample: the mirror's company with the reference label it is scored against."""
    return {
        'id': c['slug'],
        'name': c['name'],
        'one_liner': c['one_liner'],
        'long_description': c['long_description'].strip(),
        'label': label[0],
        'sub_label': label[1],
        'yc_industry': c['industry'],
        'yc_subindustry': c.get('subindustry'),
        'yc_industries': c.get('industries') or [],
        'tags': c.get('tags') or [],
        'batch': c.get('batch'),
        'text_sha256': text_sha256(c),
    }


def draw_sample(companies: list[dict], size: int, per_other: int, floor: int, since: int, keep: list[str],
                seed: int, min_description: int) -> list[dict]:
    """Domains as even as their pools allow, subindustries proportional within each, a floor per subindustry."""
    by_slug = {c['slug']: c for c in companies}
    if missing := [s for s in keep if s not in by_slug or not reference_label(by_slug[s])]:
        raise typer.BadParameter(f'cannot keep {missing}: not in the mirror or no YC subindustry')

    # (domain, leaf) -> eligible companies; Other is (Other, None). Kept companies count toward their pool.
    kept = {s: label for s in keep if (label := reference_label(by_slug[s]))}
    pools: dict[tuple[str, str | None], list[dict]] = {k: [] for k in kept.values()}
    for c in companies:
        label = reference_label(c)
        if (label and c['slug'] not in keep and batch_year(c) >= since and c.get('status') == 'Active'
                and len((c.get('long_description') or '').strip()) >= min_description):
            pools.setdefault(label, []).append(c)
    sizes = {k: len(v) + sum(x == k for x in kept.values()) for k, v in pools.items()}
    domain_quota = allocate_even({d: sum(n for k, n in sizes.items() if k[0] == d) for d in DOMAINS}, size - per_other)
    domain_quota[OTHER] = per_other

    # The seed fixes the draw and the sorts make it independent of the mirror's ordering, but the mirror
    # changes daily, so the saved sample, not a re-draw, is the fixed dataset.
    rng = random.Random(seed)
    sample: list[dict] = []
    for domain in (*DOMAINS, OTHER):
        labels = sorted((k for k in sizes if k[0] == domain), key=str)
        quota = allocate_proportional({k: sizes[k] for k in labels}, domain_quota[domain], floor)
        typer.echo(f'{domain} {domain_quota[domain]}/{sum(sizes[k] for k in labels)}'
                   + (': ' + ', '.join(f'{k[1]} {quota[k]}/{sizes[k]}' for k in labels) if domain != OTHER else ''))
        for k in labels:
            chosen = [by_slug[s] for s, v in kept.items() if v == k]
            chosen += rng.sample(sorted(pools[k], key=lambda c: c['slug']), quota[k] - len(chosen))
            sample += [record(c, k) for c in chosen]
    rng.shuffle(sample)
    return sample


def restore_sample(companies: list[dict]) -> list[dict]:
    """The published sample, rebuilt from today's mirror. Labels stay as published: they are the reference."""
    by_slug = {c['slug']: c for c in companies}
    sample, gone, edited, relabelled = [], [], [], []
    for m in read_jsonl(SAMPLE_PATH):
        c = by_slug.get(m['id'])
        if not c or not c.get('long_description'):
            gone.append(m['name'])
            continue
        row = record(c, (m['label'], m['sub_label']))
        if row['text_sha256'] != m['text_sha256']:
            edited.append(m['name'])
        if reference_label(c) != (m['label'], m['sub_label']):
            relabelled.append(m['name'])
        sample.append(row)
    typer.echo(f'restored {len(sample)} of {len(sample) + len(gone)} companies from {SAMPLE_PATH}')
    for what, names in (('no longer in the mirror, left out', gone),
                        ('description edited since the sample was drawn; new runs see the new text', edited),
                        ('relabelled by YC since; the sample keeps the published label', relabelled)):
        if names:
            typer.echo(f'  {len(names)} {what}: {", ".join(names[:8])}{" ..." if len(names) > 8 else ""}')
    return sample


def require_companies() -> None:
    if not COMPANIES_PATH.exists():
        raise typer.BadParameter(f'{COMPANIES_PATH} is missing; run `domains fetch` to build it from the YC mirror')


@app.command()
def fetch(
    new: bool = typer.Option(False, help=f'draw a fresh sample and overwrite {SAMPLE_PATH}, instead of restoring it'),
    size: int = typer.Option(500, help='with --new: companies in the sample, Other included'),
    per_other: int = typer.Option(30, help='with --new'),
    floor: int = typer.Option(5, help='with --new: minimum per subindustry, where its pool allows'),
    since: int = typer.Option(2019, help='with --new: earliest batch year'),
    keep: list[str] = typer.Option(['roofr'], help='with --new: slugs always in the sample, whatever their batch'),
    seed: int = typer.Option(7, help='with --new'),
    min_description: int = typer.Option(200, help='with --new: minimum long_description length in characters'),
):
    """Build the local company sample from the YC mirror: the published one by default, or a fresh draw."""
    companies = load_mirror()
    if new or not SAMPLE_PATH.exists():
        sample = draw_sample(companies, size, per_other, floor, since, keep, seed, min_description)
        write_jsonl(SAMPLE_PATH, ({k: r[k] for k in SAMPLE_FIELDS} for r in sample))
        typer.echo(f'{len(companies)} companies in the mirror; drew {len(sample)} -> {SAMPLE_PATH}')
    else:
        sample = restore_sample(companies)
    write_jsonl(COMPANIES_PATH, sample)
    typer.echo(f'wrote {len(sample)} companies with descriptions to {COMPANIES_PATH} (local only)')
    estimate_costs(sample)


class SubindustryDefinition(BaseModel):
    subindustry: str
    """The subindustry name, exactly as given."""
    definition: str
    """One sentence, under 30 words: what a company in it sells and to whom, worded so it can be told apart from its siblings."""


class SubindustryDefinitions(BaseModel):
    definitions: list[SubindustryDefinition]
    """One entry per subindustry listed, in the same order."""


DEFINE_INSTRUCTIONS = """You write the taxonomy a venture fund uses to classify companies. A classifier will
read each definition literally to choose between sibling subindustries, so each must state what a
company in it sells and to whom, and draw the boundary with its siblings where they could overlap.
No company names, no examples introduced with 'e.g.', no marketing language. One sentence each."""


@app.command()
def define(
    examples: int = typer.Option(5, help='one-liners per subindustry shown to Sonnet, from companies not in the sample'),
    seed: int = typer.Option(7),
    force: bool = typer.Option(False, help='overwrite an existing, possibly hand-edited, definitions file'),
):
    """Sonnet writes a one-line definition for every subindustry in the sample, once. Edit the file afterwards."""
    if DEFINITIONS_PATH.exists() and not force:
        raise typer.BadParameter(f'{DEFINITIONS_PATH} exists and may be hand-edited; pass --force to regenerate it')
    configure_logfire()
    require_companies()
    sample = read_jsonl(COMPANIES_PATH)
    subs = {d: sorted({r['sub_label'] for r in sample if r['label'] == d}) for d in DOMAINS}
    in_sample = {r['id'] for r in sample}
    mirror = load_mirror()
    rng = random.Random(seed)

    def examples_for(domain: str, leaf: str) -> list[str]:
        # Grounding from outside the sample, so no definition is written around a company it will score.
        pool = sorted((c for c in mirror if c.get('subindustry') == f'{domain} -> {leaf}' and c['slug'] not in in_sample and c.get('one_liner')),
                      key=lambda c: c['slug'])
        return [c['one_liner'] for c in rng.sample(pool, min(examples, len(pool)))]

    writer = make_backends(['sonnet_thinking'])[0]  # writing, not classifying: let it think

    async def one(domain: str):
        lines = [f'Domain: {domain}: {SEED_DOMAIN_DEFINITIONS[domain]}', '', 'Subindustries, with one-liners of real companies in each:']
        for leaf in subs[domain]:
            lines += [f'- {leaf}', *(f'    "{x}"' for x in examples_for(domain, leaf))]
        d = await writer.decide('\n'.join(lines), SubindustryDefinitions, DEFINE_INSTRUCTIONS)
        got = {x.subindustry: x.definition.strip() for x in d.output.definitions}
        if set(got) != set(subs[domain]):
            raise RuntimeError(f'{domain}: Sonnet returned {sorted(got)}, expected {subs[domain]}')
        return domain, got, d

    async def go():
        return await asyncio.gather(*(one(d) for d in DOMAINS))

    results = asyncio.run(go())
    definitions = {d: {'definition': SEED_DOMAIN_DEFINITIONS[d], 'subindustries': {leaf: got[leaf] for leaf in subs[d]}} for d, got, _ in results}
    definitions[OTHER] = {'definition': SEED_DOMAIN_DEFINITIONS[OTHER], 'subindustries': {}}
    DEFINITIONS_PATH.write_text(json.dumps(definitions, indent=2) + '\n')
    usage = [r.to_record() for *_, r in results]
    typer.echo(f'wrote {sum(map(len, subs.values()))} subindustry definitions to {DEFINITIONS_PATH} '
               f'(sonnet, USD {cost(usage, "sonnet"):.4f}). Edit freely; every schema is built from this file.')


@app.command()
def classify(
    runs: str = typer.Option(','.join(domain_runs.RUNS), help='comma list from domains/runs.py'),
    tag: str = typer.Option('', help='suffix for the results files, e.g. rerun for the Jev stability run'),
    limit: int = typer.Option(0, help='only the first N companies (smoke test)'),
    retry_errors: bool = typer.Option(False, help='ask again only the rows that failed (overloaded, timeouts) and merge them in'),
):
    """Run each question shape over the sample, one results file per run."""
    names = runs.split(',')
    if unknown := [n for n in names if n not in domain_runs.RUNS]:
        raise typer.BadParameter(f'unknown runs {unknown}; one of {", ".join(domain_runs.RUNS)}')
    require_companies()
    configure_logfire()
    asyncio.run(domain_runs.run(names, build(load_definitions()), DEMO.records(limit), tag, retry_errors))


if __name__ == '__main__':
    app()


ADJUDICATE_INSTRUCTIONS = (
    INSTRUCTIONS + ' Settle the label carefully: name the domain and subindustry that fit best, a secondary pair only '
    'if the company genuinely operates in two, and say why in one sentence.'
)


@app.command()
def adjudicate(
    runs: str = typer.Option(','.join(domain_runs.MAIN_RUNS),
                             help='a company is disputed when any of these runs disagrees with YC on domain + subindustry'),
    dry_run: bool = typer.Option(False, help='only count disputed companies and estimate the cost'),
    force: bool = typer.Option(False, help='re-label an existing file; rows marked "corrected" are kept as they are'),
    add: bool = typer.Option(False, help='keep every existing label and only label disputed companies without one'),
):
    """Opus labels every disputed company, blind: it sees the company text, not YC's label or any run's pick."""
    require_companies()
    s = build(load_definitions())
    companies = {r['id']: r for r in DEMO.records()}
    picks: dict[str, dict[str, list]] = {}
    for run in runs.split(','):
        for r in read_jsonl(RESULTS_DIR / f'{DEMO.name}.{run}.jsonl'):
            if 'error' not in r:
                picks.setdefault(r['id'], {})[run] = list(domain_runs.pick(run, r['output'], s))
    yc = lambda c: [c['label'], c['sub_label']]
    disputed = [companies[i] for i in companies if any(p != yc(companies[i]) for p in picks.get(i, {}).values())]

    kept = {}
    if ADJUDICATED_PATH.exists():
        if not (force or dry_run or add):
            raise typer.BadParameter(f'{ADJUDICATED_PATH} exists and may be hand-edited; pass --add to label only new disputes, '
                                     'or --force to re-label (corrected rows are kept)')
        kept = {r['id']: r for r in read_jsonl(ADJUDICATED_PATH) if r.get('corrected') or (add and 'error' not in r)}
    todo = [c for c in disputed if c['id'] not in kept]
    estimate = cost(estimated_rows('opus', s.adjudication, records=todo), 'opus')
    typer.echo(f'{len(disputed)} of {len(companies)} companies disputed by at least one of {runs}; '
               f'{len(todo)} to label ({len(kept)} {"existing" if add else "hand-corrected"} kept), estimated USD {estimate:.2f}')
    if dry_run:
        return

    configure_logfire()
    opus = make_backends(['opus'])[0]
    sem = asyncio.Semaphore(concurrency('opus'))

    async def one(c: dict) -> dict:
        row = {'id': c['id'], 'name': c['name'], 'yc': {'domain': c['label'], 'subindustry': c['sub_label']}, 'picks': picks.get(c['id'], {})}
        async with sem:
            try:
                d = (await opus.decide(state(c), s.adjudication, ADJUDICATE_INSTRUCTIONS)).to_record()
            except Exception as e:  # keep the batch alive, record the failure
                return {**row, 'error': repr(e), 'corrected': False}
        return {**row, 'label': d.pop('output'), 'corrected': False,
                **{k: d[k] for k in ('latency_ms', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')}}

    async def go():
        return await asyncio.gather(*(one(c) for c in todo))

    labelled = {r['id']: r for r in asyncio.run(go())}
    rows = [kept.get(c['id']) or labelled[c['id']] for c in disputed]
    write_jsonl(ADJUDICATED_PATH, rows)
    ok = [r for r in rows if 'error' not in r and r['id'] in labelled]
    typer.echo(f'wrote {len(rows)} to {ADJUDICATED_PATH} ({len(rows) - len(ok) - len(kept)} errors), USD {cost(ok, "opus"):.2f}. '
               'Edit freely; set "corrected": true on rows you change.')
