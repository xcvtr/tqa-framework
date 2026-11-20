"""ir — impulse_retrace strategy for tqa-f.

Ported from tqa-c impulse_retrace (Bybit, 5m).
Mechanics: impulse wave + scale-in on retrace + pyramiding + trailing stop.
"""
from .detect import detect