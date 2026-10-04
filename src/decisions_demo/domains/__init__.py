"""Act three: investing domains. Zero-shot, two-level classification of real companies into a fund's taxonomy.

  domains fetch && domains define && domains classify && domains adjudicate
  report domains
"""

from .act import ACT
from .report import report

__all__ = ['ACT', 'report']
