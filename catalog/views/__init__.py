"""catalog 视图包：公开只读视图在后续任务逐步加入。"""

from catalog.views.health import healthz

__all__ = ["healthz"]
