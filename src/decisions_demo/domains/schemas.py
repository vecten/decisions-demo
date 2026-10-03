"""Investing-domain output schemas, two levels deep like YC's industry -> subindustry.

Every option description and Noul criterion comes from one definitions dict
(data/domain_definitions.json), which a fund would rewrite in its own words. The
options are only known once that file is read, so they are built at run time as the
anyOf-of-described-consts an Enum with member docstrings becomes (see `options`).

Question shapes over the same taxonomy:
  domain       one Choice over the six domains plus Other
  flat         one Choice over every subindustry (plus Other); the domain is implied
  sub[d]       one Choice over domain d's subindustries, asked after the domain (sequential)
  fan_out      the domain Choice and all six sub Choices in one request; the sub is read
               from the winning domain's field
  nested       Sonnet's structured output: a domain with a subindustry valid for it
  footprint    one Noul per domain; Other has none, it is every Noul being low
  adjudication Opus on disputed companies: primary and optional secondary pick, and why
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, create_model
from pydantic_ai import BoolCriteria

from .act import DEFINITIONS_PATH

OTHER = 'Other'

# Starting point for the domain lines in the definitions file; after `domains define`
# the file is the source and these are not read again.
SEED_DOMAIN_DEFINITIONS = {
    'B2B': 'Software or services sold to businesses, such as developer tools, infrastructure, sales and marketing, HR, legal, security, analytics or supply chain.',
    'Consumer': 'Products individuals buy or use for themselves, such as apps, content, social, gaming, food, apparel, home goods, travel or consumer electronics.',
    'Fintech': 'Financial services or financial infrastructure, such as payments, banking, lending, insurance, asset management or consumer finance.',
    'Healthcare': 'Medicine and health, such as care delivery, healthcare IT, diagnostics, medical devices, therapeutics, drug discovery, wellness or industrial biotech.',
    'Industrials': 'Physical industry, such as manufacturing, robotics, energy, climate, aerospace, defense, drones, automotive or agriculture.',
    'Real Estate and Construction': 'Property and the built environment, such as housing, buying, selling or managing real estate, and construction.',
    OTHER: 'Education, government, nonprofits, or anything none of the six domains above describes.',
}

DOMAIN_QUESTION = 'Which one domain best describes what this company sells and to whom?'


def field_name(domain: str) -> str:
    return re.sub(r'\W+', '_', domain.lower()).strip('_')


def load_definitions(path: Path = DEFINITIONS_PATH) -> dict[str, dict]:
    """{domain: {'definition': str, 'subindustries': {leaf: str}}}, Other last with no subindustries."""
    return json.loads(path.read_text())


@dataclass(frozen=True)
class Schemas:
    domains: tuple[str, ...]
    """The six domains that have subindustries and a Noul, without Other."""
    parent: dict[str, str]
    """Subindustry leaf -> its domain, which is how the flat run gets its level 1."""
    domain: type[BaseModel]
    flat: type[BaseModel]
    sub: dict[str, type[BaseModel]]
    fan_out: type[BaseModel]
    nested: type[BaseModel]
    footprint: type[BaseModel]
    adjudication: type[BaseModel]


def options(described: dict[str, str]):
    """A string that must be one of the keys, each option sent with its description.

    The same shape Pydantic AI's `Choices` produces, plus a `type` on every option: Claude's strict
    mode (used whenever it thinks, see core/backends.py) rejects options without one.
    """
    return Annotated[
        Literal[tuple(described)],
        WithJsonSchema({'type': 'string', 'anyOf': [{'type': 'string', 'const': k, 'description': v} for k, v in described.items()]}),
    ]


def _model(name: str, doc: str, **fields) -> type[BaseModel]:
    return create_model(name, __doc__=doc, __config__=ConfigDict(use_attribute_docstrings=True), **fields)


def build(defs: dict[str, dict]) -> Schemas:
    domains = tuple(d for d in defs if d != OTHER)
    subs = {d: defs[d]['subindustries'] for d in domains}
    parent = {leaf: d for d in domains for leaf in subs[d]}

    domain_choice = options({d: v['definition'] for d, v in defs.items()})
    sub_choice = {d: options(subs[d]) for d in domains}
    flat_choice = options({**{leaf: f'{parent[leaf]}: {text}' for d in domains for leaf, text in subs[d].items()}, OTHER: defs[OTHER]['definition']})

    def sub_field(d: str, question: str):
        return Annotated[sub_choice[d], Field(description=question)]

    def noul(d: str):
        return Annotated[bool, BoolCriteria(true=f'The company operates in {d}: {defs[d]["definition"]}', false=f'Nothing the company does falls under {d}.'), Field(description=f'Does this company operate in {d}?')]

    # Sonnet picks the domain and a subindustry that belongs to it in one object; the
    # discriminated union makes an invalid pair (Fintech -> Gaming) unrepresentable.
    picks = [
        _model(f'{field_name(d)}_pick', f'A company in {d}.',
               domain=(Literal[d], ...), subindustry=sub_field(d, f'Which {d} subindustry best describes what this company sells and to whom?'))
        for d in domains
    ] + [_model('other_pick', f'{OTHER}: {defs[OTHER]["definition"]}', domain=(Literal[OTHER], ...))]
    pick = Annotated[Union[tuple(picks)], Field(discriminator='domain')]

    return Schemas(
        domains=domains,
        parent=parent,
        domain=_model('DomainChoice', "Assign the company to the single investing domain that fits it best, given the fund's definitions.",
                      domain=Annotated[domain_choice, Field(description=DOMAIN_QUESTION)]),
        flat=_model('SubindustryChoice', "Assign the company to the single investing subindustry that fits it best, given the fund's definitions.",
                    subindustry=Annotated[flat_choice, Field(description='Which one subindustry best describes what this company sells and to whom?')]),
        sub={d: _model(f'{field_name(d)}_SubindustryChoice', f'The company is in {d}. Assign it to the {d} subindustry that fits it best.',
                       subindustry=sub_field(d, f'Which {d} subindustry best describes what this company sells and to whom?'))
             for d in domains},
        fan_out=_model('DomainFanOut', "Assign the company to the single investing domain that fits it best, and for each domain, the subindustry it would be in if it were in that domain.",
                       domain=Annotated[domain_choice, Field(description=DOMAIN_QUESTION)],
                       **{f'{field_name(d)}_subindustry': sub_field(d, f'If this company were in {d}, which {d} subindustry would fit it best?') for d in domains}),
        nested=_model('DomainClassification', "Assign the company to the single investing domain and subindustry that fit it best, given the fund's definitions.",
                      classification=pick),
        footprint=_model('DomainFootprint', 'For each investing domain, decide independently whether the company operates in it. Several may be true, or none.',
                         **{field_name(d): noul(d) for d in domains}),
        adjudication=_model('Adjudication', "Settle which investing domain and subindustry fit this company, given the fund's definitions.",
                            primary=(pick, Field(description='The domain and subindustry that fit best.')),
                            secondary=(pick | None, Field(None, description='A second domain and subindustry that also genuinely fit, or null if none does.')),
                            explanation=(str, Field(description='One sentence on why, naming what the company sells and to whom.'))),
    )
