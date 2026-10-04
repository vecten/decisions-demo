"""Commands that work across acts: run-act and report.

Each act lives in its own package and exposes ACT (what run-act needs) and
report(results) (what report prints). Adding an act means adding it to ACTS.

  run-act --act guardrail --backends sonnet,jev
  report guardrail
"""

from __future__ import annotations

from pathlib import Path

import typer

from . import dealflow, domains, guardrail
from .core.act import RESULTS_DIR
from .core.backends import configure_logfire
from .core.report import load_results
from .core.runner import run_act

ACTS = {m.ACT.name: m for m in (guardrail, dealflow, domains)}


def _act(name: str):
    if name not in ACTS:
        raise typer.BadParameter(f'unknown act {name!r}; one of: {", ".join(ACTS)}')
    return ACTS[name]


run_act_app = typer.Typer(add_completion=False)


@run_act_app.command()
def run(
    act: str = typer.Option(..., help=' | '.join(ACTS)),
    backends: str = typer.Option('sonnet', help='comma list: jev,sonnet,luna,luna_fallback,opus'),
    limit: int = typer.Option(0, help='run only the first N records (live demo)'),
):
    """Batch one act through one or more backends, JSONL out per backend."""
    spec = _act(act).ACT
    configure_logfire()
    run_act(spec, backends.split(','), limit)


report_app = typer.Typer(add_completion=False)


@report_app.command()
def report(act: str = typer.Argument(...), results_dir: Path = typer.Option(RESULTS_DIR)):
    """Scoreboard and act-specific tables. Reads only data/, never calls a model."""
    _act(act).report(load_results(act, results_dir))
