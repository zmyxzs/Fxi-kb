"""
fxi.domain - 故事世界领域模型
"""

from .entities import EntityManager
from .events import PlotEvent
from .items import ItemManager
from .ownership import OwnershipTracker
from .phases import PhaseManager
from .relations import RelationManager

__all__ = [
    "EntityManager",
    "PhaseManager",
    "ItemManager",
    "OwnershipTracker",
    "RelationManager",
    "PlotEvent",
]
