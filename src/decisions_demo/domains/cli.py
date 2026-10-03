"""Act three: investing-domain classification of real YC companies.

  domains fetch          # 100 active YC companies, 10 per domain, YC's own label as reference
"""

from __future__ import annotations

import random

import httpx2
import typer

from ..core.act import write_jsonl
from .act import COMPANIES_PATH
from .schemas import Domain

app = typer.Typer(add_completion=False)

# Unofficial mirror of YC's public company directory (github.com/yc-oss/api), refreshed
# daily. Fine for an internal demo; check its terms before using it for anything client-facing.
YC_COMPANIES_URL = 'https://yc-oss.github.io/api/companies/all.json'

DOMAINS = {d.value for d in Domain} - {Domain.other.value}


def reference_label(company: dict) -> Domain | None:
    """The most specific of the nine domains YC's own labels name, Other outside them, None if unlabelled.

    YC's top level has only six of the nine; Infrastructure, Engineering, Product and
    Design, and Marketing are subindustries of B2B ('B2B -> Marketing'), so the leaf wins.
    """
    leaf = (company.get('subindustry') or '').split(' -> ')[-1]
    for name in (leaf, company.get('industry')):
        if name in DOMAINS:
            return Domain(name)
    if company.get('industry') in (None, '', 'Unspecified'):
        return None
    return Domain.other


@app.callback()
def main():
    """Act three: investing-domain classification."""


@app.command()
def fetch(
    per_domain: int = typer.Option(10),
    seed: int = typer.Option(7),
    min_description: int = typer.Option(200, help='minimum long_description length in characters'),
):
    """Sample active YC companies, per_domain from each of the ten labels."""
    companies = httpx2.get(YC_COMPANIES_URL, timeout=60, follow_redirects=True).raise_for_status().json()
    pools: dict[Domain, list[dict]] = {d: [] for d in Domain}
    for c in companies:
        label = reference_label(c)
        if label and c.get('status') == 'Active' and len((c.get('long_description') or '').strip()) >= min_description:
            pools[label].append(c)

    # The seed fixes the draw, the sort makes it independent of the mirror's ordering. The
    # mirror itself changes daily, so data/companies.jsonl, not a re-fetch, is the fixed sample.
    rng = random.Random(seed)
    sample: list[dict] = []
    for label, pool in pools.items():
        if len(pool) < per_domain:
            raise typer.BadParameter(f'only {len(pool)} eligible companies for {label.value}')
        for c in rng.sample(sorted(pool, key=lambda c: c['slug']), per_domain):
            sample.append({
                'id': c['slug'],
                'name': c['name'],
                'one_liner': c['one_liner'],
                'long_description': c['long_description'].strip(),
                'label': label.value,
                'yc_industry': c['industry'],
                'yc_subindustry': c.get('subindustry'),
                'tags': c.get('tags') or [],
                'batch': c.get('batch'),
            })
    rng.shuffle(sample)

    write_jsonl(COMPANIES_PATH, sample)
    typer.echo(f'{len(companies)} companies in the mirror; wrote {len(sample)} to {COMPANIES_PATH} '
               f'({ {d.value: len(p) for d, p in pools.items()} } eligible per label)')


if __name__ == '__main__':
    app()
