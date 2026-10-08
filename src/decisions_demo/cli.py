"""Commands that work across demos: run-demo and report.

Each demo lives in its own package and exposes DEMO (what run-demo needs) and
report(results) (what report prints). Adding a demo means adding it to DEMOS.

  run-demo --demo guardrail --backends sonnet,jev
  report guardrail
"""

from __future__ import annotations

from pathlib import Path

import typer

from . import dealflow, domains, guardrail
from .core.demo import RESULTS_DIR
from .core.backends import configure_logfire
from .core.report import load_results
from .core.runner import run_demo

DEMOS = {m.DEMO.name: m for m in (guardrail, dealflow, domains)}


def _demo(name: str):
    if name not in DEMOS:
        raise typer.BadParameter(f'unknown demo {name!r}; one of: {", ".join(DEMOS)}')
    return DEMOS[name]


run_demo_app = typer.Typer(add_completion=False)


@run_demo_app.command()
def run(
    demo: str = typer.Option(..., help=' | '.join(DEMOS)),
    backends: str = typer.Option('sonnet', help='comma list: jev,sonnet,luna,luna_fallback,opus'),
    limit: int = typer.Option(0, help='run only the first N records (quick check)'),
):
    """Batch one demo through one or more backends, JSONL out per backend."""
    spec = _demo(demo).DEMO
    configure_logfire()
    run_demo(spec, backends.split(','), limit)


report_app = typer.Typer(add_completion=False)


@report_app.command()
def report(demo: str = typer.Argument(...), results_dir: Path = typer.Option(RESULTS_DIR)):
    """Scoreboard and demo-specific tables. Reads only data/, never calls a model."""
    _demo(demo).report(load_results(demo, results_dir))
