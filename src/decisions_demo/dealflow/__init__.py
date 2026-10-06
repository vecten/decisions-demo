"""Demo 2: deal-flow triage. System One triages everything, System Two writes notes for the few.

  dealflow generate && dealflow triage --backends jev,sonnet && dealflow notes
  report dealflow
"""

from .act import ACT
from .report import report

__all__ = ['ACT', 'report']
