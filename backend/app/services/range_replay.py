"""区间重采领域服务：维护传感器故障或标定变更引起的历史区间重采与覆盖方式。
对历史时间窗口的传感器数据进行重采样计算，写入重采覆盖率数据文件。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class RangeReplayService(BaseModuleService):
    MODULE = "range_replay"
    ENTITY = "重采任务"
    OPERATION_TERM = "水位"
    LIST_FIELDS = ["重采编号", "测点编号", "起始时刻", "截止时刻", "重采原因", "覆盖方式", "重采点数", "重采水位"]
    REQUIRED_FIELDS = ["重采编号", "测点编号", "起始时刻"]
    STATUS_ORDER = ["草稿", "待重采", "重采中", "已完成", "已取消"]
    ACTION_RULES = {"启动重采": "重采中", "标记完成": "已完成", "取消重采": "已取消"}
    NEGATIVE_ACTIONS = ["取消重采"]

    # 数据回填操作：对传感器历史区间执行重采样计算写入重采覆盖结果数据
    def replay_range(self, replay_id: int) -> dict[str, Any]:
        """对传感器历史区间执行重采样计算并写入重采覆盖结果数据文件。"""
        replay = store.find(self.MODULE, replay_id)
        if replay is None:
            return {"error": f"重采任务 {replay_id} 不存在"}
        self.run_action(replay_id, "启动重采")
        point_key = replay["测点编号"]
        reason = replay.get("重采原因", "传感器故障")
        overwrite = replay.get("覆盖方式", "追加")

        # 读取原始数据重采
        cal_file = self._data_dir("calibrated") / f"{point_key}_calibrated.jsonl"
        records = self._read_jsonl(cal_file)
        if not records:
            edge_file = self._data_dir("edge_samples") / f"{point_key}.jsonl"
            records = self._read_jsonl(edge_file)

        # 模拟按时间区间重采样
        replay_records = []
        for r in records:
            replay_records.append({
                "replay_id": replay["重采编号"],
                "point": point_key,
                "reason": reason,
                "overwrite_mode": overwrite,
                "original_timestamp": r.get("timestamp", ""),
                "replay_value": r.get("calibrated_value_V", r.get("raw_value_mV", 0)),
                "replayed_at": time.time(),
            })
        # 将重采结果写入区间重采数据文件
        output_file = self._data_dir("replayed_ranges") / f"{replay['重采编号']}_replay.jsonl"
        written = self._write_jsonl(output_file, replay_records)
        replay["重采点数"] = str(written)
        replay["重采水位"] = str(written)
        self.run_action(replay_id, "标记完成")
        return {"replay_id": replay["重采编号"], "point": point_key,
                "reason": reason, "records_replayed": written}

    def batch_replay_faulty(self, reason: str = "传感器故障校准") -> dict[str, Any]:
        """批量重采所有因传感器故障导致异常的测点历史区间数据。"""
        all_replays = store.rows(self.MODULE)
        faulty = [r for r in all_replays if "故障" in (r.get("重采原因", ""))]
        results = []
        for r in faulty:
            res = self.replay_range(r["id"])
            results.append(res)
        # 汇总批量重采记录
        self._write_jsonl(
            self._data_dir("replayed_ranges") / "batch_replay_summary.jsonl",
            [{"reason": reason, "total_replayed": len(results), "at": time.time()}]
        )
        return {"reason": reason, "total_replayed": len(results)}