# Copyright (c) Syntropy Systems
"""whirr - Local experiment orchestration.

Queue jobs, track metrics, and wake up to results.
"""

from whirr.run import Run, init

__version__ = "0.5.2"
__all__ = ["Run", "__version__", "init"]
