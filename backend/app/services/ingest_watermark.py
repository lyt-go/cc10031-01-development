"""接入水位领域服务：维护时序接入的事件时间与处理时间双水位推进与迟到点数统计。
更新传感器接入事件水位并检测迟到数据写入滞后记录数据集。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class IngestWatermarkService(BaseModuleService):
    MODULE = "ingest_watermark"
    ENTITY = "水位标记"
    OPERATION_TERM = "接入"
    LIST_FIELDS = ["水位编号", "测点编号", "事件时间水位", "处理时间水位", "滞后秒数", "迟到点数", "推进方式", "检查时间"]
    REQUIRED_FIELDS = ["水位编号", "测点编号", "事件时间水位"]
    STATUS_ORDER = ["草稿", "正常", "滞后", "已停止"]
    ACTION_RULES = {"标记正常": "正常", "标记滞后": "滞后", "停止推进": "已停止"}
    # 数据接入操作：推进传感器测点接入事件时间水位并检测迟到采样数据
    NEGATIVE_ACTIONS = ["标记滞后", "停止推进"]

    def advance_watermark(self, watermark_id: int) -> dict[str, Any]:
        """推进传感器测点的接入事件时间水位并检测迟到采样数据，写入水位推进日志文件。"""
        wm = store.find(self.MODULE, watermark_id)
        if wm is None:
            return {"error": f"水位标记 {watermark_id} 不存在"}
        point_key = wm["测点编号"]
        event_time = wm.get("事件时间水位", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()))
        process_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

        # 检查上游边缘缓冲的采样数据是否有迟到
        edge_file = self._data_dir("edge_samples") / f"{point_key}.jsonl"
        samples = self._read_jsonl(edge_file)
        late_count = 0
        for s in samples:
            ts = s.get("timestamp", "")
            if ts and ts < event_time:
                late_count += 1

        lag = 0 if late_count == 0 else int(30 + late_count * 0.1)

        # 数据水位输出：记录水位推进到接入水位日志数据文件
        if lag > 60:
            self.run_action(watermark_id, "标记滞后")
            status = "滞后"
        else:
            self.run_action(watermark_id, "标记正常")
            status = "正常"

        wm["处理时间水位"] = process_time
        wm["滞后秒数"] = str(lag)
        wm["迟到点数"] = str(late_count)
        wm["检查时间"] = process_time

        # 记录水位推进到接入水位日志文件
        self._write_jsonl(
            self._data_dir("watermark_logs") / f"{point_key}_watermark.jsonl",
            [{"point": point_key, "event_time": event_time, "process_time": process_time,
              "lag_s": lag, "late_points": late_count, "status": status, "updated_at": time.time()}]
        )
        return {"point": point_key, "event_time": event_time, "process_time": process_time,
                "lag_seconds": lag, "late_points": late_count, "status": status}

    def check_lag(self, point_key: str) -> dict[str, Any]:
        """检测指定测点所有接入水位记录的滞后数据汇总。"""
        all_wm = [r for r in store.rows(self.MODULE) if r.get("测点编号") == point_key]
        results = []
        for wm in all_wm:
            r = self.advance_watermark(wm["id"])
            results.append(r)
        lagging = [r for r in results if r.get("status") == "滞后"]
        self._write_jsonl(
            self._data_dir("watermark_logs") / f"{point_key}_lag_summary.jsonl",
            results
        )
        return {"point": point_key, "total_watermarks": len(results),
                "lagging_count": len(lagging)}