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
)

# System Two output. Lives next to the triage results, so the report has to skip it.
NOTES_PATH = ACT.results_path('notes')
