"""
fxi.territory - 宏观据点基业动力学与组织经济模块 (全题材通用)
"""

from .buildings import TerritoryBuildingManager
from .macro_tags import MacroTagGenerator
from .population import TerritoryPopulationManager
from .resources import TerritoryResourceManager

__all__ = [
    "TerritoryResourceManager",
    "TerritoryBuildingManager",
    "TerritoryPopulationManager",
    "MacroTagGenerator",
]
