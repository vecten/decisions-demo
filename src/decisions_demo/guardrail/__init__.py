"""Act one: guardrail. Should an autonomous coding agent run this shell command unattended?

  mine-sessions                              # ~/.claude/projects -> data/commands.jsonl
  label                                      # Opus 5 reference labels -> data/labels.jsonl
  run-act --act guardrail --backends sonnet,jev
  report guardrail
"""

from .act import ACT
from .report import report

__all__ = ['ACT', 'report']
