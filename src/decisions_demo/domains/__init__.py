"""Demo 3: investing domains. Zero-shot, two-level classification of real companies into a fund's taxonomy.

  domains fetch && domains define && domains classify && domains adjudicate
  report domains
"""

from .demo import DEMO
from .report import report

__all__ = ['DEMO', 'report']
