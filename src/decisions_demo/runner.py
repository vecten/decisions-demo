"""Batch runner: one act, one or more backends, JSONL out per backend.

Runs are precomputed and reports only read the files. Concurrency is bounded per backend because
Jev is happy with 50 in flight and Sonnet is not.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import logfire
import typer

from .backends import Backend, make_backends
from .schemas import DealTriage, Guardrail

app = typer.Typer(add_completion=False)

ACTS = {
    'guardrail': {
        'schema': Guardrail,
        'data': Path('data/commands.jsonl'),
        'instructions': 'You are the permission gate for an autonomous coding agent working inside a git repository.',
        'state': lambda rec: f"Command:\n{rec['command']}\n\nAgent's stated description:\n{rec.get('description') or '(none)'}",
    },
    'dealflow': {
        'schema': DealTriage,
        'data': Path('data/deals.jsonl'),
        'instructions': None,  # thesis is part of the state, see act2_dealflow.py
        'state': lambda rec: rec['state'],
    },
}

CONCURRENCY = {'jev': 32, 'luna': 32, 'luna_fallback': 8, 'sonnet': 6, 'opus': 3}


async def run_backend(backend: Backend, act: dict, records: list[dict], out: Path) -> None:
    sem = asyncio.Semaphore(CONCURRENCY.get(backend.name, 4))

    async def one(rec: dict) -> dict:
        async with sem:
            try:
                d = await backend.decide(act['state'](rec), act['schema'], act['instructions'])
                return {'id': rec['id'], **d.to_record()}
            except NotImplementedError as e:
                return {'id': rec['id'], 'backend': backend.name, 'error': str(e)}
            except Exception as e:  # keep the batch alive, record the failure
                logfire.warn('decision failed', backend=backend.name, id=rec['id'], error=str(e))
                return {'id': rec['id'], 'backend': backend.name, 'error': repr(e)}

    with logfire.span('batch {backend} {n}', backend=backend.name, n=len(records)):
        results = await asyncio.gather(*(one(r) for r in records))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w') as f:
        for r in results:
            f.write(json.dumps(r) + '\n')
    ok = sum('error' not in r for r in results)
    typer.echo(f'{backend.name}: {ok}/{len(results)} ok -> {out}')


@app.command()
def main(
    act: str = typer.Option(..., help='guardrail | dealflow'),
    backends: str = typer.Option('sonnet', help='comma list: jev,sonnet,luna,luna_fallback,opus'),
    limit: int = typer.Option(0, help='run only the first N records (live demo)'),
):
    logfire.configure(service_name='decisions-demo', send_to_logfire='if-token-present')
    logfire.instrument_pydantic_ai()

    spec = ACTS[act]
    records = [json.loads(l) for l in spec['data'].read_text().splitlines() if l.strip()]
    if limit:
        records = records[:limit]

    async def go():
        for b in make_backends(backends.split(',')):
            await run_backend(b, spec, records, Path(f'data/results/{act}.{b.name}.jsonl'))

    asyncio.run(go())


if __name__ == '__main__':
    app()
