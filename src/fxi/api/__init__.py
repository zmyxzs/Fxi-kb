"""
fxi.api - 本地 REST API 服务
"""

from .contracts import (
    CanonCheckRequest,
    CanonCheckResponse,
    ContextAssembleRequest,
    ContextAssembleResponse,
    OOCCheckRequest,
    OOCCheckResponse,
    RippleQueryRequest,
    RippleQueryResponse,
    StateQueryRequest,
    StateQueryResponse,
)
from .server import create_app, run_server

__all__ = [
    "create_app",
    "run_server",
    "ContextAssembleRequest",
    "ContextAssembleResponse",
    "CanonCheckRequest",
    "CanonCheckResponse",
    "RippleQueryRequest",
    "RippleQueryResponse",
    "StateQueryRequest",
    "StateQueryResponse",
    "OOCCheckRequest",
    "OOCCheckResponse",
]
