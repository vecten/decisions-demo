"""Demo 2: deal-flow triage. System One triages everything, System Two writes notes for the few.

  dealflow generate      # an OpenAI model writes 50 synthetic inbound emails with intended labels and reasons
  dealflow triage --backends jev,sonnet
  dealflow notes         # Sonnet writes partner notes only for priority >= this_week (per jev)
"""

from __future__ import annotations

import asyncio
import random
import time

import typer
from pydantic import BaseModel
from pydantic_ai import Agent

from ..core.act import read_jsonl, write_jsonl
from ..core.backends import configure_logfire, make_backends
from ..core.runner import run_act
from .act import ACT, DEALS_PATH, GENERATION_PATH, NOTES_PATH, THESIS
from .schemas import DealTriage, PartnerNote, Priority

app = typer.Typer(add_completion=False)


# The emails and their intended labels come from a different model family than the Claude models that
# triage them, so Sonnet is never scored against labels its own family wrote. Newest general OpenAI model
# on the key on 2026-10-06; each email also records the exact id the API reported.
GENERATOR = 'openai:gpt-6.1-sol'
REASONING_EFFORT = 'medium'
BATCH = 10

# The inbox to write, as (kind, how many) and (length, how many); 50 each. Both forwarded threads are warm intros.
KINDS = {
    'in_thesis': (20, 'a cold pitch from a seed or Series A B2B software company in fintech infrastructure, devtools '
                      'or vertical SaaS, based in Europe or the US'),
    'out_of_thesis': (10, 'a pitch the thesis excludes: consumer, hardware, pre-revenue climate, growth or PE, or a '
                          'company outside Europe and the US'),
    'warm_intro': (8, "a founder introduced by, or forwarded from, a founder in the fund's portfolio, who is named"),
    'not_a_deal': (7, 'not an investment opportunity: a vendor, recruiter, conference invite, job seeker or spam'),
    'ambiguous': (5, 'a borderline case: the stage is unclear, or the sector sits at the edge of the thesis'),
}
LENGTHS = {
    'three_line': (12, 'three lines at most: a quick intro and an ask'),
    'short': (14, 'one short paragraph'),
    'medium': (12, 'three or four paragraphs'),
    'long': (10, 'a long founder email: six or more paragraphs covering product, traction numbers, team, the round '
                 'and the ask'),
    'forwarded_thread': (2, 'a forwarded thread: a short note from the person forwarding on top, then the original '
                            'email quoted below it with its own From and Subject lines'),
}


def inbox_plan(seed: int) -> list[tuple[str, str]]:
    """50 (kind, length) slots with the mix above, in a fixed shuffled order."""
    rng = random.Random(seed)
    kinds = [k for k, (n, _) in KINDS.items() for _ in range(n)]
    lengths = [k for k, (n, _) in LENGTHS.items() for _ in range(n) if k != 'forwarded_thread']
    kinds.remove('warm_intro'), kinds.remove('warm_intro')
    rng.shuffle(kinds), rng.shuffle(lengths)
    plan = [('warm_intro', 'forwarded_thread')] * LENGTHS['forwarded_thread'][0] + list(zip(kinds, lengths))
    rng.shuffle(plan)
    return plan


class LabelReasons(BaseModel):
    """One line per label on why the email gets it."""

    stage: str
    sector: str
    fits_thesis: str
    warm_intro: str
    priority: str
    next_action: str


class SyntheticEmail(BaseModel):
    subject: str
    sender: str
    body: str
    """The full email text, at the length the brief asks for."""
    # Reasons before labels: a first run labelled first and then wrote reasons such as "3 (low)" for a scale where
    # 3 is the most urgent. With the reason written first, the label follows from it.
    label_reasons: LabelReasons
    intended_label: DealTriage
    """The labels a careful associate would give this email, given the thesis and the reasons above."""


class SyntheticBatch(BaseModel):
    emails: list[SyntheticEmail]
    """One email per brief, in the order given."""


GENERATE_INSTRUCTIONS = f"""You write realistic inbound email to a venture fund associate, for testing a triage system.
Each email follows the brief you are given for it: what kind of email it is and how long. Vary tone, quality and
writing style; real inboxes have typos, hype, vague asks and buried details. Use invented companies and people.
For each email, first write one line of reason per label in label_reasons, then fill intended_label to match
those reasons, honestly against the fund thesis below, as a careful associate would. Priority is a scale where
higher is more urgent: 0 ignore (not a deal or clearly out of thesis), 1 later (the weekly batch), 2 this week,
3 today (time-sensitive, or a strong thesis fit with a warm intro).

{THESIS}"""


@app.command()
def generate(n: int = 50, seed: int = 7):
    """The generator writes the inbox in batches of ten until there are exactly n emails, each to a planned brief."""
    configure_logfire()
    plan = inbox_plan(seed)[:n]
    # One batch normally takes about 90 s; a stalled request once took 21 minutes, so cut it off and retry.
    writer = Agent(GENERATOR, output_type=SyntheticBatch, instructions=GENERATE_INSTRUCTIONS, model_settings={'timeout': 300, 'openai_reasoning_effort': REASONING_EFFORT})
    emails: list[tuple[SyntheticEmail, tuple[str, str], str]] = []
    usage: list[dict] = []
    for attempt in range(3 * n // BATCH):  # a batch can come back short; the next one picks up where it stopped
        if len(emails) >= n:
            break
        briefs = plan[len(emails):len(emails) + BATCH]
        written = '; '.join(f'{e.sender} ({e.subject})' for e, _, _ in emails) or 'none yet'
        prompt = (f'Write {len(briefs)} emails, one per brief, in this order:\n'
                  + '\n'.join(f'{i + 1}. {KINDS[k][1]}. Length: {LENGTHS[length][1]}.' for i, (k, length) in enumerate(briefs))
                  + f'\n\nDo not reuse a company or sender from the emails already written: {written}')
        t0 = time.perf_counter()
        result = writer.run_sync(prompt)
        got = result.output.emails[:len(briefs)]
        emails += [(e, brief, result.response.model_name) for e, brief in zip(got, briefs)]
        usage.append({'id': f'batch{attempt}', 'model': result.response.model_name, 'emails': len(got),
                      'latency_ms': round((time.perf_counter() - t0) * 1000, 1), 'input_tokens': result.usage.input_tokens or 0,
                      'output_tokens': result.usage.output_tokens or 0, 'cache_read_tokens': result.usage.cache_read_tokens or 0,
                      'cache_write_tokens': result.usage.cache_write_tokens or 0})
        typer.echo(f'batch {attempt}: {len(got)} of {len(briefs)} emails ({len(emails)}/{n})')
    if len(emails) < n:
        raise typer.Exit(f'only {len(emails)} of {n} emails after {len(usage)} batches; nothing written')

    write_jsonl(DEALS_PATH, (
        {
            'id': f'deal{i:03d}',
            'generator': {'requested': GENERATOR, 'model': model, 'reasoning_effort': REASONING_EFFORT},
            'brief': {'kind': kind, 'length': length},
            'subject': e.subject,
            'sender': e.sender,
            'body': e.body,
            'state': f'{THESIS}\n\nFrom: {e.sender}\nSubject: {e.subject}\n\n{e.body}',
            'intended_label': e.intended_label.model_dump(),
            'label_reasons': e.label_reasons.model_dump(),
        }
        for i, (e, (kind, length), model) in enumerate(emails)
    ))
    write_jsonl(GENERATION_PATH, usage)
    typer.echo(f'wrote {len(emails)} emails to {DEALS_PATH}, generation usage to {GENERATION_PATH}')


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
    # Writing, not classifying: the note writer thinks, whatever the triage baseline does.
    writer = make_backends(['sonnet_thinking'])[0]
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
