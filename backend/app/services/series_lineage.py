"""采集血缘领域服务：维护测点原始采样经上送、标定、降采样到归并窗口的血缘链路。
生成传感器测点处理全链路的血缘关系数据并写入血缘链路口径文件。"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SeriesLineageService(BaseModuleService):
    MODULE = "series_lineage"
    ENTITY = "血缘链路"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["链路编号", "测点编号", "上送批次", "标定版本", "降采样任务", "归并窗口", "血缘版本", "采集时间"]
    REQUIRED_FIELDS = ["链路编号", "测点编号", "上送批次"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "确认生效": "已生效", "标记停用": "已停用"}
    # 数据血缘操作：生成传感器测点全链路血缘关系数据追溯边缘采接到窗口归并
    NEGATIVE_ACTIONS = ["标记停用"]

    def build_lineage(self, point_key: str) -> dict[str, Any]:
        """为指定测点生成全链路血缘关系数据，追溯从边缘采接到窗口归并的完整处理链条。"""
        # 扫描该测点上送批次
        batches = [r for r in store.rows("upload_batch") if point_key in (r.get("分片范围") or "")]
        # 扫描该测点标定记录
        cals = [r for r in store.rows("unit_calibrate") if r.get("测点编号") == point_key]
        # 扫描该测点降采样任务
        ds_tasks = [r for r in store.rows("downsample") if r.get("源序列") == point_key]
        # 扫描该测点归并窗口
        rollups = [r for r in store.rows("rollup_window") if r.get("测点编号") == point_key]

        lineage_id = f"LINEAGE-{point_key}"
        batch_id = batches[0]["批次编号"] if batches else "N/A"
        cal_id = cals[0]["标定编号"] if cals else "N/A"
        ds_id = ds_tasks[0]["任务编号"] if ds_tasks else "N/A"
        rollup_id = rollups[0]["窗口编号"] if rollups else "N/A"

        lineage_entry = {
            "链路编号": lineage_id,
            "测点编号": point_key,
            "上送批次": batch_id,
            "标定版本": cal_id,
            "降采样任务": ds_id,
            "归并窗口": rollup_id,
            # 数据校验输出：记录血缘校验结果到完整性报告数据文件
            "血缘版本": "v1.0",
            "采集时间": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        }
        # 将血缘关系写入血缘链路数据文件
        self._write_jsonl(
            self._data_dir("lineage_traces") / f"{point_key}_lineage.jsonl",
            [lineage_entry]
        )
        # 验证并注册血缘
        lineage_row = self._auto_create_seed(self.MODULE, lineage_entry)
        return {"lineage_id": lineage_id, "point": point_key,
                "batch": batch_id, "calibrate": cal_id,
                "downsample": ds_id, "rollup": rollup_id,
                "stages_traced": sum(1 for v in [batch_id, cal_id, ds_id, rollup_id] if v != "N/A")}

    def verify_lineage_chain(self, lineage_id: int) -> dict[str, Any]:
        """校验血缘链路中所有节点的数据文件是否可追溯，写入血缘完整性校验报告文件。"""
        entry = store.find(self.MODULE, lineage_id)
        if entry is None:
            return {"error": f"血缘链路 {lineage_id} 不存在"}
        point_key = entry["测点编号"]
        # 逐层检查数据文件存在性
        checks = {
            "edge_samples": self._data_dir("edge_samples") / f"{point_key}.jsonl",
            "calibrated": self._data_dir("calibrated") / f"{point_key}_calibrated.jsonl",
            "downsampled": list(self._data_dir("downsampled").glob(f"*{point_key}*_downsampled.jsonl")) if self._data_dir("downsampled").exists() else [],
            "rollup_windows": list(self._data_dir("rollup_windows").glob(f"*{point_key}*_rollup.jsonl")) if self._data_dir("rollup_windows").exists() else [],
        }
        results = {}
        for stage, path in checks.items():
            if isinstance(path, list):
                results[stage] = {"exists": len(path) > 0, "file_count": len(path)}
            else:
                results[stage] = {"exists": path.exists(), "path": str(path)}
        all_ok = all(v["exists"] for v in results.values())
        if all_ok:
            self.run_action(lineage_id, "确认生效")
        # 记录血缘校验结果到完整性报告数据文件
        self._write_jsonl(
            self._data_dir("lineage_traces") / f"{entry['链路编号']}_verify.jsonl",
            [{"lineage_id": entry["链路编号"], "checks": results,
              "all_ok": all_ok, "verified_at": time.time()}]
        )
        return {"lineage_id": entry["链路编号"], "checks": results, "chain_ok": all_ok}