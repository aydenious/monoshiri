"""Self-gated approval primitive — tools that pause from inside their own body.

Public surface:
- :func:`request_approval` — entry point for sensitive tool bodies.
- :class:`HITLResult` — outcome contract.
"""

from .request import request_approval
from .result import HITLResult

__all__ = [
    "HITLResult",
    "request_approval",
]
