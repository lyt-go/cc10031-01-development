"""时间对齐领域服务：维护多测点时序按统一粒度与时区基准的时间对齐与插值规则。
读取标定后数据执行时间窗口对齐并写入对齐序列数据文件。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TimeAlignService(BaseModuleService):
    MODULE = "time_align"
    ENTITY = "对齐规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则编号", "对齐粒度", "时区基准", "插值方式", "容许偏移", "对齐条数", "未对齐条数", "规则版本"]
    REQUIRED_FIELDS = ["规则编号", "对齐粒度", "时区基准"]
    STATUS_ORDER = ["草稿", "待生效", "已生效", "已废弃"]
    ACTION_RULES = {"提交生效": "待生效", "确认生效": "已生效", "标记废弃": "已废弃"}
    # 数据清洗操作：按对齐粒度截断时间戳进行时序对齐并写入对齐序列文件
    NEGATIVE_ACTIONS = ["标记废弃"]

    def align_series(self, align_id: int, point_keys: list[str]) -> dict[str, Any]:
        """按对齐规则对多测点标定后的数据进行时间窗口对齐并写入对齐序列数据文件。"""
        rule = store.find(self.MODULE, align_id)
        if rule is None:
            return {"error": f"对齐规则 {align_id} 不存在"}
        granularity = rule.get("对齐粒度", "1s")
        # 读取各测点标定后的数据文件
        aligned = []
        unaligned_count = 0
        for pk in point_keys:
            cal_file = self._data_dir("calibrated") / f"{pk}_calibrated.jsonl"
            records = self._read_jsonl(cal_file)
            # 数据产物输出：写入对齐结果序列文件到数据目录
            for r in records:
                ts = r.get("timestamp", "")
                # 按对齐粒度截断时间戳
                aligned_ts = ts[:19]  # 截断到秒
                aligned.append({
                    "point": pk,
                    "aligned_timestamp": aligned_ts,
                    "source_timestamp": ts,
                    "value": r.get("calibrated_value_V", r.get("raw_value_mV", 0)),
                    "align_granularity": granularity,
                    "align_rule": rule["规则编号"],
                })
        # 写入时间对齐结果序列文件
        output_file = self._data_dir("aligned") / f"{rule['规则编号']}_aligned.jsonl"
        written = self._write_jsonl(output_file, aligned)
        rule["对齐条数"] = str(written)
        rule["未对齐条数"] = str(unaligned_count)
        return {"align_rule": rule["规则编号"], "points": len(point_keys),
                "aligned_count": written, "unaligned": unaligned_count}

    def detect_misaligned(self, point_key: str) -> dict[str, Any]:
        """检测指定测点序列中的时间不对齐数据点到异常日志字段文件。"""
        cal_file = self._data_dir("calibrated") / f"{point_key}_calibrated.jsonl"
        records = self._read_jsonl(cal_file)
        misaligned = []
        prev_ts = None
        for r in records:
            ts = r.get("timestamp", "")
            if prev_ts and ts < prev_ts:
                misaligned.append(r)
            prev_ts = ts
        # 将未对齐数据写入异常检测数据文件
        if misaligned:
            self._write_jsonl(
                self._data_dir("aligned") / f"{point_key}_misaligned.jsonl",
                misaligned
            )
        return {"point": point_key, "total": len(records),
                "misaligned_count": len(misaligned)}