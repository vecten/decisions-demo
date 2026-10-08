from __future__ import annotations

from pathlib import Path

from ..core.demo import Demo

# The sample as published: ids, names, YC's labels and a hash of each description, no YC text. Committed;
# `domains fetch` rebuilds COMPANIES_PATH from it, and the report reads it directly.
SAMPLE_PATH = Path('data/company_sample.jsonl')
SAMPLE_FIELDS = ('id', 'name', 'label', 'sub_label', 'yc_industry', 'yc_subindustry', 'batch', 'text_sha256')
# The local copy with descriptions, which every model run reads. Not committed: YC's text is not ours to redistribute.
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


DEMO = Demo(
    name='domains',
    data=COMPANIES_PATH,
    state=state,
    instructions=INSTRUCTIONS,
    # No single schema: each run asks its own question shape, see domains/runs.py and `domains classify`.
    # Sonnet runs without thinking here, so it gets the schema as a tool, verbatim, with the same options
    # Jev sees, and cached. `sonnet_thinking` runs on a 100-company subset for comparison.
    sonnet_thinking=False,
)
