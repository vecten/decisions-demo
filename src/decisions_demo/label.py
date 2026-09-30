"""Reference labels for the guardrail act: Opus 5 with extended thinking and an explanation.

Output goes to data/labels.jsonl. Hand-correct 20-30 of them; the explanation
column makes that fast. Call these "reference labels", not ground truth.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import logfire
import typer

from .backends import make_backends
from .runner import ACTS
from .schemas import GuardrailLabel

app = typer.Typer(add_completion=False)


@app.command()
def main(out: Path = typer.Option(Path('data/labels.jsonl'))):
    logfire.configure(service_name='decisions-demo-labels', send_to_logfire='if-token-present')
    logfire.instrument_pydantic_ai()
    spec = ACTS['guardrail']
    records = [json.loads(l) for l in spec['data'].read_text().splitlines() if l.strip()]
    judge = make_backends(['opus'])[0]
    sem = asyncio.Semaphore(3)

    async def one(rec):
        async with sem:
            d = await judge.decide(spec['state'](rec), GuardrailLabel, spec['instructions'])
            return {'id': rec['id'], 'command': rec['command'], 'label': d.output.model_dump(), 'corrected': False}

    async def go():
        return await asyncio.gather(*(one(r) for r in records))

    labels = asyncio.run(go())
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w') as f:
        for l in labels:
            f.write(json.dumps(l) + '\n')
    typer.echo(f'wrote {len(labels)} labels to {out}. Edit in place; set "corrected": true on the ones you touched.')


if __name__ == '__main__':
    app()
