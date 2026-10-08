"""Reference labels for the guardrail demo: Opus 5.5 with adaptive thinking and an explanation.

Output goes to data/labels.jsonl. Hand-correct 20-30 of them; the explanation
column makes that fast. Call these "reference labels", not ground truth.

Opus can refuse a command outright (a "cyber" refusal on one of the 100 here). A refused or failed command
keeps its previous label, with the error in "kept_after_error", so one refusal never costs the other 99.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from ..core.demo import read_jsonl, write_jsonl
from ..core.backends import configure_logfire, make_backends
from ..core.report import cost
from .demo import DEMO, LABELS_PATH
from .schemas import GuardrailLabel

app = typer.Typer(add_completion=False)


@app.command()
def main(out: Path = typer.Option(LABELS_PATH)):
    configure_logfire('decisions-demo-labels')
    records = DEMO.records()
    judge = make_backends(['opus'])[0]
    sem = asyncio.Semaphore(3)

    previous = {r['id']: r for r in read_jsonl(out)} if out.exists() else {}

    async def one(rec):
        async with sem:
            try:
                return rec, await judge.decide(DEMO.state(rec), GuardrailLabel, DEMO.instructions), None
            except Exception as e:  # a refusal or API error must not sink the rest of the batch
                return rec, None, repr(e)

    async def go():
        return await asyncio.gather(*(one(r) for r in records))

    labels, decisions, failed = [], [], []
    for rec, d, error in asyncio.run(go()):
        if d:
            decisions.append(d.to_record())
            labels.append({'id': rec['id'], 'command': rec['command'], 'label': d.output.model_dump(),
                           'model': judge.model, 'corrected': False})
        else:
            failed.append(rec['id'])
            if rec['id'] in previous:
                labels.append({**previous[rec['id']], 'kept_after_error': error})
            typer.echo(f'{rec["id"]}: {error}', err=True)
    write_jsonl(out, labels)
    typer.echo(f'wrote {len(labels)} labels to {out} ({len(failed)} failed, previous label kept where there was one); '
               f'cost ${cost(decisions, judge.name):.2f}. Edit in place; set "corrected": true on the ones you touched.')


if __name__ == '__main__':
    app()
