"""
whirr - Local experiment orchestration.

Queue jobs, track metrics, wake up to results.
"""

from whirr.run import Run, init

__version__ = "0.2.0"
__all__ = ["init", "Run", "__version__"]
