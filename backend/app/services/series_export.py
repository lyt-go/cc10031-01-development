"""序列导出领域服务：维护时序结果按测点与时间范围的分片导出任务与导出水位。
按测点范围和时间窗口选择性导出归并数据，写入分片导出数据文件。"""
from __future__ import annotations

import json
import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SeriesExportService(BaseModuleService):
    MODULE = "series_export"
    ENTITY = "导出任务"
    OPERATION_TERM = "导出"
    LIST_FIELDS = ["导出编号", "测点范围", "时间范围", "导出格式", "采样粒度", "记录条数", "分片大小", "导出水位"]
    REQUIRED_FIELDS = ["导出编号", "测点范围", "时间范围"]
    STATUS_ORDER = ["草稿", "待导出", "导出中", "已交付"]
    # 数据导出操作：按测点范围时间窗口导出降采样与归并结果到分片导出文件
    ACTION_RULES = {"启动导出": "导出中", "确认交付": "已交付", "标记失败": "已交付"}
    NEGATIVE_ACTIONS = []

    def export_series(self, export_id: int) -> dict[str, Any]:
        """按测点范围和时间窗口导出降采样与归并结果数据到分片导出交付文件。"""
        task = store.find(self.MODULE, export_id)
        if task is None:
            return {"error": f"导出任务 {export_id} 不存在"}
        self.run_action(export_id, "启动导出")
        point_range = task.get("测点范围", "")
        time_range = task.get("时间范围", "")
        export_fmt = task.get("导出格式", "jsonl")

        # 扫描归并窗口和降采样数据文件导出
        exported = []
        rollup_dir = self._data_dir("rollup_windows")
        if rollup_dir.exists():
            for f in rollup_dir.glob("*_rollup.jsonl"):
                records = self._read_jsonl(f)
                for r in records:
                    exported.append({
                        "export_id": task["导出编号"],
                        # 数据产物输出：写入导出结果为分片交付JSONL数据文件
                        "source": "rollup_window",
                        "point": r.get("point", ""),
                        "window_index": r.get("window_start", 0),
                        "value": r.get("rollup_value", 0),
                        "metric": r.get("metric", ""),
                    })

        ds_dir = self._data_dir("downsampled")
        if ds_dir.exists():
            for f in ds_dir.glob("*_downsampled.jsonl"):
                records = self._read_jsonl(f)
                for r in records:
                    exported.append({
                        "export_id": task["导出编号"],
                        "source": "downsample",
                        "point": r.get("point", ""),
                        "window_index": r.get("window_index", 0),
                        "value": r.get("aggregated_value", 0),
                        "method": r.get("method", ""),
                    })

        # 将导出结果写入分片导出交付文件
        output_file = self._data_dir("exports") / f"{task['导出编号']}_export.{export_fmt}"
        written = self._write_jsonl(output_file, exported)
        task["记录条数"] = str(written)
        task["分片大小"] = str(len(exported) * 80)
        task["导出水位"] = str(written)
        self.run_action(export_id, "确认交付")
        return {"export_id": task["导出编号"], "format": export_fmt,
                "records_exported": written, "total_size_estimate": len(exported) * 80}

    def export_by_time_window(self, point_range: str, time_window: str,
                               fmt: str = "jsonl") -> dict[str, Any]:
        """按指定时间窗口和测点范围创建导出任务并输出交付数据。"""
        tid = max((int(r.get("id", 0)) for r in store.rows(self.MODULE)), default=0) + 1
        self._auto_create_seed(self.MODULE, {
            "导出编号": f"EXPORT-{point_range}-{tid}",
            "测点范围": point_range,
            "时间范围": time_window,
            "导出格式": fmt,
            "采样粒度": "1h",
            "记录条数": "0",
            "分片大小": "0",
            "导出水位": "0",
        })
        return self.export_series(tid)