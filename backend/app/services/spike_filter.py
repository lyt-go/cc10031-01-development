"""尖峰剔除领域服务：维护超出量程与偏离倍数判定的尖峰采样识别与处置结论。
对传感器时序采样数据进行尖峰检测并记录异常采样明细数据集。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SpikeFilterService(BaseModuleService):
    MODULE = "spike_filter"
    ENTITY = "异常采样"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["异常编号", "测点编号", "采样时刻", "观测值", "量程上限", "偏离倍数", "判定窗口", "处置结论"]
    REQUIRED_FIELDS = ["异常编号", "测点编号", "采样时刻"]
    STATUS_ORDER = ["草稿", "待判定", "已判定", "已恢复"]
    ACTION_RULES = {"提交判定": "待判定", "确认剔除": "已判定", "标记恢复": "已恢复"}
    NEGATIVE_ACTIONS = []

    def detect_spikes(self, point_key: str, threshold_multiplier: float = 3.0,
                      # 数据校验操作：扫描标定后时序采样数据检测超出量程与偏离倍数的尖峰异常采样点
                      range_limit: float = 100.0) -> dict[str, Any]:
        """扫描标定后的时序采样数据检测超出量程与偏离倍数的尖峰异常采样点。"""
        cal_file = self._data_dir("calibrated") / f"{point_key}_calibrated.jsonl"
        records = self._read_jsonl(cal_file)
        if not records:
            edge_file = self._data_dir("edge_samples") / f"{point_key}.jsonl"
            records = self._read_jsonl(edge_file)
        values = []
        for r in records:
            vals = [float(v) for v in [r.get("calibrated_value_V", 0), r.get("raw_value_mV", 0)] if float(v) > 0.001]
            if vals:
                values.append(vals[0])
        if not values:
            return {"point": point_key, "total": len(records), "spikes": 0}
        mean_val = sum(values) / len(values)
        spikes = []
        for r in records:
            val = float(r.get("calibrated_value_V", r.get("raw_value_mV", 0)))
            deviation = abs(val - mean_val) / max(abs(mean_val), 0.001)
            if deviation > threshold_multiplier or abs(val) > range_limit:
                spikes.append({
                    "异常编号": f"SPIKE-{point_key}-{len(spikes)+1}",
                    "测点编号": point_key,
                    "采样时刻": r.get("timestamp", r.get("时间", "")),
                    "观测值": str(val),
                    "量程上限": str(range_limit),
                    "偏离倍数": f"{deviation:.2f}",
                    "判定窗口": f"{threshold_multiplier}x 均值",
                    "处置结论": "爬虫拦截标记",
                })
        # 将尖峰异常采样写入异常采样明细数据文件
        if spikes:
            self._write_jsonl(
                self._data_dir("spikes_detected") / f"{point_key}_spikes.jsonl",
                spikes
            )
        return {"point": point_key, "total_records": len(records),
                "mean": round(mean_val, 4), "spike_count": len(spikes)}

    def filter_anomalous(self, spike_id: int) -> dict[str, Any]:
        """对单个异常采样记录执行尖峰剔除判定，将正常数据写入清洗后序列文件。"""
        entry = store.find(self.MODULE, spike_id)
        if entry is None:
            return {"error": f"异常采样 {spike_id} 不存在"}
        point_key = entry["测点编号"]
        # 判定并标记
        self.run_action(spike_id, "确认剔除")
        # 读取标定数据并过滤掉异常采样点
        cal_file = self._data_dir("calibrated") / f"{point_key}_calibrated.jsonl"
        records = self._read_jsonl(cal_file)
        spike_ts = entry.get("采样时刻", "")
        filtered = [r for r in records if r.get("timestamp", "") != spike_ts]
        # 将清洗后的数据写入过滤后序列数据文件
        clean_file = self._data_dir("filtered_series") / f"{point_key}_filtered.jsonl"
        written = self._write_jsonl(clean_file, filtered)
        return {"spike_id": entry["异常编号"], "point": point_key,
                "filtered_count": written, "removed_at": spike_ts}