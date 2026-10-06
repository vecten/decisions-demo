from __future__ import annotations

from pathlib import Path

from ..core.act import Act
from .schemas import DealTriage

THESIS = """Fund thesis: seed and Series A, B2B software only (fintech infrastructure, devtools,
vertical SaaS). Europe and US. No consumer, no hardware, no pre-revenue climate. Ticket 1-4M EUR.
We take warm intros from portfolio founders seriously."""

DEALS_PATH = Path('data/deals.jsonl')

ACT = Act(
    name='dealflow',
    schema=DealTriage,
    data=DEALS_PATH,
    instructions=None,  # thesis is part of the state, see cli.generate
    state=lambda rec: rec['state'],
    compare_fields=('stage', 'sector', 'fits_thesis', 'warm_intro', 'priority', 'next_action'),
    # Triage without thinking, the same baseline as demo 3: the schema goes to Sonnet as a tool, as Jev sees it.
    # The partner notes are writing, so `dealflow notes` uses `sonnet_thinking` regardless.
    sonnet_thinking=False,
)

# System Two output and the generator's usage. They live next to the triage results, so the report skips them.
NOTES_PATH = ACT.results_path('notes')
GENERATION_PATH = ACT.results_path('generation')
