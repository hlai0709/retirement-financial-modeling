"""Dark design system for the retirement planner.

Design intent (research-backed):
- Fintech dark UIs use charcoal/navy bases, not pure black (reduces eye fatigue).
- One primary action per screen; progressive disclosure for dense topics.
- Plain-language microcopy at the point of interaction.
- Numbers and charts get highest visual weight; chrome stays quiet.
"""

from gui.design_system.apply import install_theme
from gui.design_system.tokens import COLORS, FONTS, SPACING

__all__ = ["COLORS", "FONTS", "SPACING", "install_theme"]