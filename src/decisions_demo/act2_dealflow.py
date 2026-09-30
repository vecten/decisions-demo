"""Act two: deal-flow triage. System One triages everything, System Two writes notes for the few.

  dealflow generate      # Sonnet writes 50 synthetic inbound emails with intended labels
  dealflow triage --backends jev,sonnet
  dealflow notes         # Sonnet writes partner notes only for priority >= this_week (per jev)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import logfire
import typer
from pydantic import BaseModel
from pydantic_ai import Agent

from .backends import make_backends
from .runner import ACTS, run_backend
from .schemas import DealTriage, PartnerNote, Priority

app = typer.Typer(add_completion=False)

THESIS = """Fund thesis: seed and Series A, B2B software only (fintech infrastructure, devtools,
vertical SaaS). Europe and US. No consumer, no hardware, no pre-revenue climate. Ticket 1-4M EUR.
We take warm intros from portfolio founders seriously."""

DEALS_PATH = Path('data/deals.jsonl')


class SyntheticEmail(BaseModel):
    subject: str
    sender: str
    body: str
    intended_label: DealTriage


class SyntheticBatch(BaseModel):
    emails: list[SyntheticEmail]


@app.command()
def generate(n: int = 50, seed_note: str = ''):
    """Ask Sonnet for a realistic, varied inbox. The intended_label is our free reference."""
    logfire.configure(service_name='decisions-demo', send_to_logfire='if-token-present')
    logfire.instrument_pydantic_ai()
    writer = Agent(
        'anthropic:claude-sonnet-5',
        output_type=SyntheticBatch,
        instructions=(
            'Write realistic inbound emails to a venture fund associate. Vary length, tone and quality. '
            f'Mix: ~40% in-thesis deals across seed/A, ~20% out-of-thesis (consumer, hardware, growth/PE), '
            '~15% warm intros from portfolio founders, ~15% not deals (vendors, recruiters, spam, conference invites), '
            '~10% deliberately ambiguous (stage unclear, sector borderline). Fill intended_label honestly for each. '
            f'Thesis for reference:\n{THESIS}\n{seed_note}'
        ),
    )
    batch = writer.run_sync(f'Write {n} emails.').output
    DEALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DEALS_PATH.open('w') as f:
        for i, e in enumerate(batch.emails):
            rec = {
                'id': f'deal{i:03d}',
                'subject': e.subject,
                'sender': e.sender,
                'body': e.body,
                'state': f'{THESIS}\n\nFrom: {e.sender}\nSubject: {e.subject}\n\n{e.body}',
                'intended_label': e.intended_label.model_dump(),
            }
            f.write(json.dumps(rec) + '\n')
    typer.echo(f'wrote {len(batch.emails)} emails to {DEALS_PATH}')


@app.command()
def triage(backends: str = 'jev,sonnet'):
    logfire.configure(service_name='decisions-demo', send_to_logfire='if-token-present')
    logfire.instrument_pydantic_ai()
    records = [json.loads(l) for l in DEALS_PATH.read_text().splitlines() if l.strip()]

    async def go():
        for b in make_backends(backends.split(',')):
            await run_backend(b, ACTS['dealflow'], records, Path(f'data/results/dealflow.{b.name}.jsonl'))

    asyncio.run(go())


@app.command()
def notes(source: str = 'jev', min_priority: int = int(Priority.this_week)):
    """System Two only where System One said it's worth it."""
    logfire.configure(service_name='decisions-demo', send_to_logfire='if-token-present')
    logfire.instrument_pydantic_ai()
    deals = {r['id']: r for r in (json.loads(l) for l in DEALS_PATH.read_text().splitlines() if l.strip())}
    triaged = [json.loads(l) for l in Path(f'data/results/dealflow.{source}.jsonl').read_text().splitlines() if l.strip()]
    chosen = [t for t in triaged if 'error' not in t and int(t['output']['priority']) >= min_priority]
    writer = make_backends(['sonnet'])[0]
    sem = asyncio.Semaphore(6)

    async def one(t):
        async with sem:
            d = await writer.decide(deals[t['id']]['state'], PartnerNote, 'Write for a busy partner. No fluff.')
            return {'id': t['id'], **d.to_record()}

    async def go():
        return await asyncio.gather(*(one(t) for t in chosen))

    out = Path('data/results/dealflow.notes.jsonl')
    with out.open('w') as f:
        for r in asyncio.run(go()):
            f.write(json.dumps(r) + '\n')
    typer.echo(f'{len(chosen)} of {len(triaged)} deals got a partner note -> {out}')


if __name__ == '__main__':
    app()
