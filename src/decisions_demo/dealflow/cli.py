"""Act two: deal-flow triage. System One triages everything, System Two writes notes for the few.

  dealflow generate      # Sonnet writes 50 synthetic inbound emails with intended labels
  dealflow triage --backends jev,sonnet
  dealflow notes         # Sonnet writes partner notes only for priority >= this_week (per jev)
"""

from __future__ import annotations

import asyncio

import typer
from pydantic import BaseModel
from pydantic_ai import Agent

from ..core.act import read_jsonl, write_jsonl
from ..core.backends import configure_logfire, make_backends
from ..core.runner import run_act
from .act import ACT, DEALS_PATH, NOTES_PATH, THESIS
from .schemas import DealTriage, PartnerNote, Priority

app = typer.Typer(add_completion=False)


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
    configure_logfire()
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
    write_jsonl(DEALS_PATH, (
        {
            'id': f'deal{i:03d}',
            'subject': e.subject,
            'sender': e.sender,
            'body': e.body,
            'state': f'{THESIS}\n\nFrom: {e.sender}\nSubject: {e.subject}\n\n{e.body}',
            'intended_label': e.intended_label.model_dump(),
        }
        for i, e in enumerate(batch.emails)
    ))
    typer.echo(f'wrote {len(batch.emails)} emails to {DEALS_PATH}')


@app.command()
def triage(backends: str = 'jev,sonnet'):
    configure_logfire()
    run_act(ACT, backends.split(','))


@app.command()
def notes(source: str = 'jev', min_priority: int = int(Priority.this_week)):
    """System Two only where System One said it's worth it."""
    configure_logfire()
    deals = {r['id']: r for r in ACT.records()}
    triaged = read_jsonl(ACT.results_path(source))
    chosen = [t for t in triaged if 'error' not in t and int(t['output']['priority']) >= min_priority]
    writer = make_backends(['sonnet'])[0]
    sem = asyncio.Semaphore(6)

    async def one(t):
        async with sem:
            d = await writer.decide(deals[t['id']]['state'], PartnerNote, 'Write for a busy partner. No fluff.')
            return {'id': t['id'], **d.to_record()}

    async def go():
        return await asyncio.gather(*(one(t) for t in chosen))

    write_jsonl(NOTES_PATH, asyncio.run(go()))
    typer.echo(f'{len(chosen)} of {len(triaged)} deals got a partner note -> {NOTES_PATH}')


if __name__ == '__main__':
    app()
