"""业务路由汇总：专用路由在前，通用资源路由兜底。"""
from __future__ import annotations

from app.routers import ingest_monitor, resource

ROUTERS = [ingest_monitor, resource]
