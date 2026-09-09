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
def create_app(*args, **kwargs):
    """Load the HTTP server lazily to keep registry imports side-effect free."""

    from .server import create_app as _create_app

    return _create_app(*args, **kwargs)


def run_server(*args, **kwargs):
    """Load and run the HTTP server lazily."""

    from .server import run_server as _run_server

    return _run_server(*args, **kwargs)

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
