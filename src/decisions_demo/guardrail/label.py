"""Reference labels for the guardrail act: Opus 5 with extended thinking and an explanation.

Output goes to data/labels.jsonl. Hand-correct 20-30 of them; the explanation
column makes that fast. Call these "reference labels", not ground truth.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from ..core.act import write_jsonl
from ..core.backends import configure_logfire, make_backends
from .act import ACT, LABELS_PATH
from .schemas import GuardrailLabel

app = typer.Typer(add_completion=False)


@app.command()
def main(out: Path = typer.Option(LABELS_PATH)):
    configure_logfire('decisions-demo-labels')
    records = ACT.records()
    judge = make_backends(['opus'])[0]
    sem = asyncio.Semaphore(3)

    async def one(rec):
        async with sem:
            d = await judge.decide(ACT.state(rec), GuardrailLabel, ACT.instructions)
            return {'id': rec['id'], 'command': rec['command'], 'label': d.output.model_dump(), 'corrected': False}

    async def go():
        return await asyncio.gather(*(one(r) for r in records))

    labels = asyncio.run(go())
    write_jsonl(out, labels)
    typer.echo(f'wrote {len(labels)} labels to {out}. Edit in place; set "corrected": true on the ones you touched.')


if __name__ == '__main__':
    app()
