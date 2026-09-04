"""
fxi.core.constants - 全局物理与业务常量
"""

DEFAULT_CONTEXT_BUDGET: int = 3500
MAX_CONTEXT_BUDGET: int = 4000
MIN_CONTEXT_BUDGET: int = 1500

VRAM_SAFE_LIMIT_MB: int = 2048

# 场景切片阈值 (字符数)
SCENE_CHUNK_MIN_CHARS: int = 512
SCENE_CHUNK_MAX_CHARS: int = 1024

# 快捷技能栏最大装备数 (Active Deck)
DEFAULT_MAX_ACTIVE_SKILLS: int = 6
