"""The runs demo 3 makes, each a backend asking one question shape of the sample.

  jev_flat          one Choice over every subindustry; the domain is the subindustry's parent
  jev_sequential    domain Choice, then the sub Choice of the winning domain: two Jev calls
  jev_fanout        domain Choice plus all six sub Choices in one call; sub read from the winner
  jev_nouls         one Noul per domain, the multi-label footprint
  luna_flat, luna_sequential, luna_fanout, luna_nouls
                    the same four shapes on OpenAI's Decisions API (gpt-6-luna), the same questions
  sonnet            nested domain -> subindustry, thinking off (DEMO.sonnet_thinking)
  sonnet_thinking   the same with thinking on, on the first SUBSET companies (the sample is shuffled)

Each writes data/results/domains.<run>.jsonl. A tag (`--tag rerun`) writes domains.<run>_<tag>.jsonl,
which is how the Jev and Luna stability runs sit next to the first ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from pydantic import BaseModel

from ..core.demo import RESULTS_DIR, Demo, read_jsonl, write_jsonl
from ..core.backends import Backend, Decision, make_backends
from ..core.runner import run_backend
from .demo import DEMO
from .schemas import OTHER, Schemas, field_name

DECISION_MODELS = ('jev', 'luna')
SHAPES = ('flat', 'sequential', 'fanout', 'nouls')
RUNS = (*(f'{m}_{shape}' for m in DECISION_MODELS for shape in SHAPES), 'sonnet', 'sonnet_thinking')
CHOICE_RUNS = {m: tuple(f'{m}_{shape}' for shape in SHAPES if shape != 'nouls') for m in DECISION_MODELS}
# Every run that names a subindustry; a company is disputed when any of them disagrees with YC. Luna's runs
# count, so its disagreements with YC get the same adjudication Jev's and Sonnet's do.
MAIN_RUNS = (*(run for m in DECISION_MODELS for run in CHOICE_RUNS[m]), 'sonnet', 'sonnet_thinking')
SUBSET = 100


class SequentialPick(BaseModel):
    domain: str
    subindustry: str | None


@dataclass
class Sequential:
    """Domain first, then the subindustry Choice for the domain it picked: two calls, one result.

    Latency and tokens are the sum of both calls; Other ends after the first.
    """

    backend: Backend
    schemas: Schemas
    name: str = field(init=False)

    def __post_init__(self):
        self.name = self.backend.name  # prices and concurrency follow the backend underneath

    async def decide(self, state: str, output_type=None, instructions: str | None = None) -> Decision:
        first = await self.backend.decide(state, self.schemas.domain, instructions)
        domain = first.output.domain
        parts = [first]
        if domain in self.schemas.sub:
            parts.append(await self.backend.decide(state, self.schemas.sub[domain], instructions))
        merge = lambda attr: {k: v for d in parts for k, v in getattr(d, attr).items()}
        return Decision(
            output=SequentialPick(domain=domain, subindustry=parts[1].output.subindustry if len(parts) > 1 else None),
            confidence=merge('confidence'),
            probabilities=merge('probabilities'),
            latency_ms=sum(d.latency_ms for d in parts),
            input_tokens=sum(d.input_tokens for d in parts),
            output_tokens=sum(d.output_tokens for d in parts),
            backend=self.name,
        )


def shape(run: str) -> str | None:
    """A decision-model run's question shape (jev_flat_rerun -> flat), None for a language-model run."""
    model, _, rest = run.partition('_')
    return rest.split('_')[0] if model in DECISION_MODELS else None


def pick(run: str, output: dict, schemas: Schemas) -> tuple[str, str | None]:
    """(domain, subindustry) from any run's output; subindustry is None for Other. Tags (_rerun) are ignored."""
    if shape(run) == 'flat':
        sub = output['subindustry']
        return (OTHER, None) if sub == OTHER else (schemas.parent[sub], sub)
    if shape(run) == 'fanout':
        domain = output['domain']
        return domain, output.get(f'{field_name(domain)}_subindustry') if domain != OTHER else None
    if run.startswith('sonnet') or run.startswith('opus'):
        c = output.get('classification') or output['primary']
        return c['domain'], c.get('subindustry')
    return output['domain'], output['subindustry']


def plan(schemas: Schemas, records: list[dict]) -> dict[str, tuple[Backend, Demo, list[dict]]]:
    *decision, sonnet, sonnet_thinking = make_backends([*DECISION_MODELS, 'sonnet', 'sonnet_thinking'], DEMO.sonnet_thinking)
    return {
        **{name: run for b in decision for name, run in {
            f'{b.name}_flat': (b, replace(DEMO, schema=schemas.flat), records),
            f'{b.name}_sequential': (Sequential(b, schemas), DEMO, records),
            f'{b.name}_fanout': (b, replace(DEMO, schema=schemas.fan_out), records),
            f'{b.name}_nouls': (b, replace(DEMO, schema=schemas.footprint), records),
        }.items()},
        'sonnet': (sonnet, replace(DEMO, schema=schemas.nested), records),
        'sonnet_thinking': (sonnet_thinking, replace(DEMO, schema=schemas.nested), records[:SUBSET]),
    }


async def run(names: list[str], schemas: Schemas, records: list[dict], tag: str = '', retry_errors: bool = False) -> None:
    """With retry_errors, only the rows that failed last time are asked again and merged into the file."""
    runs = plan(schemas, records)
    for name in names:
        backend, demo, recs = runs[name]
        out = RESULTS_DIR / f'{DEMO.name}.{name}{"_" + tag if tag else ""}.jsonl'
        if not retry_errors:
            await run_backend(backend, demo, recs, out)
            continue
        previous = read_jsonl(out)
        failed = {r['id'] for r in previous if 'error' in r}
        if not failed:
            continue
        retry = out.with_suffix('.retry.jsonl')
        await run_backend(backend, demo, [r for r in recs if r['id'] in failed], retry)
        fresh = {r['id']: r for r in read_jsonl(retry)}
        write_jsonl(out, [fresh.get(r['id'], r) for r in previous])
        retry.unlink()
