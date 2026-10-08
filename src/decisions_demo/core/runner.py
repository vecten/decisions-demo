"""Batch runner: one demo, one or more backends, JSONL out per backend.

Runs are precomputed and reports only read the files. Concurrency is bounded per backend because
Jev is happy with 50 in flight and Sonnet is not.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import logfire
import typer

from .demo import Demo, write_jsonl
from .backends import Backend, make_backends

CONCURRENCY = {'jev': 32, 'luna': 32, 'luna_fallback': 8, 'sonnet': 6, 'opus': 3}


def concurrency(backend: str) -> int:
    """By backend name, else by model family: sonnet_thinking runs like sonnet."""
    return CONCURRENCY.get(backend) or CONCURRENCY.get(backend.split('_')[0], 4)


async def run_backend(backend: Backend, demo: Demo, records: list[dict], out: Path) -> None:
    sem = asyncio.Semaphore(concurrency(backend.name))

    async def one(rec: dict) -> dict:
        async with sem:
            try:
                d = await backend.decide(demo.state(rec), demo.schema, demo.instructions)
                return {'id': rec['id'], **d.to_record()}
            except NotImplementedError as e:
                return {'id': rec['id'], 'backend': backend.name, 'error': str(e)}
            except Exception as e:  # keep the batch alive, record the failure
                logfire.warn('decision failed', backend=backend.name, id=rec['id'], error=str(e))
                return {'id': rec['id'], 'backend': backend.name, 'error': repr(e)}

    with logfire.span('batch {backend} {n}', backend=backend.name, n=len(records)):
        results = await asyncio.gather(*(one(r) for r in records))

    write_jsonl(out, results)
    ok = sum('error' not in r for r in results)
    typer.echo(f'{backend.name}: {ok}/{len(results)} ok -> {out}')


def run_demo(demo: Demo, backends: list[str], limit: int = 0) -> None:
    """Run every backend over the demo's records, one after another, each into data/results/<demo>.<backend>.jsonl."""
    if demo.schema is None:
        raise typer.BadParameter(f'demo {demo.name!r} asks several questions; use its own command (e.g. `domains classify`)')
    records = demo.records(limit)

    async def go():
        for b in make_backends(backends, demo.sonnet_thinking):
            await run_backend(b, demo, records, demo.results_path(b.name))

    asyncio.run(go())
