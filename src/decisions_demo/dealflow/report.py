"""Deal-flow report: scoreboard vs the generator's intended labels, plus what the System Two half cost."""

from __future__ import annotations

from ..core.report import console, cost, load, scoreboard
from .act import ACT, DEALS_PATH, NOTES_PATH


def report(results: dict[str, dict[str, dict]]) -> None:
    results.pop('notes', None)  # partner notes, not triage results
    deals = load(DEALS_PATH)
    labels = {k: {'label': v['intended_label']} for k, v in deals.items()}
    scoreboard(ACT, results, labels)
    notes = load(NOTES_PATH)
    if notes:
        console.print(f'partner notes written for {len(notes)} deals, cost USD {cost(list(notes.values()), "sonnet"):.4f}')
