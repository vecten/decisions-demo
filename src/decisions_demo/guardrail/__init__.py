"""Demo 1: guardrail. Should an autonomous coding agent run this shell command unattended?

  mine-sessions                              # ~/.claude/projects -> data/commands.jsonl
  label                                      # Opus reference labels -> data/labels.jsonl
  run-demo --demo guardrail --backends sonnet,jev
  report guardrail
"""

from .demo import DEMO
from .report import report

__all__ = ['DEMO', 'report']
