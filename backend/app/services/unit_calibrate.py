"""单位标定领域服务：维护传感器原始值到目标计量单位的换算系数与零点偏移标定。
对标定记录进行单位转换计算并写入标定结果数据文件。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class UnitCalibrateService(BaseModuleService):
    MODULE = "unit_calibrate"
    ENTITY = "标定记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["标定编号", "测点编号", "原始单位", "目标单位", "换算系数", "零点偏移", "标定方式", "标定时间"]
    REQUIRED_FIELDS = ["标定编号", "测点编号", "原始单位"]
    STATUS_ORDER = ["草稿", "待标定", "已标定", "已失效"]
    ACTION_RULES = {"提交标定": "待标定", "确认标定": "已标定", "标记失效": "已失效"}
    NEGATIVE_ACTIONS = ["标记失效"]

    def calibrate_values(self, calibrate_id: int) -> dict[str, Any]:
        """对标定记录的原始数据进行单位换算计算并写入标定结果归档文件。"""
        cal = store.find(self.MODULE, calibrate_id)
        if cal is None:
            return {"error": f"标定记录 {calibrate_id} 不存在"}
        entry, _ = self.run_action(calibrate_id, "确认标定")
        point_key = entry["测点编号"]
        scale = float(entry.get("换算系数", "1"))
        offset = float(entry.get("零点偏移", "0"))
        # 读取对应测点的边缘采样数据
        samples_file = self._data_dir("edge_samples") / f"{point_key}.jsonl"
        samples = self._read_jsonl(samples_file)
        # 执行单位标定计算：原始值 × 换算系数 + 零点偏移
        calibrated = []
        for s in samples:
            raw = float(s.get("raw_value", 0))
            cal_val = round(raw * scale + offset, 4)
            calibrated.append({
                "point": s["point"],
                "timestamp": s["timestamp"],
                "raw_value_mV": raw,
                "calibrated_value_V": cal_val,
                "scale": scale,
                "offset": offset,
                # 数据转换输出：将标定计算结果写入持久化标定数据JSONL文件存储
                "calibrate_id": entry["标定编号"],
            })
        # 将标定结果写入持久化标定数据文件
        output_file = self._data_dir("calibrated") / f"{point_key}_calibrated.jsonl"
        written = self._write_jsonl(output_file, calibrated)
        entry["标定时间"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        return {"calibrate_id": entry["标定编号"], "point": point_key,
                "records_calibrated": written, "scale": scale, "offset": offset}

    def batch_calibrate(self, point_keys: list[str]) -> dict[str, Any]:
        """批量执行多个测点的单位标定转换，返回标定汇总数据集。"""
        results = []
        for pk in point_keys:
            cals = [r for r in store.rows(self.MODULE)
                    if r.get("测点编号") == pk and r.get("status") in ("待标定", "已标定")]
            for cal in cals:
                r = self.calibrate_values(cal["id"])
                results.append(r)
        # 写入批量标定汇总日志文件
        self._write_jsonl(
            self._data_dir("calibrated") / "batch_calibrate_summary.jsonl",
            [{"action": "batch_calibrate", "points": len(point_keys),
              "executed": len(results), "at": time.time()}]
        )
        return {"total_points": len(point_keys), "executed": len(results), "results": results}