from __future__ import annotations

from pathlib import Path

from ..core.act import Act

COMPANIES_PATH = Path('data/companies.jsonl')
# Hand-editable taxonomy: one line per domain and per sampled subindustry. Every option
# description and Noul criterion is built from it, so this is where a fund's own definitions go.
DEFINITIONS_PATH = Path('data/domain_definitions.json')
# Opus's label for every company a run disputed with YC. Hand-editable: set "corrected": true on rows you change.
ADJUDICATED_PATH = Path('data/domain_adjudicated.jsonl')

INSTRUCTIONS = "You classify companies into a venture fund's investing domains."


def state(rec: dict) -> str:
    """Short on purpose: what the company says it is, nothing YC added."""
    return f"Company: {rec['name']}\nOne-liner: {rec['one_liner']}\n\n{rec['long_description']}"


ACT = Act(
    name='domains',
    data=COMPANIES_PATH,
    state=state,
    instructions=INSTRUCTIONS,
    # No single schema: each run asks its own question shape, see domains/runs.py and `domains classify`.
    # Sonnet runs without thinking here, so it gets the schema as a tool, verbatim, with the same options
    # Jev sees, and cached. `sonnet_thinking` runs on a 100-company subset for comparison.
    sonnet_thinking=False,
)
