"""
whirr - Local experiment orchestration.

Queue jobs, track metrics, wake up to results.
"""

from whirr.run import Run, init

__version__ = "0.5.1"
__all__ = ["init", "Run", "__version__"]
