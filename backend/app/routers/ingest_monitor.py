"""接入监控专用路由：班次看板、覆盖对照、补采申请派发与实收核对。

这些路径比通用资源路由更具体，必须先于 resource 路由注册，避免被
/api/{module}/{entry_id} 的动态路径抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.ingest_monitor import IngestMonitorService

router = APIRouter(prefix="/api/ingest_monitor", tags=["接入监控"])


def svc() -> IngestMonitorService:
    from app.services import service_for
    service = service_for("ingest_monitor")
    assert isinstance(service, IngestMonitorService)
    return service


class OpenRequestPayload(BaseModel):
    shift: str
    point: str | None = None


class ClaimPayload(BaseModel):
    claimant: str = "值班员"


class ReceiptPayload(BaseModel):
    timestamp: str
    point: str | None = None
    source: str = "edge-late"


@router.get("/board")
def shift_board(point: str | None = None) -> dict[str, Any]:
    """班次看板：各班实际观测、应收数与仍缺前后两段时刻。"""
    return svc().shift_board(point)


@router.get("/coverage")
def coverage_report(point: str | None = None) -> dict[str, Any]:
    """采样覆盖对照：按规则版本核算应收/实收/边缘等待区间。"""
    return svc().coverage_report(point)


@router.get("/requests")
def list_requests(point: str | None = None) -> dict[str, Any]:
    return {"items": svc().list_requests(point)}


@router.get("/ledger")
def dispatch_ledger() -> dict[str, Any]:
    return {"items": svc().dispatch_ledger()}


@router.post("/requests/open")
def open_request(payload: OpenRequestPayload) -> dict[str, Any]:
    return svc().open_capture_request(payload.point or IngestMonitorService.DEMO_POINT, payload.shift)


@router.post("/requests/{request_id}/issue")
def issue_request(request_id: int) -> dict[str, Any]:
    return svc().issue_capture_request(request_id)


@router.post("/requests/{request_id}/claim")
def claim_request(request_id: int, payload: ClaimPayload) -> dict[str, Any]:
    return svc().claim_dispatch(request_id, payload.claimant)


@router.post("/receipts")
def ingest_receipt(payload: ReceiptPayload) -> dict[str, Any]:
    """独立实收核对入口：迟到旧包也走这里，重复时刻自动去重。"""
    return svc().ingest_observation(
        payload.point or IngestMonitorService.DEMO_POINT, payload.timestamp, payload.source)


@router.post("/reset")
def reset_engine() -> dict[str, Any]:
    service = svc()
    service.reset()
    return {"ok": True, "message": "接入监控已重置，下次查询将重新装配演示场景"}
